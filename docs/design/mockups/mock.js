// Mockup helpers: theme from ?theme=dark, status icons, shared sidebar, the "sample numbers" banner.
// Not production code. Shapes match src/aerotrace/ui/html.py SHAPES.
(function () {
  var q = new URLSearchParams(location.search);
  // Every mockup is sample data. The launch screenshot script refuses pages marked like this.
  document.documentElement.setAttribute("data-sample", "true");
  if (q.get("theme") === "dark") document.documentElement.setAttribute("data-theme", "dark");

  var SHAPES = {
    changed: ["M0,-13L13,0L0,13L-13,0Z", "M-3,-3H3V3H-3Z"],
    breaks: ["M-10,-10H10V10H-10Z", "M-5,-5L5,5M5,-5L-5,5"],
    may: ["M-10,-10H10V10H-10Z", "M-5,-5L5,5M5,-5L-5,5", "dash"],
    affected: ["M0,-12L12,10H-12Z", "M0,-4V3M0,6V7"],
    unknown: ["M-10,0A10,10 0 1,0 10,0A10,10 0 1,0 -10,0Z", "M-3,-4C-3,-8 3,-8 3,-4C3,-1 0,-1 0,2M0,5V6"],
    no_effect: ["M-6,0A6,6 0 1,0 6,0A6,6 0 1,0 -6,0Z", "M-3,0H3"],
  };
  window.MOCK_SHAPES = SHAPES;
  function statusIcon(k) {
    var s = SHAPES[k];
    var dash = s[2] ? ' stroke-dasharray="4 3"' : "";
    return '<svg viewBox="-14 -14 28 28" aria-hidden="true"><path d="' + s[0] + '" fill="currentColor" fill-opacity="0.16" stroke="currentColor" stroke-width="2.2"' + dash + '/><path d="' + s[1] + '" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" fill="none"/></svg>';
  }
  var NAV = {
    overview: '<path d="M3 3h7v7H3zM14 3h7v4h-7zM14 11h7v10h-7zM3 14h7v7H3z"/>',
    changes: '<path d="M4 6h16M4 12h16M4 18h10"/>',
    graph: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="8"/><path d="M12 4v2M20 12h-2M12 20v-2M4 12h2"/>',
    benchmark: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    memory: '<path d="M12 3a9 9 0 1 0 9 9M12 7v5l3 2"/>',
    track: '<path d="M4 12l5 5L20 6"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/>',
    side: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    focus: '<circle cx="12" cy="12" r="3"/><path d="M3 12h3M18 12h3M12 3v3M12 18v3"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.5"/>',
    warn: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>',
    inbox: '<path d="M3 13l3-8h12l3 8v6H3z"/><path d="M3 13h5l1 3h6l1-3h5"/>',
  };
  function navIcon(k) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + NAV[k] + "</svg>";
  }
  window.MOCK_ICON = navIcon;
  var LOGO = '<svg class="logo" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="none" stroke="var(--border-strong)" stroke-width="1.5"/><circle cx="12" cy="12" r="5.5" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="3 2"/><circle cx="12" cy="12" r="2.2" fill="var(--accent)"/></svg>';

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-s]").forEach(function (el) {
      el.insertAdjacentHTML("afterbegin", statusIcon(el.getAttribute("data-s")));
    });
    document.querySelectorAll("[data-i]").forEach(function (el) {
      el.insertAdjacentHTML("afterbegin", navIcon(el.getAttribute("data-i")));
    });
    var side = document.querySelector("aside.sidebar[data-current]");
    if (side) {
      var cur = side.getAttribute("data-current");
      var t = q.get("theme") === "dark" ? "?theme=dark" : "";
      function link(id, label, href, soon) {
        return '<a href="' + href + t + '"' + (cur === id ? ' aria-current="page"' : "") + ">" + navIcon(id) + label + (soon ? '<span class="soon">soon</span>' : "") + "</a>";
      }
      side.innerHTML =
        '<div class="brand">' + LOGO + "<b>AeroTrace</b></div>" +
        '<div class="repo"><span class="dim">Repo</span><span class="mono">redis-8.10.3</span></div>' +
        '<nav class="nav" aria-label="Pages">' +
        link("overview", "Overview", "overview.html") + link("changes", "Changes", "changes.html") +
        link("graph", "Graph", "graph.html") + link("benchmark", "Benchmark", "benchmark.html") +
        '<div class="sep"></div>' +
        link("memory", "Memory", "soon.html", true) + link("track", "Track record", "soon.html", true) +
        "</nav>" +
        '<div class="foot"><button class="btn ghost icon sm" aria-label="Collapse sidebar">' + navIcon("side") +
        '</button><button class="btn ghost icon sm" aria-label="Switch theme">' + navIcon("sun") +
        '</button><span class="dim" style="margin-left:auto;font-size:12px"><kbd>?</kbd> keys</span></div>';
    }
    var strip = document.createElement("div");
    strip.className = "mock-strip";
    strip.setAttribute("role", "note");
    strip.textContent = "MOCKUP · every number on this page is a made-up sample, not a result";
    document.body.insertBefore(strip, document.body.firstChild);
    var b = document.createElement("div");
    b.className = "mock-banner";
    b.textContent = "MOCKUP · sample numbers, not results";
    document.body.appendChild(b);
  });
})();
