from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from .utere.scanner import ScanConfig, scan_dataframe_for_patterns


def _read_tickers(path: Path, market: str) -> list[str]:
    def _normalize_hk(token: str) -> str:
        t = token.strip()
        if not t:
            return t

        upper = t.upper()
        if upper.endswith(".HK"):
            base = upper[: -len(".HK")]
            if base.isdigit() and len(base) <= 4:
                return f"{base.zfill(4)}.HK"
            return upper

        if t.isdigit():
            if len(t) <= 4:
                return f"{t.zfill(4)}.HK"
            return f"{t}.HK"

        return t

    lines = path.read_text(encoding="utf-8").splitlines()
    tickers: list[str] = []
    for line in lines:
        stripped = line.strip().lstrip("\ufeff")
        if not stripped or stripped.startswith("#"):
            continue
        tickers.append(stripped)

    if market.strip().upper() == "HK":
        return [_normalize_hk(t) for t in tickers]
    return tickers


def _parse_day(text: str) -> date:
    return date.fromisoformat(text.strip())


def _parse_hhmm(text: str) -> time:
    text = text.strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time (HH:MM): {text}")
    hh = int(parts[0])
    mm = int(parts[1])
    return time(hour=hh, minute=mm)


@dataclass(frozen=True)
class SessionSpec:
    session_start: time
    session_end: time
    break_start: time | None = None
    break_end: time | None = None


def _default_session(market: str) -> tuple[str, SessionSpec]:
    m = market.strip().upper()
    if m == "HK":
        # HKEX: 09:30-12:00, 13:00-16:00 (HKT)
        return (
            "Asia/Hong_Kong",
            SessionSpec(
                session_start=time(9, 30),
                session_end=time(16, 0),
                break_start=time(12, 0),
                break_end=time(13, 0),
            ),
        )
    if m == "US":
        # NYSE/Nasdaq regular session: 09:30-16:00 (ET)
        return (
            "America/New_York",
            SessionSpec(session_start=time(9, 30), session_end=time(16, 0)),
        )

    # Safe fallback: no break
    return ("UTC", SessionSpec(session_start=time(0, 0), session_end=time(23, 59)))


def _coerce_index_to_local(df: pd.DataFrame, assume_tz: str, local_tz: str) -> pd.DataFrame:
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is None:
        # Unknown semantics from yfinance on some platforms; let user choose.
        idx = idx.tz_localize(assume_tz)
    idx = idx.tz_convert(local_tz)
    out = df.copy()
    out.index = idx
    return out


