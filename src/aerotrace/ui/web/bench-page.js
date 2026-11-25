// Benchmark results chart. Reads the page's embedded data; draws grouped bars per repo as SVG.
// No network, no inline styles (the Content-Security-Policy allows neither). Every number is also in the
// tables of the page.
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("data").textContent);
  var TOOLS = ["aerotrace", "clangd", "cscope", "ripgrep"];
  var NAME = { aerotrace: "AeroTrace", clangd: "clangd", cscope: "cscope", ripgrep: "ripgrep" };
  var REPOS = ["redis", "sqlite", "curl"];
  var RNAME = { redis: "Redis", sqlite: "SQLite", curl: "curl" };
  var state = { view: "all", metric: "found", others: true };
  var NS = "http://www.w3.org/2000/svg";
  var chart = document.getElementById("chart");

  function el(name, attrs, text) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function n(x) { return x.toLocaleString("en-US"); }
  function secs(x) { return x === null ? "n/a" : x < 1 ? Math.round(x * 1000) + " ms" : x.toFixed(1) + " s"; }

  function drawGroup(svg, x0, gw, base, H, repo, tools, v, maxFa) {
    var bw = Math.min(44, (gw - (tools.length - 1) * 10) / tools.length), gap = 10;
    var used = tools.length * bw + (tools.length - 1) * gap, left = x0 + (gw - used) / 2;
    var rv = v.repos[repo];
    tools.forEach(function (t, i) {
      var a = rv[t], x = left + i * (bw + gap), val, h;
      if (state.metric === "found") {
        val = a.found;
        h = a.truth ? (H * a.found) / a.truth : 0;
      } else {
        val = a.false_alarms;
        h = maxFa ? (H * a.false_alarms) / maxFa : 0;
      }
      var r = el("rect", { x: x + 0.5, y: base - h + 0.5, width: bw - 1, height: Math.max(h - 0.5, 0), rx: 3,
        "class": t === "aerotrace" ? "bar-at" : "bar-o" });
      r.appendChild(el("title", {}, NAME[t] + ": " + (state.metric === "found"
        ? n(a.found) + " of " + n(a.truth) + " breaks found" : n(a.false_alarms) + " false alarms")));
      svg.appendChild(r);
      svg.appendChild(el("text", { x: x + bw / 2, y: base - h - 8, "text-anchor": "middle",
        "class": t === "aerotrace" ? "val val-at" : "val" }, n(val)));
      svg.appendChild(el("text", { x: x + bw / 2, y: base + 15, "text-anchor": "middle",
        "class": t === "aerotrace" ? "tool tool-at" : "tool" }, NAME[t]));
    });
    var mid = x0 + gw / 2, a0 = rv.aerotrace;
    svg.appendChild(el("text", { x: mid, y: base + 40, "text-anchor": "middle", "class": "repo" }, RNAME[repo]));
    svg.appendChild(el("text", { x: mid, y: base + 58, "text-anchor": "middle", "class": "sub" },
      a0.cases + (a0.cases === 1 ? " case · " : " cases · ") + n(a0.truth) + (a0.truth === 1 ? " break" : " breaks")));
    var y = base + 82;
    var line = function (label, fn) {
      svg.appendChild(el("text", { x: mid, y: y, "text-anchor": "middle", "class": "meta" },
        label + " " + tools.map(fn).join(" · ")));
      y += 17;
    };
    if (state.metric === "found") line("false alarms", function (t) { return n(rv[t].false_alarms); });
    else line("found", function (t) { return n(rv[t].found) + "/" + n(rv[t].truth); });
    line("time", function (t) { return secs(rv[t].seconds); });
    line("tokens", function (t) { return rv[t].tokens === null ? "n/a" : n(rv[t].tokens); });
    return y;
  }

  function render() {
    var v = D.views[state.view];
    var tools = state.others ? TOOLS : ["aerotrace"];
    var W = chart.clientWidth || 960, narrow = W < 620;
    var maxFa = 0;
    REPOS.forEach(function (r) { tools.forEach(function (t) { maxFa = Math.max(maxFa, v.repos[r][t].false_alarms); }); });
    var faTop = maxFa <= 4 ? maxFa : Math.ceil(maxFa / 4) * 4;
    var faTicks = [];
    if (faTop > 0 && faTop <= 4) for (var q = 0; q <= faTop; q++) faTicks.push(q);
    else for (var q4 = 0; q4 <= 4; q4++) faTicks.push((faTop * q4) / 4);
    while (chart.firstChild) chart.removeChild(chart.firstChild);
    var H = 230, top = 40;
    var groups = narrow ? REPOS.map(function (r) { return [r]; }) : [REPOS];
    groups.forEach(function (rs) {
      var height = top + H + 140;
      var svg = el("svg", { viewBox: "0 0 " + W + " " + height, role: "img",
        "aria-label": (state.metric === "found" ? "Breaks found" : "False alarms") + " by tool, " + v.label + ", " + rs.map(function (r) { return RNAME[r]; }).join(", ") });
      var AX = 40;  // room for the axis labels
      var ticks = state.metric === "found" ? [0, 25, 50, 75, 100] : (faTop ? faTicks : [0]);
      var tmax = state.metric === "found" ? 100 : faTop;
      ticks.forEach(function (tv) {
        var gy = top + H - (tmax ? (H * tv) / tmax : 0);
        svg.appendChild(el("line", { x1: AX, x2: W, y1: gy, y2: gy, "class": "grid" }));
        svg.appendChild(el("text", { x: AX - 6, y: gy + 3, "text-anchor": "end", "class": "axis" },
          state.metric === "found" ? tv + "%" : n(tv)));
      });
      svg.appendChild(el("text", { x: 0, y: 12, "class": "axis" },
        state.metric === "found" ? "share of breaks found" : "false alarms"));
      var gw = (W - AX) / rs.length;
      rs.forEach(function (r, i) { drawGroup(svg, AX + i * gw + 8, gw - 16, top + H, H, r, tools, v, faTop); });
      if (state.metric === "fa" && maxFa === 0) {
        svg.appendChild(el("text", { x: AX + (W - AX) / 2, y: top + H / 2, "text-anchor": "middle", "class": "empty" },
          tools.length === 1 ? "AeroTrace has no false alarms in this view." : "No tool has any false alarms in this view."));
      }
      chart.appendChild(svg);
    });
    document.getElementById("legend").replaceChildren.apply(document.getElementById("legend"), tools.map(function (t) {
      var s = document.createElement("span"), i = document.createElement("i");
      if (t === "aerotrace") i.className = "at";
      s.appendChild(i); s.appendChild(document.createTextNode(NAME[t]));
      return s;
    }));
    var at = v.total.aerotrace;
    document.getElementById("mix").textContent = v.label + ": " + v.cases + " cases (" + v.easy + " easy, " + v.hard +
      " hard), " + n(at.truth) + " break sites. " + (state.metric === "found"
        ? "Bar height is the share of breaks found; the number is the count."
        : "Bar height is false alarms, against the most in this view.");
  }

  function wire(attr, key) {
    document.querySelectorAll("[data-" + attr + "]").forEach(function (b) {
      b.addEventListener("click", function () {
        state[key] = b.getAttribute("data-" + attr);
        document.querySelectorAll("[data-" + attr + "]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        render();
      });
    });
  }
  wire("view", "view");
  wire("metric", "metric");
  var others = document.getElementById("others");
  others.addEventListener("click", function () {
    state.others = !state.others;
    others.setAttribute("aria-checked", String(state.others));
    render();
  });
  window.addEventListener("resize", render);
  render();
})();
