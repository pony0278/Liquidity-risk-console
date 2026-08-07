"""整條管線的離線測試：不連網，用合成歷史跑完 scan.py 並檢查判定。

  python3 -m unittest discover -s tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "tests"))

import scan  # noqa: E402
from fixtures import CALM, STRESS, write_history  # noqa: E402
from lrconsole import diff as diff_mod  # noqa: E402
from lrconsole.expr import ExprError, evaluate, evaluate_value  # noqa: E402
from lrconsole.fetch import Fetcher  # noqa: E402
from lrconsole.history import load_history, save_history  # noqa: E402
from lrconsole.series import Series, build_metrics  # noqa: E402


class _Validator(HTMLParser):
    """確認產出的 HTML 標籤有收好（漏一個 </div> 版面就毀了）。"""

    VOID = {"meta", "br", "hr", "img", "input", "link", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append("多出來的 </%s>" % tag)
        elif self.stack[-1] != tag:
            self.errors.append("標籤交錯：期待 </%s>，實際 </%s>" % (self.stack[-1], tag))
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def run_scan(tmp, scenario, extra_args=()):
    data_dir = os.path.join(tmp, "data")
    out_dir = os.path.join(tmp, "reports")
    write_history(os.path.join(data_dir, "history.csv"), scenario)
    code = scan.main([
        "--config-dir", os.path.join(BASE_DIR, "config"),
        "--data-dir", data_dir,
        "--out-dir", out_dir,
        "--no-fetch", "--notify", "never", "--quiet",
        *extra_args,
    ])
    with open(os.path.join(out_dir, "latest.json"), encoding="utf-8") as handle:
        snapshot = json.load(handle)
    return code, snapshot, out_dir


class ExprTests(unittest.TestCase):
    def test_basic(self):
        self.assertIs(evaluate("a > 3", {"a": 5}), True)
        self.assertIs(evaluate("a > 3", {"a": 1}), False)
        self.assertEqual(evaluate_value("(a - b) * 100", {"a": 3.57, "b": 3.65}),
                         (3.57 - 3.65) * 100)

    def test_unknown_propagates(self):
        # 缺資料必須是 unknown，不能被當成 False——否則抓不到的指標會
        # 靜靜地變成「沒問題」。
        self.assertIsNone(evaluate("a > 3", {}))
        self.assertIsNone(evaluate("a > 3", {"a": None}))
        self.assertIsNone(evaluate("a > 3 or b > 3", {"a": 1}))
        self.assertIsNone(evaluate("a > 3 and b > 3", {"a": 99, "b": None}))

    def test_rejects_dangerous_syntax(self):
        for bad in ("__import__('os').system('ls')", "open('x')", "[1,2,3]", "a.b"):
            with self.assertRaises(ExprError):
                evaluate(bad, {"a": 1})


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.series = Series([("2026-07-%02d" % d, float(v))
                              for d, v in zip(range(1, 11), [10, 11, 12, 11, 10, 9, 12, 13, 14, 12])])

    def test_changes(self):
        self.assertEqual(self.series.latest(), 12.0)
        self.assertEqual(self.series.change(1), -2.0)
        self.assertAlmostEqual(self.series.pct_change(1), -100 * 2 / 14)
        self.assertAlmostEqual(self.series.drawdown(60), 100 * (12 / 14 - 1))

    def test_streaks(self):
        positive = Series([("2026-07-0%d" % d, v) for d, v in zip(range(1, 6), [-1, -1, 2, 3, 4])])
        self.assertEqual(positive.positive_streak(), 3)
        self.assertEqual(Series().positive_streak(), 0)

    def test_empty_series_metrics_are_none(self):
        metrics = build_metrics({"x": Series()}, {"x": ""})
        self.assertIsNone(metrics["x"])
        self.assertIsNone(metrics["x_pos_streak"], "空序列的 streak 必須是 None")
        self.assertIsNone(metrics["x_d1"])

    def test_bps_variants_for_percent_units(self):
        series = Series([("2026-07-01", 4.50), ("2026-07-02", 4.62)])
        metrics = build_metrics({"y30": series}, {"y30": "%"})
        self.assertAlmostEqual(metrics["y30_d1_bps"], 12.0, places=6)


class HistoryTests(unittest.TestCase):
    def test_round_trip_and_merge(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "history.csv")
            original = {"a": Series([("2026-07-01", 1.5), ("2026-07-02", 2.5)])}
            save_history(path, original)
            loaded = load_history(path)
            self.assertEqual(loaded["a"].points, original["a"].points)

            merged = loaded["a"].merged_with(Series([("2026-07-02", 9.9), ("2026-07-03", 3.0)]))
            self.assertEqual(merged.points,
                             [("2026-07-01", 1.5), ("2026-07-02", 9.9), ("2026-07-03", 3.0)])
        finally:
            shutil.rmtree(tmp)


class CalmScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.code, cls.snapshot, cls.out_dir = run_scan(cls.tmp, CALM)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_exit_code_clean(self):
        self.assertEqual(self.code, 0, "平靜情境不該有引信觸發或資料缺口")

    def test_level_one(self):
        self.assertEqual(self.snapshot["level"], 1)
        self.assertIn("沒有流動性危機", self.snapshot["verdict"]["headline"])

    def test_no_tripwires(self):
        fired = [w["id"] for w in self.snapshot["tripwires"] if w["state"] is True]
        self.assertEqual(fired, [])
        unknown = [w["id"] for w in self.snapshot["tripwires"] if w["state"] is None]
        self.assertEqual(unknown, [], "資料齊全時不該有無法判定的引信")

    def test_derived_series_computed(self):
        by_key = {i["key"]: i for i in self.snapshot["indicators"]}
        self.assertAlmostEqual(by_key["sofr_iorb"]["value"], -8.0, delta=0.5)
        self.assertIsNotNone(by_key["slope_2s30s"]["value"])
        self.assertIsNotNone(by_key["net_liquidity"]["value"])
        self.assertIsNotNone(by_key["hy_ig"]["value"])
        self.assertIsNotNone(by_key["payrolls"]["value"])

    def test_tier_lights_green(self):
        self.assertEqual(self.snapshot["tiers"]["1"]["status"], "ok")
        self.assertEqual(self.snapshot["tiers"]["2"]["status"], "ok")

    def test_html_is_well_formed(self):
        with open(os.path.join(self.out_dir, "index.html"), encoding="utf-8") as handle:
            html_text = handle.read()
        validator = _Validator()
        validator.feed(html_text)
        self.assertEqual(validator.errors, [])
        self.assertEqual(validator.stack, [], "有標籤沒收：%s" % validator.stack)
        self.assertNotIn("%s", html_text, "樣板留下未替換的佔位符")
        self.assertIn("系統壓力盤", html_text)
        self.assertIn("SOFR − IORB", html_text)

    def test_markdown_summary_written(self):
        names = os.listdir(self.out_dir)
        self.assertTrue(any(n.startswith("summary-") and n.endswith(".md") for n in names))


class StressScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.code, cls.snapshot, cls.out_dir = run_scan(cls.tmp, STRESS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_exit_code_flags_tripwire(self):
        self.assertEqual(self.code, scan.EXIT_TRIPWIRE)

    def test_ladder_reaches_funding_crisis(self):
        self.assertEqual(self.snapshot["level"], 4)
        here = [r for r in self.snapshot["ladder"] if r["here"]]
        self.assertEqual(len(here), 1)
        self.assertEqual(here[0]["level"], 4)

    def test_expected_tripwires(self):
        fired = {w["id"] for w in self.snapshot["tripwires"] if w["state"] is True}
        self.assertEqual(
            fired,
            {"hy_350", "sofr_flip", "jpy_spike", "long_end_jump", "vol_regime",
             "yield_up_dollar_down"})

    def test_sofr_streak_drives_funding_chain(self):
        by_id = {c["id"]: c for c in self.snapshot["chains"]}
        chain_d = by_id["D"]
        self.assertEqual(chain_d["live"], 5, "縮表→水管鏈應該全線點亮")
        self.assertEqual(chain_d["state"], "CRITICAL")

    def test_carry_chain_starts_moving(self):
        by_id = {c["id"]: c for c in self.snapshot["chains"]}
        labels = {n["label"]: n["state"] for n in by_id["B"]["nodes"]}
        self.assertEqual(labels["日圓急升"], "live", "單日 −3% 應該點亮節點 02")

    def test_crowding_reflects_data(self):
        by_title = {c["title"]: c for c in self.snapshot["crowding"]}
        self.assertEqual(by_title["公債基差交易"]["level"], "press")
        self.assertEqual(by_title["銀行 AFS／HTM 債券帳"]["level"], "press")

    def test_html_well_formed(self):
        with open(os.path.join(self.out_dir, "index.html"), encoding="utf-8") as handle:
            html_text = handle.read()
        validator = _Validator()
        validator.feed(html_text)
        self.assertEqual(validator.errors, [])
        self.assertIn("已觸發", html_text)


class DiffTests(unittest.TestCase):
    def test_escalation_is_reported(self):
        tmp = tempfile.mkdtemp()
        try:
            run_scan(tmp, CALM)
            _, snapshot, out_dir = run_scan(tmp, STRESS)
            with open(os.path.join(out_dir, "summary-%s.md" % snapshot["scan_time"][:10]),
                      encoding="utf-8") as handle:
                summary = handle.read()
            self.assertIn("升級階梯升級", summary)
            self.assertIn("引信觸發", summary)
            self.assertIn("HY OAS", summary)
        finally:
            shutil.rmtree(tmp)

    def test_first_run_has_baseline_note(self):
        tmp = tempfile.mkdtemp()
        try:
            _, snapshot, out_dir = run_scan(tmp, CALM)
            with open(os.path.join(out_dir, "summary-%s.md" % snapshot["scan_time"][:10]),
                      encoding="utf-8") as handle:
                self.assertIn("第一份基準", handle.read())
        finally:
            shutil.rmtree(tmp)


class MissingDataTests(unittest.TestCase):
    def test_missing_series_yields_unknown_not_ok(self):
        """抓不到 SOFR 時，水管引信必須是「無法判定」而不是「未觸發」。"""
        tmp = tempfile.mkdtemp()
        try:
            data_dir = os.path.join(tmp, "data")
            path = write_history(os.path.join(data_dir, "history.csv"), CALM)
            with open(path, encoding="utf-8") as handle:
                rows = [line for line in handle if ",sofr," not in line]
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(rows)

            code = scan.main([
                "--config-dir", os.path.join(BASE_DIR, "config"),
                "--data-dir", data_dir,
                "--out-dir", os.path.join(tmp, "reports"),
                "--no-fetch", "--notify", "never", "--quiet",
            ])
            self.assertEqual(code, scan.EXIT_DATA_GAP,
                             "缺了指標卻回報一切正常，是最不該有的離開碼")
            with open(os.path.join(tmp, "reports", "latest.json"), encoding="utf-8") as handle:
                snapshot = json.load(handle)
            self.assertTrue(any("SOFR" in n for n in snapshot["data_notes"]),
                            "缺口要寫進報表的 data_notes：%s" % snapshot["data_notes"])
            by_id = {w["id"]: w for w in snapshot["tripwires"]}
            self.assertIsNone(by_id["sofr_flip"]["state"])
            by_key = {i["key"]: i for i in snapshot["indicators"]}
            self.assertEqual(by_key["sofr_iorb"]["status"], "unknown")
            self.assertIn("未取得資料", snapshot["verdict"]["paragraphs"][0])
        finally:
            shutil.rmtree(tmp)


class FetchParsingTests(unittest.TestCase):
    """解析邏輯的離線測試：把 HTTP 換掉，只驗欄位怎麼被讀出來。"""

    def _fetcher(self, response):
        fetcher = Fetcher(cache_dir=None, offline=False)
        fetcher._get = lambda url, cache_name: (response, False)  # noqa: SLF001
        return fetcher

    def test_fred_csv(self):
        csv_text = ("observation_date,BAMLH0A0HYM2\n"
                    "2026-07-21,2.69\n"
                    "2026-07-22,.\n"
                    "2026-07-23,2.77\n")
        result = self._fetcher(csv_text).fetch(
            "hy_oas", {"provider": "fred", "id": "BAMLH0A0HYM2", "scale": 100})
        self.assertTrue(result.ok)
        self.assertEqual(result.series.points,
                         [("2026-07-21", 269.0), ("2026-07-23", 277.0)],
                         "缺值的 '.' 要被跳過，scale 要套用")

    def test_yahoo_chart(self):
        payload = json.dumps({"chart": {"error": None, "result": [{
            "timestamp": [1785283200, 1785369600],
            "indicators": {"quote": [{"close": [18.5, None]}]},
        }]}})
        result = self._fetcher(payload).fetch("vix", {"provider": "yahoo", "id": "^VIX"})
        self.assertTrue(result.ok)
        self.assertEqual(len(result.series), 1, "close 為 null 的交易日要跳過")
        self.assertEqual(result.series.latest(), 18.5)

    def test_yahoo_reports_error(self):
        payload = json.dumps({"chart": {"error": {"code": "Not Found"}, "result": None}})
        result = self._fetcher(payload).fetch("vix", {"provider": "yahoo", "id": "^NOPE"})
        self.assertFalse(result.ok)

    def test_nyfed_repo_sums_and_backfills_zero(self):
        payload = json.dumps({"repo": {"operations": [
            {"operationDate": "2026-07-29", "totalAmtAccepted": "20,000,000,000"},
            {"operationDate": "2026-07-29", "totalAmtAccepted": 5000000000},
            {"operationDate": "2026-07-31", "totalAmtAccepted": 0},
        ]}})
        result = self._fetcher(payload).fetch("srf", {"provider": "nyfed_repo", "id": "srf"})
        self.assertTrue(result.ok)
        points = dict(result.series.points)
        self.assertAlmostEqual(points["2026-07-29"], 25.0, msg="同日多筆要加總，單位換成 $B")
        self.assertEqual(points["2026-07-30"], 0.0, "沒有操作的營業日要補零")

    def test_future_dated_points_are_dropped(self):
        """FRED 的 IORB 會往前補到當期結尾，未來日期不能進序列。"""
        future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        csv_text = "observation_date,IORB\n2026-01-02,3.65\n%s,3.65\n%s,3.65\n" % (today, future)
        result = self._fetcher(csv_text).fetch("iorb", {"provider": "fred", "id": "IORB"})
        self.assertTrue(result.ok)
        self.assertEqual(result.series.latest_date(), today)

    def test_unknown_shape_fails_cleanly(self):
        result = self._fetcher(json.dumps({"whatever": 1})).fetch(
            "srf", {"provider": "nyfed_repo", "id": "srf"})
        self.assertFalse(result.ok)
        self.assertIn("operations", result.detail)


class PublicPageTests(unittest.TestCase):
    """公開版總覽頁：給沒讀過原始文件的人看的那一頁。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.code, cls.snapshot, cls.out_dir = run_scan(cls.tmp, STRESS)
        with open(os.path.join(cls.out_dir, "index.html"), encoding="utf-8") as handle:
            cls.html = handle.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_both_pages_written(self):
        names = os.listdir(self.out_dir)
        for expected in ("index.html", "console.html", "series.json", "latest.json"):
            self.assertIn(expected, names)

    def test_html_is_well_formed(self):
        validator = _Validator()
        validator.feed(self.html)
        self.assertEqual(validator.errors, [])
        self.assertEqual(validator.stack, [])

    def test_bootstrap_data_is_parseable(self):
        """fetch 失敗時（file:// 或離線）頁面靠這份內嵌資料，不能是壞的 JSON。"""
        for marker in ('id="boot-snapshot"', 'id="boot-series"'):
            start = self.html.index(marker)
            start = self.html.index(">", start) + 1
            end = self.html.index("</script>", start)
            payload = json.loads(self.html[start:end].replace("<\\/", "</"))
            self.assertTrue(payload)

    def test_series_payload_shape(self):
        with open(os.path.join(self.out_dir, "series.json"), encoding="utf-8") as handle:
            series = json.load(handle)
        for key in ("sofr_iorb", "hy_oas", "y30", "vix"):
            self.assertIn(key, series)
            self.assertEqual(len(series[key]["dates"]), len(series[key]["values"]))
            self.assertLessEqual(len(series[key]["values"]), 180)

    def test_severity_is_encoded_beyond_colour(self):
        """警示與明確壓力在紅綠色盲下幾乎同色，嚴重度必須另有通道。"""
        by_key = {i["key"]: i for i in self.snapshot["indicators"]}
        self.assertEqual(by_key["y30"]["severity"], 4)
        self.assertEqual(by_key["usdjpy"]["severity"], 0, "沒有閾值帶的指標不給嚴重度")
        self.assertIn('aria-label="嚴重度', self.html)
        self.assertIn("sev s", self.html)

    def test_distance_to_threshold_present(self):
        by_key = {i["key"]: i for i in self.snapshot["indicators"]}
        self.assertIn("離", by_key["vix"]["distance_text"] + by_key["hy_oas"]["distance_text"])
        track = by_key["hy_oas"]["track"]
        self.assertTrue(track["segments"])
        self.assertGreaterEqual(track["marker_pct"], 0)
        self.assertLessEqual(track["marker_pct"], 100)

    def test_track_segments_tile_the_bar_exactly(self):
        """區段要剛好鋪滿 100%，不重疊也不留縫。

        沒寫 min 的閾值帶隱含「前一帶的上界」當下界；若當成從最左邊開始，
        每段都會重疊，寬度加總可以到 230%，畫出來的比例整個是錯的。
        """
        for indicator in self.snapshot["indicators"]:
            track = indicator.get("track")
            if not track:
                continue
            total = sum(s["width_pct"] for s in track["segments"])
            self.assertAlmostEqual(total, 100.0, places=6,
                                   msg="%s 的軌道總寬 %.2f%%" % (indicator["key"], total))
            cursor = 0.0
            for seg in track["segments"]:
                self.assertAlmostEqual(seg["start_pct"], cursor, places=6,
                                       msg="%s 的區段有縫或重疊" % indicator["key"])
                cursor += seg["width_pct"]

    def test_track_band_matches_the_light(self):
        """標記所在的區段，狀態必須跟燈號一致——否則畫面自相矛盾。"""
        for indicator in self.snapshot["indicators"]:
            track = indicator.get("track")
            if not track or indicator["status"] in ("unknown", "info"):
                continue
            at = next((s for s in track["segments"]
                       if s["start_pct"] <= track["marker_pct"] < s["start_pct"] + s["width_pct"]),
                      track["segments"][-1])
            self.assertEqual(at["status"], indicator["status"],
                             "%s 的標記落在 %s 帶，但燈號是 %s"
                             % (indicator["key"], at["status"], indicator["status"]))

    def test_console_css_class_names_are_unique(self):
        """新增樣式不能跟原本的類名撞——`.tb` 原本是頁首標題區塊，
        後來加的同名規則會把 header 的 grid 佈局一起蓋掉，而且不會有任何
        測試變紅，只會版面壞掉。"""
        import re
        path = os.path.join(BASE_DIR, "templates", "console.css")
        with open(path, encoding="utf-8") as handle:
            css = handle.read()
        selectors = re.findall(r"^([.#][\w-]+)\s*\{", css, re.M)
        duplicates = {s for s in selectors if selectors.count(s) > 1}
        self.assertEqual(duplicates, set(), "重複定義的選擇器：%s" % duplicates)

    def test_no_unresolved_placeholders(self):
        self.assertNotIn("__TILE_KEYS__", self.html)
        self.assertNotIn("__TILE_WHY__", self.html)

    def test_hero_charts_replace_prose(self):
        """首屏是兩張圖：升級階梯量表 + 壓力分布；文字判讀收進摺疊。"""
        self.assertIn('id="ladder-meter"', self.html)
        self.assertIn('id="pressure-map"', self.html)
        self.assertIn('<details class="prose">', self.html)
        # 壓力分布靠 pct_rank 畫點，指標資料裡必須有這個欄位
        by_key = {i["key"]: i for i in self.snapshot["indicators"]}
        self.assertIsNotNone(by_key["y30"].get("pct_rank"))


