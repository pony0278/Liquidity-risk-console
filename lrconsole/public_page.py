"""公開版總覽頁：給沒讀過原始文件的人看「現在到底是什麼情況」。

與 render.py 的分工：
  render.py      → console.html，密度優先，給已經懂這套框架的人
  public_page.py → index.html，可讀性優先，先講結論再給數字

資料是動態載入的：頁面開啟時 fetch latest.json 與 series.json，所以即使
瀏覽器快取了 HTML，看到的仍是最新一次掃描。fetch 失敗（例如用 file://
開啟）就退回建置時內嵌的那份，頁面永遠不會空白。

嚴重度用 1–4 格的方塊表示，不是只靠顏色——警示（琥珀 #9E6F1E）與明確壓力
（橘 #B04E1E）在紅綠色盲下的 ΔE 只有 3.0，等於同一個顏色。顏色只做強化，
格數與文字標籤才是真正在傳遞嚴重度的通道。
"""

import html
import json

__all__ = ["render_public_html", "build_series_payload", "TILE_KEYS"]

# 首屏的六格。挑選標準：一層挑一個最有代表性的，加上兩根盤中引信。
TILE_KEYS = ["sofr_iorb", "hy_oas", "y30", "vix", "move", "usdjpy"]

SPARK_KEYS = TILE_KEYS + ["ccc_oas", "y10", "slope_2s30s", "rrp", "dxy", "term_premium"]

_STATUS_TEXT = {"ok": "正常", "watch": "警示", "press": "明確壓力",
                "alarm": "警報", "unknown": "無資料", "info": "僅記錄"}


def _esc(text):
    return html.escape(str(text if text is not None else ""), quote=True)


def build_series_payload(series_map, keys=None, points=180):
    """給火花線用的精簡序列。只送畫得到的部分，不送整份歷史。"""
    keys = keys or SPARK_KEYS
    payload = {}
    for key in keys:
        series = series_map.get(key)
        if not series:
            continue
        trimmed = series.trimmed(points)
        payload[key] = {
            "dates": trimmed.dates,
            "values": [round(v, 6) for v in trimmed.values],
        }
    return payload


