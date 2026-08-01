"""把 snapshot 畫回原本那張製圖風格的控制盤。

版面與 CSS 沿用手工版（templates/console.css），差別只在所有數字、燈號、
節點狀態都由 snapshot 決定，並多了一段「與上次掃描的差異」。
"""

import html
import os

__all__ = ["render_html"]

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")

_STATUS_CLASS = {"ok": "ok", "watch": "watch", "press": "press",
                 "alarm": "alarm", "unknown": "unknown", "info": "info"}


def _esc(text):
    return html.escape(str(text if text is not None else ""), quote=True)


def _mark(status):
    cls = _STATUS_CLASS.get(status, "unknown")
    return '<span class="m m-%s"></span>' % cls


def _tcls(status):
    return "t-%s" % _STATUS_CLASS.get(status, "unknown")


def _read_css():
    path = os.path.join(_TEMPLATE_DIR, "console.css")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _header(snapshot):
    strip = []
    for tier in sorted(snapshot["tiers"]):
        info = snapshot["tiers"][tier]
        strip.append(
            '<div>\n'
            '  <div class="anno">TIER %d &nbsp;%s</div>\n'
            '  <b class="%s">%s%s</b>\n'
            '  <div class="rd">%s</div>\n'
            '</div>' % (tier, _esc(info["short"]), _tcls(info["status"]),
                        _mark(info["status"]), _esc(info["label"]), _esc(info["readout"])))

    return (
        '<header>\n'
        '  <div class="tb">\n'
        '    <div>\n'
        '      <div class="anno">系統壓力盤 / SYSTEMIC PRESSURE CONSOLE</div>\n'
        '      <h1>流動性與尾部風險監測 <span>AUTO</span></h1>\n'
        '    </div>\n'
        '    <div class="tb-meta anno">\n'
        '      <div>資料截至 &nbsp;%s</div>\n'
        '      <div>掃描時間 &nbsp;%s</div>\n'
        '      <div>層級 &nbsp;TIER 1–5 ＋ 擁擠層</div>\n'
        '      <div>判定 &nbsp;<span class="%s">第 %d 階</span></div>\n'
        '    </div>\n'
        '  </div>\n'
        '  <div class="strip">\n%s\n  </div>\n'
        '</header>' % (
            _esc(snapshot.get("data_as_of") or "—"),
            _esc(snapshot["scan_time"][:16].replace("T", " ")),
            _tcls(snapshot["overall_status"]), snapshot["level"],
            "\n".join(strip)))


def _verdict(snapshot):
    verdict = snapshot["verdict"]
    paragraphs = "\n".join("    <p>%s</p>" % _esc(p) for p in verdict["paragraphs"])
    fired = [w for w in snapshot["tripwires"] if w["state"] is True]
    unknown = [w for w in snapshot["tripwires"] if w["state"] is None]

    side = [
        '    <div class="anno">本次掃描</div>',
        '    <dl>',
        '      <dt>觸發中的引信</dt><dd>%s</dd>' % (
            "、".join(_esc(w["code"]) for w in fired) if fired else "無"),
        '      <dt>無法判定</dt><dd>%s</dd>' % (
            "、".join(_esc(w["code"]) for w in unknown) if unknown else "無"),
        '      <dt>判讀原則</dt><dd>資金流動性 ≠ 市場流動性。Tier 1 是水管、慢變數；Tier 3／4 可在數小時內蒸發。水管型指標是落後指標。</dd>',
        '      <dt>看速度，不看水位</dt><dd>USD/JPY 的水位不是警報，單日反轉 −2% 才是。30Y 同理：關鍵是單日 +10bps 與黏著度。</dd>',
        '    </dl>',
    ]

    return (
        '<div class="verdict">\n'
        '  <div>\n'
        '    <span class="tag %s">第 %d 階 · %s</span>\n'
        '    <h2>%s</h2>\n%s\n'
        '  </div>\n'
        '  <div class="side">\n%s\n  </div>\n'
        '</div>' % (
            _tcls(snapshot["overall_status"]), snapshot["level"],
            _esc(next((r["title"] for r in snapshot["ladder"] if r["here"]), "")),
            _esc(verdict["headline"]), paragraphs, "\n".join(side)))


