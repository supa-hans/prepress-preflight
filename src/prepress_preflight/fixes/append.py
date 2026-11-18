"""Write changed objects to a new PDF by appending them to the original bytes.

This is a PDF incremental update, the same method as a plain Save in Acrobat.
The new file is the original file, byte for byte, followed by the replacement
objects, a cross-reference section for them and a trailer pointing back at
the original's. Nothing in the original part is touched, so it can be proved
unchanged by comparing bytes, and the update can be undone by cutting the new
file back to the original length.

Only ever writes to a new path. Refuses the input path and any existing file.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys

import pikepdf


class AppendError(Exception):
    pass


_SYNTAX_CHECK = """
import json, sys, pikepdf
with pikepdf.open(sys.argv[1]) as pdf:
    print(json.dumps([str(p) for p in pdf.check_pdf_syntax()]))
"""


def syntax_problems(path) -> set:
    """pikepdf's structure check (qpdf --check), run in a separate process.

    On linearised files the check leaves this process's stderr silently
    discarding everything written to it afterwards (seen with pikepdf 10.18),
    which would hide error messages. A separate process keeps that away.
    """
    done = subprocess.run([sys.executable, "-I", "-c", _SYNTAX_CHECK, os.fspath(path)],
                          capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise AppendError(f"verification failed: the structure check could not run ({done.stderr.strip()[-300:]})")
    return {p.replace(os.fspath(path), "FILE") for p in json.loads(done.stdout.strip().splitlines()[-1])}


def _startxref(path) -> int:
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - 2048))
        tail = f.read()
    at = tail.rfind(b"startxref")
    if at < 0:
        raise AppendError("the file has no startxref")
    return int(tail[at + 9:].split()[0])


def _uses_xref_stream(path, offset) -> bool:
    with open(path, "rb") as f:
        f.seek(offset)
        return not f.read(16).lstrip().startswith(b"xref")


def _xref_table(entries) -> bytes:
    """Classic table: one subsection per object, since they are rarely consecutive."""
    out = [b"xref\n"]
    for num, gen, offset in sorted(entries):
        out.append(f"{num} 1\n{offset:010d} {gen:05d} n\r\n".encode())
    return b"".join(out)


def _serialise(num, gen, obj) -> bytes:
    """One indirect object as file bytes. Streams keep their encoded data as is."""
    if isinstance(obj, pikepdf.Stream):
        raw = obj.read_raw_bytes()
        head = pikepdf.Dictionary({k: obj.stream_dict[k] for k in obj.stream_dict.keys()})
        head["/Length"] = len(raw)
        return (f"{num} {gen} obj\n".encode() + head.unparse(resolved=False) + b"\nstream\n" + raw +
                b"\nendstream\nendobj\n")
    return f"{num} {gen} obj\n".encode() + obj.unparse(resolved=True) + b"\nendobj\n"


def append_update(src: str, dst: str, changes: dict, additions: dict | None = None, extra_check=None) -> None:
    """Write `dst` as `src` plus new versions of the objects in `changes`, and
    the brand-new objects in `additions`.

    changes:   {(object number, generation): replacement pikepdf.Dictionary}
    additions: {(object number, generation): new pikepdf object}; numbers must
               be beyond the original's highest
    extra_check(path): further verification run on the finished file before it
               is put in place; raise to refuse it
    """
    additions = additions or {}
    if os.path.exists(dst):
        raise AppendError(f"{dst} already exists; choose a new file name")
    if os.path.realpath(dst) == os.path.realpath(src):
        raise AppendError("the new file must not be the input file")
    original_size = os.path.getsize(src)
    prev = _startxref(src)
    xref_stream = _uses_xref_stream(src, prev)
    with pikepdf.open(src) as pdf:
        if pdf.is_encrypted:
            raise AppendError("the file is encrypted; it cannot be updated safely")
        trailer = pdf.trailer
        size = int(trailer.Size)
        if any(num < size for num, _ in additions):
            raise AppendError("new objects must not reuse the original's object numbers")
        bodies = {og: _serialise(og[0], og[1], obj) for og, obj in changes.items()}
        bodies.update({og: _serialise(og[0], og[1], obj) for og, obj in additions.items()})
        size = max([size] + [num + 1 for num, _ in additions])
        digest = hashlib.md5(b"".join(bodies[og] for og in sorted(bodies))).digest()
        first_id = trailer.ID[0] if "/ID" in trailer else pikepdf.String(digest)
        new_trailer = pikepdf.Dictionary(Root=trailer.Root, Prev=prev, ID=pikepdf.Array([first_id, pikepdf.String(digest)]))
        if "/Info" in trailer:
            new_trailer["/Info"] = trailer.Info

    tmp = dst + ".partial"
    try:
        shutil.copyfile(src, tmp)
        with open(tmp, "ab") as out:
            out.write(b"\n")
            entries = []
            for (num, gen), body in sorted(bodies.items()):
                entries.append((num, gen, out.tell()))
                out.write(body)
            xref_at = out.tell()
            if xref_stream:
                # The original uses a cross-reference stream, so the update must too.
                stream_num = size
                entries.append((stream_num, 0, xref_at))
                rows, index = b"", []
                for num, gen, offset in sorted(entries):
                    rows += bytes([1]) + offset.to_bytes(8, "big") + gen.to_bytes(2, "big")
                    index += [num, 1]
                head = pikepdf.Dictionary(new_trailer)
                head["/Type"] = pikepdf.Name.XRef
                head["/Size"] = size + 1
                head["/Index"] = index
                head["/W"] = [1, 8, 2]
                head["/Length"] = len(rows)
                out.write(f"{stream_num} 0 obj\n".encode() + head.unparse(resolved=False) + b"\nstream\n" + rows +
                          b"\nendstream\nendobj\n")
            else:
                new_trailer["/Size"] = size
                out.write(_xref_table(entries) + b"trailer\n" + new_trailer.unparse(resolved=False) + b"\n")
            out.write(f"startxref\n{xref_at}\n%%EOF\n".encode())
        verify_append(src, tmp, original_size, changes, additions)
        if extra_check is not None:
            extra_check(tmp)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _same_prefix(src, dst, length) -> bool:
    with open(src, "rb") as a, open(dst, "rb") as b:
        remaining = length
        while remaining:
            n = min(remaining, 1 << 24)
            if a.read(n) != b.read(n):
                return False
            remaining -= n
    return True


def _same_object(written, intended) -> bool:
    """Whether a written object is the one intended. A stream is compared by its
    dictionary (less /Length, which is only filled in when written) and its raw data."""
    if isinstance(intended, pikepdf.Stream):
        if not isinstance(written, pikepdf.Stream):
            return False
        def head(stream):
            d = stream.stream_dict
            return pikepdf.Dictionary({k: d[k] for k in d.keys() if k != "/Length"}).unparse(resolved=True)
        return head(written) == head(intended) and written.read_raw_bytes() == intended.read_raw_bytes()
    return written.unparse(resolved=True) == intended.unparse(resolved=True)


def verify_append(src, dst, original_size, changes, additions=None) -> None:
    """Prove the new file is the original plus exactly the intended changes. Raises if not."""
    if not _same_prefix(src, dst, original_size):
        raise AppendError("verification failed: the original bytes were not preserved")
    with pikepdf.open(src) as before, pikepdf.open(dst) as after:
        # Only problems the update introduced count: many real files already
        # carry warnings (damaged streams, stale linearisation hints), and the
        # update neither causes nor can cure those.
        new = sorted(syntax_problems(dst) - syntax_problems(src))
        if new:
            raise AppendError("verification failed: the new file has structural problems: " + "; ".join(new[:3]))
        # unparse(resolved=True) prints an object's own contents (references inside
        # it stay as references); resolved=False would print only "N G R".
        for obj in before.objects:
            if not obj.is_indirect:
                continue
            og = obj.objgen
            new = after.get_object(og)
            if og in changes:
                if not _same_object(new, changes[og]):
                    raise AppendError(f"verification failed: object {og[0]} does not hold the intended change")
            elif new.unparse(resolved=True) != obj.unparse(resolved=True):
                raise AppendError(f"verification failed: object {og[0]} changed although it was not meant to")
        additions = additions or {}
        for og, obj in additions.items():
            new = after.get_object(og)
            same = new.unparse(resolved=True) == obj.unparse(resolved=True) if not isinstance(obj, pikepdf.Stream) else (
                isinstance(new, pikepdf.Stream) and new.read_raw_bytes() == obj.read_raw_bytes())
            if not same:
                raise AppendError(f"verification failed: new object {og[0]} was not written as intended")
        extra = 1 if _uses_xref_stream(dst, _startxref(dst)) else 0
        if len(after.objects) != len(before.objects) + len(additions) + extra:
            raise AppendError("verification failed: the new file has unexpected extra objects")