class PrecedentTests(unittest.TestCase):
    """傳導鏈的歷史先例——重點是「日圓在第幾幕」要真的被畫出來。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.code, cls.snapshot, cls.out_dir = run_scan(cls.tmp, CALM)
        cls.chain_b = {c["id"]: c for c in cls.snapshot["chains"]}["B"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_precedents_reach_the_snapshot(self):
        names = [p["name"] for p in self.chain_b["precedents"]]
        self.assertEqual(len(names), 2)
        self.assertTrue(any("1998" in n for n in names))
        self.assertTrue(any("2024" in n for n in names))

    def test_each_precedent_marks_the_yen_step(self):
        """沒有 kind=yen 的步驟，整段就失去意義——那格才是要對比的東西。"""
        for case in self.chain_b["precedents"]:
            kinds = [s.get("kind") for s in case["steps"]]
            self.assertIn("yen", kinds, "%s 沒有標出日圓那一幕" % case["name"])
            self.assertTrue(case.get("yen_act"), "%s 缺 yen_act" % case["name"])

    def test_the_two_cases_put_the_yen_in_opposite_acts(self):
        by_name = {p["name"]: p for p in self.chain_b["precedents"]}
        late = next(v for k, v in by_name.items() if "1998" in k)
        early = next(v for k, v in by_name.items() if "2024" in k)
        yen_index = lambda c: [s.get("kind") for s in c["steps"]].index("yen")  # noqa: E731
        self.assertGreater(yen_index(late), yen_index(early),
                           "1998 的日圓應該落在比 2024 更後面的位置")
        self.assertIn("最後一幕", late["yen_act"])
        self.assertIn("第一幕", early["yen_act"])

    def test_rendered_in_both_pages(self):
        for name in ("console.html", "index.html"):
            with open(os.path.join(self.out_dir, name), encoding="utf-8") as handle:
                html_text = handle.read()
            self.assertIn("k-yen", html_text, "%s 沒有畫出日圓那一幕" % name)
            self.assertIn("1998", html_text)
            validator = _Validator()
            validator.feed(html_text)
            self.assertEqual(validator.errors, [], "%s 標籤不完整" % name)


class ConsoleDensityTests(unittest.TestCase):
    """說明文字預設收合，但必須還在 DOM 裡——是藏起來，不是刪掉。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        _, _, cls.out_dir = run_scan(cls.tmp, CALM)
        with open(os.path.join(cls.out_dir, "console.html"), encoding="utf-8") as handle:
            cls.html = handle.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_toggle_button_exists(self):
        self.assertIn('id="toggle-notes"', self.html)
        self.assertIn("lrc-show-notes", self.html, "開關狀態要記進 localStorage")

    def test_notes_are_hidden_not_deleted(self):
        self.assertIn('class="note note-only"', self.html)
        # 抽一句具體的註記文字驗證它還在 DOM 裡
        self.assertIn("報稅日", self.html, "註記內容被刪掉了，應該只是收合")
        self.assertIn("note-only{display:none", self.html.replace(" ", ""))

    def test_note_also_available_as_tooltip(self):
        """說明收合時，滑過指標名稱仍要看得到註記。"""
        self.assertRegex(self.html, r'<td class="k" title="[^"]+">')


