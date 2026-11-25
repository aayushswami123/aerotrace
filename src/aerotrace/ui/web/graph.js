// aerotrace ui: the impact graph of one saved check, drawn on a canvas.
// Rings by distance from the change; the order inside a ring comes from the server (ui/api.py graph()),
// so the same check always gives the same picture. No labels overlap: each label tries 9 spots and is
// hidden (and counted) when none is free. Every node is also in the list view, for screen readers.
(function () {
  "use strict";
  var SHAPES = {
    changed: ["M0,-13L13,0L0,13L-13,0Z", "M-3,-3H3V3H-3Z"],
    new: ["M0,-13L13,0L0,13L-13,0Z", "M-5,0H5M0,-5V5"],
    removed: ["M0,-13L13,0L0,13L-13,0Z", "M-5,-5L5,5M5,-5L-5,5"],
    breaks: ["M-10,-10H10V10H-10Z", "M-5,-5L5,5M5,-5L-5,5"],
    may_break: ["M-10,-10H10V10H-10Z", "M-5,-5L5,5M5,-5L-5,5"],
    affected: ["M0,-12L12,10H-12Z", "M0,-4V3M0,6V7"],
    unknown: ["M-10,0A10,10 0 1,0 10,0A10,10 0 1,0 -10,0Z", "M-3,-4C-3,-8 3,-8 3,-4C3,-1 0,-1 0,2M0,5V6"],
    no_effect: ["M-6,0A6,6 0 1,0 6,0A6,6 0 1,0 -6,0Z", "M-3,0H3"],
  };
  var PRIO = { changed: 0, breaks: 1, may_break: 2, unknown: 3, affected: 4, no_effect: 9 };
  var SCALE = { changed: 1.1, breaks: 0.62, may_break: 0.62, affected: 0.56, unknown: 0.56, no_effect: 0.4 };
  var COLLAPSE_OVER = 300;  // with more nodes shown than this, rings past FAR fold into bubbles
  var FAR = 3;
  var paths = {};
  function path2d(key, i) { var k = key + i; if (!paths[k]) paths[k] = new Path2D(SHAPES[key][i]); return paths[k]; }

  function GraphView(stage, data, opts) {
    this.stage = stage;
    this.data = data;
    this.opts = opts;
    this.show = { changed: true, breaks: true, may_break: true, affected: false, unknown: false, no_effect: false };
    this.openRings = {};
    this.focus = null;
    this.selected = null;
    this.hover = null;
    this.view = { s: 1, x: 0, y: 0 };
    this.byId = {};
    var self = this;
    data.nodes.forEach(function (n) { self.byId[n.id] = n; });
    this.canvas = document.createElement("canvas");
    this.canvas.setAttribute("role", "img");
    this.canvas.setAttribute("aria-label", "Impact graph. The list view has every node as text.");
    this.canvas.setAttribute("tabindex", "0");  // reachable by keyboard; + - 0 f work while it has focus
    stage.insertBefore(this.canvas, stage.firstChild);  // under the legend, hint and tools
    var css = getComputedStyle(document.documentElement);
    var v = function (n) { return css.getPropertyValue(n).trim(); };
    this.col = { changed: v("--graph-changed"), breaks: v("--graph-breaks"), may_break: v("--graph-may"),
      affected: v("--graph-affected"), unknown: v("--graph-unknown"), no_effect: v("--graph-no-effect"),
      dot: v("--graph-dot"), edge: v("--graph-edge"), label: v("--graph-label"), mono: v("--font-mono") };
    this.stats = { frames: 0, lastMs: 0, shown: 0, labels: 0, hiddenLabels: 0 };
    this._widths = {};
    this._moving = 0;
    window.__aerotraceGraph = this.stats;
    this._wire();
    this.layout();
    this.fit();
  }

  GraphView.prototype.visibleNodes = function () {
    var self = this, keep;
    if (this.focus) {
      keep = {};
      var cur = this.byId[this.focus];
      while (cur && !keep[cur.id]) { keep[cur.id] = true; cur = cur.via ? this.byId[cur.via] : null; }
      this.data.nodes.forEach(function (n) { if (n.via === self.focus || n.group === "changed") keep[n.id] = true; });
      return this.data.nodes.filter(function (n) { return keep[n.id]; });
    }
    return this.data.nodes.filter(function (n) { return self.show[n.group]; });
  };

  GraphView.prototype.layout = function () {
    var vis = this.visibleNodes(), self = this;
    var rings = {}, maxRing = 0;
    vis.forEach(function (n) { (rings[n.ring] = rings[n.ring] || []).push(n); maxRing = Math.max(maxRing, n.ring); });
    var collapse = !this.focus && vis.length > COLLAPSE_OVER;
    this.bubbles = [];
    this.pos = {};
    var r = 0, step = 90;
    for (var d = 0; d <= maxRing; d++) {
      var ring = (rings[d] || []).slice().sort(function (a, b) { return a.order - b.order; });
      if (!ring.length) continue;
      var shown = ring, folded = [];
      if (collapse && d > FAR && !this.openRings[d]) {
        shown = ring.filter(function (n) { return n.group === "breaks" || n.group === "may_break"; });  // never folded
        folded = ring.filter(function (n) { return !(n.group === "breaks" || n.group === "may_break"); });
      }
      var need = (Math.max(shown.length, 1) * (d === 0 ? 34 : 13)) / (2 * Math.PI);  // apart on the ring at zoom 1
      r = d === 0 ? (ring.length > 1 ? Math.max(24, need) : 0) : Math.max(r + step, need);
      shown.forEach(function (n, i) {
        var a = -Math.PI / 2 + ((i + 0.5) / shown.length) * 2 * Math.PI + d * 0.35;
        self.pos[n.id] = { x: Math.cos(a) * r, y: Math.sin(a) * r, a: a, n: n };
      });
      if (folded.length) this.bubbles.push({ ring: d, count: folded.length, r: r });
    }
    this.maxR = r;
    this.stats.shown = Object.keys(this.pos).length;
    this.draw();
  };

  GraphView.prototype._anchor = function (n) {
    // the nearest drawn node toward this node's own change; `skipped` when steps between are hidden
    var cur = n.via ? this.byId[n.via] : null, seen = {}, skipped = false;
    while (cur && !this.pos[cur.id] && !seen[cur.id]) { seen[cur.id] = true; skipped = true; cur = cur.via ? this.byId[cur.via] : null; }
    if (cur && this.pos[cur.id]) return { p: this.pos[cur.id], skipped: skipped };
    if (n.root && this.pos[n.root] && n.root !== n.id) return { p: this.pos[n.root], skipped: true };
    return null;  // no drawn path: no line, rather than a line to the wrong change
  };

  GraphView.prototype.resize = function () {
    var dpr = window.devicePixelRatio || 1, w = this.stage.clientWidth, h = this.stage.clientHeight;
    this.w = w; this.h = h;
    this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(h * dpr);
    this.canvas.style.width = w + "px"; this.canvas.style.height = h + "px";
    this.dpr = dpr;
  };

  GraphView.prototype.fit = function () {
    this.resize();
    var R = (this.maxR || 60) + 60;
    var s = Math.min(this.w, this.h) / (2 * R);
    this.view = { s: Math.max(0.05, Math.min(2.5, s)), x: 0, y: 0 };
    this.draw();
  };

  GraphView.prototype.center = function (id, zoom) {
    var p = this.pos[id];
    if (!p) return;
    if (zoom) this.view.s = Math.max(this.view.s, 1.4);
    this.view.x = -p.x * this.view.s;
    this.view.y = -p.y * this.view.s;
    this.draw();
  };

  // each status shape painted once into a small image, then stamped per node (cheap at thousands of nodes)
  GraphView.prototype._sprite = function (shape, group, sc) {
    var key = shape + "|" + group + "|" + sc.toFixed(2) + "|" + this.dpr;
    this._sprites = this._sprites || {};
    if (this._sprites[key]) return this._sprites[key];
    var size = Math.ceil(32 * sc) + 4, cv = document.createElement("canvas");
    cv.width = cv.height = Math.ceil(size * this.dpr);
    var g = cv.getContext("2d"), c = this.col;
    g.scale(this.dpr, this.dpr); g.translate(size / 2, size / 2); g.scale(sc, sc);
    g.fillStyle = c[group]; g.fill(path2d(shape, 0));
    if (group !== "no_effect") { g.strokeStyle = "#0b0d10"; g.lineWidth = 2.6; g.lineCap = "round"; g.stroke(path2d(shape, 1)); }
    if (group === "may_break") { g.setLineDash([4, 3]); g.strokeStyle = c.may_break; g.lineWidth = 2; g.stroke(path2d(shape, 0)); }
    return (this._sprites[key] = { img: cv, size: size });
  };

  GraphView.prototype.toScreen = function (p) {
    return [this.w / 2 + this.view.x + p.x * this.view.s, this.h / 2 + this.view.y + p.y * this.view.s];
  };

  GraphView.prototype.draw = function () {
    if (this._raf) return;
    var self = this;
    this._raf = requestAnimationFrame(function () { self._raf = 0; self._draw(); });
  };

  GraphView.prototype._overlays = function () {
    var sr = this.stage.getBoundingClientRect(), out = [];
    this.stage.querySelectorAll("[data-overlay]").forEach(function (el) {
      if (el.hidden) return;
      var r = el.getBoundingClientRect();
      out.push({ x: r.left - sr.left - 6, y: r.top - sr.top - 6, w: r.width + 12, h: r.height + 12 });
    });
    return out;
  };

  GraphView.prototype._draw = function () {
    var t0 = performance.now(), g = this.canvas.getContext("2d"), c = this.col, self = this;
    if (!this.w) this.resize();
    var w = this.w, h = this.h, s = this.view.s;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    if (!this._dots) {
      var tile = document.createElement("canvas");
      tile.width = tile.height = 24;
      var tg = tile.getContext("2d");
      tg.fillStyle = c.dot; tg.fillRect(12, 12, 1.5, 1.5);
      this._dots = g.createPattern(tile, "repeat");
    }
    g.fillStyle = this._dots; g.fillRect(0, 0, w, h);
    var ids = Object.keys(this.pos);
    var scr = {};
    ids.forEach(function (id) { scr[id] = self.toScreen(self.pos[id]); });
    // ring guides
    g.strokeStyle = "rgba(140,150,165,0.10)";
    g.lineWidth = 1;
    var rs = {};
    ids.forEach(function (id) { var p = self.pos[id]; if (p.n.ring) rs[p.n.ring] = Math.hypot(p.x, p.y); });
    var cx = w / 2 + this.view.x, cy = h / 2 + this.view.y;
    Object.keys(rs).forEach(function (k) { g.beginPath(); g.arc(cx, cy, rs[k] * s, 0, 2 * Math.PI); g.stroke(); });
    // edges, batched by style
    // line style says how sure the link is: solid exact, dashed probable, dotted unknown;
    // faint dots when the line skips steps that are hidden right now
    var STYLE = { exact: [], probable: [5, 4], unknown: [1.5, 3] };
    var batches = {};
    ids.forEach(function (id) {
      var p = self.pos[id];
      if (!p.n.ring) return;
      var a = self._anchor(p.n);
      if (!a) return;
      var e0 = scr[a.p.n.id], e1 = scr[id];
      if (Math.max(e0[0], e1[0]) < 0 || Math.min(e0[0], e1[0]) > w || Math.max(e0[1], e1[1]) < 0 || Math.min(e0[1], e1[1]) > h) return;  // off screen
      var colour = p.n.group === "breaks" ? "rgba(255,107,94,0.65)" : p.n.group === "may_break" ? "rgba(251,191,36,0.6)" : c.edge;
      var key = a.skipped ? "skip" : colour + "|" + (STYLE[p.n.certainty] ? p.n.certainty : "unknown");
      (batches[key] = batches[key] || { colour: a.skipped ? "rgba(140,150,165,0.35)" : colour,
        dash: a.skipped ? [1, 4] : STYLE[p.n.certainty] || STYLE.unknown, list: [] }).list.push([scr[a.p.n.id], scr[id]]);
    });
    // Dashing thousands of lines is slow and unreadable at overview zoom: over DASH_MAX dashed lines on
    // screen they are drawn solid and fainter, and the hint says to zoom in to see which are probable.
    var dashed = 0;
    Object.keys(batches).forEach(function (k) { if (batches[k].dash.length) dashed += batches[k].list.length; });
    var simplify = dashed > 600;
    this.stats.dashesSimplified = simplify;
    Object.keys(batches).sort().forEach(function (k) {
      var b = batches[k];
      g.beginPath();
      b.list.forEach(function (e) { g.moveTo(e[0][0], e[0][1]); g.lineTo(e[1][0], e[1][1]); });
      g.globalAlpha = simplify && b.dash.length ? 0.45 : 1;
      g.strokeStyle = b.colour; g.setLineDash(simplify ? [] : b.dash); g.lineWidth = /255,107/.test(b.colour) ? 1.6 : 1.2; g.stroke();
    });
    g.setLineDash([]);
    g.globalAlpha = 1;
    // nodes
    var many = ids.length > 1500 && s < 0.6;
    var occupied = new Grid(32), hits = new Grid(24);
    this._overlays().forEach(function (r) { occupied.add(r); });
    ids.forEach(function (id) {
      var p = self.pos[id], q = scr[id], n = p.n;
      if (q[0] < -20 || q[1] < -20 || q[0] > w + 20 || q[1] > h + 20) return;
      var sc = SCALE[n.group] * (many ? 0.7 : 1);
      var shape = n.group === "changed" && n.change && SHAPES[n.change] ? n.change : n.group;
      g.fillStyle = c[n.group];
      if (many) { g.fillRect(q[0] - 3, q[1] - 3, 6, 6); }
      else {
        var sp = self._sprite(shape, n.group, sc);
        g.drawImage(sp.img, q[0] - sp.size / 2, q[1] - sp.size / 2, sp.size, sp.size);
      }
      var rr = 14 * sc + 2;
      occupied.add({ x: q[0] - rr, y: q[1] - rr, w: 2 * rr, h: 2 * rr });
      hits.add({ x: q[0] - rr - 3, y: q[1] - rr - 3, w: 2 * rr + 6, h: 2 * rr + 6, id: id });
      if (id === self.selected) { g.strokeStyle = "#ffffff"; g.lineWidth = 1.6; g.beginPath(); g.arc(q[0], q[1], 14, 0, 2 * Math.PI); g.stroke(); }
    });
    // folded rings
    this.bubbleHits = [];
    g.font = "600 11px " + c.mono;
    this.bubbles.forEach(function (b) {
      var text = "+ " + b.count.toLocaleString("en-US") + " more · distance " + b.ring, bw = g.measureText(text).width + 24;
      var q, x, y, tries = [-0.25, 0.25, 0.75, -0.75, 0, 0.5, 1, -0.5];
      for (var t = 0; t < tries.length; t++) {
        var a = tries[t] * Math.PI;
        q = self.toScreen({ x: Math.cos(a) * b.r, y: Math.sin(a) * b.r });
        x = q[0] - bw / 2; y = q[1] - 14;
        if (!occupied.hits({ x: x, y: y, w: bw, h: 28 })) break;
      }
      g.fillStyle = "rgba(21,24,29,0.95)"; g.strokeStyle = "#3a414d"; g.lineWidth = 1;
      g.beginPath(); g.roundRect(x, y, bw, 28, 14); g.fill(); g.stroke();
      g.fillStyle = "#d7dce4"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, q[0], q[1] + 0.5);
      occupied.add({ x: x - 4, y: y - 4, w: bw + 8, h: 36 });
      self.bubbleHits.push({ x: x, y: y, w: bw, h: 28, ring: b.ring });
    });
    // labels: by priority, 9 spots each, first free spot wins; none overlap
    var want = ids.map(function (id) { return self.pos[id]; }).filter(function (p) {
      var q = scr[p.n.id]; return q[0] > -40 && q[1] > -20 && q[0] < w + 40 && q[1] < h + 20;
    });
    var zoomedOut = s < 0.45;
    want.sort(function (a, b) {
      return (b.n.id === self.selected) - (a.n.id === self.selected) || (b.n.id === self.hover) - (a.n.id === self.hover) ||
        PRIO[a.n.group] - PRIO[b.n.group] || a.n.ring - b.n.ring || a.n.order - b.n.order;
    });
    var moving = performance.now() - this._moving < 150;
    var placed = 0, hidden = 0, budget = moving ? 80 : 600, rects = [];
    want.forEach(function (p) {
      var n = p.n, q = scr[n.id];
      var must = n.id === self.selected || n.id === self.hover || n.group === "changed";
      if (!must && (budget-- <= 0 || (zoomedOut && PRIO[n.group] > 2))) { hidden++; return; }
      g.font = (n.group === "changed" ? "600 12px " : "11px ") + c.mono;
      var label = n.name.length > 48 ? n.name.slice(0, 47) + "…" : n.name;
      var wkey = (n.group === "changed" ? "b" : "r") + label, tw = self._widths[wkey];
      if (tw === undefined) tw = self._widths[wkey] = g.measureText(label).width;
      var th = 14, o = n.group === "changed" ? 20 : 12;
      var ca = Math.cos(p.a), sa = Math.sin(p.a);
      var cands = [[q[0] + ca * o + (ca >= 0 ? 0 : -tw), q[1] + sa * o - th / 2], [q[0] + o, q[1] - th / 2], [q[0] - o - tw, q[1] - th / 2],
        [q[0] - tw / 2, q[1] - o - th], [q[0] - tw / 2, q[1] + o], [q[0] + o * 0.7, q[1] - o * 0.7 - th], [q[0] + o * 0.7, q[1] + o * 0.7],
        [q[0] - o * 0.7 - tw, q[1] - o * 0.7 - th], [q[0] - o * 0.7 - tw, q[1] + o * 0.7]];
      if (!n.ring) cands.unshift([q[0] - tw / 2, q[1] + o]);
      var spot = null;
      for (var i = 0; i < cands.length && !spot; i++) {
        var r = { x: cands[i][0] - 3, y: cands[i][1] - 1, w: tw + 6, h: th + 2 };
        if (r.x < 4 || r.y < 4 || r.x + r.w > w - 4 || r.y + r.h > h - 4) continue;
        if (!occupied.hits(r)) spot = r;
      }
      var forced = false;
      if (!spot && must) {  // always shown, kept inside the canvas
        var fx = Math.max(4, Math.min(w - tw - 10, cands[0][0] - 3)), fy = Math.max(4, Math.min(h - th - 6, cands[0][1] - 1));
        spot = { x: fx, y: fy, w: tw + 6, h: th + 2 };
        forced = true;
      }
      if (!spot) { hidden++; return; }
      rects.push({ x: spot.x, y: spot.y, w: spot.w, h: spot.h, forced: forced });
      occupied.add(spot);
      placed++;
      g.fillStyle = "rgba(11,13,16,0.82)"; g.fillRect(spot.x, spot.y, spot.w, spot.h);
      g.fillStyle = n.group === "breaks" ? "#ffb3ab" : n.group === "may_break" ? "#fcd57a" : c.label;
      g.textAlign = "left"; g.textBaseline = "middle"; g.fillText(label, spot.x + 3, spot.y + spot.h / 2 + 0.5);
    });
    this.hits = hits;
    this.stats.labels = placed;
    this.stats.labelRects = rects;
    this.stats.hiddenLabels = hidden;
    if (moving && !this._settle) this._settle = setTimeout(function () { self._settle = 0; self.draw(); }, 170);
    this.stats.frames++;
    this.stats.lastMs = performance.now() - t0;
    this.stats.totalMs = (this.stats.totalMs || 0) + this.stats.lastMs;
    this.stats.maxMs = Math.max(this.stats.maxMs || 0, this.stats.lastMs);
    if (this.opts.onDrawn) this.opts.onDrawn(this);
  };

  GraphView.prototype.nodeAt = function (x, y) {
    if (this.bubbleHits) for (var i = 0; i < this.bubbleHits.length; i++) {
      var b = this.bubbleHits[i];
      if (x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) return { ring: b.ring };
    }
    var found = this.hits ? this.hits.at(x, y) : null;
    return found ? { id: found.id } : null;
  };

  GraphView.prototype._wire = function () {
    var self = this, cv = this.canvas, down = null, moved = false;
    cv.addEventListener("pointerdown", function (e) { down = { x: e.clientX, y: e.clientY, vx: self.view.x, vy: self.view.y }; moved = false; cv.setPointerCapture(e.pointerId); });
    cv.addEventListener("pointermove", function (e) {
      if (down) {
        var dx = e.clientX - down.x, dy = e.clientY - down.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
        if (moved) { self.view.x = down.vx + dx; self.view.y = down.vy + dy; self._moving = performance.now(); self.draw(); }
        return;
      }
      var r = cv.getBoundingClientRect(), hit = self.nodeAt(e.clientX - r.left, e.clientY - r.top);
      var id = hit && hit.id ? hit.id : null;
      cv.style.cursor = hit ? "pointer" : "grab";
      if (id !== self.hover) { self.hover = id; self.draw(); }
    });
    cv.addEventListener("pointerup", function (e) {
      if (!down) return;
      down = null;
      if (moved) return;
      var r = cv.getBoundingClientRect(), hit = self.nodeAt(e.clientX - r.left, e.clientY - r.top);
      if (hit && hit.ring !== undefined) { self.openRings[hit.ring] = true; self.layout(); }
      else if (hit && hit.id) self.select(hit.id);
    });
    cv.addEventListener("wheel", function (e) {
      e.preventDefault();
      var r = cv.getBoundingClientRect(), mx = e.clientX - r.left - self.w / 2, my = e.clientY - r.top - self.h / 2;
      var f = Math.exp(-e.deltaY * 0.0015), s0 = self.view.s, s1 = Math.max(0.03, Math.min(6, s0 * f));
      self.view.x = mx - ((mx - self.view.x) * s1) / s0;
      self.view.y = my - ((my - self.view.y) * s1) / s0;
      self.view.s = s1;
      self._moving = performance.now();
      self.draw();
    }, { passive: false });
    this._onResize = function () { self.resize(); self.draw(); };
    window.addEventListener("resize", this._onResize);
    if (window.ResizeObserver) {  // the side panel opening or the sidebar collapsing changes the stage size
      this._ro = new ResizeObserver(this._onResize);
      this._ro.observe(this.stage);
    }
  };

  GraphView.prototype.zoom = function (f) {
    var s1 = Math.max(0.03, Math.min(6, this.view.s * f)), k = s1 / this.view.s;
    this.view.s = s1; this.view.x *= k; this.view.y *= k; this.draw();
  };
  GraphView.prototype.select = function (id) { this.selected = id; this.draw(); if (this.opts.onSelect) this.opts.onSelect(id); };
  GraphView.prototype.setShow = function (group, on) { this.show[group] = on; this.layout(); };
  GraphView.prototype.setFocus = function (on) { this.focus = on && this.selected ? this.selected : null; this.layout(); this.fit(); };
  GraphView.prototype.destroy = function () {
    window.removeEventListener("resize", this._onResize);
    if (this._ro) this._ro.disconnect();
  };

  // a uniform grid of rectangles, for "does this rectangle hit anything?" in near constant time
  function Grid(cell) { this.cell = cell; this.m = {}; }
  Grid.prototype._keys = function (r, f) {
    var c = this.cell;
    for (var i = Math.floor(r.x / c); i <= Math.floor((r.x + r.w) / c); i++)
      for (var j = Math.floor(r.y / c); j <= Math.floor((r.y + r.h) / c); j++) f(i + "," + j);
  };
  Grid.prototype.add = function (r) { var m = this.m; this._keys(r, function (k) { (m[k] = m[k] || []).push(r); }); };
  Grid.prototype.hits = function (r) {
    var m = this.m, found = false;
    this._keys(r, function (k) {
      if (found || !m[k]) return;
      for (var i = 0; i < m[k].length; i++) {
        var o = m[k][i];
        if (r.x < o.x + o.w && o.x < r.x + r.w && r.y < o.y + o.h && o.y < r.y + r.h) { found = true; return; }
      }
    });
    return found;
  };
  Grid.prototype.at = function (x, y) {
    var list = this.m[Math.floor(x / this.cell) + "," + Math.floor(y / this.cell)] || [], best = null;
    for (var i = list.length - 1; i >= 0; i--) {
      var o = list[i];
      if (x >= o.x && x <= o.x + o.w && y >= o.y && y <= o.y + o.h) { best = o; break; }
    }
    return best;
  };

  window.AeroTraceGraph = GraphView;
})();
