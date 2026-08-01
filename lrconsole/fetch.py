"""資料抓取：FRED、Yahoo Finance、NY Fed 公開市場操作。

只用標準函式庫，沒有第三方相依。每個來源都有重試與退避；抓失敗不會讓
整次掃描中斷，而是記成 FetchResult(ok=False) 由上層決定要不要沿用歷史值。
"""

import csv
import gzip
import io
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .series import Series

__all__ = ["FetchResult", "Fetcher"]

USER_AGENT = "liquidity-risk-console/1.0 (+https://github.com/pony0278/liquidity-risk-console)"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3


class FetchResult:
    def __init__(self, key, series=None, ok=True, provider=None, detail=""):
        self.key = key
        self.series = series if series is not None else Series()
        self.ok = ok
        self.provider = provider
        self.detail = detail

    def __repr__(self):
        return "FetchResult(%s, ok=%s, n=%d, %s)" % (
            self.key, self.ok, len(self.series), self.provider)


def _http_get(url, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, headers=None):
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "gzip",
    }
    if headers:
        request_headers.update(headers)

    last_error = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            context = ssl.create_default_context()
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - 任何網路層問題都值得重試
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError("GET 失敗 %s：%s" % (url, last_error))


def _parse_float(text):
    text = (text or "").strip()
    if text in ("", ".", "NA", "null", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


class Fetcher:
    def __init__(self, cache_dir=None, start_date=None, timeout=DEFAULT_TIMEOUT,
                 retries=DEFAULT_RETRIES, offline=False, log=None):
        self.cache_dir = cache_dir
        self.start_date = start_date or (datetime.now(timezone.utc) - timedelta(days=800)).strftime("%Y-%m-%d")
        self.timeout = timeout
        self.retries = retries
        self.offline = offline
        self.log = log or (lambda *a, **k: None)
        self.fred_api_key = os.environ.get("FRED_API_KEY", "").strip()
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # ---------- 快取 ----------

    def _cache_path(self, name):
        if not self.cache_dir:
            return None
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        return os.path.join(self.cache_dir, safe)

    def _cache_write(self, name, text):
        path = self._cache_path(name)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            self.log("  快取寫入失敗 %s：%s" % (name, exc))

    def _cache_read(self, name):
        path = self._cache_path(name)
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    def _get(self, url, cache_name):
        """抓網路，失敗時回退到快取（並標記 stale）。"""
        if self.offline:
            cached = self._cache_read(cache_name)
            if cached is None:
                raise RuntimeError("offline 模式且無快取：%s" % cache_name)
            return cached, True
        try:
            text = _http_get(url, timeout=self.timeout, retries=self.retries)
            self._cache_write(cache_name, text)
            return text, False
        except Exception as exc:  # noqa: BLE001
            cached = self._cache_read(cache_name)
            if cached is not None:
                self.log("  %s 抓取失敗（%s），改用快取" % (cache_name, exc))
                return cached, True
            raise

    # ---------- 各來源 ----------

    def fetch(self, key, source):
        provider = source.get("provider")
        handler = {
            "fred": self._fetch_fred,
            "yahoo": self._fetch_yahoo,
            "nyfed_repo": self._fetch_nyfed_repo,
        }.get(provider)
        if handler is None:
            return FetchResult(key, ok=False, provider=provider, detail="未知的 provider")
        try:
            series, stale = handler(source)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(key, ok=False, provider=provider, detail=str(exc))
        # 丟掉未來日期的觀測。FRED 的政策利率（IORB）會往前補到當期結尾，
        # Yahoo 也會給下一場交易時段的空白 bar；留著會讓「資料截至」變成
        # 未來日期，也可能被當成最新值去算單日變動。
        cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        series = Series([(d, v) for d, v in series.points if d <= cutoff])

        scale = float(source.get("scale", 1.0))
        if scale != 1.0:
            series = Series([(d, v * scale) for d, v in series.points])
        if not series:
            return FetchResult(key, ok=False, provider=provider, detail="來源回傳空序列")
        return FetchResult(key, series=series, ok=True, provider=provider,
                           detail="快取（可能過期）" if stale else "")

    def _fetch_fred(self, source):
        series_id = source["id"]
        if self.fred_api_key:
            url = ("https://api.stlouisfed.org/fred/series/observations?"
                   + urllib.parse.urlencode({
                       "series_id": series_id,
                       "api_key": self.fred_api_key,
                       "file_type": "json",
                       "observation_start": self.start_date,
                   }))
            text, stale = self._get(url, "fred_api_%s.json" % series_id)
            payload = json.loads(text)
            points = []
            for obs in payload.get("observations", []):
                value = _parse_float(obs.get("value"))
                if value is not None:
                    points.append((obs["date"], value))
            return Series(points), stale

        url = ("https://fred.stlouisfed.org/graph/fredgraph.csv?"
               + urllib.parse.urlencode({"id": series_id, "cosd": self.start_date}))
        text, stale = self._get(url, "fred_%s.csv" % series_id)
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 2:
            raise RuntimeError("FRED CSV 格式異常：%s" % series_id)
        points = []
        for row in reader:
            if len(row) < 2:
                continue
            value = _parse_float(row[1])
            if value is not None and len(row[0]) == 10:
                points.append((row[0], value))
        return Series(points), stale

    def _fetch_yahoo(self, source):
        symbol = source["id"]
        quoted = urllib.parse.quote(symbol, safe="")
        params = urllib.parse.urlencode({
            "range": source.get("range", "2y"),
            "interval": "1d",
            "includePrePost": "false",
        })
        last_error = None
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            url = "https://%s/v8/finance/chart/%s?%s" % (host, quoted, params)
            try:
                text, stale = self._get(url, "yahoo_%s.json" % symbol)
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        else:
            raise RuntimeError("Yahoo 兩個節點都失敗：%s" % last_error)

        payload = json.loads(text)
        chart = (payload.get("chart") or {})
        if chart.get("error"):
            raise RuntimeError("Yahoo 回報錯誤：%s" % chart["error"])
        results = chart.get("result") or []
        if not results:
            raise RuntimeError("Yahoo 無資料：%s" % symbol)
        result = results[0]
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        points = []
        for stamp, close in zip(stamps, closes):
            if close is None:
                continue
            date = datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d")
            points.append((date, float(close)))
        return Series(points), stale

    def _fetch_nyfed_repo(self, source):
        """NY Fed 附買回操作（含 SRF）。回傳每日成交總額，單位 $B。

        API 沒有把 SRF 單獨標成一個欄位，這裡的口徑是「當日所有 repo 操作
        的 totalAmtAccepted 加總」。平時是零，季底／報稅日會有關鍵日使用，
        判讀時請把這格當成「Fed 有沒有在放錢給市場」而不是純 SRF。
        """
        url = ("https://markets.newyorkfed.org/api/rp/repo/all/results/"
               "lastTwoWeeks.json")
        text, stale = self._get(url, "nyfed_repo.json")
        payload = json.loads(text)
        # 回傳最外層的 key 曾經是 repo，也出現過 rp／operations 直接在頂層；
        # 這裡不賭 key 名稱，找第一個帶 operations 的容器。
        operations = payload.get("operations")
        if operations is None:
            for value in payload.values():
                if isinstance(value, dict) and isinstance(value.get("operations"), list):
                    operations = value["operations"]
                    break
        if operations is None:
            raise RuntimeError("NY Fed 回應格式不符（找不到 operations）")

        totals = {}
        for op in operations:
            date = op.get("operationDate") or op.get("auctionDate")
            amount = op.get("totalAmtAccepted")
            if not date or amount is None:
                continue
            parsed = _parse_float(str(amount).replace(",", ""))
            if parsed is None:
                continue
            totals[date[:10]] = totals.get(date[:10], 0.0) + parsed / 1e9
        # 沒有操作的日子等於零使用量，補零才看得出「連續幾日有人用」。
        if totals:
            start = min(totals)
            end = max(totals)
            cursor = datetime.strptime(start, "%Y-%m-%d")
            stop = datetime.strptime(end, "%Y-%m-%d")
            while cursor <= stop:
                if cursor.weekday() < 5:
                    totals.setdefault(cursor.strftime("%Y-%m-%d"), 0.0)
                cursor += timedelta(days=1)
        return Series(list(totals.items())), stale