class AdSenseTests(unittest.TestCase):
    """廣告是選用的：沒設定就完全不存在，不是用 CSS 藏起來。"""

    def _render(self, site):
        with open(os.path.join(BASE_DIR, "reports", "latest.json"), encoding="utf-8") as handle:
            snapshot = json.load(handle)
        from lrconsole import public_page
        return public_page.render_public_html(snapshot, {}, [], site=site)

    def test_no_third_party_script_when_unconfigured(self):
        """沒填 client 時連 script 標籤都不該產生——訪客不會被第三方追蹤。"""
        for site in (None, {}, {"adsense": {"client": "", "slots": {}}}):
            html_text = self._render(site)
            self.assertNotIn("adsbygoogle", html_text)
            self.assertNotIn("googlesyndication", html_text)

    def test_units_render_only_for_filled_slots(self):
        html_text = self._render({"adsense": {
            "client": "ca-pub-0000000000000000",
            "slots": {"after_hero": "111", "footer": ""},
        }})
        self.assertIn("googlesyndication", html_text)
        self.assertEqual(html_text.count('class="adbox"'), 1,
                         "只有填了 slot 的位置該出現廣告")

    def test_privacy_page_matches_ad_state(self):
        from lrconsole import public_page
        off = public_page.render_privacy_html({})
        self.assertIn("沒有", off)
        self.assertNotIn("AdSense", off)
        on = public_page.render_privacy_html({"adsense": {"client": "ca-pub-1"}})
        self.assertIn("AdSense", on)
        self.assertIn("aboutads.info", on)

    def test_privacy_page_always_written(self):
        tmp = tempfile.mkdtemp()
        try:
            _, _, out_dir = run_scan(tmp, CALM)
            self.assertIn("privacy.html", os.listdir(out_dir))
        finally:
            shutil.rmtree(tmp)


