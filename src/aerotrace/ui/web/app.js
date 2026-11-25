// aerotrace ui: app shell, router and pages. Talks only to /api/* on the same origin.
// Repo text (names, paths, messages) is only ever put into the page with textContent, never as HTML.
(function () {
  "use strict";

  // ---------------------------------------------------------------- tiny DOM helpers
  var SVG = "http://www.w3.org/2000/svg";
  function h(tag, attrs) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "text") e.textContent = attrs[k];
      else if (k === "cls") e.className = attrs[k];
      else e.setAttribute(k, attrs[k]);
    }
    var add = function (c) {
      if (c === null || c === undefined || c === false || c === "") return;
      if (Array.isArray(c)) { c.forEach(add); return; }
      e.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    };
    for (var i = 2; i < arguments.length; i++) add(arguments[i]);
    return e;
  }
  function s(tag, attrs) {
    var e = document.createElementNS(SVG, tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    return e;
  }
  function n(x) { return Number(x).toLocaleString("en-US"); }
  function plural(x, one, many) { return n(x) + " " + (x === 1 ? one : (many || one + "s")); }

  // ---------------------------------------------------------------- icons (paths match ui/html.py SHAPES)
  var SHAPES = {
    changed: ["M0,-13L13,0L0,13L-13,0Z", "M-3,-3H3V3H-3Z"],
    breaks: ["M-10,-10H10V10H-10Z", "M-5,-5L5,5M5,-5L-5,5"],
    may_break: ["M-10,-10H10V10H-10Z", "M-5,-5L5,5M5,-5L-5,5", true],
    affected: ["M0,-12L12,10H-12Z", "M0,-4V3M0,6V7"],
    unknown: ["M-10,0A10,10 0 1,0 10,0A10,10 0 1,0 -10,0Z", "M-3,-4C-3,-8 3,-8 3,-4C3,-1 0,-1 0,2M0,5V6"],
    none: ["M-6,0A6,6 0 1,0 6,0A6,6 0 1,0 -6,0Z", "M-3,0H3"],
  };
  var WORD = { changed: "CHANGED", breaks: "BREAKS", may_break: "MAY BREAK", affected: "AFFECTED", unknown: "UNKNOWN",
    none: "NO EFFECT FOUND", no_effect: "NO EFFECT FOUND" };
  function lvl(group) { return group === "no_effect" ? "none" : group; }
  function statusIcon(level) {
    var p = SHAPES[level] || SHAPES.none, svg = s("svg", { viewBox: "-14 -14 28 28", "aria-hidden": "true" });
    svg.appendChild(s("path", { d: p[0], "class": "st-shape" + (p[2] ? " dash" : "") }));
    svg.appendChild(s("path", { d: p[1], "class": "st-mark" }));
    return svg;
  }
  function chip(level, label) {
    // a count-only chip still names its status for screen readers (and never says it twice)
    var word = label === undefined || label === WORD[level] || /[A-Z]{3}/.test(String(label)) ? null : h("span", { cls: "sr", text: WORD[level] + " " });
    return h("span", { cls: "chip " + level }, statusIcon(level), word, label === undefined ? WORD[level] : label);
  }
  var ICONS = {
    overview: "M3 3h7v7H3zM14 3h7v4h-7zM14 11h7v10h-7zM3 14h7v7H3z",
    changes: "M4 6h16M4 12h16M4 18h10",
    graph: "M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16",
    benchmark: "M4 20V10M10 20V4M16 20v-7M22 20H2",
    memory: "M12 3a9 9 0 1 0 9 9M12 7v5l3 2",
    track: "M4 12l5 5L20 6",
    side: "M3 4h18v16H3zM9 4v16",
    theme: "M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5",
    info: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 11v5M12 8v.5",
    warn: "M12 3l10 18H2zM12 10v5M12 18v.5",
    inbox: "M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5",
  };
  function icon(name, cls) {
    var svg = s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", "class": cls || "i" });
    svg.appendChild(s("path", { d: ICONS[name], "class": "ico" }));
    return svg;
  }

  // ---------------------------------------------------------------- data
  var cache = {};
  function get(path) {
    if (!cache[path]) {
      cache[path] = fetch(path, { credentials: "same-origin" }).then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) { var err = new Error(body && body.error ? body.error : "HTTP " + r.status); err.server = true; throw err; }
          return body;
        });
      }, function () { var err = new Error("The dashboard server can't be reached."); err.server = false; throw err; });
      cache[path].catch(function () { delete cache[path]; });
    }
    return cache[path];
  }

  // ---------------------------------------------------------------- states
  function emptyState(iconName, title, text, command) {
    return h("div", { cls: "state" }, icon(iconName, "big"), h("h2", { text: title }), text ? h("p", { text: text }) : null,
      command ? h("div", { cls: "cmd" }, command) : null);
  }
  function errorState(title, message, command, raw) {
    return h("div", { cls: "state error", role: "alert" }, icon("warn", "big"), h("h2", { text: title }),
      h("p", { text: message }), command ? h("div", { cls: "cmd" }, command) : null,
      raw ? h("details", { cls: "raw" }, h("summary", { cls: "dim", text: "What went wrong" }), h("pre", { text: raw })) : null);
  }
  function loading() {
    var box = h("div", { cls: "page loading", "aria-busy": "true", hidden: "" },
      h("div", { cls: "skel h14" }), h("div", { cls: "grid g3" }, h("div", { cls: "skel h96" }), h("div", { cls: "skel h96" }), h("div", { cls: "skel h96" })),
      h("p", { cls: "dim", text: "Loading…" }));
    setTimeout(function () { box.removeAttribute("hidden"); }, 150); // only for loads that take a while
    return box;
  }

  // ---------------------------------------------------------------- pages
  function pageHead(caps, title, sub) {
    return h("div", { cls: "page-head" }, h("div", null, h("p", { cls: "caps", text: caps }), h("h1", { text: title }),
      sub ? h("div", { cls: "sub" }, sub) : null));
  }

  function overview(main) {
    return Promise.all([get("/api/overview"), get("/api/runinfo")]).then(function (res) {
      var o = res[0], run = res[1];
      document.getElementById("repo-name").textContent = o.repo;
      var page = h("div", { cls: "page" });
      if (o.status !== "ok") {
        page.appendChild(pageHead("Overview", o.repo));
        page.appendChild(o.status === "no_index"
          ? emptyState("inbox", "No index yet", "Build one in this repo and it shows up here.", o.fix)
          : errorState("Can't read the index", "Build it again to fix this.", o.fix, o.message));
        main.replaceChildren(page);
        return;
      }
      var ix = o.index, cov = o.coverage;
      var when = run.indexed ? "last indexed " + run.indexed.replace("T", " ").replace("+00:00", " UTC") : "date unknown";
      page.appendChild(pageHead("Overview", o.repo, [o.tool ? "Index made by AeroTrace " + o.tool + " · " : "", h("span", { cls: "mono", text: when })]));
      if (o.changed_since_index) {
        page.appendChild(h("div", { cls: "warnbar", role: "status" }, icon("warn"),
          plural(o.changed_since_index, "file") + " changed since the last index, so answers may be out of date. Run:",
          h("code", { text: "aerotrace index" })));
      }
      page.appendChild(h("div", { cls: "grid g3" },
        h("div", { cls: "card stat" }, h("p", { cls: "caps", text: "Files" }), h("div", { cls: "n", text: n(ix.files) }),
          h("div", { cls: "d", text: n(ix.parsed) + " parsed · " + n(ix.headers) + " headers" })),
        h("div", { cls: "card stat" }, h("p", { cls: "caps", text: "Functions" }), h("div", { cls: "n", text: n(ix.functions) }),
          h("div", { cls: "d", text: n(ix.calls) + " calls · " + n(ix.data_uses) + " data uses" })),
        h("div", { cls: "card stat" }, h("p", { cls: "caps", text: "Blind spots" }), h("div", { cls: "n", text: n(o.blind_spots_total) }),
          h("div", { cls: "d", text: "places AeroTrace can't follow" }))));
      // coverage line: what was checked and, as plainly, what was not
      var pct = cov.build_files ? (100 * cov.checked) / cov.build_files : 0;
      var bar = h("i");
      bar.style.width = pct.toFixed(1) + "%";
      var notes = [];
      if (cov.with_errors) notes.push(plural(cov.with_errors, "file") + " had parse errors, so parts of them may be missing.");
      if (cov.outside_build) notes.push(plural(cov.outside_build, "source file") + " in the repo " + (cov.outside_build === 1 ? "is" : "are") +
        " not in compile_commands.json, so nothing in them was checked" + (cov.outside_examples.length ? " (for example " + cov.outside_examples.slice(0, 3).join(", ") + ")." : "."));
      page.appendChild(h("div", { cls: "coverage", role: "note" }, icon("info"),
        h("div", { cls: "grow" },
          h("div", null, "Checked ", h("b", { cls: "mono", text: n(cov.checked) }), " of ", h("b", { cls: "mono", text: n(cov.build_files) }), " files in this build."),
          notes.length ? h("div", { cls: "muted", text: notes.join(" ") }) : null,
          h("div", { cls: "bar", role: "img", "aria-label": n(cov.checked) + " of " + n(cov.build_files) + " build files checked" }, bar))));
      // recent checks + blind spots
      var recent = h("section", { cls: "card", "aria-labelledby": "recent-h" },
        h("div", { cls: "head-row" }, h("h2", { id: "recent-h", text: "Recent checks" }), h("a", { href: "#/changes", text: "All checks →" })));
      if (!o.recent_checks.length) {
        recent.appendChild(h("div", { cls: "state compact" }, h("p", { text: "No checks yet. Run:" }), h("div", { cls: "cmd", text: "aerotrace check" }),
          h("p", { cls: "dim", text: "Saved answers go in .aerotrace/checks/." })));
      } else {
        var ul = h("ul", { cls: "list" });
        o.recent_checks.forEach(function (c) {
          var date = run.checks[c.id];
          ul.appendChild(h("li", null,
            c.ok ? chip(c.level, String(c.counts.breaks || c.counts.may_break || c.counts.affected || c.counts.unknown || "")) : chip("unknown", "?"),
            h("div", { cls: "grow" }, h("a", { href: "#/graph/" + encodeURIComponent(c.id), text: c.ok ? c.verdict : c.problem }),
              h("div", { cls: "fl", text: (c.source || "source unknown") + " · " + c.id })),
            h("span", { cls: "fl", text: date ? date.replace("T", " ").slice(0, 16) : "date unknown" })));
        });
        recent.appendChild(ul);
      }
      var blind = h("section", { cls: "card", "aria-labelledby": "blind-h" }, h("h2", { id: "blind-h", text: "Blind spots" }),
        h("p", { cls: "muted", text: "Where links can't be proven. Answers that pass through these say so." }));
      if (!o.blind_spots.length) blind.appendChild(h("p", { cls: "dim", text: "None recorded in this index." }));
      else {
        var bl = h("ul", { cls: "list" });
        o.blind_spots.forEach(function (b) { bl.appendChild(h("li", null, h("div", { cls: "grow", text: b.label }), h("span", { cls: "mono", text: n(b.count) }))); });
        if (cov.with_errors) bl.appendChild(h("li", null, h("div", { cls: "grow", text: "Files with parse errors" }), h("span", { cls: "mono", text: n(cov.with_errors) })));
        blind.appendChild(bl);
      }
      page.appendChild(h("div", { cls: "grid g2" }, recent, blind));
      main.replaceChildren(page);
    });
  }

  function dateText(d) { return d ? d.replace("T", " ").slice(0, 16) + " UTC" : "date unknown"; }

  function changes(main) {
    return Promise.all([get("/api/checks"), get("/api/runinfo"), get("/api/overview")]).then(function (res) {
      var list = res[0].checks, run = res[1], o = res[2];
      document.getElementById("repo-name").textContent = o.repo;
      var page = h("div", { cls: "page" }, pageHead("Changes", "Past checks",
        ["Every ", h("code", { text: "aerotrace check" }), " run in this repo, newest first. Open one to see its graph."]));
      if (o.changed_since_index) {
        page.appendChild(h("div", { cls: "warnbar", role: "status" }, icon("warn"),
          plural(o.changed_since_index, "file") + " changed since the last index, so newer checks may differ. Run:", h("code", { text: "aerotrace index" })));
      }
      if (!list.length) {
        page.appendChild(emptyState("inbox", "No checks yet", "Check a change and it shows up here. Answers are kept in " + res[0].dir + "/.", "aerotrace check"));
        main.replaceChildren(page);
        return;
      }
      var body = h("tbody");
      list.forEach(function (c) {
        var href = "#/graph/" + encodeURIComponent(c.id);
        var link = h("a", { href: href, text: c.ok ? c.verdict : c.problem });
        var detail = c.ok
          ? [c.changed || "nothing AeroTrace can place changed", c.counts.not_followed ? plural(c.counts.not_followed, "part") + " not followed" : null]
              .filter(Boolean).join(" · ")
          : "this answer can't be shown";
        var tr = h("tr", { cls: "row" },
          h("td", null, c.ok ? chip(c.level) : chip("unknown", "CAN'T READ")),
          h("td", null, link, h("div", { cls: "fl", text: detail })),
          h("td", { cls: "mono src", text: c.source || "source unknown" }),
          h("td", { cls: "num", text: dateText(run.checks[c.id]) }));
        tr.addEventListener("click", function (ev) { if (ev.target.tagName !== "A") location.hash = href; });
        body.appendChild(tr);
      });
      page.appendChild(h("div", { cls: "tablewrap" }, h("table", null,
        h("thead", null, h("tr", null, h("th", { scope: "col", text: "Verdict" }), h("th", { scope: "col", text: "What it found" }),
          h("th", { scope: "col", text: "Source" }), h("th", { scope: "col", cls: "num", text: "Date" }))), body)));
      page.appendChild(h("p", { cls: "dim small", text: "Showing " + plural(list.length, "saved check") + ". AeroTrace keeps the last 50, in " + res[0].dir + "/." }));
      main.replaceChildren(page);
    });
  }

  var GWORD = { changed: "CHANGED", breaks: "BREAKS", may_break: "MAY BREAK", affected: "AFFECTED", unknown: "UNKNOWN", no_effect: "NO EFFECT FOUND" };
  var GMEANS = { changed: "part of the change", breaks: "won't build or link", may_break: "may not build; one step is a guess",
    affected: "builds, but behaviour may change", unknown: "AeroTrace couldn't follow it", no_effect: "checked, no effect found" };
  var view = null;
  var graphKeys = null;

  function graphPage(main, args) {
    if (view) { view.destroy(); view = null; }
    var id = args[0] ? decodeURIComponent(args[0]) : null;
    if (!id) {
      return get("/api/checks").then(function (c) {
        var first = c.checks.filter(function (x) { return x.ok; })[0];
        if (first) { location.replace("#/graph/" + encodeURIComponent(first.id)); return; }
        main.replaceChildren(h("div", { cls: "page" }, pageHead("Graph", "Impact graph"),
          emptyState("graph", "No check to draw yet", "Check a change and its graph shows up here.", "aerotrace check")));
      });
    }
    return get("/api/graph/" + encodeURIComponent(id)).then(function (d) {
      var counts = d.counts;
      var bar = h("div", { cls: "gbar" }, chip(d.level), h("span", { cls: "verdict", text: d.verdict, title: d.verdict }),
        h("span", { cls: "fl src", text: d.source || "" }), h("span", { cls: "sp" }));
      var chips = h("div", { cls: "chips", role: "group", "aria-label": "Show on the graph" });
      ["breaks", "may_break", "affected", "unknown", "no_effect"].forEach(function (g) {
        if (!counts[g]) return;
        var lvl = g === "no_effect" ? "none" : g;
        chips.appendChild(h("button", { type: "button", cls: "chip filter " + lvl, "aria-pressed": String(g === "breaks" || g === "may_break"), "data-group": g },
          statusIcon(lvl), h("span", { cls: "w", text: GWORD[g] + " " }), h("span", { cls: "c", text: n(counts[g]) })));
      });
      bar.appendChild(chips);
      var search = h("input", { type: "search", cls: "search", placeholder: "Find a function", "aria-label": "Find a function, or file:line", "data-search": "" });
      bar.appendChild(search);
      var listBtn = h("button", { type: "button", cls: "btn sm", "aria-pressed": "false", text: "List" });
      bar.appendChild(listBtn);
      var stage = h("div", { cls: "stage" });
      var legend = h("div", { cls: "glass legend", "data-overlay": "", role: "group", "aria-label": "Legend" });
      ["changed", "breaks", "may_break", "affected", "unknown", "no_effect"].forEach(function (g) {
        legend.appendChild(h("div", { cls: "r" }, h("span", { cls: "lg lg-" + g }, statusIcon(lvl(g))), h("span", { text: GWORD[g] }),
          h("span", { cls: "m", text: GMEANS[g] })));
      });
      legend.appendChild(h("div", { cls: "lines" }, h("span", null, h("i", { cls: "e" }), "exact"), h("span", null, h("i", { cls: "e dash" }), "probable"),
        h("span", null, h("i", { cls: "e dot" }), "unknown"), h("span", null, h("i", { cls: "e skip" }), "through hidden steps")));
      var hint = h("div", { cls: "hint", "data-overlay": "" });
      var tools = h("div", { cls: "glass tools", "data-overlay": "", role: "group", "aria-label": "View" },
        h("button", { type: "button", "aria-label": "Zoom in (+)", text: "+" }), h("button", { type: "button", "aria-label": "Zoom out (-)", text: "−" }),
        h("button", { type: "button", "aria-label": "Fit to screen (0)", text: "⤢" }), h("button", { type: "button", "aria-label": "Focus on the selected node (f)", "aria-pressed": "false", text: "◎" }));
      stage.appendChild(legend); stage.appendChild(hint); stage.appendChild(tools);
      var list = h("div", { cls: "glist", hidden: "" });
      var panel = h("aside", { cls: "panel", "aria-label": "Details", hidden: "" });
      var body2 = h("div", { cls: "body2" }, stage, list, panel);
      var notes = [];
      if (d.not_followed) notes.push(h("span", { cls: "chip unknown" }, statusIcon("unknown"), plural(d.not_followed, "part") + " of the change not followed"));
      if (d.stopped_early) notes.push(h("span", { cls: "chip unknown" }, statusIcon("unknown"), "stopped at depth " + d.depth + "; more callers exist further up"));
      if (d.omitted) notes.push(h("span", { cls: "chip unknown" }, statusIcon("unknown"), n(d.omitted) + " nodes left out (over the 5,000 cap)"));
      var seeNot = null;
      if (d.blind_spots.length) {
        var ul = h("ul", { cls: "blind" });
        d.blind_spots.forEach(function (b) { ul.appendChild(h("li", { cls: b.scope === "change" ? "" : "dim", text: b.text })); });
        seeNot = h("details", { cls: "gblind" }, h("summary", { text: "What AeroTrace couldn't see (" + d.blind_spots.length + ")" }), ul);
        if (d.blind_spots.some(function (b) { return b.scope === "change"; })) seeNot.setAttribute("open", "");  // about this change: shown
      }
      var top = notes.length || seeNot ? h("div", { cls: "gnotes" }, notes, seeNot) : null;
      main.replaceChildren(h("div", { cls: "gwrap" }, bar, top, body2));
      if (!document.body.contains(stage)) return;  // a newer page replaced this one while it loaded

      function updateHint(v) {
        var off = 0;
        d.nodes.forEach(function (x) { if (!v.show[x.group]) off++; });
        var parts = [];
        if (v.focus) parts.push("Focus: the selected node, its path and what depends on it. Press f to leave.");
        else if (off) parts.push(n(off) + " nodes are in groups that are off. Turn a chip on to show them.");
        if (v.stats.hiddenLabels) parts.push(plural(v.stats.hiddenLabels, "label") + " hidden so none overlap; zoom in to see more.");
        if (v.stats.dashesSimplified) parts.push("Zoom in to see which lines are probable (dashed) or unknown (dotted).");
        hint.textContent = parts.join("  ·  ");
        hint.hidden = !parts.length;
      }
      var gv = new window.AeroTraceGraph(stage, d, { onSelect: openNode, onDrawn: updateHint });
      view = gv;

      function renderList() {
        var vis = gv.visibleNodes().slice().sort(function (a, b) { return a.ring - b.ring || a.order - b.order; });
        var tbody = h("tbody");
        vis.slice(0, 2000).forEach(function (x) {
          var b = h("button", { type: "button", cls: "linkish", text: x.name });
          b.addEventListener("click", function () { gv.select(x.id); });
          tbody.appendChild(h("tr", null, h("td", null, chip(lvl(x.group), x.word)), h("td", null, b),
            h("td", { cls: "fl", text: x.file + ":" + x.line }), h("td", { cls: "num", text: String(x.ring) })));
        });
        var off = 0;
        d.nodes.forEach(function (x) { if (!gv.show[x.group]) off++; });
        list.replaceChildren(h("table", null, h("caption", { cls: "dim", text: "Shown: " + plural(vis.length, "node") + (vis.length > 2000 ? " (the first 2,000 are listed)" : "") +
          (off && !gv.focus ? ". " + plural(off, "more node") + " are in groups that are off; turn a chip on to list them." : "") }),
          h("thead", null, h("tr", null, h("th", { text: "Status" }), h("th", { text: "Name" }), h("th", { text: "Where" }), h("th", { cls: "num", text: "Distance" }))), tbody));
      }
      chips.querySelectorAll("[data-group]").forEach(function (b) {
        b.addEventListener("click", function () {
          var on = b.getAttribute("aria-pressed") !== "true";
          b.setAttribute("aria-pressed", String(on));
          gv.setShow(b.getAttribute("data-group"), on);
          if (!list.hidden) renderList();
        });
      });
      var tb = tools.querySelectorAll("button");
      function toggleFocus() {
        if (!gv.selected && !gv.focus) return;
        gv.setFocus(!gv.focus);
        tb[3].setAttribute("aria-pressed", String(!!gv.focus));
      }
      tb[0].addEventListener("click", function () { gv.zoom(1.25); });
      tb[1].addEventListener("click", function () { gv.zoom(0.8); });
      tb[2].addEventListener("click", function () { gv.fit(); });
      tb[3].addEventListener("click", toggleFocus);
      listBtn.addEventListener("click", function () {
        var on = list.hidden;
        list.hidden = !on; stage.hidden = on;
        listBtn.setAttribute("aria-pressed", String(on));
        if (on) renderList(); else gv.fit();
      });
      search.addEventListener("keydown", function (e) {
        if (e.key !== "Enter") return;
        var q = search.value.trim().toLowerCase();
        if (!q) return;
        var m = q.match(/^(.+):(\d+)$/);
        var hit = d.nodes.filter(function (x) {
          return m ? x.file.toLowerCase().endsWith(m[1]) && x.line === Number(m[2]) : x.name.toLowerCase().indexOf(q) >= 0;
        }).sort(function (a, b) { return (a.name.toLowerCase() === q ? 0 : 1) - (b.name.toLowerCase() === q ? 0 : 1) || a.ring - b.ring || a.order - b.order; })[0];
        if (!hit) { search.setCustomValidity("No node matches"); search.reportValidity(); return; }
        search.setCustomValidity("");
        if (!gv.show[hit.group]) {
          var c = chips.querySelector('[data-group="' + hit.group + '"]');
          if (c) c.setAttribute("aria-pressed", "true");
          gv.setShow(hit.group, true);
        }
        gv.select(hit.id); gv.center(hit.id, true);
      });
      var returnTo = null;
      function closePanel() {
        panel.hidden = true; gv.selected = null; gv.draw();
        if (returnTo && document.body.contains(returnTo)) returnTo.focus({ preventScroll: true });
      }
      function openNode(nodeId) {
        if (panel.hidden) returnTo = document.activeElement;
        panel.hidden = false;
        panel.replaceChildren(h("div", { cls: "pad dim", text: "Loading…" }));
        get("/api/node?check=" + encodeURIComponent(d.id) + "&node=" + encodeURIComponent(nodeId)).then(function (x) {
          var close = h("button", { type: "button", cls: "btn ghost icon sm", "aria-label": "Close details (Esc)", text: "✕" });
          close.addEventListener("click", closePanel);
          var copy = h("button", { type: "button", cls: "btn ghost sm copy", text: x.file + ":" + x.line, "aria-label": "Copy " + x.file + ":" + x.line });
          copy.addEventListener("click", function () { if (navigator.clipboard) navigator.clipboard.writeText(x.file + ":" + x.line); copy.textContent = "Copied"; });
          var head = h("header", null, h("div", { cls: "grow" }, chip(lvl(x.group), x.word), h("h2", { cls: "mono pname", text: x.name }), copy), close);
          var b = h("div", { cls: "pbody" });
          if (x.why) b.appendChild(h("section", null, h("p", { cls: "caps", text: "Why" }), h("p", { text: x.why })));
          if (x.blind_spots && x.blind_spots.length) {
            var bl = h("ul", { cls: "blind" });
            x.blind_spots.forEach(function (t) { bl.appendChild(h("li", { text: t })); });
            b.appendChild(h("section", null, h("p", { cls: "caps", text: "What AeroTrace couldn't see here" }), bl));
          }
          var code = h("section", null, h("p", { cls: "caps", text: "Code" }));
          if (x.snippet) {
            var sn = h("div", { cls: "snippet", role: "region", "aria-label": "Code from " + x.snippet.file });
            x.snippet.lines.forEach(function (l) { sn.appendChild(h("div", { cls: l.hit ? "hit" : "" }, h("span", { cls: "ln", text: String(l.n) }), l.text)); });
            code.appendChild(sn);
          } else code.appendChild(h("p", { cls: "dim", text: x.snippet_note || "This file isn't in the index, so its code isn't shown." }));
          b.appendChild(code);
          b.appendChild(h("section", null, h("p", { cls: "caps", text: "How sure" }), h("dl", { cls: "kv" },
            h("dt", { text: "Certainty" }), h("dd", null, h("b", { text: x.certainty }), x.certainty_means ? ": " + x.certainty_means : ""),
            h("dt", { text: "Link" }), h("dd", { cls: "mono", text: x.link || "part of the change" }),
            h("dt", { text: "Distance" }), h("dd", { cls: "mono", text: String(x.distance) }))));
          var ol = h("ol", { cls: "path" });
          x.path.forEach(function (p) {
            var a = h("button", { type: "button", cls: "linkish mono", text: p.name });
            a.addEventListener("click", function () { if (gv.pos[p.id]) { gv.select(p.id); gv.center(p.id); } else openNode(p.id); });
            ol.appendChild(h("li", { cls: "g-" + p.group }, h("i", { "aria-hidden": "true" }), a, " ", h("span", { cls: "fl", text: p.file + ":" + p.line + " · " + p.word.toLowerCase() })));
          });
          b.appendChild(h("section", null, h("p", { cls: "caps", text: "Path from the change" }), ol));
          panel.replaceChildren(head, b);
          close.focus({ preventScroll: true });
        }, function (e) { panel.replaceChildren(h("div", { cls: "pad" }, errorState("Can't show this node", String(e.message), null, null))); });
      }
      escHandler = function () { if (!panel.hidden) closePanel(); else if (gv.focus) toggleFocus(); };
      graphKeys = function (k) {
        if (k === "f") toggleFocus();
        else if (k === "0") gv.fit();
        else if (k === "+" || k === "=") gv.zoom(1.25);
        else if (k === "-") gv.zoom(0.8);
        else return false;
        return true;
      };
    });
  }

  var TOOLS = ["aerotrace", "clangd", "cscope", "ripgrep"];
  var TOOL_NAME = { aerotrace: "AeroTrace", clangd: "clangd", cscope: "cscope", ripgrep: "ripgrep" };

  function benchmarkPage(main) {
    return get("/api/benchmark").then(function (r) {
      var d = r.benchmark;
      var page = h("div", { cls: "page" });
      if (!d) {
        page.appendChild(pageHead("Benchmark", "Benchmark"));
        page.appendChild(emptyState("benchmark", "No benchmark results in this build",
          "This copy of AeroTrace was built without its published results.", null));
        main.replaceChildren(page);
        return;
      }
      page.appendChild(pageHead("Recall · False alarms · Time · Tokens", "Benchmark",
        ["AeroTrace's own accuracy benchmark, run " + d.run + " on " + d.machine + ". Not about this repo."]));
      page.appendChild(h("div", { cls: "card lead" }, d.headline.map(function (t) { return h("p", { text: t }); })));
      if (d.wrong_safe && d.wrong_safe.length) {
        page.appendChild(h("div", { cls: "warnbar bad", role: "note" }, icon("warn"),
          "A wrong \"safe\" answer in " + plural(d.wrong_safe.length, "case") + ": in " + d.wrong_safe.join(", ") +
          " the build fails, but AeroTrace says \"None is sure to break\". A product bug, reported."));
      }
      var seg = h("div", { cls: "seg", role: "group", "aria-label": "Which cases" });
      var holder = h("div");
      var keys = Object.keys(d.views);
      function show(key) {
        seg.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-v") === key)); });
        var v = d.views[key], t = v.total;
        var maxFa = Math.max.apply(null, TOOLS.map(function (x) { return t[x].false_alarms; }).concat([1]));
        var rows = h("tbody");
        TOOLS.forEach(function (x) {
          var a = t[x], pct = a.truth ? (100 * a.found) / a.truth : 0;
          var fbar = h("i", { cls: x === "aerotrace" ? "at" : "" }); fbar.style.width = pct.toFixed(1) + "%";
          var abar = h("i", { cls: x === "aerotrace" ? "at" : "" }); abar.style.width = ((100 * a.false_alarms) / maxFa).toFixed(1) + "%";
          rows.appendChild(h("tr", null, h("th", { scope: "row", text: TOOL_NAME[x] }),
            h("td", null, h("div", { cls: "hbar", role: "img", "aria-label": n(a.found) + " of " + n(a.truth) + " breaks found" }, fbar), h("span", { cls: "mono", text: n(a.found) + " / " + n(a.truth) })),
            h("td", null, h("div", { cls: "hbar fa", role: "img", "aria-label": n(a.false_alarms) + " false alarms" }, abar), h("span", { cls: "mono", text: n(a.false_alarms) })),
            h("td", { cls: "num", text: a.seconds === null ? "n/a" : a.seconds < 1 ? Math.round(a.seconds * 1000) + " ms" : a.seconds.toFixed(1) + " s" }),
            h("td", { cls: "num", text: a.tokens === null ? "not counted" : n(a.tokens) })));
        });
        holder.replaceChildren(h("p", { cls: "muted", text: v.label + ": " + plural(v.cases, "case") + " (" + v.easy + " easy, " + v.hard + " hard), " + plural(t.aerotrace.truth, "break site") + "." }),
          h("div", { cls: "tablewrap" }, h("table", { cls: "bench" },
            h("thead", null, h("tr", null, h("th", { scope: "col", text: "Tool" }), h("th", { scope: "col", text: "Breaks found" }), h("th", { scope: "col", text: "False alarms" }),
              h("th", { scope: "col", cls: "num", text: "Median time" }), h("th", { scope: "col", cls: "num", text: "Tokens" }))), rows)));
      }
      keys.forEach(function (k) {
        var b = h("button", { type: "button", "data-v": k, text: d.views[k].label });
        b.addEventListener("click", function () { show(k); });
        seg.appendChild(b);
      });
      page.appendChild(h("div", { cls: "controls" }, seg, h("a", { cls: "btn sm", href: "/benchmark-report", text: "Open the full results page →" })));
      page.appendChild(holder);
      page.appendChild(h("p", { cls: "dim small", text: "Ties: " + d.ties.length + " of " + d.build_cases + " build cases. AeroTrace finds fewer in " +
        (d.fewer.join(", ") || "none") + ". Tokens: " + (d.tokens_counted ? "counted." : "not counted yet.") }));
      show("all");
      main.replaceChildren(page);
    });
  }

  function comingSoon(title, what) {
    return function (main) {
      main.replaceChildren(h("div", { cls: "page" }, h("div", { cls: "state" }, icon(title === "Memory" ? "memory" : "track", "big"),
        h("p", { cls: "caps", text: "Coming soon" }), h("h2", { text: title }), h("p", { text: what }))));
      return Promise.resolve();
    };
  }

  var PAGES = {
    overview: overview,
    changes: changes,
    graph: graphPage,
    benchmark: benchmarkPage,
    memory: comingSoon("Memory", "This page will show what AeroTrace has learned about this repo across checks. It isn't built yet, so there is nothing to show, not even sample data."),
    track: comingSoon("Track record", "This page will show how AeroTrace's past answers held up. It isn't built yet, so there is nothing to show, not even sample data."),
  };

  // ---------------------------------------------------------------- router
  var main = document.getElementById("main");
  var generation = 0;
  function route() {
    var mine = ++generation;
    var parts = (location.hash || "#/overview").slice(2).split("/");
    var name = PAGES[parts[0]] ? parts[0] : "overview";
    if (name !== "graph") { graphKeys = null; escHandler = null; if (view) { view.destroy(); view = null; } }
    document.querySelectorAll(".nav a").forEach(function (a) {
      if (a.getAttribute("data-page") === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = "AeroTrace · " + name.charAt(0).toUpperCase() + name.slice(1);
    main.replaceChildren(loading());
    // each page renders into its own holder; only the newest route's holder is put on screen
    var host = { replaceChildren: function () {
      if (mine !== generation) { if (view && view.stage && !document.body.contains(view.stage)) { view.destroy(); view = null; } return; }
      main.replaceChildren.apply(main, arguments);
    } };
    PAGES[name](host, parts.slice(1)).catch(function (e) {
      if (mine !== generation) return;
      var answered = e && e.server;  // the server answered with an error, so it hasn't stopped
      main.replaceChildren(h("div", { cls: "page" }, answered
        ? errorState("This page couldn't load", "The dashboard answered with an error. Rebuilding the index usually fixes it:", "aerotrace index", String(e.message))
        : errorState("This page couldn't load", "The dashboard server isn't answering; it may have stopped. Start it again:", "aerotrace ui", String(e && e.message || e))));
    });
    main.focus({ preventScroll: true });
  }

  // ---------------------------------------------------------------- shell controls
  document.querySelectorAll(".nav a").forEach(function (a) { a.setAttribute("title", a.textContent.replace("soon", "").trim()); });
  document.querySelectorAll("[data-icon]").forEach(function (el) {
    var name = el.getAttribute("data-icon");
    el.insertBefore(icon(name), el.firstChild);
  });
  var root = document.documentElement;
  function setTheme(t) {
    if (t === "dark") root.setAttribute("data-theme", "dark"); else root.removeAttribute("data-theme");
    document.getElementById("theme").setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
    try { localStorage.setItem("aerotrace-theme", t); } catch (e) { /* private mode: fine */ }
  }
  var saved = null;
  try { saved = localStorage.getItem("aerotrace-theme"); } catch (e) { /* ignore */ }
  setTheme(saved || (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  document.getElementById("theme").addEventListener("click", function () { setTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark"); });
  var app = document.getElementById("app"), collapse = document.getElementById("collapse");
  collapse.addEventListener("click", function () {
    var c = app.classList.toggle("collapsed");
    collapse.setAttribute("aria-expanded", String(!c));
    collapse.setAttribute("aria-label", c ? "Expand sidebar" : "Collapse sidebar");
  });
  var keys = document.getElementById("keys");
  document.getElementById("keys-btn").addEventListener("click", function () { keys.showModal(); });
  document.getElementById("keys-close").addEventListener("click", function () { keys.close(); });
  var pending = "";
  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "Escape") { if (keys.open) keys.close(); document.dispatchEvent(new CustomEvent("aerotrace:escape")); return; }
    if (keys.open) return;  // no shortcuts act on the page behind the dialog
    if (e.key === "?") { keys.showModal(); e.preventDefault(); return; }
    if (e.key === "/") { var box = document.querySelector("[data-search]"); if (box) { box.focus(); e.preventDefault(); } return; }
    if (e.key === "t") { setTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark"); return; }
    if (graphKeys && location.hash.indexOf("#/graph") === 0 && graphKeys(e.key)) { e.preventDefault(); return; }
    if (pending === "g") {
      var go = { o: "overview", c: "changes", g: "graph", b: "benchmark" }[e.key];
      pending = "";
      if (go) location.hash = "#/" + go;
      return;
    }
    if (e.key === "g") { pending = "g"; setTimeout(function () { pending = ""; }, 1000); }
  });
  var escHandler = null;  // one listener; the graph page sets what Esc does there
  document.addEventListener("aerotrace:escape", function () { if (escHandler) escHandler(); });
  window.addEventListener("hashchange", route);
  route();
})();
