"use strict";
(() => {
  const KEY = window.PREFLIGHT_KEY;
  const $ = (id) => document.getElementById(id);
  const ORDER = { fail: 0, unchecked: 1, warn: 2, note: 3 };
  const LABEL = { fail: "Fail", unchecked: "Could not check", warn: "Warn", note: "Note" };
  const state = { report: null, page: 1, zoom: null, selected: null, path: null, mode: "inspect", which: "before",
                  preview: null, previewBusy: false, queue: [], queueReport: null, queueEffect: null,
                  exported: null, plate: "", inksFor: null, here: null, hereHover: null, herePoint: null };

  // What each fix asks for. Field values become the same options the command line takes.
  const FIXES = {
    "trim-from-marks": { help: "Sets the trim box to where the crop marks point, and the bleed box to that plus the bleed.",
      fields: [["min-bleed", "Bleed (mm)", "number", "3"]] },
    "add-bleed": { help: "Makes bleed where artwork stops at the trim, beneath the artwork and outside the trim.",
      fields: [["method", "Method", "select", "mirror", ["mirror", "stretch"]], ["min-bleed", "Bleed (mm)", "number", "3"],
               ["clip-to-bleed", "Clip everything to the bleed box", "check"], ["marks", "Add trim marks", "check"],
               ["bound", "Bound job: no bleed at the spine", "check"]] },
    "add-marks": { help: "Adds trim marks outside the bleed box, without making bleed.",
      fields: [["clip-to-bleed", "Clip everything to the bleed box", "check"]] },
    "set-boxes": { help: "Sets the trim box to the job size and makes sure the bleed box gives the bleed.",
      fields: [["trim", "Job size (W x H mm)", "text", "", "210x297"], ["min-bleed", "Bleed (mm)", "number", "3"]] },
    "scale": { help: "Scales the whole page so the trim is the job size.",
      fields: [["trim", "Job size (W x H mm)", "text", "", "148x210"],
               ["shape", "If the shape differs by more than 1%", "select", "", ["", "fill", "fit"], ["Refuse", "Fill (crop to cover)", "Fit (fit inside)"]]] },
    "spot-to-cmyk": { help: "Converts spot colours to CMYK using their own CMYK definition.",
      fields: [["spot", "Only these spots (one per line; leave empty for all)", "lines", ""]] },
    "rename-spot": { help: "Renames a spot colour, or merges it into one already in the file.",
      fields: [["spot", "OLD=NEW, one per line", "lines", "", "Pantone 185C=PANTONE 185 C"]] },
  };

  async function api(path, opts = {}) {
    const sep = path.includes("?") ? "&" : "?";
    const res = await fetch(`${path}${sep}key=${encodeURIComponent(KEY)}`, opts);
    const body = res.headers.get("Content-Type")?.includes("json") ? await res.json() : null;
    if (!res.ok) throw new Error((body && body.error) || `Request failed (${res.status})`);
    return body;
  }

  function pageImage(n, width, which = "before", plate = "") {
    let tag = plate ? `&plate=${encodeURIComponent(plate)}` : "";
    if (which === "after" && state.preview) tag += `&which=after&v=${encodeURIComponent(state.preview.fix + state.preview.options.join(" "))}`;
    if (which === "base") tag += `&which=base&v=${state.queue.length}-${encodeURIComponent(state.queue.map((q) => q.fix).join("+"))}`;
    return `/api/page?n=${n}&w=${Math.round(width)}${tag}&key=${encodeURIComponent(KEY)}`;
  }

  async function open(body) {
    $("path-error").hidden = true;
    try {
      show(await api("/api/open", { method: "POST", body: JSON.stringify(body) }));
      poll();
    } catch (e) {
      $("path-error").textContent = e.message;
      $("path-error").hidden = false;
    }
  }

  async function poll() {
    const s = await api("/api/state");
    show(s);
    if (s.busy || s.preview_busy) setTimeout(poll, 500);
  }

  function renderRecent(list) {
    const ul = $("recent-list");
    ul.textContent = "";
    $("recent").hidden = !list.length;
    for (const r of list) {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.disabled = !r.exists;
      const name = document.createElement("span");
      name.textContent = r.name + (r.exists ? "" : " (moved or deleted)");
      if (!r.exists) name.className = "gone";
      const where = document.createElement("span");
      where.className = "where";
      where.textContent = r.path;
      b.append(name, where);
      b.onclick = () => open({ path: r.path });
      li.append(b);
      ul.append(li);
    }
  }

  async function loadRecentMenu() {
    let list = [];
    try { list = (await api("/api/recent")).recent; } catch (e) { list = []; }
    const menu = $("recent-menu");
    menu.textContent = "";
    const first = document.createElement("option");
    first.value = ""; first.textContent = "Recent…";
    menu.append(first);
    for (const r of list.filter((x) => x.exists)) {
      const o = document.createElement("option");
      o.value = r.path; o.textContent = r.name; o.title = r.path;
      menu.append(o);
    }
    menu.hidden = list.length === 0;
  }

  function show(s) {
    $("name").textContent = s.name || "No file open";
    if (!s.path) renderRecent(s.recent || []);
    document.title = s.name ? `${s.name} · Preflight` : "Preflight";
    $("busy").hidden = !s.busy;
    if (s.error) {
      $("empty").hidden = false;
      $("sheet").hidden = true;
      $("path-error").textContent = s.error;
      $("path-error").hidden = false;
    }
    if (!s.report) {
      $("status").hidden = true;
      if (!s.path) { $("empty").hidden = false; $("sheet").hidden = true; }
      return;
    }
    if (state.path !== s.path) {
      state.path = s.path; state.page = 1; state.zoom = null; state.selected = null; state.mainLoaded = false;
    }
    state.report = s.report;
    const previewChanged = JSON.stringify(s.preview) !== JSON.stringify(state.preview);
    state.preview = s.preview;
    state.previewBusy = s.preview_busy;
    if (previewChanged) { if (!state.preview || state.preview.empty) state.which = "before"; renderProposal(); }
    $("preview-status").textContent = s.preview_busy ? "Working…" : "";
    $("preview").disabled = s.preview_busy;
    const queueChanged = JSON.stringify(s.queue) !== JSON.stringify(state.queue) || s.exported !== state.exported;
    state.queue = s.queue || [];
    state.queueReport = s.queue_report;
    state.queueEffect = s.queue_effect;
    state.exported = s.exported;
    $("export-error").hidden = !s.export_error;
    $("export-error").textContent = s.export_error || "";
    if (queueChanged) renderReview();
    $("keep").hidden = !(state.preview && !state.preview.empty && !state.preview.error) || s.preview_busy;
    $("empty").hidden = true;
    $("sheet").hidden = false;
    const status = $("status");
    status.textContent = { PASS: "Pass", WARN: "Warn", FAIL: "Fail" }[s.report.status] || s.report.status;
    status.className = `badge ${s.report.status}`;
    status.hidden = false;
    renderThumbs();
    renderFindings();
    renderPage();
  }

  // What the canvas shows: the original, the result of the review list ("base"), or a preview.
  function view() {
    const queued = state.queue.length > 0 && state.queueReport;
    if (state.mode === "propose") {
      if (state.which === "after" && state.preview && state.preview.report) return ["after", state.preview.report];
      return queued ? ["base", state.queueReport] : ["before", state.report];
    }
    if ((state.mode === "review" || state.mode === "export") && queued && state.which === "after") return ["base", state.queueReport];
    return ["before", state.report];
  }

  function showingAfter() {
    return view()[0] !== "before";
  }

  function geometry(n) {
    return view()[1].page_geometry.find((g) => g.page === n);
  }

  function failPages() {
    const pages = new Set();
    for (const f of state.report.findings) if (f.severity === "fail") (f.pages.length ? f.pages : []).forEach((p) => pages.add(p));
    return pages;
  }

  function renderThumbs() {
    const nav = $("thumbs");
    nav.textContent = "";
    const failing = failPages();
    for (let n = 1; n <= state.report.pages; n++) {
      const b = document.createElement("button");
      b.className = "thumb" + (n === state.page ? " on" : "");
      b.setAttribute("aria-label", `Page ${n}`);
      const img = document.createElement("img");
      img.loading = "lazy";
      img.dataset.src = pageImage(n, 160);
      if (state.mainLoaded) img.src = img.dataset.src;
      img.alt = "";
      const label = document.createElement("span");
      label.className = "n";
      label.textContent = n;
      b.append(img, label);
      if (failing.has(n)) { const d = document.createElement("span"); d.className = "dot"; d.title = "A fail on this page"; b.append(d); }
      b.onclick = () => { state.page = n; state.selected = null; renderThumbs(); renderFindings(); renderPage(); };
      nav.append(b);
    }
    nav.querySelector(".thumb.on")?.scrollIntoView({ block: "nearest" });
  }

  function loadThumbs() {
    // after the page in view, so it is never stuck behind dozens of thumbnails
    state.mainLoaded = true;
    for (const img of $("thumbs").querySelectorAll("img")) if (!img.src) img.src = img.dataset.src;
  }

  function pagesText(pages) {
    if (!pages.length) return "Whole document";
    if (pages.length === state.report.pages && pages.length > 1) return `All ${pages.length} pages`;
    const shown = pages.slice(0, 12).join(", ");
    return (pages.length === 1 ? "Page " : "Pages ") + shown + (pages.length > 12 ? " …" : "");
  }

  function renderFindings() {
    const r = state.report;
    const counts = { fail: 0, warn: 0, unchecked: 0 };
    r.findings.forEach((f) => { if (f.severity in counts) counts[f.severity]++; });
    const bits = [];
    if (counts.fail) bits.push(`${counts.fail} fail${counts.fail > 1 ? "s" : ""}`);
    if (counts.unchecked) bits.push(`${counts.unchecked} could not check`);
    if (counts.warn) bits.push(`${counts.warn} warning${counts.warn > 1 ? "s" : ""}`);
    $("summary").textContent = `${r.pages} page${r.pages > 1 ? "s" : ""} · ` + (bits.join(", ") || "nothing to fix");

    const list = $("findings");
    list.textContent = "";
    const items = r.findings.map((f, i) => ({ f, i })).sort((a, b) => ORDER[a.f.severity] - ORDER[b.f.severity]);
    for (const { f, i } of items) {
      const b = document.createElement("button");
      b.className = "finding" + (state.selected === i ? " on" : "");
      const head = document.createElement("div");
      head.className = "head";
      const sev = document.createElement("span");
      sev.className = `sev sev-${f.severity}`;
      sev.textContent = LABEL[f.severity];
      const check = document.createElement("span");
      check.className = "check";
      check.textContent = f.check;
      head.append(sev, check);
      const msg = document.createElement("div");
      msg.className = "msg";
      msg.textContent = f.message;
      const where = document.createElement("div");
      where.className = "where";
      where.textContent = pagesText(f.pages) + (f.locations.length ? " · shown on the page" : "");
      b.append(head, msg, where);
      b.onclick = () => select(i);
      for (const fix of (r.suggestions && r.suggestions[i]) || []) {
        const s = document.createElement("span");
        s.className = "suggest";
        s.setAttribute("role", "button");
        s.tabIndex = 0;
        s.textContent = `Fix: ${fix}`;
        const go = (e) => { e.stopPropagation(); chooseFix(fix); };
        s.onclick = go;
        s.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") go(e); };
        b.append(s);
      }
      list.append(b);
    }
    const passed = $("passed");
    passed.hidden = !r.passed_checks.length || state.mode === "export";
    passed.querySelector("summary").textContent = `Passed ${r.passed_checks.length} checks`;
    passed.querySelector("p").textContent = r.passed_checks.join(", ");
  }

  function select(i) {
    state.selected = state.selected === i ? null : i;
    const f = state.report.findings[i];
    if (state.selected !== null) {
      const onPage = f.locations.find((l) => l.view) || null;
      const target = onPage ? onPage.page : f.pages[0];
      if (target && target !== state.page) { state.page = target; renderThumbs(); }
    }
    renderFindings();
    renderPage();
  }

  function fitZoom(g) {
    const box = $("canvas").getBoundingClientRect();
    return Math.min((box.width - 48) / g.width, (box.height - 48) / g.height);
  }

  function renderPage() {
    const g = geometry(state.page);
    if (!g) return;
    const zoom = state.zoom || fitZoom(g);
    const w = g.width * zoom, h = g.height * zoom;
    const img = $("page-img");
    const want = Math.min(4000, Math.ceil((w * (window.devicePixelRatio || 1)) / 200) * 200);
    const src = pageImage(state.page, want, view()[0], state.plate);
    loadInks();
    if (img.dataset.src !== src) {
      img.dataset.src = src;
      img.onload = () => { if (!state.mainLoaded) loadThumbs(); };
      img.src = src;
    }
    img.style.width = `${w}px`;
    img.style.height = `${h}px`;
    // 100% shows the page at its printed size (72 points to the inch, 96 CSS pixels to the inch)
    const pct = zoom * 100 * 96 / 72;
    $("zoom-label").textContent = pct < 10 ? `${pct.toFixed(1)}%` : `${Math.round(pct)}%`;

    const svg = $("overlay");
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    svg.setAttribute("viewBox", `0 0 ${g.width} ${g.height}`);
    svg.textContent = "";
    const rect = (b, cls, color, dash, width, pad = 0) => {
      const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      r.setAttribute("x", b[0] - pad); r.setAttribute("y", b[1] - pad);
      r.setAttribute("width", Math.max(b[2] - b[0], 0.5) + 2 * pad); r.setAttribute("height", Math.max(b[3] - b[1], 0.5) + 2 * pad);
      r.setAttribute("fill", pad ? color : "none");
      if (pad) r.setAttribute("fill-opacity", "0.15");
      r.setAttribute("stroke", color);
      r.setAttribute("vector-effect", "non-scaling-stroke");
      r.setAttribute("stroke-width", width);
      if (dash) r.setAttribute("stroke-dasharray", dash);
      r.classList.add(cls);
      svg.append(r);
    };
    const css = getComputedStyle(document.documentElement);
    if ($("show-media").checked && g.media) rect(g.media, "media", css.getPropertyValue("--media"), "2 3", 1);
    if ($("show-bleed").checked && g.bleed) rect(g.bleed, "bleed", css.getPropertyValue("--bleed"), "6 4", 1);
    if ($("show-trim").checked && g.trim) rect(g.trim, "trim", css.getPropertyValue("--trim"), "", 1);
    if (state.herePoint && state.herePoint.page === state.page) {
      const [px, py] = state.herePoint.at;
      const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      c.setAttribute("cx", px); c.setAttribute("cy", py); c.setAttribute("r", 4 / zoom);
      c.setAttribute("fill", "none"); c.setAttribute("stroke", css.getPropertyValue("--accent"));
      c.setAttribute("stroke-width", 2); c.setAttribute("vector-effect", "non-scaling-stroke");
      svg.append(c);
      if (state.hereHover && state.hereHover.view) rect(state.hereHover.view, "obj", css.getPropertyValue("--accent"), "", 2, 2 / zoom);
    }
    if (state.selected !== null && !showingAfter()) {
      const f = state.report.findings[state.selected];
      // padded by a few screen pixels, so that a word or a hairline still shows at Fit
      for (const l of f.locations) if (l.page === state.page && l.view) rect(l.view, "hit", css.getPropertyValue("--hit"), "", 2, 4 / zoom);
    }
  }

  // ---- objects under a point ---------------------------------------------------

  async function inspectAt(event) {
    const g = geometry(state.page);
    if (!g) return;
    const img = $("page-img").getBoundingClientRect();
    const x = (event.clientX - img.left) / img.width * g.width;
    const y = (event.clientY - img.top) / img.height * g.height;
    state.herePoint = { page: state.page, at: [x, y] };
    state.hereHover = null;
    let objects = [];
    try { objects = (await api(`/api/objects?n=${state.page}&x=${x.toFixed(2)}&y=${y.toFixed(2)}&which=${view()[0]}`)).objects; }
    catch (e) { objects = []; }
    showObjects(objects);
    renderPage();
  }

  function showObjects(objects) {
    $("here").hidden = false;
    $("here-none").hidden = objects.length > 0;
    const ol = $("here-list");
    ol.textContent = "";
    for (const o of objects) {
      const li = document.createElement("li");
      const kind = document.createElement("div");
      kind.className = "o-kind";
      kind.textContent = `${o.kind}: ${o.colour}`;
      const props = document.createElement("div");
      props.className = "o-props";
      const bits = [];
      if (o.ink !== null && o.ink !== undefined) bits.push(`total ink ${o.ink}%`);
      if (o.width !== undefined) bits.push(`${o.width} pt line`);
      if (o.font) bits.push(o.font);
      if (o.pixels) bits.push(`${o.pixels[0]} × ${o.pixels[1]} px`);
      if (o.ppi) bits.push(`${o.ppi} ppi`);
      if (o.inside) bits.push(`inside ${o.inside}`);
      props.textContent = bits.join(" · ");
      li.append(kind, props);
      const flags = [];
      if (o.overprint) flags.push("overprint");
      if (o.blend && o.blend !== "Normal") flags.push(`blend: ${o.blend}`);
      if (o.group_blend) flags.push(`in a ${o.group_blend} group`);
      if (flags.length) { const f = document.createElement("div"); f.className = "o-flag small"; f.textContent = flags.join(" · "); li.append(f); }
      li.onmouseenter = () => { state.hereHover = o; renderPage(); };
      li.onmouseleave = () => { state.hereHover = null; renderPage(); };
      ol.append(li);
    }
  }

  function closeObjects() {
    $("here").hidden = true;
    state.herePoint = null; state.hereHover = null;
    if (state.report) renderPage();
  }

  $("page-img").addEventListener("click", inspectAt);
  $("here-close").onclick = closeObjects;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("here").hidden) closeObjects(); });

  // ---- plates ---------------------------------------------------------------

  async function loadInks() {
    const which = view()[0];
    const key = `${state.path}|${which}|${state.page}|${state.queue.length}|${state.preview ? state.preview.fix + state.preview.options.join(" ") : ""}`;
    if (state.inksFor === key) return;
    state.inksFor = key;
    let inks = [];
    try { inks = (await api(`/api/inks?n=${state.page}&which=${which}`)).inks; } catch (e) { inks = []; }
    if (state.inksFor !== key) return;
    const select = $("plate");
    select.textContent = "";
    const all = document.createElement("option");
    all.value = ""; all.textContent = "All inks";
    select.append(all);
    for (const ink of inks) {
      const o = document.createElement("option");
      o.value = ink.name;
      const tags = [ink.process ? "" : "spot", ink.printing ? "" : "non-printing"].filter(Boolean).join(", ");
      o.textContent = `${ink.name}${tags ? ` (${tags})` : ""}: ${ink.coverage}%`;
      select.append(o);
    }
    if (state.plate && !inks.some((i) => i.name === state.plate)) state.plate = "";
    select.value = state.plate;
  }

  $("plate").onchange = () => { state.plate = $("plate").value; renderPage(); };

  // ---- propose --------------------------------------------------------------

  function setMode(mode) {
    state.mode = mode;
    for (const m of ["inspect", "propose", "review", "export"]) {
      $(`mode-${m}`).classList.toggle("on", mode === m);
      $(`mode-${m}`).setAttribute("aria-selected", mode === m);
    }
    $("propose").hidden = mode !== "propose";
    $("review").hidden = mode !== "review";
    $("export").hidden = mode !== "export";
    $("confirm").hidden = true;
    $("which").hidden = mode === "inspect";
    for (const id of ["summary", "findings", "passed"]) $(id).hidden = mode === "export" || (id === "passed" && !state.report);
    if (mode === "review" || mode === "export") setWhich(state.queue.length ? "after" : "before");
    if (mode === "export") renderExport();
    if (state.report) renderPage();
  }

  function setWhich(which) {
    state.which = which;
    $("show-before").classList.toggle("on", which === "before");
    $("show-after").classList.toggle("on", which === "after");
    if (state.report) renderPage();
  }

  function chooseFix(name) {
    setMode("propose");
    $("fix").value = name;
    renderFields();
  }

  function renderFields() {
    const spec = FIXES[$("fix").value];
    $("fix-help").textContent = spec.help;
    const box = $("fix-fields");
    box.textContent = "";
    for (const [name, label, kind, value, extra, labels] of spec.fields) {
      if (kind === "check") {
        const l = document.createElement("label");
        l.className = "check-field";
        const c = document.createElement("input");
        c.type = "checkbox"; c.dataset.name = name; c.dataset.kind = kind;
        l.append(c, document.createTextNode(label));
        box.append(l);
        continue;
      }
      const l = document.createElement("label");
      l.className = "field";
      l.append(document.createTextNode(label));
      let input;
      if (kind === "select") {
        input = document.createElement("select");
        extra.forEach((v, i) => { const o = document.createElement("option"); o.value = v; o.textContent = (labels || extra)[i]; input.append(o); });
        input.value = value;
      } else if (kind === "lines") {
        input = document.createElement("textarea");
        input.rows = 3;
        if (extra) input.placeholder = extra;
      } else {
        input = document.createElement("input");
        input.type = kind === "number" ? "number" : "text";
        if (kind === "number") { input.step = "0.5"; input.min = "0"; }
        input.value = value;
        if (extra) input.placeholder = extra;
      }
      input.dataset.name = name; input.dataset.kind = kind;
      l.append(input);
      box.append(l);
    }
  }

  function optionTokens() {
    const tokens = [];
    for (const el of $("fix-fields").querySelectorAll("[data-name]")) {
      const name = el.dataset.name, kind = el.dataset.kind;
      if (kind === "check") { if (el.checked) tokens.push(`--${name}`); continue; }
      const v = el.value.trim();
      if (!v) continue;
      if (name === "shape") { tokens.push(`--${v}`); continue; }
      if (kind === "lines") { v.split("\n").map((x) => x.trim()).filter(Boolean).forEach((x) => tokens.push(`--${name}`, x)); continue; }
      tokens.push(`--${name}`, v);
    }
    return tokens;
  }

  async function preview() {
    $("preview-status").textContent = "";
    try {
      show(await api("/api/preview", { method: "POST", body: JSON.stringify({ fix: $("fix").value, options: optionTokens() }) }));
      poll();
    } catch (e) {
      $("preview-status").textContent = e.message;
    }
  }

  function renderProposal() {
    const out = $("proposal");
    out.textContent = "";
    const p = state.preview;
    if (!p) return;
    const h = (text) => { const e = document.createElement("h3"); e.textContent = text; out.append(e); };
    const list = (items) => {
      const ul = document.createElement("ul");
      const seen = new Map();
      for (const it of items) seen.set(it.text, [...(seen.get(it.text) || []), it.page]);
      for (const [text, pages] of seen) {
        const li = document.createElement("li");
        li.textContent = `${pagesText(pages)}: ${text}`;
        ul.append(li);
      }
      out.append(ul);
    };
    if (p.error) { const e = document.createElement("p"); e.className = "error"; e.textContent = `Nothing was made: ${p.error}`; out.append(e); }
    if (p.reasons.length) { h("What it would do"); list(p.reasons); }
    if (p.skipped.length) { h("Left alone"); list(p.skipped); }
    if (p.empty && !p.error) { const e = document.createElement("p"); e.className = "muted"; e.textContent = "Nothing to change."; out.append(e); }
    if (p.effect) {
      h(`Effect on preflight: ${p.effect.status_before} → ${p.effect.status_after}`);
      const add = (cls, label, text) => { const d = document.createElement("div"); d.className = `change ${cls}`; d.textContent = `${label} ${text}`; out.append(d); };
      p.effect.gone.forEach((f) => add("gone", "Gone:", `${f.check}: ${f.message}`));
      p.effect.changed.forEach((f) => add("", "Changed:", `${f.check}: ${f.after}`));
      p.effect.new.forEach((f) => add(f.severity === "note" ? "note" : "new", f.severity === "note" ? "Now:" : "New:", `${f.check}: ${f.message}`));
      if (!p.effect.gone.length && !p.effect.changed.length && !p.effect.new.length) add("", "", "No change to fails or warnings.");
      setWhich("after");
    }
  }

  async function keep() {
    try { show(await api("/api/keep", { method: "POST", body: "{}" })); setMode("review"); }
    catch (e) { $("preview-status").textContent = e.message; }
  }

  async function removeStep(i) {
    try { show(await api("/api/remove", { method: "POST", body: JSON.stringify({ index: i }) })); poll(); }
    catch (e) { $("queue-effect").textContent = e.message; }
  }

  function renderReview() {
    $("mode-review").innerHTML = "Review" + (state.queue.length ? ` <span class="count">${state.queue.length}</span>` : "");
    const ol = $("queue");
    ol.textContent = "";
    state.queue.forEach((q, i) => {
      const li = document.createElement("li");
      const head = document.createElement("div");
      head.className = "q-head";
      const name = document.createElement("span");
      name.className = "q-name";
      name.textContent = q.fix;
      const remove = document.createElement("button");
      remove.textContent = "Remove";
      remove.setAttribute("aria-label", `Remove ${q.fix}`);
      remove.onclick = () => removeStep(i);
      head.append(name, remove);
      const opts = document.createElement("div");
      opts.className = "q-opts";
      opts.textContent = q.options.length ? q.options.join(" ") : "default options";
      const what = document.createElement("div");
      what.textContent = q.reasons.length ? q.reasons[0].text + (q.reasons.length > 1 ? ` (and ${q.reasons.length - 1} more)` : "") : "";
      li.append(head, opts, what);
      ol.append(li);
    });
    const out = $("queue-effect");
    out.textContent = "";
    if (!state.queue.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "Nothing kept yet. Preview a fix in Propose, then add it to review.";
      out.append(p);
    } else if (state.queueEffect) {
      const e = state.queueEffect;
      const h = document.createElement("h3");
      h.textContent = `All together: ${e.status_before} → ${e.status_after}`;
      out.append(h);
      const add = (cls, label, text) => { const d = document.createElement("div"); d.className = `change ${cls}`; d.textContent = `${label} ${text}`; out.append(d); };
      e.gone.forEach((f) => add("gone", "Gone:", `${f.check}: ${f.message}`));
      e.changed.forEach((f) => add("", "Changed:", `${f.check}: ${f.after}`));
      e.new.forEach((f) => add(f.severity === "note" ? "note" : "new", f.severity === "note" ? "Now:" : "New:", `${f.check}: ${f.message}`));
    }
    if (state.mode === "export") renderExport();
  }

  function renderExport() {
    const n = state.queue.length;
    $("export-summary").textContent = n
      ? `${n} fix${n > 1 ? "es" : ""} will be written to a new file: ${state.queue.map((q) => q.fix).join(", then ")}.`
      : "Nothing to export yet. Keep a fix in Review first.";
    $("export-go").disabled = !n;
    $("export-choose").disabled = !n;
    $("export-done").hidden = !state.exported;
    $("export-written").textContent = state.exported ? `Written and verified: ${state.exported}` : "";
  }

  async function chooseSaveName() {
    try {
      const r = await api("/api/save-name", { method: "POST", body: "{}" });
      if (r.path) $("export-path").value = r.path;
    } catch (e) { $("export-error").textContent = e.message; $("export-error").hidden = false; }
  }

  function askExport() {
    const dest = $("export-path").value.trim();
    $("export-error").hidden = true;
    if (!dest) { $("export-error").textContent = "Choose where to save the new file first."; $("export-error").hidden = false; return; }
    const n = state.queue.length;
    $("confirm-text").textContent = `Write ${n} fix${n > 1 ? "es" : ""} to the new file ${dest}? The original is not changed.`;
    $("confirm").hidden = false;
    $("confirm-yes").focus();
  }

  async function doExport() {
    $("confirm").hidden = true;
    $("export-go").disabled = true;
    try {
      show(await api("/api/export", { method: "POST", body: JSON.stringify({ path: $("export-path").value.trim(), confirm: true }) }));
    } catch (e) {
      $("export-error").textContent = e.message;
      $("export-error").hidden = false;
    }
    renderExport();
  }

  $("keep").onclick = keep;
  $("mode-review").onclick = () => setMode("review");
  $("mode-export").onclick = () => setMode("export");
  $("export-choose").onclick = chooseSaveName;
  $("export-go").onclick = askExport;
  $("confirm-yes").onclick = doExport;
  $("confirm-no").onclick = () => { $("confirm").hidden = true; };
  $("export-path").oninput = () => { $("export-error").hidden = true; };
  $("reveal").onclick = () => api("/api/reveal", { method: "POST", body: "{}" });

  const fixSelect = $("fix");
  for (const name of Object.keys(FIXES)) { const o = document.createElement("option"); o.value = name; o.textContent = name; fixSelect.append(o); }
  fixSelect.onchange = renderFields;
  renderFields();
  $("preview").onclick = preview;
  $("mode-inspect").onclick = () => setMode("inspect");
  $("mode-propose").onclick = () => setMode("propose");
  $("show-before").onclick = () => setWhich("before");
  $("show-after").onclick = () => setWhich("after");

  // ---- drag and drop ------------------------------------------------------
  // In the native window, PyWebView hands a dropped file's real path to Python,
  // which opens it and calls preflightRefresh. A browser hides the path, so
  // there a drop only explains how to open the file instead.
  let noticeTimer = null;
  window.preflightNotice = (text) => {
    const n = $("notice");
    n.textContent = text;
    n.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { n.hidden = true; }, 5000);
  };
  window.preflightRefresh = () => { state.path = null; poll(); };
  const native = () => typeof window.pywebview !== "undefined";
  document.addEventListener("dragover", (e) => { e.preventDefault(); $("canvas").classList.add("drop"); });
  document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) $("canvas").classList.remove("drop"); });
  document.addEventListener("drop", (e) => {
    $("canvas").classList.remove("drop");
    if (native()) return;
    e.preventDefault();
    window.preflightNotice("A browser does not tell the app where a dropped file is. Use Open PDF, or run the app in its own window (preflight app).");
  });

  $("open").onclick = () => open({ choose: true });
  $("recent-menu").onchange = () => { const p = $("recent-menu").value; $("recent-menu").value = ""; if (p) open({ path: p }); };
  $("recent-menu").onfocus = loadRecentMenu;
  loadRecentMenu();
  $("path-form").onsubmit = (e) => { e.preventDefault(); open({ path: $("path").value.trim() }); };
  $("path").oninput = () => { $("path-error").hidden = true; };
  ["show-trim", "show-bleed", "show-media"].forEach((id) => { $(id).onchange = renderPage; });
  const zoomBy = (k) => { const g = geometry(state.page); if (!g) return; state.zoom = (state.zoom || fitZoom(g)) * k; renderPage(); };
  $("zoom-in").onclick = () => zoomBy(1.25);
  $("zoom-out").onclick = () => zoomBy(0.8);
  $("zoom-fit").onclick = () => { state.zoom = null; renderPage(); };
  window.addEventListener("resize", () => { if (state.report && state.zoom === null) renderPage(); });
  document.addEventListener("keydown", (e) => {
    if (!state.report || e.target.tagName === "INPUT") return;
    if (e.key === "ArrowRight" || e.key === "PageDown") { state.page = Math.min(state.report.pages, state.page + 1); state.selected = null; renderThumbs(); renderFindings(); renderPage(); }
    if (e.key === "ArrowLeft" || e.key === "PageUp") { state.page = Math.max(1, state.page - 1); state.selected = null; renderThumbs(); renderFindings(); renderPage(); }
  });
  poll();
})();