class RebuildTests(unittest.TestCase):
    def test_rebuild_preserves_scan_time_and_changes(self):
        """重畫頁面不是一次新的掃描，時間戳與變更清單都要沿用。"""
        tmp = tempfile.mkdtemp()
        try:
            run_scan(tmp, CALM)
            _, second, out_dir = run_scan(tmp, STRESS)
            self.assertTrue(second["changes"], "第二次掃描應該產生變更清單")

            code = scan.main([
                "--config-dir", os.path.join(BASE_DIR, "config"),
                "--data-dir", os.path.join(tmp, "data"),
                "--out-dir", out_dir,
                "--rebuild", "--notify", "never", "--quiet",
            ])
            self.assertNotEqual(code, scan.EXIT_ERROR)
            with open(os.path.join(out_dir, "latest.json"), encoding="utf-8") as handle:
                rebuilt = json.load(handle)
            self.assertEqual(rebuilt["scan_time"], second["scan_time"])
            self.assertEqual(rebuilt["changes"], second["changes"])
        finally:
            shutil.rmtree(tmp)


class SelfTestTests(unittest.TestCase):
    def test_shipped_config_passes(self):
        self.assertEqual(scan.main(["--self-test", "--quiet"]), 0)

    def test_million_denominated_fred_series_are_scaled(self):
        """WTREGEN／WRESBAL／WALCL 在 FRED 的單位是百萬美元。

        漏掉換算不會讓任何測試變紅，只會讓報表出現 $910776B 這種數字，
        還會連累 net_liquidity 與週變動的閾值——所以在這裡釘住。
        """
        with open(os.path.join(BASE_DIR, "config", "indicators.json"), encoding="utf-8") as handle:
            indicators = {i["key"]: i for i in json.load(handle)["indicators"]}
        for key in ("tga", "reserves", "walcl"):
            scale = indicators[key]["sources"][0].get("scale")
            self.assertEqual(scale, 0.001, "%s 少了百萬→十億的換算" % key)
        # RRPONTSYD 本來就是十億，多套一次反而會錯。
        self.assertNotIn("scale", indicators["rrp"]["sources"][0])


