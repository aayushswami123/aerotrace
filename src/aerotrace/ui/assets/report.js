/* AeroTrace report: draws the impact graph from the inlined data. No libraries, no network.
   Rules for this file: no line comments (the no-URL test scans for double slashes), and data
   only ever reaches the page through textContent or attributes, never as HTML. */
(function () {
  "use strict";
  var T0 = performance.now();
  var SVG_NS = "http://www.w3.org/2000/svg";
  var root = document.documentElement;
  var D = JSON.parse(document.getElementById("data").textContent);

  /* ------------------------------------------------------------ theme */
  var THEMES = ["auto", "light", "dark"];
  var themeBtn = document.getElementById("theme");
  function setTheme(t, remember) {
    if (t === "auto") { root.removeAttribute("data-theme"); } else { root.setAttribute("data-theme", t); }
    themeBtn.textContent = "Theme: " + t;
    if (remember) { try { window.localStorage.setItem("aerotrace-theme", t); } catch (e) { /* file: may block storage */ } }
  }
  var saved = "auto";
  try { saved = window.localStorage.getItem("aerotrace-theme") || "auto"; } catch (e) { saved = "auto"; }
  setTheme(THEMES.indexOf(saved) >= 0 ? saved : "auto", false);
  themeBtn.addEventListener("click", function () {
    var cur = root.getAttribute("data-theme") || "auto";
    setTheme(THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length], true);
  });

  var svg = document.getElementById("graph");
  var N = D.nodes;
  var COUNT = N.length;
  if (!svg || !COUNT) { root.setAttribute("data-render-ms", String(Math.round(performance.now() - T0))); return; }

  /* node fields */
  var NAME = 0, KIND = 1, FILE = 2, LINE = 3, ST = 4, CERT = 5, LINK = 6, WHY = 7, DIST = 8, VIA = 9, LINES = 10, WORD = 11, VIANAME = 12;
  var STATUS = D.statuses;
  var ST_BREAKS = STATUS.indexOf("breaks"), ST_UNKNOWN = STATUS.indexOf("unknown");
  var RING_CAP = 40, FOCUS_CAP = 200, LIST_NOTE = document.getElementById("listnote");
  var statusLine = document.getElementById("status");
  var announce = document.getElementById("announce");
  var details = document.getElementById("details");
  var search = document.getElementById("search");

  function fmt(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }
  function sel_(tag, attrs) {
    var e = document.createElementNS(SVG_NS, tag);
    for (var k in attrs) { if (Object.prototype.hasOwnProperty.call(attrs, k)) { e.setAttribute(k, attrs[k]); } }
    return e;
  }
  function kindWord(i) { var k = D.kinds[N[i][KIND]]; return k === "function" ? "" : " (" + k.replace("_", " ") + ")"; }
  function where(i) { return D.files[N[i][FILE]] + ":" + N[i][LINE]; }
  function sayNode(i) {
    return N[i][WORD] + ": " + N[i][NAME] + kindWord(i) + ", " + D.files[N[i][FILE]] + " line " + N[i][LINE] +
      ". Link: " + D.certainties[N[i][CERT]] + ".";
  }

  /* ------------------------------------------------------------ adjacency */
  var E = D.edges;
  var adj = [];
  for (var a = 0; a < COUNT; a++) { adj.push([]); }
  for (var k = 0; k < E.length; k++) {
    adj[E[k][0]].push(E[k][1]);
    if (E[k][1] !== E[k][0]) { adj[E[k][1]].push(E[k][0]); }
  }

  /* ------------------------------------------------------------ layout: rings by distance */
  var X = new Float64Array(COUNT), Y = new Float64Array(COUNT), ANG = new Float64Array(COUNT);
  var placed = new Uint8Array(COUNT);
  var rings = [], given = [];
  var i, d;
  for (i = 0; i < COUNT; i++) {
    d = N[i][DIST];
    while (rings.length <= d) { rings.push([]); given.push([]); }
    rings[d].push(i);
    given[d].push(i);
  }
  var SPACING = 30, prevR = 0;
  for (d = 0; d < rings.length; d++) {
    var ring = rings[d];
    if (!ring.length) { continue; }
    ring.sort(function (p, q) {
      var pa = N[p][VIA] >= 0 && placed[N[p][VIA]] ? ANG[N[p][VIA]] : 99;
      var qa = N[q][VIA] >= 0 && placed[N[q][VIA]] ? ANG[N[q][VIA]] : 99;
      return pa - qa || N[p][ST] - N[q][ST] || p - q;
    });
    var r;
    if (d === 0) { r = ring.length === 1 ? 0 : Math.max(90, ring.length * SPACING / (2 * Math.PI)); }
    else { r = Math.max(prevR + 140, ring.length * SPACING / (2 * Math.PI)); }
    for (var j = 0; j < ring.length; j++) {
      var n = ring[j];
      var ang = -Math.PI / 2 + 2 * Math.PI * j / ring.length;
      ANG[n] = ang;
      X[n] = Math.round(r * Math.cos(ang) * 10) / 10;
      Y[n] = Math.round(r * Math.sin(ang) * 10) / 10;
      placed[n] = 1;
    }
    prevR = r;
  }

  /* Long names are cut so labels stay readable; the full name is in the hover title and the details. */
  var LABEL_MAX = 28;
  function shortName(s) {
    if (s.length <= LABEL_MAX) { return s; }
    if (s.indexOf("::") >= 0) { return "\u2026" + s.slice(s.length - LABEL_MAX + 1); }
    return s.slice(0, LABEL_MAX - 1) + "\u2026";
  }

  /* ------------------------------------------------------------ draw every node once */
  var nodeLayer = document.getElementById("nodes");
  var bubbleLayer = document.getElementById("bubbles");
  var G = new Array(COUNT);
  var LIST = {};
  var frag = document.createDocumentFragment();
  for (i = 0; i < COUNT; i++) {
    var st = N[i][ST];
    var always = STATUS[st] === "changed" || STATUS[st] === "new" || STATUS[st] === "removed" || st === ST_BREAKS;
    var g = sel_("g", {
      "class": "node s-" + STATUS[st] + (always ? " always" : ""),
      "id": "n" + i,
      "data-i": String(i),
      "transform": "translate(" + X[i] + "," + Y[i] + ")",
      "aria-label": sayNode(i)
    });
    g.appendChild(sel_("path", { "class": "shape", "d": D.shapes[st][0] }));
    g.appendChild(sel_("path", { "class": "icon", "d": D.shapes[st][1] }));
    var t = sel_("text", { "class": "lbl", "x": "15", "y": "4" });
    t.textContent = shortName(N[i][NAME]);
    g.appendChild(t);
    var tt = sel_("title", {});
    tt.textContent = sayNode(i);
    g.appendChild(tt);
    frag.appendChild(g);
    G[i] = g;
  }
  nodeLayer.appendChild(frag);
  var buttons = document.querySelectorAll("#list li[data-i]");
  for (i = 0; i < buttons.length; i++) {
    var li = buttons[i];
    var idx = li.getAttribute("data-i");
    (LIST[idx] = LIST[idx] || []).push(li);
  }

  /* ------------------------------------------------------------ what is visible */
  var expanded = [], expandAll = false, pinned = new Uint8Array(COUNT);
  var focusOn = -1, query = "", matches = null, cur = -1;
  var vis = new Uint8Array(COUNT);
  var HAY = new Array(COUNT);
  for (i = 0; i < COUNT; i++) { HAY[i] = (N[i][NAME] + " " + D.files[N[i][FILE]] + ":" + N[i][LINE]).toLowerCase(); }

  function withChain(set, i0) {
    var guard = 0;
    for (var x = i0; x >= 0 && !set[x] && guard < 1000; x = N[x][VIA], guard++) { set[x] = 1; }
    set[i0] = 1;
  }
  function computeVisible() {
    vis.fill(0);
    var x;
    if (focusOn >= 0) {
      withChain(vis, focusOn);
      var nb = adj[focusOn];
      for (x = 0; x < nb.length && x < FOCUS_CAP; x++) { vis[nb[x]] = 1; }
      return;
    }
    for (var dd = 0; dd < given.length; dd++) {
      var ids = given[dd];
      var all = dd === 0 || expandAll || expanded[dd];
      var lim = all ? ids.length : (dd === 1 ? Math.min(RING_CAP, ids.length) : 0);
      for (x = 0; x < lim; x++) { vis[ids[x]] = 1; }
    }
    for (x = 0; x < COUNT; x++) {
      if (N[x][ST] === ST_BREAKS || N[x][ST] === ST_UNKNOWN || pinned[x]) { withChain(vis, x); }
    }
    if (matches) { for (x = 0; x < matches.length; x++) { withChain(vis, matches[x]); } }
  }

  var eCalls = document.getElementById("e-calls"), eData = document.getElementById("e-data"), eHl = document.getElementById("e-hl");
  function edgePaths() {
    var calls = [], data = [], hl = [];
    for (var x = 0; x < E.length; x++) {
      var p = E[x][0], q = E[x][1];
      if (p === q || !vis[p] || !vis[q]) { continue; }
      var seg = "M" + X[p] + " " + Y[p] + "L" + X[q] + " " + Y[q];
      if (cur >= 0 && (p === cur || q === cur)) { hl.push(seg); }
      else if (E[x][2] === 0) { calls.push(seg); } else { data.push(seg); }
    }
    eCalls.setAttribute("d", calls.join(""));
    eData.setAttribute("d", data.join(""));
    eHl.setAttribute("d", hl.join(""));
  }

  function bubbles() {
    while (bubbleLayer.firstChild) { bubbleLayer.removeChild(bubbleLayer.firstChild); }
    BOXES = [];
    if (focusOn >= 0) { return 0; }
    var hiddenTotal = 0;
    for (var dd = 1; dd < rings.length; dd++) {
      var hid = [];
      for (var x = 0; x < rings[dd].length; x++) { if (!vis[rings[dd][x]]) { hid.push(rings[dd][x]); } }
      if (!hid.length) { continue; }
      hiddenTotal += hid.length;
      var by = {}, order = [];
      for (x = 0; x < hid.length; x++) {
        var w = N[hid[x]][WORD].toLowerCase();
        if (!by[w]) { by[w] = 0; order.push(w); }
        by[w]++;
      }
      order.sort(function (p, q) { return by[q] - by[p] || (p < q ? -1 : 1); });
      var parts = [];
      for (x = 0; x < order.length; x++) { parts.push(fmt(by[order[x]]) + " " + order[x]); }
      var label = "+" + fmt(hid.length) + " more at distance " + dd + ": " + parts.join(", ");
      var m = hid[Math.floor(hid.length / 2)];
      BOXES.push([X[m], Y[m], label.length * 6.2 + 16]);
      var b = sel_("g", { "class": "bubble", "data-ring": String(dd), "transform": "translate(" + X[m] + "," + Y[m] + ")" });
      b.appendChild(sel_("rect", { "x": "-8", "y": "-11", "rx": "9", "width": String(label.length * 6.2 + 16), "height": "22" }));
      var tx = sel_("text", { "x": "0", "y": "4" });
      tx.textContent = label;
      b.appendChild(tx);
      var ti = sel_("title", {});
      ti.textContent = label + ". Click or press E to show them.";
      b.appendChild(ti);
      bubbleLayer.appendChild(b);
    }
    return hiddenTotal;
  }

  function listFilter() {
    var keep = null, why = "";
    if (focusOn >= 0) { keep = vis; why = "in focus on " + N[focusOn][NAME]; }
    else if (matches) {
      keep = new Uint8Array(COUNT);
      for (var x = 0; x < matches.length; x++) { keep[matches[x]] = 1; }
      why = "matching “" + query + "”";
    }
    var shown = 0, hidden = 0;
    for (var key in LIST) {
      if (!Object.prototype.hasOwnProperty.call(LIST, key)) { continue; }
      var on = !keep || keep[+key];
      var items = LIST[key];
      for (var y = 0; y < items.length; y++) { items[y].hidden = !on; if (on) { shown++; } else { hidden++; } }
    }
    if (keep) {
      LIST_NOTE.hidden = false;
      LIST_NOTE.textContent = "The list shows only items " + why + ": " + fmt(shown) + " shown, " + fmt(hidden) +
        " hidden. Press Escape to show all.";
    } else { LIST_NOTE.hidden = true; LIST_NOTE.textContent = ""; }
  }

  function apply() {
    computeVisible();
    var shown = 0;
    for (var x = 0; x < COUNT; x++) {
      var c = "node s-" + STATUS[N[x][ST]];
      if (G[x].classList.contains("always")) { c += " always"; }
      if (!vis[x]) { c += " hid"; } else { shown++; }
      if (x === cur) { c += " sel"; }
      if (matches) { c += HAYMATCH[x] ? " match" : " dim"; }
      G[x].setAttribute("class", c);
    }
    shownCount = shown;
    edgePaths();
    var folded = bubbles();
    var msg;
    if (focusOn >= 0) {
      msg = "Focus: " + N[focusOn][NAME] + " (" + fmt(shown) + " of " + fmt(COUNT) + " nodes shown). Press Escape to show all.";
    } else if (matches) {
      msg = fmt(matches.length) + " match" + (matches.length === 1 ? "" : "es") + " for “" + query + "”.";
    } else {
      msg = "Showing " + fmt(shown) + " of " + fmt(COUNT) + " nodes." +
        (folded ? " " + fmt(folded) + (folded === 1 ? " is" : " are") + " folded into “+N more” bubbles: click one, press E, or use Expand all." : "");
    }
    if (D.omitted) { msg += " " + fmt(D.omitted) + " more were left out of this report."; }
    statusLine.textContent = msg;
    listFilter();
    document.getElementById("focusbtn").setAttribute("aria-pressed", focusOn >= 0 ? "true" : "false");
    document.getElementById("expand").textContent = expandAll ? "Collapse" : "Expand all";
  }
  var HAYMATCH = new Uint8Array(COUNT), shownCount = 0, BOXES = [];

  /* ------------------------------------------------------------ view box: pan and zoom */
  var vb = { x: -400, y: -300, w: 800, h: 600 };
  var frame = 0;
  function lodScale() {
    var rect = svg.getBoundingClientRect();
    return Math.min(rect.width / vb.w, rect.height / vb.h);
  }
  function setView() {
    if (frame) { return; }
    frame = window.requestAnimationFrame(function () {
      frame = 0;
      svg.setAttribute("viewBox", vb.x.toFixed(1) + " " + vb.y.toFixed(1) + " " + vb.w.toFixed(1) + " " + vb.h.toFixed(1));
      var sc = lodScale();
      if (shownCount <= 60 || sc > 0.8) { svg.classList.add("lod"); } else { svg.classList.remove("lod"); }
      /* Labels keep about 13 screen pixels when zoomed out (one CSS variable, so it stays cheap at 5,000 nodes). */
      svg.style.setProperty("--lbl", (sc > 0 ? Math.min(Math.max(13 / sc, 13), 48) : 13).toFixed(1) + "px");
    });
  }
  function fit() {
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (var x = 0; x < COUNT; x++) {
      if (!vis[x]) { continue; }
      if (X[x] < minX) { minX = X[x]; } if (X[x] > maxX) { maxX = X[x]; }
      if (Y[x] < minY) { minY = Y[x]; } if (Y[x] > maxY) { maxY = Y[x]; }
    }
    for (x = 0; x < BOXES.length; x++) {
      minX = Math.min(minX, BOXES[x][0]); maxX = Math.max(maxX, BOXES[x][0] + BOXES[x][2]);
      minY = Math.min(minY, BOXES[x][1]); maxY = Math.max(maxY, BOXES[x][1]);
    }
    if (minX === Infinity) { minX = minY = -100; maxX = maxY = 100; }
    var pad = 80;
    vb = { x: minX - pad, y: minY - pad, w: Math.max(200, maxX - minX + 2 * pad + 120), h: Math.max(200, maxY - minY + 2 * pad) };
    setView();
  }
  function zoom(f, cx, cy) {
    if (cx === undefined) { cx = vb.x + vb.w / 2; cy = vb.y + vb.h / 2; }
    var nw = Math.min(Math.max(vb.w * f, 60), 200000);
    f = nw / vb.w;
    vb = { x: cx - (cx - vb.x) * f, y: cy - (cy - vb.y) * f, w: vb.w * f, h: vb.h * f };
    setView();
  }
  function toSvg(ev) {
    var m = svg.getScreenCTM();
    if (!m) { return { x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 }; }
    var pt = svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    return pt.matrixTransform(m.inverse());
  }
  function panTo(x) {
    if (X[x] < vb.x || X[x] > vb.x + vb.w || Y[x] < vb.y || Y[x] > vb.y + vb.h) {
      vb.x = X[x] - vb.w / 2; vb.y = Y[x] - vb.h / 2; setView();
    }
  }
  svg.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    var p = toSvg(ev);
    zoom(ev.deltaY > 0 ? 1.15 : 1 / 1.15, p.x, p.y);
  }, { passive: false });
  var drag = null;
  svg.addEventListener("pointerdown", function (ev) {
    if (ev.target.closest && ev.target.closest("[data-i],[data-ring]")) { return; }
    var rect = svg.getBoundingClientRect();
    drag = { x: ev.clientX, y: ev.clientY, vx: vb.x, vy: vb.y, s: Math.max(vb.w / rect.width, vb.h / rect.height) };
    svg.classList.add("drag");
    svg.setPointerCapture(ev.pointerId);
  });
  svg.addEventListener("pointermove", function (ev) {
    if (!drag) { return; }
    vb.x = drag.vx - (ev.clientX - drag.x) * drag.s;
    vb.y = drag.vy - (ev.clientY - drag.y) * drag.s;
    setView();
  });
  function endDrag() { drag = null; svg.classList.remove("drag"); }
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);

  /* ------------------------------------------------------------ selection and details */
  function dd_(dl, term, value) {
    dl.appendChild(el("dt", "", term));
    var v = el("dd");
    if (typeof value === "string") { v.textContent = value; } else { v.appendChild(value); }
    dl.appendChild(v);
  }
  function ranges(ls) {
    var s = ls.slice().sort(function (p, q) { return p - q; }), out = [], k2 = 0;
    while (k2 < s.length) {
      var start = s[k2];
      while (k2 + 1 < s.length && s[k2 + 1] <= s[k2] + 1) { k2++; }
      out.push(start === s[k2] ? String(start) : start + "-" + s[k2]);
      k2++;
    }
    return out.join(", ");
  }
  function showDetails(x) {
    details.textContent = "";
    var h = el("h2", "", N[x][NAME]);
    details.appendChild(h);
    var w = el("p", "it s-" + STATUS[N[x][ST]]);
    w.appendChild(el("i", "", D.icons[STATUS[N[x][ST]]] + " "));
    w.appendChild(el("em", "", N[x][WORD]));
    details.appendChild(w);
    var dl = el("dl");
    var loc = el("div");
    loc.appendChild(el("span", "", where(x)));
    var copy = el("button", "", "Copy file:line");
    copy.type = "button";
    var box = el("input");
    box.id = "copybox"; box.readOnly = true; box.value = where(x); box.setAttribute("aria-label", "file and line");
    copy.addEventListener("click", function () {
      function fallback() { box.focus(); box.select(); copy.textContent = "Selected: press Ctrl+C or Cmd+C"; }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(box.value).then(function () { copy.textContent = "Copied"; }, fallback);
      } else { fallback(); }
    });
    loc.appendChild(document.createTextNode(" "));
    loc.appendChild(copy);
    loc.appendChild(box);
    dd_(dl, "Where", loc);
    dd_(dl, "Kind", D.kinds[N[x][KIND]].replace("_", " "));
    dd_(dl, "Why it is listed", N[x][WHY] || "(no reason given)");
    var cert = D.certainties[N[x][CERT]];
    dd_(dl, "How sure is the link", cert + ": " + D.meaning[cert]);
    if (N[x][LINK]) { dd_(dl, "Link label", N[x][LINK]); }
    dd_(dl, "Distance", N[x][DIST] === 0 ? "part of the change" : N[x][DIST] + " step" + (N[x][DIST] === 1 ? "" : "s") + " from the change");
    if (N[x][VIA] >= 0 || N[x][VIANAME]) {
      var path = el("div", "path");
      var guard = 0;
      for (var y = N[x][VIA]; y >= 0 && guard < 50; y = N[y][VIA], guard++) {
        var b = el("button", "", "→ " + N[y][NAME] + " (" + N[y][WORD].toLowerCase() + ")");
        b.type = "button";
        b.setAttribute("data-go", String(y));
        path.appendChild(b);
      }
      if (N[x][VIA] < 0) { path.appendChild(el("span", "", "→ " + N[x][VIANAME] + " (left out of this report)")); }
      dd_(dl, "Path back to the change", path);
    }
    if (N[x][LINES].length) { dd_(dl, "Lines of the link", ranges(N[x][LINES])); }
    details.appendChild(dl);
  }
  function select(x) {
    if (x < 0 || x >= COUNT) { return; }
    var prev = cur;
    cur = x;
    if (prev >= 0 && LIST[prev]) { for (var a2 = 0; a2 < LIST[prev].length; a2++) { var bb = LIST[prev][a2].querySelector("button"); if (bb) { bb.classList.remove("sel"); } } }
    if (LIST[x]) { for (var a3 = 0; a3 < LIST[x].length; a3++) { var b3 = LIST[x][a3].querySelector("button"); if (b3) { b3.classList.add("sel"); } } }
    if (!vis[x]) { pinned[x] = 1; }
    apply();
    svg.setAttribute("aria-activedescendant", "n" + x);
    showDetails(x);
    announce.textContent = sayNode(x);
    panTo(x);
  }
  details.addEventListener("click", function (ev) {
    var b = ev.target.closest ? ev.target.closest("[data-go]") : null;
    if (b) { select(+b.getAttribute("data-go"), "path"); }
  });
  svg.addEventListener("click", function (ev) {
    var t2 = ev.target.closest ? ev.target.closest("[data-i],[data-ring]") : null;
    if (!t2) { return; }
    if (t2.hasAttribute("data-ring")) { expanded[+t2.getAttribute("data-ring")] = true; apply(); return; }
    select(+t2.getAttribute("data-i"), "graph");
  });
  document.getElementById("list").addEventListener("click", function (ev) {
    var b = ev.target.closest ? ev.target.closest("li[data-i]") : null;
    if (b) { select(+b.getAttribute("data-i"), "list"); }
  });

  /* ------------------------------------------------------------ focus, expand, search */
  function toggleFocus() {
    if (focusOn >= 0) { focusOn = -1; } else if (cur >= 0) { focusOn = cur; } else { return; }
    apply(); fit();
  }
  function clearAll() {
    var changed = focusOn >= 0 || matches;
    focusOn = -1;
    if (matches) { search.value = ""; query = ""; matches = null; HAYMATCH.fill(0); }
    if (changed) { apply(); fit(); }
  }
  document.getElementById("focusbtn").addEventListener("click", toggleFocus);
  document.getElementById("fit").addEventListener("click", fit);
  document.getElementById("zin").addEventListener("click", function () { zoom(1 / 1.3); });
  document.getElementById("zout").addEventListener("click", function () { zoom(1.3); });
  document.getElementById("expand").addEventListener("click", function () {
    expandAll = !expandAll;
    if (!expandAll) { expanded = []; pinned.fill(0); }
    apply(); fit();
  });
  var timer = 0;
  function runSearch() {
    query = search.value.trim();
    var q = query.toLowerCase();
    HAYMATCH.fill(0);
    if (!q) { matches = null; }
    else {
      matches = [];
      for (var x = 0; x < COUNT; x++) { if (HAY[x].indexOf(q) >= 0) { matches.push(x); HAYMATCH[x] = 1; } }
    }
    apply();
  }
  search.addEventListener("input", function () { window.clearTimeout(timer); timer = window.setTimeout(runSearch, 80); });
  search.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { window.clearTimeout(timer); runSearch(); if (matches && matches.length) { select(matches[0], "search"); svg.focus(); } }
    else if (ev.key === "Escape") { clearAll(); }
  });

  /* ------------------------------------------------------------ keyboard */
  function nearest(dx, dy) {
    var from = cur >= 0 ? cur : rings[0][0];
    var best = -1, bestScore = Infinity;
    for (var x = 0; x < COUNT; x++) {
      if (x === from || !vis[x]) { continue; }
      var vx = X[x] - X[from], vy = Y[x] - Y[from];
      var along = vx * dx + vy * dy;
      if (along <= 0) { continue; }
      var across = Math.abs(vx * dy - vy * dx);
      var score = along + 2 * across;
      if (score < bestScore) { bestScore = score; best = x; }
    }
    return best;
  }
  svg.addEventListener("keydown", function (ev) {
    var k2 = ev.key, used = true;
    if (k2 === "ArrowRight") { select(nearest(1, 0), "key"); }
    else if (k2 === "ArrowLeft") { select(nearest(-1, 0), "key"); }
    else if (k2 === "ArrowUp") { select(nearest(0, -1), "key"); }
    else if (k2 === "ArrowDown") { select(nearest(0, 1), "key"); }
    else if (k2 === "Home") { select(rings[0][0], "key"); }
    else if (k2 === "Enter" || k2 === " ") { if (cur < 0) { select(rings[0][0], "key"); } else { announce.textContent = sayNode(cur); } }
    else if (k2 === "f" || k2 === "F") { toggleFocus(); }
    else if (k2 === "e" || k2 === "E") {
      if (cur >= 0 && N[cur][DIST] + 1 < rings.length) { expanded[N[cur][DIST] + 1] = true; expanded[N[cur][DIST]] = true; }
      else { expandAll = true; }
      apply();
    }
    else if (k2 === "Escape") { clearAll(); }
    else if (k2 === "+" || k2 === "=") { zoom(1 / 1.3); }
    else if (k2 === "-") { zoom(1.3); }
    else if (k2 === "0") { fit(); }
    else { used = false; }
    if (used) { ev.preventDefault(); }
  });
  document.addEventListener("keydown", function (ev) {
    var tag = ev.target && ev.target.tagName;
    if (ev.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA") { ev.preventDefault(); search.focus(); }
  });

  /* ------------------------------------------------------------ first render */
  apply();
  fit();
  window.requestAnimationFrame(function () {
    root.setAttribute("data-render-ms", String(Math.round(performance.now() - T0)));
  });
}());
