"""流動性與尾部風險自動掃描。

模組分工：
  fetch     — 抓 FRED／Yahoo／NY Fed，含重試、快取與離線回退
  series    — 時間序列與衍生度量（單日變動、回落、連續天數…）
  history   — 本地歷史累積（上游刪資料也不怕）
  expr      — 規則檔用的安全表達式求值器
  evaluate  — 規則 → snapshot（燈號、引信、階梯、傳導鏈）
  diff      — 兩次掃描之間的變化
  render    — snapshot → HTML 控制盤
  notify    — 可選的 webhook 推播
"""

__version__ = "1.0.0"