_CSS = """
:root{
  --paper:#E4E8EF; --paper-2:#DBE0E9; --card:#EDF0F5;
  --ink:#1B2A41; --ink-soft:#5A6B85; --ink-faint:#8695AB;
  --rule:#B4BFD0; --rule-hard:#8C9AB0;
  --ok:#2E6E4F; --watch:#9E6F1E; --press:#B04E1E; --alarm:#96241F; --dead:#9AA6B8;
  --mono: ui-monospace,"SF Mono","Cascadia Mono","Roboto Mono",Menlo,Consolas,monospace;
  --sans: "Noto Sans TC","PingFang TC","Microsoft JhengHei","Hiragino Sans TC",system-ui,sans-serif;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--paper);color:var(--ink);
  font-family:var(--sans);font-size:16px;line-height:1.65;
  background-image:
    repeating-linear-gradient(0deg,transparent 0 39px,rgba(27,42,65,.04) 39px 40px),
    repeating-linear-gradient(90deg,transparent 0 39px,rgba(27,42,65,.04) 39px 40px);
}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 90px}
.anno{font-family:var(--mono);font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink-soft);font-weight:600}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
a{color:var(--ink)}

/* ---------- 頁首 ---------- */
header{border-bottom:1.5px solid var(--ink);padding:30px 0 16px;margin-bottom:0}
.masthead{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;flex-wrap:wrap}
h1{font-family:var(--mono);font-size:clamp(20px,3vw,29px);font-weight:700;
  letter-spacing:-.02em;margin:6px 0 0;line-height:1.15}
.stamp{text-align:right;line-height:1.85;white-space:nowrap}
.stamp .live{display:inline-flex;align-items:center;gap:6px;color:var(--ok);font-weight:700}
.dot{width:7px;height:7px;border-radius:50%;background:var(--ok);flex:none}
@media (prefers-reduced-motion:no-preference){.dot{animation:pulse 2.4s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}}

/* ---------- 結論 ---------- */
.verdict{padding:34px 0 30px;border-bottom:1px solid var(--rule)}
.hero{font-size:clamp(15px,1.6vw,17px);color:var(--ink-soft);margin:0 0 6px}
.hero-n{font-family:var(--sans);font-size:clamp(46px,8vw,74px);font-weight:700;
  line-height:1;letter-spacing:-.03em;display:block;margin:2px 0 8px}
.hero-title{font-size:clamp(19px,2.6vw,26px);font-weight:700;line-height:1.35;
  margin:0 0 14px;max-width:24ch}
.verdict p{margin:0 0 10px;max-width:66ch;color:var(--ink-soft);font-size:15px}
.verdict-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:38px}
@media(max-width:800px){.verdict-grid{grid-template-columns:1fr;gap:24px}}

/* ---------- 嚴重度格 ---------- */
.sev{display:inline-flex;gap:2px;vertical-align:-1px}
.sev i{width:6px;height:13px;background:var(--rule);display:block}
.sev.s1 i:nth-child(-n+1),.sev.s2 i:nth-child(-n+2),
.sev.s3 i:nth-child(-n+3),.sev.s4 i:nth-child(-n+4){background:currentColor}
.t-ok{color:var(--ok)} .t-watch{color:var(--watch)} .t-press{color:var(--press)}
.t-alarm{color:var(--alarm)} .t-unknown{color:var(--dead)} .t-info{color:var(--ink-faint)}

/* ---------- 五層 ---------- */
.tiers{display:grid;grid-template-columns:repeat(5,1fr);border-top:1px solid var(--rule)}
.tier{border-left:1px solid var(--rule);padding:16px 14px 18px}
.tier:first-child{border-left:0}
.tier .st{display:flex;align-items:center;gap:8px;margin:7px 0 3px}
.tier .st b{font-size:16px;font-weight:700}
.tier .rd{font-family:var(--mono);font-size:11.5px;color:var(--ink-soft)}
.tier .plain{font-size:12.5px;color:var(--ink-soft);margin-top:9px;line-height:1.5}
@media(max-width:860px){.tiers{grid-template-columns:1fr 1fr}
.tier{border-left:0;border-top:1px solid var(--rule)}}

/* ---------- 區塊 ---------- */
section{padding-top:46px}
.shead{display:flex;align-items:baseline;gap:14px;border-bottom:1.5px solid var(--ink);
  padding-bottom:8px;flex-wrap:wrap}
.shead h2{font-family:var(--mono);font-size:15px;font-weight:700;margin:0;letter-spacing:.02em}
.shead .anno{margin-left:auto}
.lede{color:var(--ink-soft);font-size:14px;max-width:74ch;margin:14px 0 22px}

/* ---------- 警報卡 ---------- */
.alerts{display:grid;gap:0;border-top:1px solid var(--rule)}
.alert{padding:16px 0 18px;border-bottom:1px solid var(--rule);
  display:grid;grid-template-columns:auto minmax(0,1fr);gap:16px;align-items:start}
.alert.fired{background:rgba(150,36,31,.07);box-shadow:inset 3px 0 0 var(--alarm);
  padding-left:14px}
.alert .badge{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;font-weight:700;
  text-transform:uppercase;border:1px solid currentColor;padding:3px 7px 2px;white-space:nowrap}
.alert code{font-family:var(--mono);font-size:14px;font-weight:700;display:block;margin-bottom:5px}
.alert p{margin:0;font-size:13.5px;color:var(--ink-soft);max-width:70ch}

/* ---------- 指標卡 ---------- */
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(302px,1fr));
  gap:0;border-top:1px solid var(--rule)}
.tile{border-bottom:1px solid var(--rule);border-right:1px solid var(--rule);padding:18px 20px 20px}
.tile:nth-child(3n){border-right:0}
@media(max-width:1000px){.tile:nth-child(3n){border-right:1px solid var(--rule)}
.tile:nth-child(2n){border-right:0}}
@media(max-width:660px){.tile{border-right:0!important}}
.tile .cap{display:flex;align-items:baseline;justify-content:space-between;gap:10px}
.tile .name{font-size:14.5px;font-weight:700}
.tile .val{font-family:var(--sans);font-size:31px;font-weight:700;letter-spacing:-.02em;
  line-height:1.15;margin:8px 0 0}
.tile .delta{font-family:var(--mono);font-size:12px;color:var(--ink-soft);margin-left:9px;
  font-weight:600;letter-spacing:-.01em}
.tile .why{font-size:12.5px;color:var(--ink-soft);margin:10px 0 0;line-height:1.5}
.tile .dist{font-family:var(--mono);font-size:11.5px;margin-top:9px;font-weight:600}
.spark{margin-top:12px;position:relative}
.spark svg{display:block;width:100%;height:44px;overflow:visible}
.spark .axis{stroke:var(--rule);stroke-width:1}
.spark .line{fill:none;stroke:var(--ink-faint);stroke-width:2;
  stroke-linejoin:round;stroke-linecap:round}
.spark .recent{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.spark .head{stroke:var(--paper);stroke-width:2}
.spark .hit{fill:transparent;cursor:crosshair}
.spark .cross{stroke:var(--rule-hard);stroke-width:1;stroke-dasharray:2 2;visibility:hidden}
.spark .cursor{visibility:hidden;stroke:var(--paper);stroke-width:1.5}
.tip{position:absolute;pointer-events:none;background:var(--ink);color:#fff;
  font-family:var(--mono);font-size:11px;padding:4px 7px;white-space:nowrap;
  transform:translate(-50%,-100%);opacity:0;transition:opacity .1s;z-index:5}

/* ---------- 閾值軌道 ---------- */
.track{margin-top:11px}
.track .bar{display:flex;height:9px;background:var(--paper-2);position:relative}
.track .seg{height:100%;opacity:.32}
.track .seg+.seg{margin-left:1px}
.track .mk{position:absolute;top:-3px;width:2px;height:15px;background:var(--ink)}
.track .lab{display:flex;justify-content:space-between;font-family:var(--mono);
  font-size:9.5px;color:var(--ink-faint);margin-top:4px;letter-spacing:.06em}
.bg-ok{background:var(--ok)} .bg-watch{background:var(--watch)}
.bg-press{background:var(--press)} .bg-alarm{background:var(--alarm)}

/* ---------- 階梯 ---------- */
.ladder{border-top:1px solid var(--rule)}
.rung{display:grid;grid-template-columns:46px minmax(0,1fr) auto;gap:16px;align-items:baseline;
  padding:14px 0;border-bottom:1px solid var(--rule)}
.rung .n{font-family:var(--mono);font-size:21px;font-weight:700;color:var(--rule-hard);line-height:1}
.rung h3{margin:0;font-size:15.5px;font-weight:700}
.rung .sig{font-family:var(--mono);font-size:11.5px;color:var(--ink-soft);margin-top:3px}
.rung .rd{font-family:var(--mono);font-size:11.5px;font-weight:600;text-align:right;white-space:nowrap}
.rung.here{background:rgba(176,78,30,.10);box-shadow:inset 3px 0 0 var(--press);padding-left:12px}
.rung.here .n{color:var(--press)}

/* ---------- 傳導鏈 ---------- */
.chains{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0;
  border-top:1px solid var(--rule)}
.chain{border-bottom:1px solid var(--rule);border-right:1px solid var(--rule);padding:17px 20px 19px}
.chain:nth-child(2n){border-right:0}
@media(max-width:760px){.chains{grid-template-columns:1fr}.chain{border-right:0}}
.chain .hd{display:flex;align-items:baseline;gap:10px}
.chain h3{margin:0;font-size:15px;font-weight:700;min-width:0}
.chain .cnt{font-family:var(--mono);font-size:11.5px;color:var(--ink-soft);margin-left:auto;
  white-space:nowrap}
.pips{display:flex;gap:3px;margin:11px 0 9px}
.pips span{flex:1;height:7px;background:var(--rule)}
.pips span.on{background:var(--press)}
.chain .nx{font-size:12.5px;color:var(--ink-soft);line-height:1.5}
.chain .nx b{color:var(--ink);font-weight:700}

/* ---------- 歷史先例 ---------- */
.prec{margin-top:13px;border-top:1px solid var(--rule);padding-top:11px}
.prec summary{cursor:pointer;font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink-soft);font-weight:600;list-style:none}
.prec summary::-webkit-details-marker{display:none}
.prec summary::before{content:"▸ ";color:var(--ink-faint)}
.prec[open] summary::before{content:"▾ "}
.prec summary:hover{color:var(--ink)}
.pcase{margin-top:14px;border-left:2px solid var(--rule-hard);padding-left:13px}
.pcase h4{margin:0;font-size:13.5px;font-weight:700}
.pact{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--press);margin:5px 0 2px}
.pmeta{font-family:var(--mono);font-size:10.5px;color:var(--ink-soft);margin-bottom:9px}
.psteps{border-top:1px solid var(--rule)}
.pstep{display:grid;grid-template-columns:62px minmax(0,1fr);gap:9px;padding:6px 0;
  border-bottom:1px solid var(--rule);font-size:12px;line-height:1.45}
.pstep .pw{font-family:var(--mono);font-size:10px;color:var(--ink-faint);white-space:nowrap}
.pstep.k-yen{background:rgba(176,78,30,.11);box-shadow:inset 2px 0 0 var(--press);
  padding-left:7px;margin-left:-7px}
.pstep.k-yen .pl{font-weight:700}
.pstep.k-yen .pw{color:var(--press)}
.pstep.k-low .pl{color:var(--alarm)}
.pstep.k-recover .pl{color:var(--ok)}
.plesson{font-size:12px;color:var(--ink-soft);margin:10px 0 0;line-height:1.55}

/* ---------- 說明 ---------- */
.explain{display:grid;grid-template-columns:repeat(auto-fit,minmax(268px,1fr));gap:28px;
  border-top:1px solid var(--rule);padding-top:22px}
.explain h3{font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  margin:0 0 8px}
.explain p{margin:0 0 10px;font-size:13.5px;color:var(--ink-soft);line-height:1.6}
footer{margin-top:52px;border-top:1.5px solid var(--ink);padding-top:18px;
  font-size:12.5px;color:var(--ink-soft)}
footer a{color:var(--ink-soft)}
.disc{margin-top:18px;padding-top:13px;border-top:1px solid var(--rule);font-size:11.5px}
.err{background:rgba(150,36,31,.09);border-left:3px solid var(--alarm);padding:12px 14px;
  margin:16px 0;font-size:13.5px;display:none}
"""


