# Playbook — u‑t‑e‑r‑e Pattern Mining System (HK first)

This playbook is for running the **rule-based pattern mining** system.
It does **NOT** do prediction / ML training / trading signals.

## 0) Requirements

- Windows + PowerShell
- Python venv in this repo: `.venv`

Use the repo’s python:

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" --version
```

If you are new to Python, you can follow this playbook top-to-bottom. Most commands are copy/paste.

## 1) Install dependencies

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m pip install -r requirements.txt
```

If you get errors about missing packages later, run this command again.

## 1b) (Optional) Start the Web UI (easiest)

This repo includes a simple Web UI (built with Streamlit) so non-technical users can run scans by clicking buttons.

Start it:

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m streamlit run app.py
```

Then open the local URL shown in the terminal (usually `http://localhost:8501`).

What the UI can do:

- Choose **Daily** vs **Intraday (minute, one day)**
- Choose ticker file, day, market (HK/US), minute interval (1m/2m/5m/15m...)
- Run scan and download CSV outputs
- English/中文 switch
- Tickers can be selected from an existing file OR uploaded (`.txt`) OR pasted

Important:

- Intraday minute data is huge. Always use a **small** ticker file for intraday.
- Yahoo Finance can rate-limit. If it happens, reduce batch size or use 5m / 15m.

## 2) Generate HK ticker universe (Yahoo format)

Yahoo Finance doesn’t provide a public “download all tickers” endpoint.
We generate the HK list from HKEX’s official **ListOfSecurities.xlsx**.

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.hk_tickers --refresh --out tickers_hk_all.txt
```

Output:

- `tickers_hk_all.txt` (one ticker per line like `0700.HK`)

## 3) Run a small smoke test (recommended)

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.main --tickers tickers_hk.txt --period 1y --window 30 --scan-start 2026-01-01 --batch-size 5 --refresh-cache
```

Outputs:

- `outputs/completed_patterns.csv`
- `outputs/incomplete_UTE_patterns.csv`

## 4) Run full HK scan (large-scale)

Recommended baseline for 2700+ tickers:

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.main --tickers tickers_hk_all.txt --period 5y --window 30 --scan-start 2026-01-01 --batch-size 30 --cache-dir data/cache --max-retries 3 --backoff-seconds 2 --pause-between-batches 0.2
```

### Notes on performance & reliability

- **First run is slow**: it downloads and populates `data/cache/`.
- **Later runs are faster**: cached per-ticker CSVs are reused.

## 5) Intraday (分鐘圖) scan — one day only

This mode uses the **same U‑T‑E‑R‑E rule engine**, but downloads **minute bars** and scans **only within a single trading day**.

Because minute data is huge, use a small chosen ticker file (example: `tickers_intraday_choose.txt`) and refresh every run (no cache).

HK example (1m, within HK regular session; excludes lunch break by default):

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.minute_scan --tickers tickers_intraday_choose.txt --day 2026-02-14 --market HK --interval 1m
```

Outputs (written to `outputs/` with prefix `intraday_` by default):

- `intraday_completed_patterns.csv`
- `intraday_incomplete_UTE_patterns.csv`
- `intraday_u_signals.csv`
- `intraday_all_patterns.csv`
- If Yahoo rate-limits, try:
  - `--batch-size 10`
  - `--pause-between-batches 1.0`

## 5b) Example: test with 市值 100 億以上 list

Start with 5m first (faster + fewer requests):

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.minute_scan --tickers "tickers_hk_市值_100億以上.txt" --day 2026-02-12 --market HK --interval 5m --batch-size 10 --latest-only --out-prefix "mcap100b_20260212_5m"
```

Then try 1m (much heavier):

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.minute_scan --tickers "tickers_hk_市值_100億以上.txt" --day 2026-02-12 --market HK --interval 1m --batch-size 10 --latest-only --out-prefix "mcap100b_20260212_1m"
```

## 6) Resuming long runs

Outputs are written **incrementally**.
If the run stops, you can choose:

- Overwrite outputs (default): just run again.
- Append to outputs:

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.main --tickers tickers_hk_all.txt --period 5y --window 30 --scan-start 2026-01-01 --append-output
```

When appending, you may get duplicates if you re-scan the same tickers/date range. If you want, you can de-dup later in Excel/Pandas.

## 7) Output schema

### `outputs/completed_patterns.csv`

Columns:

- `StockCode`
- `U_Date`, `T_Date`, `E1_Date`, `R_Date`, `E2_Date`
- `Pattern_Complete_Date` (= `E2_Date`)

### `outputs/incomplete_UTE_patterns.csv`

Columns:

- `StockCode`
- `U_Date`, `T_Date`, `E1_Date`
- `Status` (always `UTE_incomplete`)
- `Last_Date` (latest available bar date)

## 7b) Latest-only outputs (one row per stock)

If you want only the latest match per stock:

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.main --tickers tickers_hk_all.txt --period 5y --window 30 --scan-start 2026-01-01 --latest-only
```

Outputs:

- `outputs/completed_patterns_latest.csv`
- `outputs/incomplete_UTE_patterns_latest.csv`

## 8) Troubleshooting


- `ModuleNotFoundError` / missing packages:
  - Run the install command in section 1 with the repo’s python.
- XLSX read fails:
  - Ensure `openpyxl` is installed (it is in `requirements.txt`).
- Many tickers fail to download:
  - Reduce `--batch-size` and increase `--pause-between-batches`.
  - Keep `--max-retries` at 3–5.

## 9) Hosting the UI from GitHub (what is possible)

GitHub Pages is **static** (HTML/JS only) and cannot run Python scanning code.

If you want a real web UI linked from your GitHub repo, common options are:

- Streamlit Community Cloud (simple):
  - Push this repo to GitHub
  - Go to Streamlit Community Cloud and deploy
  - Set the app entry file to `app.py`
  - It will give you a public URL

Recommended (Option A):
- Use Streamlit for the UI (runs the Python scanning)
- Use GitHub just to host the code + documentation

- Run locally only (no hosting): use the command in section 1b.

If you tell me your preferred option (public URL vs local-only), I can adjust the docs to match your exact setup.

## Appendix: Optional stricter U confirmation

Enable stricter reversal confirmation (`Close_u > Close_{u-1}`):

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.main --u-confirm-close-gt-prev
```

Reset/disable it explicitly (default):

```powershell
& "C:/pattern mining system of utere/.venv/Scripts/python.exe" -m src.main --no-u-confirm-close-gt-prev
```
