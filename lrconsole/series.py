"""時間序列容器與衍生度量。

序列一律是 [(YYYY-MM-DD, float), ...] 依日期遞增，且不含缺值。
所有「第 n 期前」都是以「觀測值」計數而非日曆日——週資料的 _d1 就是
上週，日資料的 _d1 就是上一個交易日，這正是判讀時要的口徑。
"""

__all__ = ["Series", "build_metrics"]


class Series:
    def __init__(self, points=None):
        self.points = sorted(points or [], key=lambda p: p[0])

    def __len__(self):
        return len(self.points)

    def __bool__(self):
        return bool(self.points)

    @property
    def dates(self):
        return [d for d, _ in self.points]

    @property
    def values(self):
        return [v for _, v in self.points]

    def as_dict(self):
        return dict(self.points)

    def latest(self):
        return self.points[-1][1] if self.points else None

    def latest_date(self):
        return self.points[-1][0] if self.points else None

    def nth_back(self, n):
        """n 期前的值（n=0 為最新）。不足則 None。"""
        if len(self.points) <= n:
            return None
        return self.points[-1 - n][1]

    def change(self, n):
        latest, prior = self.latest(), self.nth_back(n)
        if latest is None or prior is None:
            return None
        return latest - prior

    def pct_change(self, n):
        latest, prior = self.latest(), self.nth_back(n)
        if latest is None or prior is None or prior == 0:
            return None
        return (latest / prior - 1.0) * 100.0

    def window(self, n):
        return self.values[-n:] if self.points else []

    def drawdown(self, n):
        """自最近 n 期高點的回落百分比（負值）。"""
        window = self.window(n)
        if len(window) < 2:
            return None
        peak = max(window)
        if peak == 0:
            return None
        return (window[-1] / peak - 1.0) * 100.0

    def runup(self, n):
        window = self.window(n)
        if len(window) < 2:
            return None
        trough = min(window)
        if trough == 0:
            return None
        return (window[-1] / trough - 1.0) * 100.0

    def positive_streak(self):
        """由最新往回數，連續 > 0 的期數。"""
        streak = 0
        for _, v in reversed(self.points):
            if v > 0:
                streak += 1
            else:
                break
        return streak

    def rising_streak(self):
        """由最新往回數，連續較前一期上升的期數。"""
        streak = 0
        for i in range(len(self.points) - 1, 0, -1):
            if self.points[i][1] > self.points[i - 1][1]:
                streak += 1
            else:
                break
        return streak

    def merged_with(self, other):
        """以 other 覆蓋同日期的值，回傳新序列（用於歷史檔 + 新抓資料）。"""
        combined = self.as_dict()
        combined.update(other.as_dict())
        return Series(list(combined.items()))

    def trimmed(self, keep):
        return Series(self.points[-keep:]) if keep and len(self.points) > keep else self


_WINDOWS = (1, 5, 20, 60)


def build_metrics(series_map, unit_map=None):
    """把 {key: Series} 攤平成表達式可用的變數表。

    每個 key 產生：
      k, k_prev, k_date, k_d1/_d5/_d20/_d60, k_pct1/_pct5/_pct20/_pct60,
      k_dd60, k_ru60, k_pos_streak, k_up_streak, k_min60, k_max60,
      k_min250, k_max250, k_n
    單位為 % 的指標另外提供 k_d1_bps／k_d5_bps／k_d20_bps（×100）。
    """
    unit_map = unit_map or {}
    metrics = {}
    for key, series in series_map.items():
        metrics[key] = series.latest()
        metrics[key + "_prev"] = series.nth_back(1)
        metrics[key + "_n"] = len(series)
        for n in _WINDOWS:
            change = series.change(n)
            metrics["%s_d%d" % (key, n)] = change
            metrics["%s_pct%d" % (key, n)] = series.pct_change(n)
            if unit_map.get(key) == "%":
                metrics["%s_d%d_bps" % (key, n)] = None if change is None else change * 100.0
        metrics[key + "_dd60"] = series.drawdown(60)
        metrics[key + "_ru60"] = series.runup(60)
        # 沒有資料時 streak 必須是 None 而非 0——否則「抓不到 SOFR」會被
        # 當成「水管沒問題」，那是最危險的誤判方向。
        metrics[key + "_pos_streak"] = series.positive_streak() if series else None
        metrics[key + "_up_streak"] = series.rising_streak() if series else None
        window60 = series.window(60)
        metrics[key + "_min60"] = min(window60) if window60 else None
        metrics[key + "_max60"] = max(window60) if window60 else None
        window250 = series.window(250)
        metrics[key + "_min250"] = min(window250) if window250 else None
        metrics[key + "_max250"] = max(window250) if window250 else None
    return metrics