def _changes(snapshot, changes):
    rows = []
    for severity, text in changes:
        rows.append(
            '    <div class="chg-row"><span class="sev sev-%s">%s</span><span>%s</span></div>'
            % (severity, {"alert": "觸發", "warn": "留意", "info": "記錄"}.get(severity, "記錄"),
               _esc(text)))
    return (
        '<section>\n'
        '  <div class="shead"><h3>本次變化</h3>'
        '<span class="anno">DELTA · 與上次掃描相比</span>'
        '<span class="anno">SECTION 0</span></div>\n'
        '  <p class="lede">自動掃描的重點不在重印水位，而在標出<strong>移動</strong>：'
        '階梯升降、引信開關、傳導鏈節點推進，以及超過門檻的數值變動。</p>\n'
        '  <div class="changes">\n%s\n  </div>\n'
        '</section>' % "\n".join(rows))


def _crowding(snapshot):
    cards = []
    for card in snapshot["crowding"]:
        readout = ('        <div class="rdout">%s</div>' % _esc(card["readout"])) if card["readout"] else ""
        cards.append(
            '    <div class="cw">\n'
            '      <div class="lv %s">%s%s</div>\n'
            '      <h4>%s</h4>\n'
            '      <dl>\n'
            '        <dt>擁擠度</dt><dd>%s</dd>\n'
            '        <dt>觸發器</dt><dd>%s</dd>\n'
            '      </dl>\n%s\n'
            '    </div>' % (
                _tcls(card["level"]), _mark(card["level"]), _esc(card["state_text"]),
                _esc(card["title"]), _esc(card["density"]), _esc(card["trigger"]), readout))

    return (
        '<section>\n'
        '  <div class="shead"><h3>擁擠層</h3>'
        '<span class="anno">CROWDING · 槓桿堆在哪裡</span>'
        '<span class="anno">SECTION A</span></div>\n'
        '  <p class="lede">尾部風險無法預測觸發點，但可以監控「槓桿堆在哪裡」。'
        '這一層不產生燈號，它決定<strong>一旦有事，瀑布會從哪裡開始</strong>。'
        '引信長度＝部位擁擠度；觸發器＝會點燃它的具體事件。</p>\n'
        '  <div class="crowd">\n%s\n  </div>\n'
        '</section>' % "\n".join(cards))


def _gauges(snapshot):
    rows = []
    by_tier = {}
    for indicator in snapshot["indicators"]:
        if indicator["hidden"]:
            continue
        by_tier.setdefault(indicator["tier"], []).append(indicator)

    for tier in sorted(by_tier):
        title = snapshot["tiers"].get(tier, {}).get("title") or "TIER %d" % tier
        rows.append('      <tr class="tier"><td colspan="8">%s</td></tr>' % _esc(title))
        for indicator in by_tier[tier]:
            change = ('<span class="chg">%s</span>' % _esc(indicator["change_display"])) \
                if indicator["change_display"] else ""
            stale = ' <span class="stale">STALE</span>' if not indicator["fetch_ok"] else ""
            rows.append(
                '      <tr id="g-%s"%s>\n'
                '        <td class="k">%s%s</td>\n'
                '        <td class="v %s">%s%s</td>\n'
                '        <td class="th">%s</td>\n'
                '        <td class="st %s">%s%s</td>\n'
                '        <td class="dt">%s%s</td>\n'
                '        <td class="src">%s</td>\n'
                '        <td class="src">%s</td>\n'
                '        <td class="note">%s</td>\n'
                '      </tr>' % (
                    _esc(indicator["key"]),
                    ' class="ext"' if indicator["ext"] else "",
                    _esc(indicator["label"]), " ⭐" if indicator["star"] else "",
                    _tcls(indicator["status"]), _esc(indicator["display"]), change,
                    _esc(indicator["threshold_text"]),
                    _tcls(indicator["status"]), _mark(indicator["status"]),
                    _esc(indicator["status_label"]),
                    _esc(indicator["date"] or "—"), stale,
                    _esc(indicator["freq"]), _esc(indicator["source_label"]),
                    _esc(indicator["note"])))

    return (
        '<section>\n'
        '  <div class="shead"><h3>指標盤</h3>'
        '<span class="anno">GAUGES · TIER 1–5</span>'
        '<span class="anno">SECTION B</span></div>\n'
        '  <p class="lede">標記 <strong>＋</strong> 者為原始十項之外的擴充格。'
        '分兩層使用：<strong>盤中引信</strong>（每日、設單日變動警報）'
        '＝ USD/JPY 單日 %%、10Y／30Y 單日 bps、VIX、MOVE、WTI；'
        '<strong>慢燈</strong>（每週）＝ SOFR−IORB、準備金、TGA、失業金。</p>\n'
        '  <div class="wrap">\n'
        '  <table>\n'
        '    <thead><tr><th>指標</th><th>最新</th><th>閾值帶</th><th>燈號</th>'
        '<th>資料日</th><th>頻率</th><th>資料源</th><th>註記</th></tr></thead>\n'
        '    <tbody>\n%s\n    </tbody>\n'
        '  </table>\n  </div>\n'
        '</section>' % "\n".join(rows))


