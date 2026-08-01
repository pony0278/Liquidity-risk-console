# 流動性與尾部風險監測 — 自動掃描

把原本手工維護的那張「系統壓力盤」變成一支腳本：**每 3 天自動抓資料 →
套用閾值與傳導鏈規則 → 重畫同一張控制盤 → 告訴你這三天有什麼在動。**

只用 Python 3 標準函式庫，沒有任何第三方套件要裝。

```
scan.py                     主程式
lrconsole/                  fetch / series / expr / evaluate / diff / render / notify
config/indicators.json      指標、資料源、閾值帶
config/rules.json           引信、升級階梯、傳導鏈、擁擠層
templates/console.css       版面（與手工版同一份）
scripts/run_scan.sh         macOS／Linux 排程包裝
scripts/run_scan.bat        Windows 批次檔
scripts/install_cron.sh     一鍵安裝 cron
scripts/install_task_windows.ps1   一鍵安裝 Windows 工作排程
scripts/com.lrc.scan.plist  macOS launchd 版
.github/workflows/scan.yml  GitHub Actions 版（不需要自己的電腦開機）
tests/                      離線測試（不連網）
```

---

## 1. 快速開始

```bash
python3 scan.py --self-test    # 檢查設定檔與所有規則表達式，不連網
python3 scan.py                # 完整掃描
open reports/index.html        # Linux 用 xdg-open，Windows 直接雙擊
```

第一次跑會抓約兩年的歷史，之後每次只是增量更新。

---

## 2. 排成每 3 天自動跑

### 為什麼不用 cron 的 `*/3`

`0 8 */3 * *` 的意思是「每月的 1, 4, 7 … 31 號」，月底到月初會出現只隔
一天的縫；而且電腦當下沒開機就整次跳過。

這裡的做法是：**排程每天叫一次，由包裝腳本的戳記檔（`data/.last_scan`）
決定要不要真的掃。** 間隔穩定是 3 天，漏跑也會在下一次補上。想改間隔就設
`LRC_INTERVAL_DAYS`。

### macOS / Linux

```bash
chmod +x scripts/*.sh
./scripts/install_cron.sh          # 每天 08:10 叫一次，實際每 3 天掃一次
./scripts/install_cron.sh --remove # 移除

./scripts/run_scan.sh --force      # 手動立刻跑一次
```

macOS 上 cron 常被權限擋（需要「完全磁碟取用權限」），比較可靠的是 launchd：

```bash
sed "s|__REPO_DIR__|$(pwd)|g" scripts/com.lrc.scan.plist > ~/Library/LaunchAgents/com.lrc.scan.plist
launchctl load -w ~/Library/LaunchAgents/com.lrc.scan.plist
```

launchd 的好處是**睡眠中錯過的時段，醒來會補跑**。

### Windows

```bat
scripts\run_scan.bat --force
```

