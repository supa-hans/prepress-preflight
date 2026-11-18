"""Evaluate PDF functions: sampled (type 0), exponential (2), stitching (3) and
PostScript calculator (4).

Colour spaces and gradients use these to turn tints into colours: a DeviceN
space's tint transform maps its inks' tints to CMYK, and a gradient's function
maps a position along it to tints. Converting spot colours to CMYK needs them
worked out exactly, not approximated, so each type follows the PDF reference
(ISO 32000-1, 7.10). Results are checked against MuPDF's own rendering in the
tests.
"""
from __future__ import annotations

import math

import pikepdf


class FunctionError(ValueError):
    pass


def _floats(arr, default=None):
    return [float(v) for v in arr] if arr is not None else default


def _clip(values, bounds):
    if not bounds:
        return values
    return [min(max(v, bounds[2 * i]), bounds[2 * i + 1]) for i, v in enumerate(values)]


def _interpolate(x, x0, x1, y0, y1):
    return y0 if x1 == x0 else y0 + (x - x0) * (y1 - y0) / (x1 - x0)


class Function:
    """A PDF function object, ready to evaluate: f([inputs]) -> [outputs]."""

    def __init__(self, obj):
        self.obj = obj
        d = obj.stream_dict if isinstance(obj, pikepdf.Stream) else obj
        self.kind = int(d.get("/FunctionType", -1))
        self.domain = _floats(d.get("/Domain"), [0.0, 1.0])
        self.range = _floats(d.get("/Range"))
        if self.kind == 0:
            self._init_sampled(obj, d)
        elif self.kind == 2:
            self.c0 = _floats(d.get("/C0"), [0.0])
            self.c1 = _floats(d.get("/C1"), [1.0])
            self.n = float(d.get("/N", 1))
        elif self.kind == 3:
            self.parts = [Function(f) for f in d.Functions]
            self.bounds = _floats(d.get("/Bounds"), [])
            self.encode = _floats(d.Encode)
        elif self.kind == 4:
            self.program = _parse_postscript(obj.read_bytes())
        else:
            raise FunctionError(f"function type {self.kind} is not supported")

    @property
    def outputs(self) -> int:
        if self.range:
            return len(self.range) // 2
        if self.kind == 2:
            return len(self.c0)
        if self.kind == 3:
            return self.parts[0].outputs
        raise FunctionError("number of outputs unknown")

    def __call__(self, inputs: list[float]) -> list[float]:
        x = _clip([float(v) for v in inputs], self.domain)
        if self.kind == 0:
            out = self._sampled(x)
        elif self.kind == 2:
            t = x[0]
            f = t ** self.n if self.n != 1 else t
            out = [a + f * (b - a) for a, b in zip(self.c0, self.c1)]
        elif self.kind == 3:
            out = self._stitched(x[0])
        else:
            out = _run_postscript(self.program, x)
        return _clip(out, self.range)

    # ---- type 0 ----------------------------------------------------------
    def _init_sampled(self, obj, d):
        self.size = [int(v) for v in d.Size]
        self.bits = int(d.BitsPerSample)
        if self.bits not in (1, 2, 4, 8, 12, 16, 24, 32):
            raise FunctionError("unsupported bits per sample")
        m = len(self.size)
        self.encode = _floats(d.get("/Encode"), [v for s in self.size for v in (0.0, float(s - 1))])
        self.decode = _floats(d.get("/Decode"), self.range)
        if self.range is None:
            raise FunctionError("sampled function has no Range")
        n = len(self.range) // 2
        data = obj.read_bytes()
        count = n * math.prod(self.size)
        big = int.from_bytes(data, "big")
        total_bits = len(data) * 8
        if count * self.bits > total_bits:
            raise FunctionError("sampled function has too few samples")
        top = (1 << self.bits) - 1
        self.samples = [(big >> (total_bits - (i + 1) * self.bits)) & top for i in range(count)]
        self.top, self.n_out, self.m = top, n, m

    def _sample_at(self, index):
        flat, stride = 0, 1
        for i, s in zip(index, self.size):
            flat += i * stride
            stride *= s
        base = flat * self.n_out
        return [self.decode[2 * j] + self.samples[base + j] / self.top * (self.decode[2 * j + 1] - self.decode[2 * j])
                for j in range(self.n_out)]

    def _sampled(self, x):
        # encode each input to sample coordinates, then interpolate (multilinear)
        coords = []
        for i, v in enumerate(x):
            e = _interpolate(v, self.domain[2 * i], self.domain[2 * i + 1], self.encode[2 * i], self.encode[2 * i + 1])
            coords.append(min(max(e, 0.0), self.size[i] - 1))
        lows = [min(int(math.floor(c)), s - 2) if s > 1 else 0 for c, s in zip(coords, self.size)]
        fracs = [c - lo if s > 1 else 0.0 for c, lo, s in zip(coords, lows, self.size)]
        out = [0.0] * self.n_out
        for corner in range(1 << self.m):
            weight, index = 1.0, []
            for i in range(self.m):
                step = (corner >> i) & 1
                if self.size[i] == 1 and step:
                    weight = 0.0
                    break
                weight *= fracs[i] if step else 1 - fracs[i]
                index.append(lows[i] + step)
            if weight:
                for j, v in enumerate(self._sample_at(index)):
                    out[j] += weight * v
        return out

    # ---- type 3 ----------------------------------------------------------
    def _stitched(self, t):
        d0, d1 = self.domain[0], self.domain[1]
        edges = [d0] + self.bounds + [d1]
        k = 0
        while k < len(self.bounds) and t >= self.bounds[k]:
            k += 1
        lo, hi = edges[k], edges[k + 1]
        e0, e1 = self.encode[2 * k], self.encode[2 * k + 1]
        return self.parts[k]([_interpolate(t, lo, hi, e0, e1)])