def _filter_one_day_and_session(
    df: pd.DataFrame,
    day: date,
    session: SessionSpec,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    idx = pd.to_datetime(df.index)
    # Expect tz-aware by now; if not, treat as-is.
    day_mask = idx.date == day

    t = idx.time
    in_session = (t >= session.session_start) & (t <= session.session_end)

    if session.break_start is not None and session.break_end is not None:
        in_break = (t >= session.break_start) & (t < session.break_end)
        in_session = in_session & (~in_break)

    out = df.loc[day_mask & in_session]
    return out


def _extract_ticker_frame(download_df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if download_df is None or len(download_df) == 0:
        return pd.DataFrame()

    df = download_df
    if isinstance(df.columns, pd.MultiIndex):
        level0 = set(df.columns.get_level_values(0))
        level1 = set(df.columns.get_level_values(1))

        if ticker in level1:
            try:
                return df.xs(ticker, axis=1, level=1, drop_level=True)
            except Exception:
                return pd.DataFrame()
        if ticker in level0:
            try:
                return df.xs(ticker, axis=1, level=0, drop_level=True)
            except Exception:
                return pd.DataFrame()
        return pd.DataFrame()

    return df


def _basic_clean_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    needed = ["Open", "High", "Low", "Close"]
    df = df.dropna(subset=needed).sort_index()
    df = df[~df.index.duplicated(keep="first")]

    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    open_ = df["Open"].astype(float)
    close = df["Close"].astype(float)

    valid = (high >= low) & (open_ >= low) & (open_ <= high) & (close >= low) & (close <= high)
    return df.loc[valid]


def _download_intraday_batch(
    tickers: list[str],
    start_dt: datetime,
    end_dt: datetime,
    interval: str,
) -> pd.DataFrame:
    # yfinance wants strings; keep them ISO-ish.
    return yf.download(
        tickers=" ".join(tickers),
        start=start_dt.strftime("%Y-%m-%d"),
        end=end_dt.strftime("%Y-%m-%d"),
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
        group_by="column",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="u-t-e-r-e minute/intraday pattern mining (one day only)")
    parser.add_argument(
        "--tickers",
        type=str,
        default="tickers_hk.txt",
        help="Path to chosen tickers list (keep it small for minute data)",
    )
    parser.add_argument(
        "--day",
        type=str,
        required=True,
        help="Trading day to scan (YYYY-MM-DD). Scanner only uses this single day.",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default="1m",
        help="yfinance interval for intraday (e.g. 1m, 2m, 5m, 15m)",
    )
    parser.add_argument(
        "--market",
        type=str,
        default="HK",
        help="Market session defaults: HK or US (controls timezone + default session hours)",
    )
    parser.add_argument(
        "--tz",
        type=str,
        default=None,
        help="Override local timezone for session filtering (e.g. Asia/Hong_Kong).",
    )
    parser.add_argument(
        "--assume-tz",
        type=str,
        default="UTC",
        help="If yfinance returns tz-naive timestamps, assume they are in this timezone (default: UTC).",
    )
    parser.add_argument(
        "--session-start",
        type=str,
        default=None,
        help="Override session start HH:MM (local tz)",
    )
    parser.add_argument(
        "--session-end",
        type=str,
        default=None,
        help="Override session end HH:MM (local tz)",
    )
    parser.add_argument(
        "--break-start",
        type=str,
        default=None,
        help="Optional break start HH:MM to exclude (local tz)",
    )
    parser.add_argument(
        "--break-end",
        type=str,
        default=None,
        help="Optional break end HH:MM to exclude (local tz)",
    )

    # Scan rules: reuse the exact same rule engine.
    parser.add_argument("--window", type=int, default=30, help="Sliding window size (bars)")
    parser.add_argument(
        "--windows",
        type=str,
        default=None,
        help="Comma-separated window sizes to scan and merge, e.g. 20,50,100,200. If set, overrides --window.",
    )
    parser.add_argument(
        "--min-window-support",
        type=int,
        default=1,
        help="Noise filter: keep matches that appear in at least N windows (default: 1 = no filtering).",
    )
    parser.add_argument("--u-lookback", type=int, default=3, help="Bars immediately before U (min 3)")
    parser.add_argument("--downtrend-bearish-min", type=int, default=2, help="Min bearish bars before U")

    u_trend_mode = parser.add_mutually_exclusive_group()
    u_trend_mode.add_argument(
        "--u-use-downtrend",
        dest="u_use_drawdown_filter",
        action="store_false",
        help="Use strict downtrend (recommended for one-day intraday scans)",
    )
    u_trend_mode.add_argument(
        "--u-use-drawdown",
        dest="u_use_drawdown_filter",
        action="store_true",
        help="Use drawdown filter (NOT compatible with one-day-only minute scans)",
    )
    parser.set_defaults(u_use_drawdown_filter=False)

    parser.add_argument("--max-r-bars", type=int, default=10)
    parser.add_argument("--max-e2-bars", type=int, default=3)
    parser.add_argument("--r-strict-next", action="store_true")

    parser.add_argument("--batch-size", type=int, default=20, help="yfinance download batch size")

    parser.add_argument(
        "--out-prefix",
        type=str,
        default="intraday",
        help="Output filename prefix written to outputs/ (default: intraday)",
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Only keep latest match per ticker (uses *_latest.csv)",
    )

    args = parser.parse_args()

    def _parse_windows(text: str | None) -> list[int]:
        if text is None:
            return []
        cleaned = text.strip()
        if not cleaned:
            return []
        parts = [p.strip() for p in cleaned.replace(" ", ",").split(",")]
        out: list[int] = []
        for p in parts:
            if not p:
                continue
            w = int(p)
            if w <= 0:
                raise ValueError("Window sizes must be positive")
            out.append(w)
        seen: set[int] = set()
        uniq: list[int] = []
        for w in out:
            if w in seen:
                continue
            seen.add(w)
            uniq.append(w)
        return uniq

    windows = _parse_windows(args.windows) if args.windows else [int(args.window)]
    min_support = max(1, int(args.min_window_support))

    scan_day = _parse_day(args.day)

    if bool(args.u_use_drawdown_filter):
        raise SystemExit(
            "Intraday mode scans within one day only, so drawdown filter cannot work. "
            "Use --u-use-downtrend (default)."
        )

    tz_default, session_default = _default_session(args.market)
    local_tz = str(args.tz or tz_default)

    session = session_default
    if args.session_start is not None:
        session = SessionSpec(
            session_start=_parse_hhmm(args.session_start),
            session_end=session.session_end,
            break_start=session.break_start,
            break_end=session.break_end,
        )
    if args.session_end is not None:
        session = SessionSpec(
            session_start=session.session_start,
            session_end=_parse_hhmm(args.session_end),
            break_start=session.break_start,
            break_end=session.break_end,
        )
    if args.break_start is not None or args.break_end is not None:
        if args.break_start is None or args.break_end is None:
            raise SystemExit("If specifying break hours, set BOTH --break-start and --break-end")
        session = SessionSpec(
            session_start=session.session_start,
            session_end=session.session_end,
            break_start=_parse_hhmm(args.break_start),
            break_end=_parse_hhmm(args.break_end),
        )

    tickers = _read_tickers(Path(args.tickers), market=args.market)
    if not tickers:
        raise SystemExit("No tickers found")

    def _make_scan_cfg(window_size: int) -> ScanConfig:
        return ScanConfig(
            window_size=int(window_size),
            u_lookback=int(args.u_lookback),
            downtrend_bearish_min=int(args.downtrend_bearish_min),
            u_use_drawdown_filter=False,
            max_r_bars=int(args.max_r_bars),
            max_e2_bars=int(args.max_e2_bars),
            r_strict_next=bool(args.r_strict_next),
        )

    start_dt = datetime(scan_day.year, scan_day.month, scan_day.day)
    end_dt = start_dt + timedelta(days=1)

    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    suffix = "_latest" if args.latest_only else ""
    completed_path = out_dir / f"{args.out_prefix}_completed_patterns{suffix}.csv"
    incomplete_path = out_dir / f"{args.out_prefix}_incomplete_UTE_patterns{suffix}.csv"
    u_path = out_dir / f"{args.out_prefix}_u_signals{suffix}.csv"
    combined_path = out_dir / f"{args.out_prefix}_all_patterns{suffix}.csv"

    completed_fields = [
        "StockCode",
        "U_Date",
        "T_Date",
        "E1_Date",
        "R_Date",
        "E2_Date",
        "Pattern_Complete_Date",
    ]
    incomplete_fields = ["StockCode", "U_Date", "T_Date", "E1_Date", "Status", "Last_Date"]
    u_fields = ["StockCode", "U_Date", "Status", "Last_Date"]
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

    latest_completed_by_ticker: dict[str, dict] = {}
    latest_incomplete_by_ticker: dict[str, dict] = {}
    latest_u_by_ticker: dict[str, dict] = {}

    # Download and scan in batches (no caching; intraday should refresh every run).
    completed_count = 0
    incomplete_count = 0
    u_count = 0

    with completed_path.open("w", newline="", encoding="utf-8") as f_completed, incomplete_path.open(
        "w", newline="", encoding="utf-8"
    ) as f_incomplete, u_path.open("w", newline="", encoding="utf-8") as f_u, combined_path.open(
        "w", newline="", encoding="utf-8"
    ) as f_combined:
        w_completed = csv.DictWriter(f_completed, fieldnames=completed_fields)
        w_incomplete = csv.DictWriter(f_incomplete, fieldnames=incomplete_fields)
        w_u = csv.DictWriter(f_u, fieldnames=u_fields)
        w_combined = csv.DictWriter(f_combined, fieldnames=combined_fields)

        w_completed.writeheader()
        w_incomplete.writeheader()
        w_u.writeheader()
        w_combined.writeheader()

        for batch_start in range(0, len(tickers), max(1, int(args.batch_size))):
            batch = tickers[batch_start : batch_start + max(1, int(args.batch_size))]
            batch_df = _download_intraday_batch(batch, start_dt=start_dt, end_dt=end_dt, interval=str(args.interval))

            for ticker in batch:
                df = _extract_ticker_frame(batch_df, ticker=ticker)
                if df is None or df.empty:
                    continue

                keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
                df = df[keep_cols]
                df = _basic_clean_ohlc(df)
                if df.empty:
                    continue

                # Coerce to tz-aware local timestamps, then filter to that day and session.
                df = _coerce_index_to_local(df, assume_tz=str(args.assume_tz), local_tz=local_tz)
                df = _filter_one_day_and_session(df, day=scan_day, session=session)
                if df.empty:
                    continue

                def _k_completed(r: dict) -> tuple:
                    return (
                        r.get("StockCode", ticker),
                        r.get("U_Date", ""),
                        r.get("T_Date", ""),
                        r.get("E1_Date", ""),
                        r.get("R_Date", ""),
                        r.get("E2_Date", ""),
                        "COMPLETED",
                    )

                def _k_incomplete(r: dict) -> tuple:
                    return (
                        r.get("StockCode", ticker),
                        r.get("U_Date", ""),
                        r.get("T_Date", ""),
                        r.get("E1_Date", ""),
                        "UTE_incomplete",
                    )

                def _k_u(r: dict) -> tuple:
                    return (r.get("StockCode", ticker), r.get("U_Date", ""), "U_only")

                completed_by_key: dict[tuple, dict] = {}
                incomplete_by_key: dict[tuple, dict] = {}
                u_by_key: dict[tuple, dict] = {}

                completed_support: dict[tuple, set[int]] = {}
                incomplete_support: dict[tuple, set[int]] = {}
                u_support: dict[tuple, set[int]] = {}

                for w in windows:
                    cfg_w = _make_scan_cfg(window_size=w)
                    c, inc, uo = scan_dataframe_for_patterns(
                        df,
                        ticker=ticker,
                        cfg=cfg_w,
                        scan_start_date=None,
                        output_datetime=True,
                    )

                    for row in c:
                        k = _k_completed(row)
                        completed_by_key.setdefault(k, row)
                        completed_support.setdefault(k, set()).add(int(w))

                    for row in inc:
                        k = _k_incomplete(row)
                        incomplete_by_key.setdefault(k, row)
                        incomplete_support.setdefault(k, set()).add(int(w))

                    for row in uo:
                        k = _k_u(row)
                        u_by_key.setdefault(k, row)
                        u_support.setdefault(k, set()).add(int(w))

                completed = [
                    r for k, r in completed_by_key.items() if len(completed_support.get(k, set())) >= min_support
                ]
                incomplete = [
                    r for k, r in incomplete_by_key.items() if len(incomplete_support.get(k, set())) >= min_support
                ]
                u_only = [r for k, r in u_by_key.items() if len(u_support.get(k, set())) >= min_support]

                completed.sort(key=lambda r: (r.get("StockCode", ""), r.get("Pattern_Complete_Date", ""), r.get("U_Date", "")))
                incomplete.sort(key=lambda r: (r.get("StockCode", ""), r.get("E1_Date", ""), r.get("U_Date", "")))
                u_only.sort(key=lambda r: (r.get("StockCode", ""), r.get("U_Date", "")))

                if args.latest_only:
                    if completed:
                        best = max(completed, key=lambda r: str(r.get("Pattern_Complete_Date", "")))
                        prev = latest_completed_by_ticker.get(ticker)
                        if (prev is None) or str(best.get("Pattern_Complete_Date", "")) > str(
                            prev.get("Pattern_Complete_Date", "")
                        ):
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
                    continue

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

        if args.latest_only:
            completed_rows = list(latest_completed_by_ticker.values())
            incomplete_rows = list(latest_incomplete_by_ticker.values())
            u_rows = list(latest_u_by_ticker.values())

            for row in sorted(completed_rows, key=lambda r: (r.get("StockCode", ""), r.get("Pattern_Complete_Date", ""))):
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

            for row in sorted(incomplete_rows, key=lambda r: (r.get("StockCode", ""), r.get("E1_Date", ""))):
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

            for row in sorted(u_rows, key=lambda r: (r.get("StockCode", ""), r.get("U_Date", ""))):
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

            completed_count = len(completed_rows)
            incomplete_count = len(incomplete_rows)
            u_count = len(u_rows)

    print(f"Intraday day={scan_day.isoformat()} interval={args.interval} market={args.market} tz={local_tz}")
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
