#!/usr/bin/env python3
"""流動性與尾部風險自動掃描。

  python3 scan.py                 # 完整掃描：抓資料 → 判定 → 產報表
  python3 scan.py --offline       # 只用快取／歷史重算（不連網）
  python3 scan.py --self-test     # 檢查設定檔與規則表達式，不連網

離開碼：
  0  正常
  10 有升級觸發器或盤中引信成立（給排程器當「該看一眼」的訊號）
  20 部分指標抓取失敗（判定仍完成，但有缺口）
  30 兩者都有
  1  掃描本身失敗
"""

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from lrconsole import diff as diff_mod  # noqa: E402
from lrconsole import history as history_mod  # noqa: E402
from lrconsole import notify as notify_mod  # noqa: E402
from lrconsole import public_page  # noqa: E402
from lrconsole import render as render_mod  # noqa: E402
from lrconsole.evaluate import build_snapshot, resolve_series  # noqa: E402
from lrconsole.expr import ExprError, referenced_names  # noqa: E402
from lrconsole.fetch import Fetcher  # noqa: E402
from lrconsole.series import Series, build_metrics  # noqa: E402

EXIT_OK = 0
EXIT_TRIPWIRE = 10
EXIT_DATA_GAP = 20
EXIT_ERROR = 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="流動性與尾部風險自動掃描")
    parser.add_argument("--config-dir", default=os.path.join(BASE_DIR, "config"))
    parser.add_argument("--data-dir", default=os.path.join(BASE_DIR, "data"))
    parser.add_argument("--out-dir", default=os.path.join(BASE_DIR, "reports"))
    parser.add_argument("--offline", action="store_true",
                        help="不連網，只用快取與 data/history.csv 重算")
    parser.add_argument("--no-fetch", action="store_true",
                        help="完全跳過抓取，純粹用 data/history.csv 重畫報表")
    parser.add_argument("--rebuild", action="store_true",
                        help="只重畫頁面（模板改動時用）。等同 --no-fetch，並沿用上一份"
                             "快照的掃描時間與變更清單——重畫不是掃描，不該蓋掉這兩者")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--history-days", type=int, default=1500,
                        help="history.csv 每個系列保留的觀測筆數上限")
    parser.add_argument("--keep-daily", type=int, default=60,
                        help="reports/ 裡帶日期的存檔（console-／snapshot-／summary-）"
                             "保留幾天份，0＝不清理")
    parser.add_argument("--notify", choices=["auto", "always", "never"], default="auto",
                        help="auto＝只在有觸發或有變化時推播")
    parser.add_argument("--self-test", action="store_true",
                        help="只檢查設定檔與表達式，不抓資料、不寫報表")
    parser.add_argument("--repo-url", default="https://github.com/pony0278/Liquidity-risk-console",
                        help="公開版頁尾的原始碼連結")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def log_factory(quiet):
    def log(*args):
        if not quiet:
            print(*args, flush=True)
    return log


def load_configs(config_dir):
    with open(os.path.join(config_dir, "indicators.json"), encoding="utf-8") as handle:
        indicators_cfg = json.load(handle)
    with open(os.path.join(config_dir, "rules.json"), encoding="utf-8") as handle:
        rules_cfg = json.load(handle)
    rules_cfg["_tiers"] = indicators_cfg.get("tiers", [])

    # site.json 是選用的：沒有這個檔就一切照舊，頁面不會有廣告相關的任何東西。
    site_cfg = {}
    site_path = os.path.join(config_dir, "site.json")
    if os.path.exists(site_path):
        with open(site_path, encoding="utf-8") as handle:
            site_cfg = json.load(handle)
    return indicators_cfg["indicators"], rules_cfg, site_cfg


def known_variable_names(indicator_cfg):
    """self-test 用：所有可能出現在表達式裡的變數名稱。"""
    empty = {i["key"]: Series() for i in indicator_cfg}
    unit_map = {i["key"]: i.get("unit", "") for i in indicator_cfg}
    return set(build_metrics(empty, unit_map))