_JS = r"""
(function () {
  "use strict";

  var STATUS_CLASS = { ok: "ok", watch: "watch", press: "press", alarm: "alarm",
                       unknown: "unknown", info: "info" };
  var SEVERITY_WORD = { 1: "正常", 2: "警示", 3: "明確壓力", 4: "警報" };
  var TILE_KEYS = __TILE_KEYS__;
  var TILE_WHY = __TILE_WHY__;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function cls(status) { return "t-" + (STATUS_CLASS[status] || "unknown"); }
  function el(id) { return document.getElementById(id); }

  // 嚴重度：4 格方塊 + 文字。顏色只是強化，格數與標籤才是主要通道，
  // 因為警示與明確壓力在紅綠色盲下幾乎同色。
  function sevMark(severity, status) {
    var n = severity || 0;
    return '<span class="sev s' + n + ' ' + cls(status) + '" role="img" aria-label="嚴重度 '
      + n + ' / 4"><i></i><i></i><i></i><i></i></span>';
  }

  function renderHeader(snap) {
    el("as-of").textContent = snap.data_as_of || "—";
    el("scanned").textContent = (snap.scan_time || "").slice(0, 16).replace("T", " ");
    var fired = (snap.tripwires || []).filter(function (w) { return w.state === true; });
    el("hero-n").textContent = "第 " + snap.level + " 階";
    el("hero-n").className = "hero-n " + cls(snap.overall_status);
    el("hero-title").textContent = (snap.verdict && snap.verdict.headline) || "";
    el("hero-sub").textContent = fired.length
      ? "有 " + fired.length + " 條升級條件成立"
      : "沒有任何升級條件成立";
    var paras = (snap.verdict && snap.verdict.paragraphs) || [];
    el("hero-paras").innerHTML = paras.map(function (p) {
      return "<p>" + esc(p) + "</p>";
    }).join("");
  }

  function renderTiers(snap) {
    var keys = Object.keys(snap.tiers || {}).sort(function (a, b) { return a - b; });
    el("tiers").innerHTML = keys.map(function (k) {
      var t = snap.tiers[k];
      return '<div class="tier">'
        + '<div class="anno">TIER ' + esc(k) + " &nbsp;" + esc(t.short) + "</div>"
        + '<div class="st ' + cls(t.status) + '">' + sevMark(t.severity, t.status)
        + "<b>" + esc(t.label) + "</b></div>"
        + '<div class="rd">' + esc(t.readout) + "</div>"
        + '<div class="plain">' + esc(t.plain || "") + "</div>"
        + "</div>";
    }).join("");
  }

  function renderAlerts(snap) {
    var wires = (snap.tripwires || []).slice().sort(function (a, b) {
      var rank = function (w) { return w.state === true ? 0 : (w.state === null ? 1 : 2); };
      return rank(a) - rank(b);
    });
    el("alerts").innerHTML = wires.map(function (w) {
      var fired = w.state === true;
      var unknown = w.state === null;
      var badge = fired ? "已觸發" : (unknown ? "資料不足" : "未觸發");
      var tone = fired ? "t-alarm" : (unknown ? "t-unknown" : "t-ok");
      return '<div class="alert' + (fired ? " fired" : "") + '">'
        + '<span class="badge ' + tone + '">' + badge + "</span>"
        + "<div><code>" + esc(w.code) + "</code>"
        + '<div class="anno">' + esc(w.group) + "</div>"
        + "<p>" + esc(w.desc) + "</p></div></div>";
    }).join("");
  }

  // 火花線：單一序列，所以不需要圖例——標題就是序列名。近 20 個觀測用
  // 狀態色強調，其餘用弱化灰，避免整條線都在喊。
  function sparkline(host, series, status) {
    if (!series || !series.values || series.values.length < 2) {
      host.innerHTML = '<div class="anno" style="padding:12px 0">歷史資料累積中</div>';
      return;
    }
    var values = series.values, dates = series.dates;
    var n = values.length, W = 300, H = 44, PAD = 3;
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    var span = (hi - lo) || Math.abs(hi) || 1;
    var x = function (i) { return (i / (n - 1)) * W; };
    var y = function (v) { return H - PAD - ((v - lo) / span) * (H - PAD * 2); };
    var path = function (from) {
      var d = "";
      for (var i = from; i < n; i++) d += (i === from ? "M" : "L") + x(i).toFixed(1) + " " + y(values[i]).toFixed(1);
      return d;
    };
    var recentFrom = Math.max(0, n - 20);
    var zeroLine = (lo < 0 && hi > 0)
      ? '<line class="axis" x1="0" y1="' + y(0).toFixed(1) + '" x2="' + W + '" y2="' + y(0).toFixed(1) + '"/>'
      : "";

    host.innerHTML =
      '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" role="img" '
      + 'aria-label="近 ' + n + ' 個觀測的走勢">'
      + zeroLine
      + '<path class="line" d="' + path(0) + '"/>'
      + '<path class="recent ' + cls(status) + '" style="stroke:currentColor" d="' + path(recentFrom) + '"/>'
      + '<circle class="head ' + cls(status) + '" style="fill:currentColor" cx="' + x(n - 1).toFixed(1)
      + '" cy="' + y(values[n - 1]).toFixed(1) + '" r="3"/>'
      + '<line class="cross" x1="0" y1="0" x2="0" y2="' + H + '"/>'
      + '<circle class="cursor" r="3.5" cx="0" cy="0" style="fill:var(--ink)"/>'
      + '<rect class="hit" x="0" y="0" width="' + W + '" height="' + H + '"/>'
      + "</svg>"
      + '<div class="tip"></div>';

    var svg = host.querySelector("svg");
    var cross = host.querySelector(".cross");
    var cursor = host.querySelector(".cursor");
    var tip = host.querySelector(".tip");

    function move(event) {
      var box = svg.getBoundingClientRect();
      var px = ((event.touches ? event.touches[0].clientX : event.clientX) - box.left) / box.width;
      var i = Math.max(0, Math.min(n - 1, Math.round(px * (n - 1))));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
      cursor.setAttribute("cx", x(i)); cursor.setAttribute("cy", y(values[i]));
      cross.style.visibility = cursor.style.visibility = "visible";
      tip.textContent = dates[i] + "  " + values[i];
      tip.style.left = (x(i) / W * 100) + "%";
      tip.style.top = "-2px";
      tip.style.opacity = "1";
    }
    function leave() {
      cross.style.visibility = cursor.style.visibility = "hidden";
      tip.style.opacity = "0";
    }
    svg.addEventListener("mousemove", move);
    svg.addEventListener("touchmove", move, { passive: true });
    svg.addEventListener("mouseleave", leave);
    svg.addEventListener("touchend", leave);
  }

  function trackHtml(indicator) {
    var t = indicator.track;
    if (!t || !t.segments || !t.segments.length) return "";
    var segs = t.segments.map(function (s) {
      return '<div class="seg bg-' + (STATUS_CLASS[s.status] || "unknown")
        + '" style="width:' + s.width_pct.toFixed(2) + '%" title="' + esc(s.label) + '"></div>';
    }).join("");
    var first = t.segments[0], last = t.segments[t.segments.length - 1];
    return '<div class="track"><div class="bar">' + segs
      + '<div class="mk" style="left:calc(' + t.marker_pct.toFixed(2) + '% - 1px)"></div></div>'
      + '<div class="lab"><span>' + esc(first.label) + "</span><span>"
      + esc(last.label) + "</span></div></div>";
  }

  function renderTiles(snap, series) {
    var byKey = {};
    (snap.indicators || []).forEach(function (i) { byKey[i.key] = i; });
    var host = el("tiles");
    host.innerHTML = TILE_KEYS.map(function (key) {
      var i = byKey[key];
      if (!i) return "";
      var delta = i.change_display ? '<span class="delta">' + esc(i.change_display) + " 單日</span>" : "";
      var dist = i.distance_text
        ? '<div class="dist ' + cls(i.status) + '">' + esc(i.distance_text) + "</div>" : "";
      var stale = (i.stale_days != null && i.stale_days > 7)
        ? ' <span class="t-press" style="font-weight:700">· 已 ' + i.stale_days + ' 天未更新</span>'
        : "";
      return '<div class="tile">'
        + '<div class="cap"><span class="name">' + esc(i.label) + "</span>"
        + '<span class="' + cls(i.status) + '">' + sevMark(i.severity, i.status)
        + ' <span style="font-family:var(--mono);font-size:11px;font-weight:700;letter-spacing:.08em">'
        + esc(i.status_label) + "</span></span></div>"
        + '<div class="val">' + esc(i.display) + delta + "</div>"
        + dist
        + trackHtml(i)
        + '<div class="spark" data-key="' + esc(key) + '"></div>'
        + '<p class="why">' + esc(TILE_WHY[key] || i.note || "") + "</p>"
        + '<div class="anno" style="margin-top:8px">資料日 ' + esc(i.date || "—")
        + " · " + esc(i.source_label || "") + stale + "</div>"
        + "</div>";
    }).join("");

    host.querySelectorAll(".spark").forEach(function (node) {
      var key = node.dataset.key;
      sparkline(node, series[key], (byKey[key] || {}).status);
    });
  }

  function renderLadder(snap) {
    el("ladder").innerHTML = (snap.ladder || []).map(function (r) {
      return '<div class="rung' + (r.here ? " here" : "") + '">'
        + '<div class="n">' + r.level + "</div>"
        + "<div><h3>" + esc(r.title) + '</h3><div class="sig">' + esc(r.signal) + "</div></div>"
        + '<div class="rd ' + (r.here ? "t-press" : "t-ok") + '">'
        + (r.here ? "◀ 現在在這裡" : esc(r.readout || "未到")) + "</div></div>";
    }).join("");
  }

  // 歷史先例：預設收合，但摘要行直接寫出「日圓在第幾幕」——那是整段的
  // 重點，不該藏在展開後面。
  function precedentsHtml(chain) {
    var cases = chain.precedents || [];
    if (!cases.length) return "";
    var acts = cases.map(function (c) {
      return esc((c.name || "").split("·")[0].trim()) + " " + esc(c.yen_act || "");
    }).join(" ／ ");

    var body = cases.map(function (c) {
      var steps = (c.steps || []).map(function (st) {
        return '<div class="pstep k-' + esc(st.kind || "") + '">'
          + '<span class="pw">' + esc(st.week || "") + "</span>"
          + '<span class="pl">' + esc(st.label || "") + "</span></div>";
      }).join("");
      return '<div class="pcase"><h4>' + esc(c.name || "") + "</h4>"
        + '<div class="pact">日圓在 ' + esc(c.yen_act || "") + "</div>"
        + '<div class="pmeta">' + esc(c.shape || "") + " · " + esc(c.length || "")
        + " · " + esc(c.outcome || "") + "</div>"
        + '<div class="psteps">' + steps + "</div>"
        + '<p class="plesson">' + esc(c.lesson || "") + "</p></div>";
    }).join("");

    return '<details class="prec"><summary>歷史上走完的樣子：' + acts + "</summary>"
      + body + "</details>";
  }

  function renderChains(snap) {
    el("chains").innerHTML = (snap.chains || []).map(function (c) {
      var pips = (c.nodes || []).map(function (nd) {
        return '<span class="' + (nd.state === "live" ? "on" : "") + '" title="' + esc(nd.label) + '"></span>';
      }).join("");
      var next = (c.nodes || []).filter(function (nd) { return nd.state !== "live"; })[0];
      return '<div class="chain"><div class="hd">'
        + "<h3>" + esc(c.title) + "</h3>"
        + '<span class="cnt">' + c.live + " / " + c.total + " 節點</span></div>"
        + '<div class="pips">' + pips + "</div>"
        + '<div class="nx">' + (next
          ? "下一步：<b>" + esc(next.label) + "</b><br>" + esc(next.cond)
          : "整條鏈已全部觸發。") + "</div>"
        + precedentsHtml(c) + "</div>";
    }).join("");
  }

  function render(snap, series) {
    renderHeader(snap);
    renderTiers(snap);
    renderAlerts(snap);
    renderTiles(snap, series || {});
    renderLadder(snap);
    renderChains(snap);
    document.title = "第 " + snap.level + " 階 · 流動性與尾部風險監測";
  }

  function bootstrap(id) {
    var node = document.getElementById(id);
    try { return JSON.parse(node.textContent); } catch (e) { return null; }
  }

  var fallbackSnap = bootstrap("boot-snapshot");
  var fallbackSeries = bootstrap("boot-series") || {};
  render(fallbackSnap, fallbackSeries);

  // 動態載入：HTML 可能被瀏覽器或 CDN 快取，但 JSON 每次都帶時間戳重抓，
  // 所以開著的分頁在下一次掃描後重新整理就會看到新數字。
  function refresh() {
    var bust = "?t=" + Date.now();
    Promise.all([
      fetch("latest.json" + bust, { cache: "no-store" }).then(function (r) { return r.json(); }),
      fetch("series.json" + bust, { cache: "no-store" }).then(function (r) { return r.json(); })
        .catch(function () { return fallbackSeries; }),
    ]).then(function (out) {
      if (out[0] && out[0].scan_time) {
        render(out[0], out[1] || {});
        el("live-note").textContent = "已載入最新掃描";
      }
    }).catch(function () {
      // 用 file:// 開啟或離線時 fetch 會失敗，此時內嵌的那份已經畫好了。
      el("live-note").textContent = "使用頁面內嵌的快照";
    });
  }
  refresh();
  setInterval(refresh, 15 * 60 * 1000);
})();
"""