class TripwireDeltaTests(unittest.TestCase):
    """「還亮著」不是新聞。改成每天掃之後，一根連續亮著的引信如果每次都
    算觸發，就會每天推播、每天開一張 issue，講的卻是同一件事。"""

    @staticmethod
    def _snap(states):
        return {"tripwires": [{"id": k, "code": k.upper(), "state": v}
                              for k, v in states.items()]}

    def test_no_previous_counts_everything_as_new(self):
        delta = diff_mod.tripwire_delta(self._snap({"a": True, "b": False}), None)
        self.assertEqual(delta["new"], ["A"])
        self.assertEqual(delta["cleared"], [])
        self.assertEqual(delta["ongoing"], [])

    def test_still_lit_is_ongoing_not_new(self):
        before = self._snap({"a": True, "b": False})
        after = self._snap({"a": True, "b": True})
        delta = diff_mod.tripwire_delta(after, before)
        self.assertEqual(delta["new"], ["B"])
        self.assertEqual(delta["ongoing"], ["A"])
        self.assertEqual(delta["cleared"], [])

    def test_cleared_is_reported(self):
        delta = diff_mod.tripwire_delta(self._snap({"a": False}), self._snap({"a": True}))
        self.assertEqual(delta["cleared"], ["A"])
        self.assertEqual(delta["new"], [])

    def test_unknown_state_is_not_a_trigger(self):
        """資料缺時 state 是 None，那是「不知道」，不能當成觸發或解除。"""
        delta = diff_mod.tripwire_delta(self._snap({"a": None}), self._snap({"a": None}))
        self.assertEqual(delta, {"new": [], "cleared": [], "ongoing": []})

    def test_wire_removed_from_config_still_reports_cleared(self):
        delta = diff_mod.tripwire_delta({"tripwires": []}, self._snap({"a": True}))
        self.assertEqual(delta["cleared"], ["A"])

    def test_full_scan_marks_second_identical_scan_as_ongoing(self):
        tmp = tempfile.mkdtemp()
        try:
            _, first, _ = run_scan(tmp, STRESS)
            self.assertTrue(first["tripwire_delta"]["new"], "第一次掃到的引信應算新亮")
            _, second, _ = run_scan(tmp, STRESS)
            self.assertEqual(second["tripwire_delta"]["new"], [])
            self.assertEqual(sorted(second["tripwire_delta"]["ongoing"]),
                             sorted(first["tripwire_delta"]["new"]))
        finally:
            shutil.rmtree(tmp)

    def test_ongoing_only_does_not_notify(self):
        sent = []
        original = scan.notify_mod.send_webhook
        scan.notify_mod.send_webhook = lambda text: (sent.append(text), (True, "stub"))[1]
        tmp = tempfile.mkdtemp()
        try:
            run_scan(tmp, STRESS, extra_args=("--notify", "auto"))
            self.assertEqual(len(sent), 1, "第一次有新引信，應該推播")
            run_scan(tmp, STRESS, extra_args=("--notify", "auto"))
            self.assertEqual(len(sent), 1, "同樣的引信還亮著，不該再推一次")
        finally:
            scan.notify_mod.send_webhook = original
            shutil.rmtree(tmp)

    def test_rebuild_inherits_delta_instead_of_zeroing_it(self):
        """重畫時 previous 就是快照自己，重算必然全空——那會把剛偵測到的
        新引信抹掉，發佈頁面的那一步反而消掉了警報。"""
        tmp = tempfile.mkdtemp()
        try:
            run_scan(tmp, CALM)
            _, scanned, out_dir = run_scan(tmp, STRESS)
            self.assertTrue(scanned["tripwire_delta"]["new"])
            scan.main([
                "--config-dir", os.path.join(BASE_DIR, "config"),
                "--data-dir", os.path.join(tmp, "data"),
                "--out-dir", out_dir,
                "--rebuild", "--notify", "never", "--quiet",
            ])
            with open(os.path.join(out_dir, "latest.json"), encoding="utf-8") as handle:
                rebuilt = json.load(handle)
            self.assertEqual(rebuilt["tripwire_delta"], scanned["tripwire_delta"])
        finally:
            shutil.rmtree(tmp)


