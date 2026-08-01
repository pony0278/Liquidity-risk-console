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

    def test_no_unresolved_placeholders(self):
        self.assertNotIn("__TILE_KEYS__", self.html)
        self.assertNotIn("__TILE_WHY__", self.html)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
