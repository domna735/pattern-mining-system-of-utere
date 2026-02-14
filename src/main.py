from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

import pandas as pd

from .utere.data_source import FetchConfig, iter_many_ohlc_yfinance
from .utere.scanner import ScanConfig, scan_dataframe_for_patterns


def _parse_user_date(text: str) -> date:
    """Parse a date from common user formats.

    Accepts:
    - YYYY-MM-DD
    - DD/MM/YYYY
    - MM/DD/YYYY
    """
    text = text.strip()
    # 1) ISO
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    # 2) Slash formats
    if "/" in text:
        parts = text.split("/")
        if len(parts) == 3:
            a, b, c = parts
            if len(c) == 4:
                # Try DD/MM/YYYY then MM/DD/YYYY
                da, db, dc = int(a), int(b), int(c)
                try:
                    return date(dc, db, da)
                except ValueError:
                    return date(dc, da, db)

    raise ValueError(f"Unsupported date format: {text}")


def _read_tickers(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    tickers: list[str] = []
    for line in lines:
        stripped = line.strip().lstrip("\ufeff")
        if not stripped or stripped.startswith("#"):
            continue
        tickers.append(stripped)
    return tickers


def main() -> int:
    parser = argparse.ArgumentParser(description="u-t-e-r-e pattern mining (rule-based)")
    parser.add_argument(
        "--tickers",
        type=str,
        default="tickers_hk.txt",
        help="Path to tickers list (default: HK tickers)",
    )
    parser.add_argument("--period", type=str, default="5y", help="yfinance period, e.g. 1y, 5y, max")
    parser.add_argument("--interval", type=str, default="1d", help="yfinance interval, e.g. 1d")
    parser.add_argument("--window", type=int, default=30, help="Sliding window size (bars)")
    parser.add_argument(
        "--u-lookback",
        type=int,
        default=3,
        help="Bars immediately before U (recommended 3-5). Used for min(Low) rebound check; also used by --u-use-downtrend.",
    )
    parser.add_argument(
        "--downtrend-bearish-min",
        type=int,
        default=2,
        help="Minimum bearish candles within the downtrend window (default: 2)",
    )

    u_trend_mode = parser.add_mutually_exclusive_group()
    u_trend_mode.add_argument(
        "--u-use-drawdown",
        dest="u_use_drawdown_filter",
        action="store_true",
        help="Use 1-year drawdown filter for U (default: enabled)",
    )
    u_trend_mode.add_argument(
        "--u-use-downtrend",
        dest="u_use_drawdown_filter",
        action="store_false",
        help="Use strict downtrend (Lower-Low + Lower-High + bearish count) for U",
    )
    parser.set_defaults(u_use_drawdown_filter=True)

    parser.add_argument(
        "--u-drawdown-lookback",
        type=int,
        default=252,
        help="Lookback bars for drawdown peak (default: 252 trading days)",
    )
    parser.add_argument(
        "--u-drawdown-min",
        type=float,
        default=0.30,
        help="Min drawdown from peak High to U Close (default: 0.30 = 30%%)",
    )
    parser.add_argument(
        "--u-drawdown-max",
        type=float,
        default=0.50,
        help="Max drawdown from peak High to U Close (default: 0.50 = 50%%)",
    )

    u_prev_bearish_group = parser.add_mutually_exclusive_group()
    u_prev_bearish_group.add_argument(
        "--u-prev-must-be-bearish",
        dest="u_prev_must_be_bearish",
        action="store_true",
        help="Require the candle before U to be bearish (default: enabled)",
    )
    u_prev_bearish_group.add_argument(
        "--no-u-prev-must-be-bearish",
        dest="u_prev_must_be_bearish",
        action="store_false",
        help="Allow the candle before U to be non-bearish (less strict)",
    )
    parser.set_defaults(u_prev_must_be_bearish=True)

    u_prev_high_group = parser.add_mutually_exclusive_group()
    u_prev_high_group.add_argument(
        "--u-confirm-close-gt-prev-high",
        dest="u_confirm_close_gt_prev_high",
        action="store_true",
        help="Require U Close > previous High (default: enabled)",
    )
    u_prev_high_group.add_argument(
        "--no-u-confirm-close-gt-prev-high",
        dest="u_confirm_close_gt_prev_high",
        action="store_false",
        help="Disable the U Close > previous High requirement (less strict)",
    )
    parser.set_defaults(u_confirm_close_gt_prev_high=True)

    u_confirm_group = parser.add_mutually_exclusive_group()
    u_confirm_group.add_argument(
        "--u-confirm-close-gt-prev",
        dest="u_confirm_close_gt_prev",
        action="store_true",
        help="If set, require U Close > previous Close for stricter reversal confirmation",
    )
    u_confirm_group.add_argument(
        "--no-u-confirm-close-gt-prev",
        dest="u_confirm_close_gt_prev",
        action="store_false",
        help="Explicitly disable the stricter U confirmation (default)",
    )
    parser.set_defaults(u_confirm_close_gt_prev=False)
    parser.add_argument(
        "--max-r-bars",
        type=int,
        default=10,
        help="Max bars after E1 to search for R (local swing high)",
    )
    parser.add_argument(
        "--r-strict-next",
        action="store_true",
        help="If set, R must be exactly the next bar after E1 (r = e1 + 1)",
    )
    parser.add_argument(
        "--max-e2-bars",
        type=int,
        default=3,
        help="Max bars after R to look for E2 breakout",
    )
    parser.add_argument(
        "--scan-start",
        type=str,
        default=None,
        help="Only scan patterns whose U date is on/after this date (e.g. 2026-01-01 or 01/01/2026)",
    )
    parser.add_argument("--batch-size", type=int, default=30, help="yfinance download batch size")
    parser.add_argument("--cache-dir", type=str, default="data/cache", help="Cache directory for per-ticker CSV")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache (always download from yfinance)",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Re-download and overwrite cached files",
    )
    parser.add_argument(
        "--no-update-cache",
        action="store_true",
        help="Do not top-up cached files before scanning (use cache as-is)",
    )
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries for download failures")
    parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=2.0,
        help="Base backoff seconds (exponential) between retries",
    )
    parser.add_argument(
        "--pause-between-batches",
        type=float,
        default=0.2,
        help="Sleep seconds between yfinance batches",
    )
    parser.add_argument(
        "--append-output",
        action="store_true",
        help="Append to existing output CSVs instead of overwriting",
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Only keep the latest match per stock (writes *_latest.csv outputs)",
    )
    parser.add_argument(
        "--out-prefix",
        type=str,
        default="",
        help="Optional output filename prefix written to outputs/ (e.g. daily_20260214). If empty, uses default filenames.",
    )

    args = parser.parse_args()

    scan_start_date: date | None = None
    if args.scan_start:
        scan_start_date = _parse_user_date(args.scan_start)

    tickers_path = Path(args.tickers)
    tickers = _read_tickers(tickers_path)
    if not tickers:
        raise SystemExit(f"No tickers found in {tickers_path}")

    cfg = ScanConfig(
        window_size=args.window,
        u_lookback=args.u_lookback,
        downtrend_bearish_min=int(args.downtrend_bearish_min),
        u_use_drawdown_filter=bool(args.u_use_drawdown_filter),
        u_drawdown_lookback=int(args.u_drawdown_lookback),
        u_drawdown_min=float(args.u_drawdown_min),
        u_drawdown_max=float(args.u_drawdown_max),
        u_prev_must_be_bearish=bool(args.u_prev_must_be_bearish),
        u_confirm_close_gt_prev_high=bool(args.u_confirm_close_gt_prev_high),
        u_confirm_close_gt_prev=bool(args.u_confirm_close_gt_prev),
        max_r_bars=args.max_r_bars,
        max_e2_bars=args.max_e2_bars,
        r_strict_next=bool(args.r_strict_next),
    )

    fetch_cfg = FetchConfig(
        period=args.period,
        interval=args.interval,
        cache_dir=args.cache_dir,
        use_cache=not bool(args.no_cache),
        update_cache=not bool(args.no_update_cache),
        refresh_cache=bool(args.refresh_cache),
        batch_size=int(args.batch_size),
        max_retries=int(args.max_retries),
        backoff_seconds=float(args.backoff_seconds),
        pause_between_batches=float(args.pause_between_batches),
    )

    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = str(args.out_prefix).strip()
    pfx = f"{prefix}_" if prefix else ""

    completed_path = out_dir / (
        f"{pfx}completed_patterns_latest.csv" if args.latest_only else f"{pfx}completed_patterns.csv"
    )
    incomplete_path = out_dir / (
        f"{pfx}incomplete_UTE_patterns_latest.csv" if args.latest_only else f"{pfx}incomplete_UTE_patterns.csv"
    )
    u_path = out_dir / (f"{pfx}u_signals_latest.csv" if args.latest_only else f"{pfx}u_signals.csv")
    combined_path = out_dir / (f"{pfx}all_patterns_latest.csv" if args.latest_only else f"{pfx}all_patterns.csv")

    completed_fields = [
        "StockCode",
        "U_Date",
        "T_Date",
        "E1_Date",
        "R_Date",
        "E2_Date",
        "Pattern_Complete_Date",
    ]
    incomplete_fields = [
        "StockCode",
        "U_Date",
        "T_Date",
        "E1_Date",
        "Status",
        "Last_Date",
    ]
    u_fields = [
        "StockCode",
        "U_Date",
        "Status",
        "Last_Date",
    ]
    combined_fields = [
        "StockCode",
        "U_Date",
        "T_Date",
        "E1_Date",
        "R_Date",
        "E2_Date",
        "Pattern_Complete_Date",
        "Last_Date",
        "Status",
    ]

    if args.latest_only and args.append_output:
        raise SystemExit("--latest-only cannot be used with --append-output")

    if not args.append_output:
        # Overwrite mode
        try:
            if completed_path.exists():
                completed_path.unlink()
            if incomplete_path.exists():
                incomplete_path.unlink()
            if u_path.exists():
                u_path.unlink()
            if combined_path.exists():
                combined_path.unlink()
        except PermissionError as e:
            raise SystemExit(
                "Cannot overwrite output CSV because it is open/locked by another program. "
                "Close the CSV (Excel, etc.) OR rerun with --out-prefix to write to new files.\n"
                f"Details: {e}"
            )

    completed_needs_header = (not completed_path.exists()) or completed_path.stat().st_size == 0
    incomplete_needs_header = (not incomplete_path.exists()) or incomplete_path.stat().st_size == 0
    u_needs_header = (not u_path.exists()) or u_path.stat().st_size == 0
    combined_needs_header = (not combined_path.exists()) or combined_path.stat().st_size == 0

    completed_count = 0
    incomplete_count = 0
    u_count = 0

    latest_completed_by_ticker: dict[str, dict] = {}
    latest_incomplete_by_ticker: dict[str, dict] = {}
    latest_u_by_ticker: dict[str, dict] = {}

    with completed_path.open("a", newline="", encoding="utf-8") as f_completed, incomplete_path.open(
        "a", newline="", encoding="utf-8"
    ) as f_incomplete, u_path.open("a", newline="", encoding="utf-8") as f_u, combined_path.open(
        "a", newline="", encoding="utf-8"
    ) as f_combined:
        w_completed = csv.DictWriter(f_completed, fieldnames=completed_fields)
        w_incomplete = csv.DictWriter(f_incomplete, fieldnames=incomplete_fields)
        w_u = csv.DictWriter(f_u, fieldnames=u_fields)
        w_combined = csv.DictWriter(f_combined, fieldnames=combined_fields)

        if completed_needs_header:
            w_completed.writeheader()
        if incomplete_needs_header:
            w_incomplete.writeheader()
        if u_needs_header:
            w_u.writeheader()
        if combined_needs_header:
            w_combined.writeheader()

        processed = 0
        for ticker, df in iter_many_ohlc_yfinance(tickers, cfg=fetch_cfg):
            if df is None or df.empty:
                continue

            completed, incomplete, u_only = scan_dataframe_for_patterns(
                df, ticker=ticker, cfg=cfg, scan_start_date=scan_start_date
            )

            if args.latest_only:
                # Keep only the latest match per ticker.
                if completed:
                    best = max(completed, key=lambda r: str(r.get("Pattern_Complete_Date", "")))
                    prev = latest_completed_by_ticker.get(ticker)
                    if (prev is None) or str(best.get("Pattern_Complete_Date", "")) > str(prev.get("Pattern_Complete_Date", "")):
                        latest_completed_by_ticker[ticker] = best

                if incomplete:
                    best = max(incomplete, key=lambda r: str(r.get("E1_Date", "")))
                    prev = latest_incomplete_by_ticker.get(ticker)
                    if (prev is None) or str(best.get("E1_Date", "")) > str(prev.get("E1_Date", "")):
                        latest_incomplete_by_ticker[ticker] = best

                if u_only:
                    best = max(u_only, key=lambda r: str(r.get("U_Date", "")))
                    prev = latest_u_by_ticker.get(ticker)
                    if (prev is None) or str(best.get("U_Date", "")) > str(prev.get("U_Date", "")):
                        latest_u_by_ticker[ticker] = best
            else:
                for row in completed:
                    w_completed.writerow({k: row.get(k, "") for k in completed_fields})
                    w_combined.writerow(
                        {
                            "StockCode": row.get("StockCode", ""),
                            "U_Date": row.get("U_Date", ""),
                            "T_Date": row.get("T_Date", ""),
                            "E1_Date": row.get("E1_Date", ""),
                            "R_Date": row.get("R_Date", ""),
                            "E2_Date": row.get("E2_Date", ""),
                            "Pattern_Complete_Date": row.get("Pattern_Complete_Date", ""),
                            "Last_Date": "",
                            "Status": "COMPLETED",
                        }
                    )
                    completed_count += 1
                for row in incomplete:
                    w_incomplete.writerow({k: row.get(k, "") for k in incomplete_fields})
                    w_combined.writerow(
                        {
                            "StockCode": row.get("StockCode", ""),
                            "U_Date": row.get("U_Date", ""),
                            "T_Date": row.get("T_Date", ""),
                            "E1_Date": row.get("E1_Date", ""),
                            "R_Date": "",
                            "E2_Date": "",
                            "Pattern_Complete_Date": "",
                            "Last_Date": row.get("Last_Date", ""),
                            "Status": row.get("Status", "UTE_incomplete"),
                        }
                    )
                    incomplete_count += 1

                for row in u_only:
                    w_u.writerow({k: row.get(k, "") for k in u_fields})
                    w_combined.writerow(
                        {
                            "StockCode": row.get("StockCode", ""),
                            "U_Date": row.get("U_Date", ""),
                            "T_Date": "",
                            "E1_Date": "",
                            "R_Date": "",
                            "E2_Date": "",
                            "Pattern_Complete_Date": "",
                            "Last_Date": row.get("Last_Date", ""),
                            "Status": row.get("Status", "U_only"),
                        }
                    )
                    u_count += 1

            processed += 1
            if processed % 100 == 0:
                print(
                    f"Processed {processed}/{len(tickers)} tickers | Completed={completed_count} | Incomplete={incomplete_count}"
                )

    if args.latest_only:
        # Overwrite the files with latest-only rows.
        completed_rows = list(latest_completed_by_ticker.values())
        incomplete_rows = list(latest_incomplete_by_ticker.values())
        u_rows = list(latest_u_by_ticker.values())

        with completed_path.open("w", newline="", encoding="utf-8") as f_completed:
            w = csv.DictWriter(f_completed, fieldnames=completed_fields)
            w.writeheader()
            for row in sorted(completed_rows, key=lambda r: (r.get("StockCode", ""), r.get("Pattern_Complete_Date", ""))):
                w.writerow({k: row.get(k, "") for k in completed_fields})

        with incomplete_path.open("w", newline="", encoding="utf-8") as f_incomplete:
            w = csv.DictWriter(f_incomplete, fieldnames=incomplete_fields)
            w.writeheader()
            for row in sorted(incomplete_rows, key=lambda r: (r.get("StockCode", ""), r.get("E1_Date", ""))):
                w.writerow({k: row.get(k, "") for k in incomplete_fields})

        with u_path.open("w", newline="", encoding="utf-8") as f_u:
            w = csv.DictWriter(f_u, fieldnames=u_fields)
            w.writeheader()
            for row in sorted(u_rows, key=lambda r: (r.get("StockCode", ""), r.get("U_Date", ""))):
                w.writerow({k: row.get(k, "") for k in u_fields})

        # Combined file (latest-only)
        combined_rows: list[dict] = []
        for row in completed_rows:
            combined_rows.append(
                {
                    "StockCode": row.get("StockCode", ""),
                    "U_Date": row.get("U_Date", ""),
                    "T_Date": row.get("T_Date", ""),
                    "E1_Date": row.get("E1_Date", ""),
                    "R_Date": row.get("R_Date", ""),
                    "E2_Date": row.get("E2_Date", ""),
                    "Pattern_Complete_Date": row.get("Pattern_Complete_Date", ""),
                    "Last_Date": "",
                    "Status": "COMPLETED",
                }
            )
        for row in incomplete_rows:
            combined_rows.append(
                {
                    "StockCode": row.get("StockCode", ""),
                    "U_Date": row.get("U_Date", ""),
                    "T_Date": row.get("T_Date", ""),
                    "E1_Date": row.get("E1_Date", ""),
                    "R_Date": "",
                    "E2_Date": "",
                    "Pattern_Complete_Date": "",
                    "Last_Date": row.get("Last_Date", ""),
                    "Status": row.get("Status", "UTE_incomplete"),
                }
            )

        for row in u_rows:
            combined_rows.append(
                {
                    "StockCode": row.get("StockCode", ""),
                    "U_Date": row.get("U_Date", ""),
                    "T_Date": "",
                    "E1_Date": "",
                    "R_Date": "",
                    "E2_Date": "",
                    "Pattern_Complete_Date": "",
                    "Last_Date": row.get("Last_Date", ""),
                    "Status": row.get("Status", "U_only"),
                }
            )

        with combined_path.open("w", newline="", encoding="utf-8") as f_combined:
            w = csv.DictWriter(f_combined, fieldnames=combined_fields)
            w.writeheader()
            for row in sorted(combined_rows, key=lambda r: (r.get("StockCode", ""), r.get("U_Date", ""), r.get("Status", ""))):
                w.writerow({k: row.get(k, "") for k in combined_fields})

        completed_count = len(completed_rows)
        incomplete_count = len(incomplete_rows)
        u_count = len(u_rows)

    print(f"Completed patterns: {completed_count}")
    print(f"Incomplete UTE patterns: {incomplete_count}")
    print(f"U-only signals: {u_count}")
    print(f"Wrote: {completed_path}")
    print(f"Wrote: {incomplete_path}")
    print(f"Wrote: {u_path}")
    print(f"Wrote: {combined_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
