// Grouped bar chart for the results page mockup. SAMPLE numbers: invented to show layout, not results.
// The real page is generated from bench/results/<run>/results.json by ui/bench_page.py.
window.MOCK_CHART = function (el, opts) {
  var SAMPLE = {
    tools: ["AeroTrace", "clangd refs", "cscope", "ripgrep"],
    metric: { all: "recall", easy: "recall", hard: "recall" },
    groups: {
      all: [
        { name: "Redis", cases: 10, v: [90, 80, 70, 60], fa: [10, 20, 30, 40], time: ["1 s", "2 s", "3 s", "4 s"], tok: ["1k", "2k", "3k", "4k"] },
        { name: "SQLite", cases: 10, v: [90, 80, 60, 50], fa: [10, 20, 30, 40], time: ["1 s", "2 s", "3 s", "4 s"], tok: ["1k", "2k", "3k", "4k"] },
        { name: "curl", cases: 8, v: [80, 90, 70, 60], fa: [10, 20, 30, 40], time: ["1 s", "2 s", "3 s", "4 s"], tok: ["1k", "2k", "3k", "4k"] },
      ],
      easy: [
        { name: "Redis", cases: 5, v: [100, 100, 90, 90] }, { name: "SQLite", cases: 5, v: [100, 90, 90, 80] }, { name: "curl", cases: 4, v: [90, 100, 90, 80] },
      ],
      hard: [
        { name: "Redis", cases: 5, v: [80, 60, 40, 30] }, { name: "SQLite", cases: 5, v: [80, 70, 30, 20] }, { name: "curl", cases: 4, v: [70, 80, 50, 40] },
      ],
    },
  };
  var view = opts.view || "all", others = opts.others !== false;
  var css = getComputedStyle(document.documentElement);
  function c(n) { return css.getPropertyValue(n).trim(); }
  var SHORT = ["AeroTrace", "clangd", "cscope", "rg"];
  var tools = others ? SAMPLE.tools : SAMPLE.tools.slice(0, 1);
  var groups = SAMPLE.groups[view];
  var W = el.clientWidth || 900, narrow = W < 560;
  if (narrow && !opts._one) {
    // Phones: one small chart per repo, stacked, so nothing overlaps or runs off the edge.
    el.innerHTML = "";
    groups.forEach(function (g) {
      var d = document.createElement("div"); d.style.marginBottom = "24px"; el.appendChild(d);
      window.MOCK_CHART(d, { view: view, others: others, _one: g, _w: W });
    });
    var lg = document.getElementById(opts.legend); if (lg) window.MOCK_CHART(document.createElement("div"), { view: view, others: others, legend: opts.legend, _one: groups[0], _w: W });
    return;
  }
  if (opts._one) { groups = [opts._one]; W = opts._w; narrow = false; }
  var bw = narrow ? 22 : 40, gap = narrow ? 6 : 10, ggap = narrow ? 28 : 72;
  var gw = tools.length * bw + (tools.length - 1) * gap;
  var H = 260, top = 28, base = top + H;
  var total = groups.length * gw + (groups.length - 1) * ggap;
  var x0 = Math.max(0, (W - total) / 2);
  var svg = '<svg width="' + W + '" height="' + (base + (view === "all" ? (opts._one ? 72 + tools.length * 18 : 132) : 70)) + '" role="img" aria-label="Recall by tool and repo">';
  for (var t = 0; t <= 4; t++) {
    var y = base - (H * t) / 4;
    svg += '<line x1="0" x2="' + W + '" y1="' + y + '" y2="' + y + '" stroke="' + c("--chart-grid") + '"/>';
  }
  groups.forEach(function (g, gi) {
    var gx = x0 + gi * (gw + ggap);
    tools.forEach(function (tn, ti) {
      var val = g.v[ti], h = (H * val) / 100, x = gx + ti * (bw + gap);
      var fill = ti === 0 ? c("--chart-1") : c("--chart-other-fill"), stroke = ti === 0 ? "none" : c("--chart-other-stroke");
      svg += '<rect x="' + (x + 0.5) + '" y="' + (base - h + 0.5) + '" width="' + (bw - 1) + '" height="' + (h - 0.5) + '" rx="3" fill="' + fill + '" stroke="' + stroke + '"><title>' + tn + ": " + val + "% recall</title></rect>";
      svg += '<text x="' + (x + bw / 2) + '" y="' + (base + 14) + '" text-anchor="middle" font-family="' + c("--font-mono") + '" font-size="' + (narrow ? 9 : 10) + '" fill="' + (ti === 0 ? c("--accent") : c("--text-3")) + '">' + SHORT[ti] + "</text>";
      svg += '<text x="' + (x + bw / 2) + '" y="' + (base - h - 8) + '" text-anchor="middle" font-family="' + c("--font-mono") + '" font-size="' + (narrow ? 10 : 13) + '" font-weight="' + (ti === 0 ? 600 : 400) + '" fill="' + (ti === 0 ? c("--accent") : c("--text-2")) + '">' + val + "</text>";
    });
    var mid = gx + gw / 2;
    svg += '<text x="' + mid + '" y="' + (base + 38) + '" text-anchor="middle" font-family="' + c("--font-sans") + '" font-size="15" font-weight="600" fill="' + c("--text") + '">' + g.name + "</text>";
    svg += '<text x="' + mid + '" y="' + (base + 55) + '" text-anchor="middle" font-family="' + c("--font-mono") + '" font-size="11" fill="' + c("--text-3") + '">' + g.cases + " cases</text>";
    if (view === "all" && opts._one) {
      tools.forEach(function (tn, ti) {
        svg += '<text x="' + mid + '" y="' + (base + 80 + ti * 18) + '" text-anchor="middle" font-family="' + c("--font-mono") + '" font-size="11" fill="' + c("--text-2") + '">' + tn + ": " + g.fa[ti] + " fa · " + g.time[ti] + " · " + g.tok[ti] + " tok</text>";
      });
    } else if (view === "all") {
      svg += '<text x="' + mid + '" y="' + (base + 80) + '" text-anchor="middle" font-family="' + c("--font-mono") + '" font-size="11" fill="' + c("--text-2") + '">false alarms ' + (others ? g.fa.join(" · ") : g.fa[0]) + "</text>";
      svg += '<text x="' + mid + '" y="' + (base + 98) + '" text-anchor="middle" font-family="' + c("--font-mono") + '" font-size="11" fill="' + c("--text-2") + '">time ' + (others ? g.time.join(" · ") : g.time[0]) + "</text>";
      svg += '<text x="' + mid + '" y="' + (base + 116) + '" text-anchor="middle" font-family="' + c("--font-mono") + '" font-size="11" fill="' + c("--text-2") + '">tokens ' + (others ? g.tok.join(" · ") : g.tok[0]) + "</text>";
    }
  });
  svg += "</svg>";
  el.innerHTML = svg;
  var leg = document.getElementById(opts.legend);
  if (leg) leg.innerHTML = tools.map(function (tn, i) {
    return '<span style="display:inline-flex;align-items:center;gap:8px;margin-right:20px"><i style="width:12px;height:12px;border-radius:3px;box-sizing:border-box;background:' + (i === 0 ? c("--chart-1") : c("--chart-other-fill")) + ';border:1px solid ' + (i === 0 ? c("--chart-1") : c("--chart-other-stroke")) + '"></i>' + tn + "</span>";
  }).join("");
};
