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
    breaks: ["M-10,-10H10V10H-10Z", "M-5,-5L5,5M5,-5L-5,5"],
    may_break: ["M-10,-10H10V10H-10Z", "M-5,-5L5,5M5,-5L-5,5", true],
    affected: ["M0,-12L12,10H-12Z", "M0,-4V3M0,6V7"],
    unknown: ["M-10,0A10,10 0 1,0 10,0A10,10 0 1,0 -10,0Z", "M-3,-4C-3,-8 3,-8 3,-4C3,-1 0,-1 0,2M0,5V6"],
    none: ["M-6,0A6,6 0 1,0 6,0A6,6 0 1,0 -6,0Z", "M-3,0H3"],
  };
  var WORD = { breaks: "BREAKS", may_break: "MAY BREAK", affected: "AFFECTED", unknown: "UNKNOWN", none: "NO EFFECT FOUND" };
  function statusIcon(level) {
    var p = SHAPES[level] || SHAPES.none, svg = s("svg", { viewBox: "-14 -14 28 28", "aria-hidden": "true" });
    svg.appendChild(s("path", { d: p[0], "class": "st-shape" + (p[2] ? " dash" : "") }));
    svg.appendChild(s("path", { d: p[1], "class": "st-mark" }));
    return svg;
  }
  function chip(level, label) {
    // a count-only chip still names its status for screen readers
    var word = label === undefined ? null : h("span", { cls: "sr", text: WORD[level] + " " });
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

  function notBuiltYet(caps, title, what) {
    return function (main) {
      main.replaceChildren(h("div", { cls: "page" }, pageHead(caps, title),
        emptyState("inbox", "Built in the next step", what, null)));
      return Promise.resolve();
    };
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
    changes: notBuiltYet("Changes", "Past checks", "This page will list every saved aerotrace check with its verdict, date and source."),
    graph: notBuiltYet("Graph", "Impact graph", "This page will draw a saved check: the change in the middle, what breaks around it."),
    benchmark: notBuiltYet("Benchmark", "Benchmark", "This page will show the accuracy benchmark results."),
    memory: comingSoon("Memory", "This page will show what AeroTrace has learned about this repo across checks. It isn't built yet, so there is nothing to show, not even sample data."),
    track: comingSoon("Track record", "This page will show how AeroTrace's past answers held up. It isn't built yet, so there is nothing to show, not even sample data."),
  };

  // ---------------------------------------------------------------- router
  var main = document.getElementById("main");
  function route() {
    var parts = (location.hash || "#/overview").slice(2).split("/");
    var name = PAGES[parts[0]] ? parts[0] : "overview";
    document.querySelectorAll(".nav a").forEach(function (a) {
      if (a.getAttribute("data-page") === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = "AeroTrace · " + name.charAt(0).toUpperCase() + name.slice(1);
    main.replaceChildren(loading());
    PAGES[name](main, parts.slice(1)).catch(function (e) {
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
    if (pending === "g") {
      var go = { o: "overview", c: "changes", g: "graph", b: "benchmark" }[e.key];
      pending = "";
      if (go) location.hash = "#/" + go;
      return;
    }
    if (e.key === "g") { pending = "g"; setTimeout(function () { pending = ""; }, 1000); }
  });
  window.addEventListener("hashchange", route);
  route();
})();
