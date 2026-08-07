# 流動性與尾部風險監測 — 自動掃描

把原本手工維護的那張「系統壓力盤」變成一支腳本：**每天自動抓資料 →
套用閾值與傳導鏈規則 → 重畫同一張控制盤 → 告訴你從上次到現在有什麼在動。**

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

## 2. 排成自動跑

### 為什麼是每天

非隱藏指標裡絕大多數是每日收盤，而 `config/rules.json` 的引信判定的
就是**單日**變動（USD/JPY 單日 −2%、10Y／30Y 單日 +10bps）。間隔拉成 N 天
等於每 N 個交易日只看 1 個收盤，落在被跳過那幾天的尖刺永遠不會被評估到。
慢燈（TGA、準備金、初領失業金）一週才動一次，每天掃它只是每天確認「還沒
發佈」，沒有代價；公開 repo 的 Actions 分鐘數也是免費無上限的。

想改成週報式的節奏就設 `LRC_INTERVAL_DAYS=7`（搭配週四跑，週三的 H.4.1
與週四的失業金都已經出來）。

### 為什麼不用 cron 的 `*/N`

`0 8 */3 * *` 的意思是「每月的 1, 4, 7 … 31 號」，月底到月初會出現只隔
一天的縫；而且電腦當下沒開機就整次跳過。

這裡的做法是：**排程每天叫一次，由包裝腳本的戳記檔（`data/.last_scan`）
決定要不要真的掃。** 間隔就是 `LRC_INTERVAL_DAYS`（預設 1），漏跑也會在
下一次補上。

門檻不是「整整 `N × 24` 小時」，而是再減去 `LRC_INTERVAL_SLACK_HOURS`
（預設 6）。GitHub 的排程實測會延遲 1 小時 26 分到 2 小時 45 分不等，兩次
實際執行因此相隔 22～26 小時；門檻抓死 24 小時的話，只要前一次晚、這一次
早，就會被判成「還沒到」而整天不掃，而且不會有任何東西變紅。

### macOS / Linux

```bash
chmod +x scripts/*.sh
./scripts/install_cron.sh          # 每天 08:10 叫一次
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

`.github/workflows/scan.yml` 已經設好：每天 22:00 UTC 掃一次（＝美東
17:00／18:00，夏令冬令都在美股收盤與 H.15 殖利率發佈之後），把 `reports/`
與 `data/history.csv` 提交回 repo，並在**有新引信亮起時**自動開 issue。

「新」是關鍵：判斷依據是引信集合有沒有變（`latest.json` 的
`tripwire_delta`），不是「現在有沒有引信亮著」。否則一根連續亮 30 天的引信
會開 30 張 issue、推 30 次 webhook，講的卻是同一件事。

可選的 secrets：

| Secret | 用途 |
| --- | --- |
| `FRED_API_KEY` | 走 FRED 官方 API（比公開 CSV 端點穩定）。[免費申請](https://fredaccount.stlouisfed.org/apikeys) |
| `LRC_WEBHOOK_URL` | Slack／Discord webhook，有變化時推播摘要 |

---

## 2.5 公開網頁（GitHub Pages）

`publish` job 會把 `reports/` 發佈到 GitHub Pages，網址是
`https://<帳號>.github.io/<repo>/`，每次掃描後自動更新。

第一次跑如果卡在權限，到 **Settings → Pages → Source** 手動選 `GitHub Actions`。

頁面分兩層：

| 網址 | 給誰看 |
| --- | --- |
| `/`（index.html） | **公開版總覽**——先講結論（第幾階、為什麼），再給六格關鍵指標，每格附閾值軌道、火花線與白話說明 |
| `/console.html` | **完整版控制盤**——全部指標、四條傳導鏈、擁擠層，密度優先 |
| `/latest.json` · `/series.json` | 結構化資料，可自行接走 |

### 「動態」是指什麼

資料每天更新一次，所以頁面**不是**即時報價。動態的部分是：頁面載入時
會用 `fetch` 重抓 `latest.json` 與 `series.json`（帶時間戳避開快取），因此
即使瀏覽器或 CDN 快取了 HTML，看到的仍是最新一次掃描；開著的分頁每 15
分鐘也會自己重抓一次。

`fetch` 失敗時（用 `file://` 直接開、或離線）會退回建置時內嵌在頁面裡的
那份快照，頁面永遠不會是空白的。

想要真正的即時盤中報價，需要付費的行情 API 加上一個代理伺服器——瀏覽器直接
打 Yahoo／FRED 會被 CORS 擋下來。這套的定位是**趨勢與門檻距離**，不是報價機。

### 可讀性上的取捨