_TILE_WHY = {
    "sofr_iorb": "銀行隔夜借錢的成本減去把錢擺在央行的利息。負值＝現金充裕；一旦由負轉正並持續，才是真正的流動性危機。",
    "hy_oas": "高收益債比公債多付的利息。歷史上所有崩盤都先在這裡出現裂縫，領先股市 1–4 週。",
    "y30": "30 年期公債殖利率。它是所有長期資產的折現率——一動，高估值的股票最先被壓縮。",
    "vix": "未來 30 天股市波動的定價，俗稱恐慌指數。破 30 才進入被迫去槓桿的階段。",
    "move": "債市版的 VIX。通常領先 VIX 一到三週，是目前最被忽略的早期訊號。",
    "usdjpy": "日圓匯率。水位高本身不是警報，但代表借日圓去買別的資產的部位堆得更大——危險的是它急升的那一天。",
}


def render_public_html(snapshot, series_payload, changes, repo_url=""):
    boot_snapshot = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    boot_series = json.dumps(series_payload, ensure_ascii=False, separators=(",", ":"))
    script = (_JS
              .replace("__TILE_KEYS__", json.dumps(TILE_KEYS))
              .replace("__TILE_WHY__", json.dumps(_TILE_WHY, ensure_ascii=False)))

    change_rows = "".join(
        '<div class="alert"><span class="badge %s">%s</span><div><p>%s</p></div></div>'
        % ({"alert": "t-alarm", "warn": "t-watch"}.get(sev, "t-info"),
           {"alert": "變化", "warn": "留意", "info": "記錄"}.get(sev, "記錄"), _esc(text))
        for sev, text in changes[:8])

    console_link = "console.html"
    repo_line = ('<a href="%s">原始碼與完整歷史</a> · ' % _esc(repo_url)) if repo_url else ""

    return """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>流動性與尾部風險監測</title>
<meta name="description" content="美國流動性與尾部風險的五層監測盤，每 3 天自動更新。先講結論，再給數字。">
<meta property="og:title" content="流動性與尾部風險監測">
<meta property="og:description" content="五層壓力盤 · 每 3 天自動掃描 · 資料取自 FRED／NY Fed／市場報價">
<style>%s</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="masthead">
    <div>
      <div class="anno">系統壓力盤 / SYSTEMIC PRESSURE CONSOLE</div>
      <h1>流動性與尾部風險監測</h1>
    </div>
    <div class="stamp anno">
      <div class="live"><span class="dot"></span>每 3 天自動掃描</div>
      <div>資料截至 <span id="as-of" class="num">—</span></div>
      <div>掃描時間 <span id="scanned" class="num">—</span></div>
      <div id="live-note">載入中…</div>
    </div>
  </div>
</header>

<div class="verdict">
  <div class="verdict-grid">
    <div>
      <div class="hero" id="hero-sub">載入中…</div>
      <span class="hero-n" id="hero-n">—</span>
      <div class="hero-title" id="hero-title"></div>
    </div>
    <div id="hero-paras"></div>
  </div>
</div>

<div class="tiers" id="tiers"></div>

<section>
  <div class="shead"><h2>現在該注意什麼</h2>
    <span class="anno">TRIPWIRES · 任一成立即重新評估</span></div>
  <p class="lede">這幾條是獨立的升級條件，不需要同時成立。任何一條由「未觸發」翻成
    「已觸發」，就代表情勢的<strong>性質</strong>變了，不只是數字動了。</p>
  <div class="alerts" id="alerts"></div>
</section>

<section>
  <div class="shead"><h2>關鍵指標</h2>
    <span class="anno">GAUGES · 六格總覽</span></div>
  <p class="lede">每格是一層的代表。<strong>橫條是閾值帶</strong>——直線是現在的位置，
    顏色由左到右代表越來越嚴重；下方的折線是近半年走勢，滑過去可看每一天的數值。
    嚴重度用方塊格數表示，不只用顏色。</p>
  <div class="tiles" id="tiles"></div>
</section>

<section>
  <div class="shead"><h2>現在在第幾階</h2>
    <span class="anno">ESCALATION</span></div>
  <p class="lede">「危機」有幾個明確標誌：資金斷裂、信用擴張、被迫去槓桿、相關性衝到 1。
    階梯取最高成立者——越往下，代表市場的運作本身出了問題，而不只是價格在動。</p>
  <div class="ladder" id="ladder"></div>
</section>

<section>
  <div class="shead"><h2>壓力沿著哪條路走</h2>
    <span class="anno">TRANSMISSION · 傳導鏈</span></div>
  <p class="lede">每條鏈是一條實際的傳導路徑。價值不在預測哪根引信會被點燃，
    而在看出壓力已經走到第幾步，以及<strong>下一步需要什麼條件</strong>。</p>
  <div class="chains" id="chains"></div>
</section>

<section>
  <div class="shead"><h2>與上次掃描相比</h2>
    <span class="anno">DELTA</span></div>
  <p class="lede">自動掃描的重點不在重印水位，而在標出<strong>移動</strong>。</p>
  <div class="alerts">%s</div>
</section>

<section>
  <div class="shead"><h2>怎麼讀這張表</h2><span class="anno">HOW TO READ</span></div>
  <div class="explain">
    <div>
      <h3>資金流動性 ≠ 市場流動性</h3>
      <p>Tier 1 是「水管」，慢變數，數日到數週才動一次，但它一旦出事就是真正的危機。
        Tier 3／4 是市場流動性，可以在數小時內蒸發。</p>
      <p>所以水管型指標是<strong>落後</strong>指標——它亮燈時事情已經很大了；
        而波動率亮燈不代表系統有事。</p>
    </div>
    <div>
      <h3>層級是管道，不是嚴重度</h3>
      <p>Tier 1 到 5 標示的是壓力走哪條管道、以什麼速度傳導，不是嚴重度排名。
        2024 年 8 月的日圓套利平倉是純 Tier 3／4 事件，日經單日崩 12%%，
        但從未碰到 Tier 1。</p>
    </div>
    <div>
      <h3>看速度，不看水位</h3>
      <p>USD/JPY 到 163 本身不是警報，單日反轉 −2%% 才是。30Y 同理：
        關鍵是單日 +10bps 與之後黏不黏得住，而不是它站在哪個整數關卡。</p>
      <p>閾值需要隨 regime 校準——寫在降息預期時代的門檻，在升息預期時代會失效。</p>
    </div>
    <div>
      <h3>抓不到資料 ≠ 沒問題</h3>
      <p>任何一條規則只要引用到缺資料的指標，一律標成「資料不足」而不是「未觸發」。
        把抓不到的指標當成安全，是最危險的誤判方向。</p>
    </div>
  </div>
</section>

<footer>
  <div>資料來源：FRED（聖路易聯準銀行）· NY Fed 公開市場操作 · 市場報價。
    每 3 天由 GitHub Actions 自動掃描並重建本頁。</div>
  <div style="margin-top:8px">%s<a href="%s">完整版控制盤（含全部 23 格指標與傳導鏈細節）</a>
    · <a href="latest.json">原始 JSON</a></div>
  <p class="disc">本頁為個人監測用的指標與傳導路徑整理，數值取自公開資料源並可能有時間落差或修正，
    <strong>不構成投資建議</strong>。判定為「重定價」不代表不會升級，
    只代表升級所需的條件目前尚未成立。</p>
</footer>

</div>

<script id="boot-snapshot" type="application/json">%s</script>
<script id="boot-series" type="application/json">%s</script>
<script>%s</script>
</body>
</html>
""" % (_CSS, change_rows, repo_line, console_link,
       boot_snapshot.replace("</", "<\\/"), boot_series.replace("</", "<\\/"), script)
