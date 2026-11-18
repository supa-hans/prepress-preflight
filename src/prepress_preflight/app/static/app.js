"use strict";
(() => {
  const KEY = window.PREFLIGHT_KEY;
  const $ = (id) => document.getElementById(id);
  const ORDER = { fail: 0, unchecked: 1, warn: 2, note: 3 };
  const LABEL = { fail: "Fail", unchecked: "Could not check", warn: "Warn", note: "Note" };
  const state = { report: null, page: 1, zoom: null, selected: null, path: null };

  async function api(path, opts = {}) {
    const sep = path.includes("?") ? "&" : "?";
    const res = await fetch(`${path}${sep}key=${encodeURIComponent(KEY)}`, opts);
    const body = res.headers.get("Content-Type")?.includes("json") ? await res.json() : null;
    if (!res.ok) throw new Error((body && body.error) || `Request failed (${res.status})`);
    return body;
  }

  function pageImage(n, width) {
    return `/api/page?n=${n}&w=${Math.round(width)}&key=${encodeURIComponent(KEY)}`;
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
    if (s.busy) setTimeout(poll, 500);
  }

  function show(s) {
    $("name").textContent = s.name || "No file open";
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

  function geometry(n) {
    return state.report.page_geometry.find((g) => g.page === n);
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
      list.append(b);
    }
    const passed = $("passed");
    passed.hidden = !r.passed_checks.length;
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
    const src = pageImage(state.page, want);
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
    if (state.selected !== null) {
      const f = state.report.findings[state.selected];
      // padded by a few screen pixels, so that a word or a hairline still shows at Fit
      for (const l of f.locations) if (l.page === state.page && l.view) rect(l.view, "hit", css.getPropertyValue("--hit"), "", 2, 4 / zoom);
    }
  }

  $("open").onclick = () => open({ choose: true });
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