# ---- type 4: the PostScript calculator ---------------------------------------

def _parse_postscript(data: bytes):
    text = data.decode("latin-1").replace("{", " { ").replace("}", " } ")
    tokens = text.split()
    pos = 0

    def block():
        nonlocal pos
        items = []
        while pos < len(tokens):
            tok = tokens[pos]
            pos += 1
            if tok == "{":
                items.append(block())
            elif tok == "}":
                return items
            else:
                try:
                    items.append(int(tok) if tok.lstrip("+-").isdigit() else float(tok))
                except ValueError:
                    items.append(tok)
        return items

    while pos < len(tokens) and tokens[pos] != "{":
        pos += 1
    pos += 1
    return block()


def _run_postscript(program, inputs):
    stack = list(inputs)
    _execute(program, stack)
    return [float(v) for v in stack]


def _execute(program, s):
    i = 0
    while i < len(program):
        op = program[i]
        i += 1
        if isinstance(op, (int, float)) and not isinstance(op, bool):
            s.append(op)
            continue
        if isinstance(op, list):  # a procedure, taken by the if/ifelse that follows
            if i < len(program) and program[i] == "if":
                cond = s.pop()
                if cond:
                    _execute(op, s)
                i += 1
            elif i < len(program) and isinstance(program[i], list) and i + 1 < len(program) and program[i + 1] == "ifelse":
                other = program[i]
                cond = s.pop()
                _execute(op if cond else other, s)
                i += 2
            else:
                raise FunctionError("procedure not followed by if or ifelse")
            continue
        _operator(op, s)


def _num(v):
    return float(v) if not isinstance(v, bool) else v


def _operator(op, s):
    pop = s.pop
    if op == "add": b, a = pop(), pop(); s.append(a + b)
    elif op == "sub": b, a = pop(), pop(); s.append(a - b)
    elif op == "mul": b, a = pop(), pop(); s.append(a * b)
    elif op == "div": b, a = pop(), pop(); s.append(a / b if b else 0.0)
    elif op == "idiv": b, a = pop(), pop(); s.append(int(a / b) if b else 0)
    elif op == "mod": b, a = pop(), pop(); s.append(int(math.fmod(a, b)) if b else 0)
    elif op == "neg": s.append(-pop())
    elif op == "abs": s.append(abs(pop()))
    elif op == "ceiling": s.append(float(math.ceil(pop())))
    elif op == "floor": s.append(float(math.floor(pop())))
    elif op == "round": s.append(float(math.floor(pop() + 0.5)))
    elif op == "truncate": s.append(float(math.trunc(pop())))
    elif op == "sqrt": s.append(math.sqrt(max(pop(), 0.0)))
    elif op == "sin": s.append(math.sin(math.radians(pop())))
    elif op == "cos": s.append(math.cos(math.radians(pop())))
    elif op == "atan": b, a = pop(), pop(); s.append(math.degrees(math.atan2(a, b)) % 360)
    elif op == "exp": b, a = pop(), pop(); s.append(a ** b)
    elif op == "ln": s.append(math.log(pop()))
    elif op == "log": s.append(math.log10(pop()))
    elif op == "cvi": s.append(int(pop()))
    elif op == "cvr": s.append(float(pop()))
    elif op == "dup": s.append(s[-1])
    elif op == "pop": pop()
    elif op == "exch": s[-1], s[-2] = s[-2], s[-1]
    elif op == "copy":
        n = int(pop())
        if n:
            s.extend(s[-n:])
    elif op == "index": n = int(pop()); s.append(s[-1 - n])
    elif op == "roll":
        j, n = int(pop()), int(pop())
        if n:
            j %= n
            top = s[-n:]
            del s[-n:]
            s.extend(top[-j:] + top[:-j] if j else top)
    elif op == "true": s.append(True)
    elif op == "false": s.append(False)
    elif op in ("eq", "ne", "gt", "ge", "lt", "le"):
        b, a = pop(), pop()
        s.append({"eq": a == b, "ne": a != b, "gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[op])
    elif op in ("and", "or", "xor"):
        b, a = pop(), pop()
        if isinstance(a, bool):
            s.append({"and": a and b, "or": a or b, "xor": a != b}[op])
        else:
            a, b = int(a), int(b)
            s.append({"and": a & b, "or": a | b, "xor": a ^ b}[op])
    elif op == "not":
        a = pop()
        s.append((not a) if isinstance(a, bool) else ~int(a))
    elif op == "bitshift": n, a = int(pop()), int(pop()); s.append(a << n if n >= 0 else a >> -n)
    else:
        raise FunctionError(f"PostScript operator {op!r} is not supported")
