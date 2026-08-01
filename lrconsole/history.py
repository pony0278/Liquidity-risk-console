"""歷史序列的本地保存。

存在的理由：FRED 對部分系列（例如 BAMLH0A0HYM2）只保留近三年觀測值，
Yahoo 的 chart API 也只給有限區間。腳本每次掃描都把抓到的點併進
data/history.csv，久了自己就有一份不會被上游刪掉的歷史。
"""

import csv
import json
import os

from .series import Series

__all__ = ["load_history", "save_history", "load_json", "save_json"]


def load_history(path):
    if not os.path.exists(path):
        return {}
    series_map = {}
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = (row.get("key") or "").strip()
            date = (row.get("date") or "").strip()
            raw = (row.get("value") or "").strip()
            if not key or len(date) != 10 or not raw:
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            series_map.setdefault(key, []).append((date, value))
    return {key: Series(points) for key, points in series_map.items()}


def save_history(path, series_map, keep=None):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "key", "value"])
        for key in sorted(series_map):
            series = series_map[key]
            if keep:
                series = series.trimmed(keep)
            for date, value in series.points:
                writer.writerow([date, key, repr(round(value, 6))])
    os.replace(tmp, path)


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def save_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    os.replace(tmp, path)