排進工作排程器（原生支援「每 N 天」）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task_windows.ps1
powershell -ExecutionPolicy Bypass -File scripts\install_task_windows.ps1 -Remove
```

`-StartWhenAvailable` 已經開啟，關機錯過的話開機後會補跑。

### GitHub Actions（不必自己的電腦開機）

`.github/workflows/scan.yml` 已經設好：每天叫一次、實際每 3 天掃一次，
把 `reports/` 與 `data/history.csv` 提交回 repo，並在有引信觸發時自動開 issue。

可選的 secrets：

| Secret | 用途 |
| --- | --- |
| `FRED_API_KEY` | 走 FRED 官方 API（比公開 CSV 端點穩定）。[免費申請](https://fredaccount.stlouisfed.org/apikeys) |
| `LRC_WEBHOOK_URL` | Slack／Discord webhook，有變化時推播摘要 |

---

## 3. 產出什麼

| 檔案 | 內容 |
| --- | --- |
| `reports/index.html` | 最新的控制盤（永遠是這個檔名，書籤不會失效） |
| `reports/console-YYYY-MM-DD.html` | 當次存檔 |
| `reports/summary-YYYY-MM-DD.md` | 純文字摘要，適合貼進筆記或推播 |
| `reports/latest.json` · `snapshot-YYYY-MM-DD.json` | 結構化判定，供下次 diff 或自行分析 |
| `data/history.csv` | 累積的歷史序列 |
| `logs/scan-*.log` | 每次執行的完整輸出（保留最近 40 份） |

HTML 版面與手工版一致，另外多了一段 **「本次變化」**：階梯升降、引信開關、
傳導鏈節點推進、以及超過門檻的數值變動——自動掃描的價值在標出**移動**，
不是重印水位。

### 離開碼

排程器可以直接吃這個碼決定要不要吵你：

| 碼 | 意義 |
| --- | --- |
| 0 | 正常 |
| 10 | 有升級觸發器或盤中引信成立 |
| 20 | 部分指標抓取失敗（判定仍完成，但有缺口） |
| 30 | 兩者都有 |
| 1 | 掃描本身失敗 |

---

## 4. 資料源

| 來源 | 用途 | 備註 |
| --- | --- | --- |
| FRED | SOFR、IORB、OAS、殖利率、準備金、TGA、RRP、失業金… | 有 `FRED_API_KEY` 走 API，否則走公開 CSV |
| Yahoo Finance | VIX、VVIX、MOVE、^TNX／^TYX、USD/JPY、DXY、WTI、NDX／SOX／N225 | FRED 沒有 MOVE／VVIX，只能走這裡 |
| NY Fed Markets API | 附買回操作金額（含 SRF） | 見下方口徑說明 |

指標可以有多個來源，第一個成功的勝出（例如 30Y 先試 Yahoo 的 `^TYX`
取當日盤中，失敗才退回 FRED 的 `DGS30`）。

### 已知的資料坑（都已經處理，但值得知道）

- **FRED 只留近 3 年**：`BAMLH0A0HYM2` 等系列自 2026/4 起只保留近三年觀測值。
  腳本每次都把抓到的點併進 `data/history.csv`，久了就有一份上游刪不掉的歷史。
- **MOVE 不在 FRED**：改抓 Yahoo 的 `^MOVE`。
- **SRF 沒有單獨欄位**：NY Fed API 給的是每筆 repo 操作，腳本把當日
  `totalAmtAccepted` 加總。所以這格的正確讀法是「Fed 今天有沒有在放錢給市場」，
  而不是純 SRF；季底與報稅日的關鍵日使用屬正常。
- **期限溢價用 Kim-Wright 不是 ACM**：NY Fed 的 ACM 只提供 xls，腳本改用 FRED 的
  `THREEFYTP10`。口徑不同但方向一致。
- **抓不到 ≠ 沒問題**：任何規則只要引用到缺資料的變數，一律判為「無法判定」
  而不是「未觸發」，報表上會標成 `NO DATA`／`STALE`。這是刻意的——
  抓不到 SOFR 被靜靜當成「水管沒問題」是最危險的誤判方向。

---

## 5. 自己改閾值與規則

判定邏輯全部在 `config/`，改完跑 `python3 scan.py --self-test` 就會告訴你
有沒有寫錯（拼錯的變數名、指到不存在的指標、語法錯誤都會被抓出來）。

### 閾值帶

```json
"bands": [
  { "status": "ok",    "max": 350, "label": "寬鬆" },
  { "status": "watch", "max": 450, "label": "轉折觀察" },
  { "status": "press", "max": 600, "label": "減倉區" },
  { "status": "alarm",              "label": "信用事件" }
]
```

由上而下比對，第一個吻合者勝出（`min` 含、`max` 不含）。想改成看變動而非
水位，用 `change_bands`（例如準備金看 4 期斜率、TGA 看週變動）。

沒有 `bands` 也沒有 `change_bands` 的指標（USD/JPY、DXY、2Y）會顯示成
**僅記錄**的灰燈——它們是對照用的讀數，硬給一個綠燈會讓人誤以為已經檢查過了。

### 表達式變數

引信、階梯、傳導鏈節點、擁擠層都用同一套表達式。每個指標 `<key>` 自動提供：

| 變數 | 意義 |
| --- | --- |
| `<key>` | 最新值 |
| `<key>_prev` | 前一期 |
| `<key>_d1` `_d5` `_d20` `_d60` | n 期前到現在的變動 |
| `<key>_pct1` `_pct5` `_pct20` `_pct60` | 同上，百分比 |
| `<key>_d1_bps` `_d5_bps` `_d20_bps` | 單位為 `%` 的指標另有 bps 版 |
| `<key>_dd60` `<key>_ru60` | 自 60 期高點的回落／低點的反彈（%） |
| `<key>_pos_streak` | 連續為正的期數（例：`sofr_iorb_pos_streak >= 3`） |
| `<key>_up_streak` | 連續上升的期數 |
| `<key>_min60` `_max60` `_min250` `_max250` | 區間高低 |
| `<key>_n` | 觀測點數 |

「期」是**觀測值**不是日曆日：日資料的 `_d1` 是上一個交易日，週資料的
`_d1` 是上週。這正是判讀時要的口徑。

表達式只允許算術、比較、`and`／`or`／`not` 與 `abs`／`min`／`max`／`round`，
不是 `eval`，塞不進任何程式碼。

### 加一個新指標

```json
{
  "key": "my_metric",
  "label": "我的指標",
  "tier": 4,
  "unit": "bps",
  "decimals": 0,
  "freq": "每日",
  "source_label": "FRED SERIESID",
  "sources": [{ "provider": "fred", "id": "SERIESID", "scale": 100 }],
  "bands": [{ "status": "ok", "max": 100 }, { "status": "alarm" }],
  "note": "為什麼要看這格"
}
```

衍生指標用 `"derived": "(a - b) * 100"`（在各成分的日期交集上逐日計算，
所以衍生指標同樣有完整歷史與單日變動），或 `"derived_diff": "other_key"`
取逐期差分。`"hidden": true` 的指標只用來算別的東西，不會出現在表上。

---

## 6. 測試

```bash
python3 -m unittest discover -s tests -v
```

26 個測試，完全離線：用合成的「平靜」與「壓力」兩套歷史跑完整條管線，
檢查階梯判定、引信觸發、傳導鏈推進、HTML 標籤是否收好、以及
**缺資料時是否正確判為「無法判定」而不是「未觸發」**。

想看壓力情境長什麼樣：

```bash
mkdir -p /tmp/lrc/data && python3 - <<'PY'
import sys; sys.path[:0] = ['.', 'tests']
from fixtures import STRESS, write_history
import scan
write_history('/tmp/lrc/data/history.csv', STRESS)
scan.main(['--data-dir','/tmp/lrc/data','--out-dir','/tmp/lrc/reports','--no-fetch','--notify','never'])
PY
```

---

## 7. 常用參數

```
--offline          不連網，只用快取與 history.csv 重算
--no-fetch         完全跳過抓取，純粹重畫報表
--self-test        只檢查設定檔與表達式
--notify always|auto|never
--history-days N   history.csv 每個系列保留的觀測筆數（預設 1500）
--timeout / --retries
```

---

## 免責

本工具為個人監測用的指標與傳導路徑整理，數值取自公開資料源並可能有時間
落差或修正，**不構成投資建議**。閾值需隨 regime 校準；判定為「重定價」
不代表不會升級，只代表升級所需的條件目前尚未成立。
