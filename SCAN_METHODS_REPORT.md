# 掃描條件方法（U‑T‑E‑R‑E）

日期：2026-02-16

---

## 1) 資料來源與前處理（OHLC）

資料來源：Yahoo Finance（透過 `yfinance`）

資料粒度：日線（Open / High / Low / Close；Volume 如有就保留）

預設下載參數（CLI 預設）：

- `--period 5y`
- `--interval 1d`

快取（Cache）：

- 預設目錄：`data/cache/`
- 命名：`{Ticker}__{Period}__{Interval}.csv`（例如 `0700.HK__5y__1d.csv`）

Ticker 輸入（tickers `.txt`）：

- 會忽略空白行、以及以 `#` 開頭嘅註解行
- 會自動處理 UTF‑8 BOM（避免出現 `\ufeff0700.HK` 呢種壞 symbol）
- HK ticker 支援「只輸入數字」：例如 `700` 會 normalize 成 `0700.HK`

前處理（見 `src/utere/data_source.py::_basic_clean_ohlc`）：

- 以時間排序（DatetimeIndex）
- 移除 OHLC 欄位有 NaN 嘅列
- 移除重複 timestamp
- 移除明顯「壞 K 線」：
  - `High < Low` 會剔除
  - `Open` / `Close` 必須落喺 `[Low, High]` 範圍內，否則剔除

---

## 2) 掃描狀態（Pattern States）與輸出分類

系統喺同一隻股票嘅 DataFrame 入面，沿時間向前掃，尋找以下序列：

- **U**：符合「跌浪後首次反彈」定義嘅陽燭
- **T**：U 後某一日，第一次陰燭
- **E1**：U 後某一日，必須陽燭，並且向上突破 T 嘅 High
- **R**：E1 後某一日出現嘅「局部擺頂 / swing high」
- **E2**：R 後數日內出現嘅陽燭突破（High 高過 R 嘅 High）

掃描結果分三類：

- **COMPLETED**：搵到 `U‑T‑E1‑R‑E2`
- **UTE_incomplete**：已形成 `U‑T‑E1`，但喺指定視窗內搵唔到 R 或搵到 R 但搵唔到 E2（而且未被「新低重置」淘汰）
- **U_only**：搵到 U，但喺指定視窗內搵唔到 T 或搵唔到 E1（即未能形成 U‑T‑E1）

---

## 3) K 線定義（Bullish / Bearish）

- 陽燭（bullish）：`Close > Open`
- 陰燭（bearish）：`Close < Open`

（`Close == Open` 既唔當陽亦唔當陰；對某啲規則會造成 fail）

---

## 3.1) 分鐘圖 / Intraday 版本嘅解讀（One-day only）

當用分鐘圖（例如 `1m`, `5m`）去掃描，而且限制「只掃一個交易日」：

- 本系統所有規則其實都係 **per bar / per candle**（每一支 K 線）運作。
- 所以文件入面講嘅「某一日」「前一日」喺分鐘圖模式應該解讀為：
  - 「某一支陰燭 / 陽燭」
  - 「前一支 K 線（上一分鐘/上一個 bar）」
- 例如：
  - **T = 第一次陰燭** 係指「U 之後第一支陰燭」，唔係下一日。
  - `U Close > High(U-1)` 係指「U 收市高過上一支 bar 嘅 High」。

注意：如果只用單一交易日嘅分鐘圖資料，`drawdown filter`（需要長 lookback bars）通常唔適用；建議用嚴格 downtrend 模式。

---

## 4) 主要掃描參數（ScanConfig）— 預設值一覽

以下係 `src/utere/scanner.py::ScanConfig` 目前預設：

- `window_size = 30`：每個候選 U 之後嘅「最大掃描視窗」
  - 亦支援 multi-window 掃描：一次跑多個 `window_size`，再合併/去重（見下文 `--windows`）
