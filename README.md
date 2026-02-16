# u-t-e-r-e Pattern Mining (Rule-Based)

This project scans historical OHLC candlestick data to **mine**:
- completed **u-t-e-r-e** patterns
- incomplete **U-T-E** patterns (watchlist)

It does **NOT** do prediction, ML training, or trading signals.

## Quick start

1) Install deps

```powershell
pip install -r requirements.txt
```

2) Run scan (uses `tickers.txt` by default)

```powershell
python -m src.main --period 5y --window 30
```

## Web UI (English/中文)

This repo includes a simple Web UI (Streamlit) that lets you:
- choose Daily vs Intraday (minute)
- choose day + minute interval
- choose window mode: single window OR multi-windows list (e.g. 20,50,100,200)
- select a tickers file OR upload a `.txt` OR paste tickers
- run scan and download results

Run locally:

```powershell
python -m streamlit run app.py
```

### Deploy from GitHub (Streamlit Community Cloud)

GitHub Pages cannot run Python. For a public UI, deploy `app.py` on Streamlit Community Cloud:

1) Push this repo to GitHub
2) In Streamlit Cloud: "Create app" → select this repo
3) App file: `app.py`
4) Deploy

Streamlit will give you a public URL.

For Hong Kong tickers (default), use `tickers_hk.txt` with symbols like `0700.HK`.
You can also input numeric HK codes (e.g. `700`) and the system will normalize to `0700.HK`.
US tickers can be placed in `tickers_us.txt` (e.g. `AAPL`) and passed via `--tickers tickers_us.txt`.

Note: advanced noise filtering for multi-window scans is available in CLI via `--min-window-support`.

## Get full HK ticker list

Yahoo Finance does not provide a public "download all tickers" endpoint.
This repo generates the HK list by downloading the official HKEX **ListOfSecurities.xlsx** and converting stock codes to Yahoo tickers (`0001.HK`).

```powershell
python -m src.hk_tickers --refresh --out tickers_hk_all.txt
```

Then scan all HK tickers:

```powershell
python -m src.main --tickers tickers_hk_all.txt --period 5y --window 30
```

## Large-scale scan tips (2700+ HK tickers)

This scanner supports batching, caching, and retries to reduce Yahoo rate-limit issues:

```powershell
python -m src.main --tickers tickers_hk_all.txt --period 5y --window 30 --scan-start 2026-01-01 --batch-size 30 --cache-dir data/cache --max-retries 3 --backoff-seconds 2 --pause-between-batches 0.2
```

- First run will download and populate cache; later runs reuse cached CSVs.
- Use `--refresh-cache` to force re-download, or `--no-cache` to disable caching.

## Intraday / 分鐘圖 (one-day scan)

If you want to scan minute charts, use the intraday runner which:
- downloads minute bars fresh every run (no cache)
- filters to **one trading day** and **market session time**

Example (HK, 1m):

```powershell
python -m src.minute_scan --tickers tickers_intraday_choose.txt --day 2026-02-14 --market HK --interval 1m
```

To limit scanning to more recent patterns, set a scan start date (U must be on/after that date):

```powershell
python -m src.main --scan-start 2026-01-01
```

3) Outputs

- `outputs/completed_patterns.csv`
- `outputs/incomplete_UTE_patterns.csv`

## Latest-only view (one row per stock)

If you only want the **latest** match per stock (useful for review/watchlist):

```powershell
python -m src.main --tickers tickers_hk_all.txt --period 5y --window 30 --scan-start 2026-01-01 --latest-only
```

Outputs:
- `outputs/completed_patterns_latest.csv`
- `outputs/incomplete_UTE_patterns_latest.csv`

## Notes

- Data source: `yfinance` (Yahoo Finance). You can swap to other API later.
- Pattern rules are implemented in `src/utere/scanner.py`.

## Optional stricter U confirmation

To enable the stricter rule `Close_u > Close_{u-1}`:

```powershell
python -m src.main --u-confirm-close-gt-prev
```

To explicitly reset/disable it (default):

```powershell
python -m src.main --no-u-confirm-close-gt-prev
```