- **嚴重度不只靠顏色。** 警示（琥珀 `#9E6F1E`）與明確壓力（橘 `#B04E1E`）在
  紅綠色盲下的 ΔE 只有 3.0，等於同一個顏色。所以嚴重度用 **1–4 格的方塊 +
  文字標籤**表達，顏色只做強化。
- **講「離門檻還有多遠」而不是只給數字。** 「HY OAS 284」對外人沒有意義，
  「離 350 還有 66bps」才有。每格都畫出閾值軌道與現在的位置。
- **資料太舊會標出來。** 過期的指標會標紅字提示，避免有人把兩週前的 MOVE
  當成當前值讀。門檻跟著各指標宣告的 `freq` 走（每日 7 天、每週 14、每月 75），
  不是同一把尺——月頻的非農用日頻的尺去量會天天喊過期，天天亮的警告等於沒有警告。

---

## 3. 產出什麼

| 檔案 | 內容 |
| --- | --- |
| `reports/index.html` | **公開版總覽**：先講結論，給沒讀過原始文件的人看 |
| `reports/console.html` | **完整版控制盤**：密度優先，全部指標與傳導鏈細節 |
| `reports/series.json` | 火花線用的精簡序列（近 180 個觀測） |
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
| 5 | **`scripts/run_scan.sh` 專用**：間隔還沒到，這次沒掃（`scan.py` 不會回這個碼） |

碼 10 是「現在有引信亮著」，不是「剛剛亮起來」。要判斷後者請看
`reports/latest.json` 的 `tripwire_delta`：

```json
"tripwire_delta": {"new": [...], "cleared": [...], "ongoing": [...]}
```

碼 5 的時候 `reports/` 還是上一次的內容，別把它當成本次結果。

---

## 4. 資料源

| 來源 | 用途 | 備註 |
| --- | --- | --- |
| FRED | SOFR、IORB、OAS、殖利率、準備金、TGA、RRP、失業金… | 有 `FRED_API_KEY` 走 API，否則走公開 CSV |
| Yahoo Finance | VIX、VVIX、MOVE、^TNX／^TYX、USD/JPY、DXY、WTI、NDX／SOX／N225 | FRED 沒有 MOVE／VVIX，只能走這裡 |
| NY Fed Markets API | 附買回操作金額（含 SRF） | 見下方口徑說明 |

指標可以有多個來源，**第一個「成功而且沒過期」的勝出**（例如 30Y 先試
Yahoo 的 `^TYX` 取當日盤中，失敗或停更才退回 FRED 的 `DGS30`）。

只看「成不成功」是不夠的：Yahoo 的 `^MOVE` 會正常回應 200、給滿滿兩年的
資料，只是最後一筆停在三週前。那在「第一個成功的就用」之下算成功，備援
來源永遠不會被叫到，等於沒加。全部來源都過期時，取其中最新的那個。

### 已知的資料坑（都已經處理，但值得知道）

- **FRED 只留近 3 年**：`BAMLH0A0HYM2` 等系列自 2026/4 起只保留近三年觀測值。
  腳本每次都把抓到的點併進 `data/history.csv`，久了就有一份上游刪不掉的歷史。
- **MOVE 不在 FRED，而且 Yahoo 那條會整段停更**：實測停在 7/17 超過三週，
  期間 API 照常回 200。沒有同尺標的第二來源可用，所以備援是另一格指標
  （`vxtlt`，Cboe 的 TLT 波動率）。它**刻意不接進任何引信、傳導鏈或擁擠層**
  ——尺標不同的替代品拿去餵原本為 MOVE 校準的閾值，比沒有資料更危險。
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
--notify always|auto|never（auto＝引信集合有變或有 alert／warn 級變化才推）
--history-days N   history.csv 每個系列保留的觀測筆數（預設 1500）
--keep-daily N     reports/ 裡帶日期的存檔保留幾天份（預設 60，0＝不清理）
--timeout / --retries
```

每次掃描會多出 `console-DATE.html`、`snapshot-DATE.json`、`summary-DATE.md`
約 180 KB；每天掃就是一年 65 MB。`--keep-daily` 只清工作目錄與 Pages 站台，
git 物件庫裡的舊版本仍然留著（不改寫歷史）。真正的資料在
`data/history.csv`，這些存檔隨時可以用 `--rebuild` 重畫出來。

---

## 免責

本工具為個人監測用的指標與傳導路徑整理，數值取自公開資料源並可能有時間
落差或修正，**不構成投資建議**。閾值需隨 regime 校準；判定為「重定價」
不代表不會升級，只代表升級所需的條件目前尚未成立。