- `u_lookback = 3`：用嚟定義 U 前底 / 或 downtrend 計算嘅 lookback（會被 clamp）
- `downtrend_bearish_min = 2`：嚴格 downtrend 模式下，近 3–5 棒內最少陰燭數
- `u_use_drawdown_filter = True`：U 的賣壓資格預設用「一年回撤」而非嚴格 3 棒下跌結構
- `u_drawdown_lookback = 252`：回撤 lookback（約 1 年交易日）
- `u_drawdown_min = 0.30`、`u_drawdown_max = 0.50`：回撤介乎 30%–50%
- `u_reset_on_new_low = True`：U 後如破「U 前底」則重置 / 淘汰（目前無 CLI 開關，屬 hard-coded 預設）
- `u_prev_must_be_bearish = True`：U 前一日必須係陰燭
- `u_confirm_close_gt_prev_high = True`：U 收市要高過前一日 High
- `u_confirm_close_gt_prev = False`：可選更嚴格：U 收市要高過前一日 Close（預設關）
- `max_r_bars = 10`：E1 後最多再睇 10 棒去搵 R
- `max_e2_bars = 3`：R 後最多再睇 3 棒去搵 E2
- `r_strict_next = False`：如果開啟，R 必須係 `E1+1`（預設關）

CLI 對應（見 `src/main.py`）：

- `--window` → `window_size`
- `--windows` → multi-window 掃描（例如 `20,50,100,200`），會覆蓋 `--window`
- `--min-window-support` → noise filter：只保留「至少出現於 N 個 window」嘅結果（預設 `1` = 不過濾）
- `--u-lookback` → `u_lookback`
- `--downtrend-bearish-min` → `downtrend_bearish_min`
- `--u-use-drawdown` / `--u-use-downtrend` → `u_use_drawdown_filter`
- `--u-drawdown-lookback` / `--u-drawdown-min` / `--u-drawdown-max`
- `--u-prev-must-be-bearish` / `--no-u-prev-must-be-bearish`
- `--u-confirm-close-gt-prev-high` / `--no-u-confirm-close-gt-prev-high`
- `--u-confirm-close-gt-prev` / `--no-u-confirm-close-gt-prev`
- `--max-r-bars` → `max_r_bars`
- `--max-e2-bars` → `max_e2_bars`
- `--r-strict-next` → `r_strict_next`

### 4.1) Multi-window 掃描（merge + de-dup + filter noise）

當用 `--windows 20,50,100,200`：

- 系統會對同一隻股票，用多個 `window_size` 分別掃描
- 然後將結果 **合併（merge）**、**去重（de-dup）**、再按時間排序
- `--min-window-support N` 可以用嚟降低 noise：
  - 例如 `N=2` 表示：某條 pattern（同一 ticker + 同一組 U/T/E... 日期）需要至少喺 2 個不同 window 之下都掃到，先會保留

備註：

- Streamlit UI 已提供 Window 模式：`Single window` / `Multi windows`（輸入一串 windows）
- 目前 `--min-window-support` 係 CLI 參數；如要喺 UI 用 noise filter，需要用 CLI 跑（或再擴充 UI）

---

## 5) U 規則（_u_rule）— 「跌浪後首次反彈」

實作位置：`src/utere/scanner.py::_u_rule`

對 index = `u_idx` 嘅候選棒，要成為 **U** 必須同時滿足：

### 5.1 U 必須係陽燭

- `Close(U) > Open(U)`

### 5.2 「首次反彈」保護（避免連續陽燭當 U）

- 前一日（`U-1`）**唔可以係陽燭**（即如果 `U-1` 係陽燭就直接 fail）
- 而且（預設）前一日（`U-1`）**必須係陰燭**：
  - 由 `u_prev_must_be_bearish=True` 控制

### 5.3 賣壓 / 跌浪資格（二選一模式）

#### 模式 A（預設）：一年回撤（drawdown filter）

當 `u_use_drawdown_filter=True`：

- `peak = max(High)`，取樣區間係「由 `U-1` 往前 `u_drawdown_lookback` 棒」
- `current = Close(U)`
- 回撤：

  `drawdown = (peak - current) / peak`