class PruneTests(unittest.TestCase):
    def test_keeps_newest_n_dates_and_spares_undated_files(self):
        tmp = tempfile.mkdtemp()
        try:
            dates = ["2026-01-%02d" % d for d in range(1, 11)]
            for date in dates:
                for name in ("console-%s.html", "snapshot-%s.json", "summary-%s.md"):
                    open(os.path.join(tmp, name % date), "w").close()
            for name in ("console.html", "index.html", "latest.json", "series.json"):
                open(os.path.join(tmp, name), "w").close()

            scan.prune_dated_reports(tmp, 3, lambda *a: None)
            left = set(os.listdir(tmp))
            for date in dates[-3:]:
                self.assertIn("console-%s.html" % date, left)
            for date in dates[:-3]:
                self.assertNotIn("console-%s.html" % date, left)
                self.assertNotIn("snapshot-%s.json" % date, left)
                self.assertNotIn("summary-%s.md" % date, left)
            # 沒有日期的那幾個是每次掃描都要覆寫的正本，絕對不能被掃到。
            for name in ("console.html", "index.html", "latest.json", "series.json"):
                self.assertIn(name, left)
        finally:
            shutil.rmtree(tmp)

    def test_zero_means_no_pruning(self):
        tmp = tempfile.mkdtemp()
        try:
            open(os.path.join(tmp, "summary-2020-01-01.md"), "w").close()
            scan.prune_dated_reports(tmp, 0, lambda *a: None)
            self.assertIn("summary-2020-01-01.md", os.listdir(tmp))
        finally:
            shutil.rmtree(tmp)