def _chains(snapshot):
    blocks = []
    for chain in snapshot["chains"]:
        nodes = []
        for node in chain["nodes"]:
            state = node["state"]
            flag = ""
            if state in ("live", "armed", "unknown"):
                flag = '          <span class="flag">%s</span>\n' % {
                    "live": "LIVE", "armed": "ARMED", "unknown": "NO DATA"}[state]
            jump = ' data-jump="%s" tabindex="0" role="button"' % _esc(node["jump"]) if node["jump"] else ""
            nodes.append(
                '        <div class="node %s"%s>\n%s'
                '          <span class="step">%s</span>\n'
                '          <span class="lbl">%s</span>\n'
                '          <span class="cond">%s</span>\n'
                '        </div>' % (state, jump, flag, _esc(node["step"]),
                                    _esc(node["label"]), _esc(node["cond"])))

        blocks.append(
            '    <div class="chain">\n'
            '      <div class="chain-hd">\n'
            '        %s\n'
            '        <h4>%s</h4>\n'
            '        <span class="state %s">%s</span>\n'
            '        <span class="cnt">%d / %d 節點已觸發</span>\n'
            '      </div>\n'
            '      <div class="run">\n%s\n      </div>\n'
            '      <p class="chain-note">%s</p>\n'
            '    </div>' % (
                _mark(chain["status"]), _esc(chain["title"]),
                _tcls(chain["status"]), _esc(chain["state"]),
                chain["live"], chain["total"], "\n".join(nodes), _esc(chain["note"])))

    return (
        '<section>\n'
        '  <div class="shead"><h3>邏輯傳導鏈</h3>'
        '<span class="anno">TRANSMISSION · 壓力走到第幾步</span>'
        '<span class="anno">SECTION C</span></div>\n'
        '  <p class="lede">每條鏈是一條實際的傳導路徑。'
        '<strong>實線橘框＝該節點條件目前已成立；虛線＝尚未觸發。</strong>'
        '價值不在預測哪根引信會被點燃，而在看出壓力沿著哪條路走到了第幾步。'
        '點擊已觸發節點可跳至對應指標。</p>\n'
        '  <div class="chains">\n%s\n  </div>\n'
        '</section>' % "\n".join(blocks))


def _ladder(snapshot):
    rows = []
    for rung in snapshot["ladder"]:
        rows.append(
            '    <div class="rung%s">\n'
            '      <div class="lv">%d</div>\n'
            '      <div><h4>%s</h4></div>\n'
            '      <div class="sig">%s</div>\n'
            '      <div class="rd %s">%s</div>\n'
            '    </div>' % (
                " here" if rung["here"] else "", rung["level"], _esc(rung["title"]),
                _esc(rung["signal"]),
                "t-press" if rung["here"] else "t-ok",
                "◀ 你在這裡" if rung["here"] else _esc(rung["readout"] or "未到")))

    return (
        '<section>\n'
        '  <div class="shead"><h3>升級階梯</h3>'
        '<span class="anno">ESCALATION · 現在在第幾階</span>'
        '<span class="anno">SECTION D</span></div>\n'
        '  <p class="lede">「危機」有幾個標誌：資金斷裂、信用擴張、被迫去槓桿、'
        '相關性衝到 1。階梯由下方規則自動判定，取最高成立者。</p>\n'
        '  <div class="ladder">\n%s\n  </div>\n'
        '</section>' % "\n".join(rows))