- 必須符合：

  `u_drawdown_min <= drawdown <= u_drawdown_max`

預設值：252 棒、30%～50%。

#### 模式 B（可選）：嚴格 downtrend 結構（_is_downtrend）

當 `u_use_drawdown_filter=False`（即 CLI 用 `--u-use-downtrend`）：

以下三個條件 **全部都要中**（以 `end_idx = U-1` 計）：

1) 近 3 棒 Lower‑Low：`Low[end] < Low[end-1] < Low[end-2]`
2) 近 3 棒 Lower‑High：`High[end] < High[end-1] < High[end-2]`
3) 近 3–5 棒（由 `u_lookback` 決定但會 clamp 到 3–5）內陰燭數：
   - `count(Close < Open) >= downtrend_bearish_min`

### 5.4 U 收市要站上「U 前底」

無論模式 A/B，都會計一個「U 前底」低點，然後要求：

- `Close(U) > preU_bottom_low`

其中 `preU_bottom_low` 定義：

- 模式 A（drawdown）：取 `min(Low)` over 近 `u_lookback` 棒、結束於 `U-1`
- 模式 B（downtrend）：固定取近 3 棒、結束於 `U-1`（即 `min(Low[U-3..U-1])`）

### 5.5 U 收市要高過前一日 High（預設開）

當 `u_confirm_close_gt_prev_high=True`：

- `Close(U) > High(U-1)`

### 5.6 可選更嚴格確認（預設關）

當 `u_confirm_close_gt_prev=True`：

- `Close(U) > Close(U-1)`

---

## 6) T / E1 規則（唔需要連續日）

一旦確認某日係 U（`u_idx`）：

- **T**：由 `u_idx + 1` 起向後搵，第一支陰燭（`Close < Open`）
- **E1**：由 `T+1` 起向後搵，第一支同時滿足以下條件嘅 K 線：
  - 陽燭（`Close > Open`）
  - 並且 `High(E1) > High(T)`（向上突破 T 嘅 High）

以上搜尋都必須喺 `window_size` 視窗內完成；而且如果期間出現「新低重置」（`Low < reset_low`），會即刻放棄該 setup。

如果 U 存在但喺指定視窗內搵唔到 T 或搵唔到 E1：

- 系統會輸出一條 **U_only**（只記錄 U 日期）

---

## 7) R 規則（局部擺頂 swing high）

只要已經成功形成 `U‑T‑E1`，就會喺 E1 後面去搵 R。

搜尋範圍：

- 若 `r_strict_next=True`（CLI `--r-strict-next`）：
  - 只檢查 `r_idx = e1_idx + 1`
- 否則（預設）：
  - 由 `e1_idx + 1` 起，最多檢查到 `e1_idx + max_r_bars`
  - 注意：R 規則需要 `r-1` 同 `r+1` 存在，所以實作上會 cap 到 `window_end - 1`

R 成立條件（`_is_local_swing_high`）：

- `High(R) > High(R-1)` 且 `High(R) > High(R+1)`
- `Low(R)  > Low(R-1)`  且 `Low(R)  > Low(R+1)`

搵唔到 R：

- 會變成 **UTE_incomplete**（但仍會先經過「新低淘汰」檢查，見下一節）

---

## 8) E2 規則（突破 R 高位）

若搵到 R（`r_found_idx`），就喺 R 後面最多 `max_e2_bars` 棒內搵 E2：

- E2 必須係陽燭
- 同時 `High(E2) > High(R)`

搵到 E2：

- 記錄為 **COMPLETED**

搵唔到 E2：

- 記錄為 **UTE_incomplete**（同樣要先經過「新低淘汰」檢查）

---

## 9) 「新低重置 / 淘汰」規則（u_reset_on_new_low）

目前實作係：`u_reset_on_new_low=True`（預設開，未提供 CLI 開關）。

核心概念：一旦 U 出現後，如果之後任何一日嘅 `Low` 跌穿「U 前底（reset_low）」
就視為 setup 失效：