def self_test(indicator_cfg, rules_cfg, log):
    problems = []
    keys = {i["key"] for i in indicator_cfg}
    if len(keys) != len(indicator_cfg):
        problems.append("indicators.json 有重複的 key")

    for indicator in indicator_cfg:
        if not indicator.get("sources") and not indicator.get("derived") \
                and not indicator.get("derived_diff"):
            problems.append("%s 既沒有 sources 也不是 derived" % indicator["key"])
        if indicator.get("derived"):
            missing = referenced_names(indicator["derived"]) - keys
            if missing:
                problems.append("%s 的 derived 引用了不存在的指標：%s"
                                % (indicator["key"], ", ".join(sorted(missing))))
        if indicator.get("derived_diff") and indicator["derived_diff"] not in keys:
            problems.append("%s 的 derived_diff 指向不存在的指標" % indicator["key"])

    names = known_variable_names(indicator_cfg)

    def check(expr, where):
        if not expr:
            return
        try:
            missing = referenced_names(expr) - names
        except SyntaxError as exc:
            problems.append("%s 表達式語法錯誤：%s（%s）" % (where, expr, exc))
            return
        if missing:
            problems.append("%s 引用了不存在的變數：%s（%s）"
                            % (where, ", ".join(sorted(missing)), expr))

    for wire in rules_cfg.get("tripwires", []):
        check(wire.get("expr"), "引信 %s" % wire.get("id"))
    for rung in rules_cfg.get("ladder", []):
        check(rung.get("expr"), "階梯 L%s" % rung.get("level"))
        check_vars = set(rung.get("readout_vars", [])) - names
        if check_vars:
            problems.append("階梯 L%s 的 readout_vars 不存在：%s"
                            % (rung.get("level"), ", ".join(sorted(check_vars))))
    for chain in rules_cfg.get("chains", []):
        for node in chain["nodes"]:
            check(node.get("expr"), "%s／%s" % (chain["id"], node.get("step")))
            if node.get("jump") and node["jump"][2:] not in keys:
                problems.append("%s／%s 的 jump 指向不存在的指標：%s"
                                % (chain["id"], node.get("step"), node["jump"]))
    for card in rules_cfg.get("crowding", []):
        check(card.get("state_expr"), "擁擠層 %s" % card.get("title"))
        missing = set(card.get("live_vars", [])) - names
        if missing:
            problems.append("擁擠層 %s 的 live_vars 不存在：%s"
                            % (card.get("title"), ", ".join(sorted(missing))))

    for problem in problems:
        log("  ✗ %s" % problem)
    if not problems:
        log("  ✓ 設定檔與所有表達式檢查通過（%d 個指標、%d 條引信、%d 條傳導鏈）"
            % (len(indicator_cfg), len(rules_cfg.get("tripwires", [])),
               len(rules_cfg.get("chains", []))))
    return problems


_DATED_REPORT = re.compile(r"^(console|snapshot|summary)-(\d{4}-\d{2}-\d{2})\.(html|json|md)$")


def prune_dated_reports(out_dir, keep, log):
    """只留最近 keep 天的帶日期存檔。

    每次掃描會多出 console-／snapshot-／summary- 三個檔，約 180 KB。每 3 天
    一次時無所謂，改成每天就是一年 65 MB 進版控。真正的資料在
    data/history.csv，這些是可以從它重畫出來的副本。

    注意：刪掉只讓工作目錄與 Pages 站台變乾淨，git 物件庫裡的舊版本仍然
    留著——這裡不試圖改寫歷史。
    """
    if keep <= 0 or not os.path.isdir(out_dir):
        return
    dates = set()
    for name in os.listdir(out_dir):
        match = _DATED_REPORT.match(name)
        if match:
            dates.add(match.group(2))
    doomed = sorted(dates, reverse=True)[keep:]
    if not doomed:
        return
    removed = 0
    for name in os.listdir(out_dir):
        match = _DATED_REPORT.match(name)
        if match and match.group(2) in doomed:
            os.remove(os.path.join(out_dir, name))
            removed += 1
    log("  清掉 %d 個超過 %d 天份的存檔（最舊保留到 %s）"
        % (removed, keep, sorted(dates, reverse=True)[keep - 1]))