def _tripwires(snapshot):
    cards = []
    for wire in snapshot["tripwires"]:
        if wire["state"] is True:
            cls, state_text = " fired", "已觸發"
        elif wire["state"] is None:
            cls, state_text = " unknown", "資料不足"
        else:
            cls, state_text = "", "未觸發"
        cards.append(
            '    <div class="wire%s">\n'
            '      <div class="anno">%s</div>\n'
            '      <span class="st %s">%s</span>\n'
            '      <code>%s</code>\n'
            '      <p>%s</p>\n'
            '    </div>' % (
                cls, _esc(wire["group"]),
                "t-alarm" if wire["state"] is True else ("t-unknown" if wire["state"] is None else "t-ok"),
                state_text, _esc(wire["code"]), _esc(wire["desc"])))

    return (
        '<section>\n'
        '  <div class="shead"><h3>升級觸發器</h3>'
        '<span class="anno">TRIPWIRES · 任一成立即重新評估</span>'
        '<span class="anno">SECTION E</span></div>\n'
        '  <p class="lede">這幾條是獨立條件，不需同時成立。'
        '前三條是「長債重定價 → 真危機」的升級訊號；後幾條是盤中引信。</p>\n'
        '  <div class="wires">\n%s\n  </div>\n'
        '</section>' % "\n".join(cards))


def _footer(snapshot):
    failures = [i for i in snapshot["indicators"] if not i["fetch_ok"] and not i["hidden"]]
    failure_items = "".join(
        "<li>%s — %s</li>" % (_esc(i["label"]), _esc(i["fetch_detail"] or "未知原因"))
        for i in failures) or "<li>本次全部指標抓取正常。</li>"

    notes = "".join("<li>%s</li>" % _esc(n) for n in snapshot.get("data_notes", [])) \
        or "<li>—</li>"

    watch = []
    for chain in snapshot["chains"]:
        nxt = next((n for n in chain["nodes"] if n["state"] != "live"), None)
        if nxt:
            watch.append("<li>%s 的下一個節點：%s（%s）</li>"
                         % (_esc(chain["title"].split("—")[0].strip()),
                            _esc(nxt["label"]), _esc(nxt["cond"])))

    return (
        '<footer>\n'
        '  <div class="cols">\n'
        '    <div><h5>本次抓取狀況</h5><ul>%s</ul></div>\n'
        '    <div><h5>已知資料坑</h5><ul>%s</ul></div>\n'
        '    <div><h5>下次檢查重點</h5><ul>%s</ul></div>\n'
        '  </div>\n'
        '  <p class="disc">本表由 scan.py 自動產生（掃描時間 %s）。'
        '數值取自公開資料源並可能有時間落差或修正，不構成投資建議。'
        '閾值需隨 regime 校準；判定為「重定價」不代表不會升級，'
        '只代表升級所需的條件目前尚未成立。</p>\n'
        '</footer>' % (failure_items, notes, "".join(watch) or "<li>—</li>",
                       _esc(snapshot["scan_time"])))


_SCRIPT = """
(function(){
  document.querySelectorAll('.node[data-jump]').forEach(function(n){
    function go(){
      var t=document.getElementById(n.dataset.jump);
      if(!t) return;
      document.querySelectorAll('tr.hl').forEach(function(r){r.classList.remove('hl')});
      t.classList.add('hl');
      t.scrollIntoView({behavior:'smooth',block:'center'});
    }
    n.addEventListener('click',go);
    n.addEventListener('keydown',function(e){
      if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}
    });
  });
})();
"""


def render_html(snapshot, changes):
    body = "\n\n".join([
        _header(snapshot),
        _verdict(snapshot),
        _changes(snapshot, changes),
        _crowding(snapshot),
        _gauges(snapshot),
        _chains(snapshot),
        _ladder(snapshot),
        _tripwires(snapshot),
        _footer(snapshot),
    ])
    return (
        '<!DOCTYPE html>\n<html lang="zh-Hant">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>系統壓力盤 — 流動性與尾部風險監測 %s</title>\n'
        '<style>\n%s\n</style>\n</head>\n<body>\n<div class="sheet">\n\n%s\n\n</div>\n'
        '<script>%s</script>\n</body>\n</html>\n'
        % (_esc(snapshot["scan_time"][:10]), _read_css(), body, _SCRIPT))