- 即刻放棄呢個 setup
- 掃描指針跳去破底嗰日之後繼續

`reset_low` 定義：

- `reset_low = _pre_u_bottom_low(...)`
- 即：`min(Low)` over 近 `u_lookback` 棒、結束於 `U-1`

實作上有兩層保護：

### 9.1 搜尋 R / E2 期間即時重置

- 喺搵 R（E1 後）同搵 E2（R 後）嘅 for-loop 入面，只要遇到 `Low < reset_low` 就觸發 reset

### 9.2 對 watchlist（UTE_incomplete）做「之後破底」淘汰

就算喺指定 horizon 內搵唔到 R / E2，本來會寫入 `UTE_incomplete`。
但系統會額外檢查：

- 如果由 `U+1` 起，未來任何一日嘅最低 Low（用 suffix min 計）跌穿 `reset_low`
- 就 **唔會寫入** 呢條 `UTE_incomplete`（避免將已破底嘅 setup 留喺 watchlist）

---

## 10) 輸出檔案（CSV）同欄位

輸出目錄：`outputs/`

如使用 `--latest-only`：

- 每隻股票只保留「最新一條」結果
- 檔名會寫成 `*_latest.csv`
- 並且 `--latest-only` 唔可以同 `--append-output` 一齊用

### 10.1 Completed patterns

- 檔案：`outputs/completed_patterns.csv` 或 `outputs/completed_patterns_latest.csv`
- 欄位：
  - `StockCode, U_Date, T_Date, E1_Date, R_Date, E2_Date, Pattern_Complete_Date`

### 10.2 Incomplete UTE patterns（watchlist）

- 檔案：`outputs/incomplete_UTE_patterns.csv` 或 `outputs/incomplete_UTE_patterns_latest.csv`
- 欄位：
  - `StockCode, U_Date, T_Date, E1_Date, Status, Last_Date`
- `Status` 目前固定值：`UTE_incomplete`

### 10.3 U-only signals

- 檔案：`outputs/u_signals.csv` 或 `outputs/u_signals_latest.csv`
- 欄位：
  - `StockCode, U_Date, Status, Last_Date`
- `Status` 目前固定值：`U_only`

### 10.4 合併輸出（All patterns）

- 檔案：`outputs/all_patterns.csv` 或 `outputs/all_patterns_latest.csv`
- 欄位：
  - `StockCode, U_Date, T_Date, E1_Date, R_Date, E2_Date, Pattern_Complete_Date, Last_Date, Status`

`Status` 含意：

- `COMPLETED`：完整 `U‑T‑E1‑R‑E2`
- `UTE_incomplete`：有 `U‑T‑E1` 但未於 horizon 內完成（並且未破底淘汰）
- `U_only`：有 U 但未即刻形成 `U‑T‑E1`

---

## 11) CLI（主程式）常用例子

小型 smoke test（保留 latest-only）：

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.main `
  --tickers tickers_hk.txt `
  --period 1y --interval 1d `
  --window 30 `
  --scan-start 2026-01-01 `
  --batch-size 5 `
  --refresh-cache `
  --latest-only
```

切換到嚴格 downtrend 模式：

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.main `
  --tickers tickers_hk_all.txt `
  --u-use-downtrend `
  --u-lookback 3 `
  --downtrend-bearish-min 2
```

調整一年回撤範圍（例如 35%～60%）：

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.main `
  --u-use-drawdown `
  --u-drawdown-min 0.35 `
  --u-drawdown-max 0.60
```

---

## 12) 目前限制 / 注意事項

- **U_only** 只會喺「U 出現，但喺指定視窗內搵唔到 T 或搵唔到 E1（未能形成 U‑T‑E1）」時先會輸出。
- `u_reset_on_new_low` 目前係 `ScanConfig` 內 hard-coded 預設開，`src/main.py` 暫未提供 CLI 開關。
- 本系統係規則式掃描器，只係做歷史形態挖掘，唔會提供預測或買賣建議。