def main(argv=None):
    args = parse_args(argv)
    log = log_factory(args.quiet)
    scan_time = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")

    previous = history_mod.load_json(os.path.join(args.out_dir, "latest.json"))
    inherited_changes = None
    inherited_delta = None
    if args.rebuild:
        args.no_fetch = True
        if previous and previous.get("scan_time"):
            # 重畫時 previous 就是這份快照自己，比出來的引信變化必然全空——
            # 那會把「今天有新引信亮起」抹成「沒事」，於是發佈頁面的那一步
            # 反而消掉了掃描剛剛偵測到的警報。沿用，不重算。
            inherited_delta = previous.get("tripwire_delta")
            # 重畫頁面不是一次新的掃描：時間戳與「與上次相比」都必須沿用，
            # 否則每次改模板都會謊報掃描時間，並把真正的變更清單洗掉。
            scan_time = previous["scan_time"]
            # 沒有可沿用的清單時，寧可空著也不要重算——重算是拿新快照去比
            # 「同一份快照」，得到的是閾值調整、模板改動這類重建痕跡，
            # 會被讀成市場的變化（例如「CCC 燈號 底層分化 → 正常
            # （1006 → 1006）」，數字根本沒動，動的是我改的門檻）。
            inherited_changes = ([tuple(c) for c in previous.get("changes", [])]
                                 or [("info", "頁面重建，變更清單待下次掃描更新。")])

    log("== 流動性與尾部風險掃描 %s ==" % scan_time)

    indicator_cfg, rules_cfg, site_cfg = load_configs(args.config_dir)

    log("\n[0/5] 設定檔自我檢查")
    problems = self_test(indicator_cfg, rules_cfg, log)
    if args.self_test:
        return EXIT_OK if not problems else EXIT_ERROR
    if problems:
        log("  設定檔有問題，先修好再跑。")
        return EXIT_ERROR

    history_path = os.path.join(args.data_dir, "history.csv")
    cache_dir = os.path.join(args.data_dir, "cache")

    log("\n[1/5] 讀取本地歷史 %s" % history_path)
    history = history_mod.load_history(history_path)
    log("  已有 %d 個序列、%d 個觀測點"
        % (len(history), sum(len(s) for s in history.values())))

    fetched = {}
    data_notes = []
    if args.no_fetch:
        log("\n[2/5] 跳過抓取（--no-fetch），直接用本地歷史重算")
    else:
        log("\n[2/5] 抓取資料" + ("（offline 模式：只用快取）" if args.offline else ""))
        fetcher = Fetcher(cache_dir=cache_dir, timeout=args.timeout, retries=args.retries,
                          offline=args.offline, log=log)
        for indicator in indicator_cfg:
            sources = indicator.get("sources")
            if not sources:
                continue
            key = indicator["key"]
            last = None
            for source in sources:
                result = fetcher.fetch(key, source)
                last = result
                if result.ok:
                    break
            fetched[key] = last
            if last.ok:
                log("  ✓ %-14s %-6s %4d 點，最新 %s %s"
                    % (key, last.provider, len(last.series),
                       last.series.latest_date(), last.detail))
            else:
                log("  ✗ %-14s %s" % (key, last.detail))
                data_notes.append("%s 抓取失敗：%s" % (indicator.get("label", key), last.detail))

    log("\n[3/5] 合併歷史並計算衍生序列")
    series_map, notes = resolve_series(indicator_cfg, fetched, history,
                                       record_failures=not args.no_fetch)
    log("  可用序列 %d 個" % len(series_map))
    history_mod.save_history(history_path, series_map, keep=args.history_days)
    log("  已寫回 %s" % history_path)

    log("\n[4/5] 套用規則")
    snapshot = build_snapshot(indicator_cfg, rules_cfg, series_map, notes, scan_time, data_notes)
    if inherited_changes is not None:
        changes = inherited_changes
        log("  （重畫模式：沿用上一份快照的變更清單）")
    else:
        changes = diff_mod.diff_snapshots(snapshot, previous)
    # 存進快照，重畫時才有得沿用。
    snapshot["changes"] = [list(c) for c in changes]

    # 抓取「成功」但序列是空的（或整個沒有歷史）同樣是缺口。不另外標出來的話，
    # --no-fetch 或來源默默回空值時會得到一份全灰的報表卻回報離開碼 0。
    for indicator in snapshot["indicators"]:
        if indicator["hidden"] or indicator["value"] is not None:
            continue
        note = "%s 無可用資料" % indicator["label"]
        if not any(note.split()[0] in existing for existing in data_notes):
            data_notes.append(note)
    snapshot["data_notes"] = data_notes

    fired = [w for w in snapshot["tripwires"] if w["state"] is True]
    delta = inherited_delta if inherited_delta is not None \
        else diff_mod.tripwire_delta(snapshot, previous)
    snapshot["tripwire_delta"] = delta
    log("  判定：第 %d 階 · %s" % (snapshot["level"], snapshot["verdict"]["headline"]))
    log("  觸發中的引信：%s" % ("、".join(w["code"] for w in fired) if fired else "無"))
    if delta["new"]:
        log("    ↳ 新亮：%s" % "、".join(delta["new"]))
    if delta["cleared"]:
        log("    ↳ 解除：%s" % "、".join(delta["cleared"]))
    if delta["ongoing"]:
        log("    ↳ 續亮：%s（不重複通知）" % "、".join(delta["ongoing"]))

    log("\n[5/5] 產出報表")
    date_tag = scan_time[:10]
    os.makedirs(args.out_dir, exist_ok=True)

    # 公開版的六格用精簡清單；完整版表格 23 列全部都要畫，而它是伺服器端
    # 渲染，不會反映成使用者的下載量。
    console_series = public_page.build_series_payload(
        series_map, keys=[i["key"] for i in indicator_cfg if not i.get("hidden")])
    series_payload = public_page.build_series_payload(series_map)
    html_text = render_mod.render_html(snapshot, changes, console_series)
    for name in ("console.html", "console-%s.html" % date_tag):
        path = os.path.join(args.out_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(html_text)
        log("  寫出 %s" % path)

    # 公開版總覽（index.html）。序列另外存一份給火花線用，頁面會在載入時
    # 重抓，所以即使 HTML 被快取，看到的仍是最新一次掃描。
    history_mod.save_json(os.path.join(args.out_dir, "series.json"), series_payload)
    public_html = public_page.render_public_html(
        snapshot, series_payload, changes, repo_url=args.repo_url, site=site_cfg)
    path = os.path.join(args.out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(public_html)
    log("  寫出 %s（公開版）" % path)

    # 隱私權政策：AdSense 審核會看，而且投放個人化廣告本來就需要。
    privacy_path = os.path.join(args.out_dir, "privacy.html")
    with open(privacy_path, "w", encoding="utf-8") as handle:
        handle.write(public_page.render_privacy_html(site_cfg, repo_url=args.repo_url))
    log("  寫出 %s" % privacy_path)

    # ads.txt 必須在網域根目錄，所以只有在用自訂網域時才有意義；
    # 放在 github.io 的子路徑下 Google 根本不會去讀。
    ad_client = (site_cfg.get("adsense") or {}).get("client", "").strip()
    if ad_client:
        publisher = ad_client.replace("ca-pub-", "")
        with open(os.path.join(args.out_dir, "ads.txt"), "w", encoding="utf-8") as handle:
            handle.write("google.com, pub-%s, DIRECT, f08c47fec0942fa0\n" % publisher)
        log("  寫出 ads.txt")
        if not site_cfg.get("custom_domain", "").strip():
            log("  ⚠ 已設定 AdSense 但沒有自訂網域——github.io 的母網域無法驗證，"
                "審核不會過，ads.txt 也不會被讀取。")

    domain = site_cfg.get("custom_domain", "").strip()
    if domain:
        with open(os.path.join(args.out_dir, "CNAME"), "w", encoding="utf-8") as handle:
            handle.write(domain + "\n")
        log("  寫出 CNAME（%s）" % domain)

    summary = diff_mod.render_markdown_summary(snapshot, changes)
    summary_path = os.path.join(args.out_dir, "summary-%s.md" % date_tag)
    with open(summary_path, "w", encoding="utf-8") as handle:
        handle.write(summary)
    log("  寫出 %s" % summary_path)

    history_mod.save_json(os.path.join(args.out_dir, "latest.json"), snapshot)
    history_mod.save_json(os.path.join(args.out_dir, "snapshot-%s.json" % date_tag), snapshot)
    prune_dated_reports(args.out_dir, args.keep_daily, log)

    log("\n--- 本次變化 ---")
    for severity, text in changes:
        log("  %s %s" % ({"alert": "🔴", "warn": "🟡", "info": "·"}.get(severity, "·"), text))

    # 條件刻意不是「fired 非空」：那是「還亮著」，每天掃就會每天推播同一件事。
    # 要推的是「集合變了」——新亮、解除，或有 alert／warn 等級的其他變化。
    should_notify = args.notify == "always" or (
        args.notify == "auto" and (delta["new"] or delta["cleared"]
                                   or any(s in ("alert", "warn") for s, _ in changes)))
    if should_notify:
        ok, detail = notify_mod.send_webhook(notify_mod.build_message(snapshot, changes))
        log("\n推播：%s" % detail)

    exit_code = EXIT_OK
    if fired:
        exit_code += EXIT_TRIPWIRE
    if data_notes:
        exit_code += EXIT_DATA_GAP
    log("\n完成，離開碼 %d" % exit_code)
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(EXIT_ERROR)