class ScheduleTests(unittest.TestCase):
    """排程的間隔判定。這一類錯誤不會讓任何東西變紅，只會讓掃描默默不跑。"""

    def _run_wrapper(self, stamp_epoch):
        import subprocess
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "scripts"))
            os.makedirs(os.path.join(tmp, "data"))
            shutil.copy(os.path.join(BASE_DIR, "scripts", "run_scan.sh"),
                        os.path.join(tmp, "scripts", "run_scan.sh"))
            with open(os.path.join(tmp, "data", ".last_scan"), "w") as handle:
                handle.write("%d\n" % stamp_epoch)
            # LRC_PYTHON=true 讓包裝腳本走完流程但不真的跑掃描。
            proc = subprocess.run(
                ["bash", os.path.join(tmp, "scripts", "run_scan.sh")],
                env={**os.environ, "LRC_PYTHON": "true"},
                capture_output=True, text=True, timeout=60)
            return proc.returncode, proc.stdout + proc.stderr
        finally:
            shutil.rmtree(tmp)

    def test_default_interval_is_daily(self):
        with open(os.path.join(BASE_DIR, "scripts", "run_scan.sh"), encoding="utf-8") as handle:
            self.assertIn("${LRC_INTERVAL_DAYS:-1}", handle.read())

    def test_same_day_is_skipped_with_its_own_exit_code(self):
        """跳過必須跟「跑完了」分得開：跳過時 reports/ 是上一次的內容，
        呼叫端若當成本次結果，昨天的引信會被重新當成新的。"""
        import time
        code, output = self._run_wrapper(int(time.time()))
        self.assertEqual(code, 5)
        self.assertIn("本次跳過", output)

    def test_schedule_jitter_does_not_lose_a_day(self):
        """GitHub 的排程實測延遲 1:26～2:45（跨度 1 小時 18 分），兩次實際執行
        會相隔 22～26 小時。門檻若是整整 24 小時，只要前一次晚、這一次早就會
        整天不掃——而且不會有任何東西變紅，只是默默沒資料。"""
        import time
        now = int(time.time())
        for hours in (23, 22, 19):
            code, output = self._run_wrapper(now - hours * 3600)
            self.assertNotEqual(code, 5, "距上次 %d 小時不該被跳過" % hours)
            self.assertIn("開始掃描", output)

    def test_far_too_soon_is_still_skipped(self):
        """容許提前 6 小時，不是不管間隔。"""
        import time
        code, _ = self._run_wrapper(int(time.time()) - 10 * 3600)
        self.assertEqual(code, 5)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        path = os.path.join(BASE_DIR, ".github", "workflows", "scan.yml")
        with open(path, encoding="utf-8") as handle:
            self.text = handle.read()

    def test_scheduled_after_us_close(self):
        """22:00 UTC ＝ 美東 17:00／18:00，夏令冬令都在收盤與 H.15 發佈之後。
        12:00 UTC 是開盤前，抓到的永遠是前一個交易日。"""
        self.assertIn('cron: "0 22 * * *"', self.text)

    def test_issue_is_gated_on_new_wires_not_on_exit_code(self):
        self.assertIn("steps.delta.outputs.new_count", self.text)
        self.assertNotIn("steps.scan.outputs.status == '10'", self.text)

    def test_delta_step_is_skipped_when_the_scan_did_not_run(self):
        """間隔沒到時 reports/latest.json 是上一次的，不能拿來判斷「新引信」。"""
        self.assertIn("steps.scan.outputs.ran == '1'", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
