from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ScanConfig:
    window_size: int = 30
    # Number of bars used to define the immediate downtrend before U.
    # Recommended: 3-5 (minimum 3).
    u_lookback: int = 3
    # Minimum bearish candles within the downtrend window.
    downtrend_bearish_min: int = 2
    # Use 1-year drawdown filter instead of strict downtrend rules.
    # Drawdown is measured from peak High in the last `u_drawdown_lookback` bars (ending at U-1)
    # to U's Close.
    u_use_drawdown_filter: bool = True
    u_drawdown_lookback: int = 252
    u_drawdown_min: float = 0.30
    u_drawdown_max: float = 0.50
    # If price makes a new low after U (below the pre-U bottom), reset and abandon the current setup.
    u_reset_on_new_low: bool = True
    # Require the candle before U to be bearish (helps enforce "first rebound" after downtrend).
    u_prev_must_be_bearish: bool = True
    # Require U close > previous bar's high.
    u_confirm_close_gt_prev_high: bool = True
    # Optional stricter confirmation: U close must be > previous close.
    u_confirm_close_gt_prev: bool = False
    max_r_bars: int = 10
    max_e2_bars: int = 3
    r_strict_next: bool = False


def _is_bullish(row: pd.Series) -> bool:
    return float(row["Close"]) > float(row["Open"])


def _is_bearish(row: pd.Series) -> bool:
    return float(row["Close"]) < float(row["Open"])


def _is_downtrend(df: pd.DataFrame, end_idx: int, bars: int, bearish_min: int) -> bool:
    """Return True if the bars ending at `end_idx` form a downtrend.

    Final strict definition (UTERE): all 3 must hold.

    1) Lower-Low structure on the most recent 3 bars:
       Low[end] < Low[end-1] < Low[end-2]
    2) Lower-High structure on the most recent 3 bars:
       High[end] < High[end-1] < High[end-2]
    3) Bearish candle count over the most recent 3–5 bars (ending at end_idx):
       count(Close < Open) >= bearish_min

    Note: the `bars` parameter controls only the bearish-count window and is clamped to [3, 5].
    """
    end_idx = int(end_idx)
    if end_idx < 2 or end_idx >= len(df):
        return False

    # ① + ②: structure uses the most recent 3 bars.
    low_0 = float(df.iloc[end_idx]["Low"])
    low_1 = float(df.iloc[end_idx - 1]["Low"])
    low_2 = float(df.iloc[end_idx - 2]["Low"])
    if not (low_0 < low_1 < low_2):
        return False

    high_0 = float(df.iloc[end_idx]["High"])
    high_1 = float(df.iloc[end_idx - 1]["High"])
    high_2 = float(df.iloc[end_idx - 2]["High"])
    if not (high_0 < high_1 < high_2):
        return False

    # ③: bearish candle count uses the most recent 3–5 bars.
    window_bars = int(bars)
    if window_bars < 3:
        window_bars = 3
    if window_bars > 5:
        window_bars = 5

    start_idx = end_idx - window_bars + 1
    if start_idx < 0:
        return False

    window = df.iloc[start_idx : end_idx + 1]
    opens = window["Open"].astype(float).to_numpy()
    closes = window["Close"].astype(float).to_numpy()
    bearish_count = int((closes < opens).sum())
    return bearish_count >= int(bearish_min)


def _passes_drawdown_filter(df: pd.DataFrame, u_idx: int, cfg: ScanConfig) -> bool:
    """Return True if price is ~30-50% off the peak before U (in bars).

    Definition (as requested):
    - peak = max(High) over last `cfg.u_drawdown_lookback` bars ending at (u_idx-1)
    - current = Close at U
    - drawdown = (peak - current) / peak
    - require cfg.u_drawdown_min <= drawdown <= cfg.u_drawdown_max
    """
    if u_idx <= 0:
        return False

    lookback = int(cfg.u_drawdown_lookback)
    if lookback < 2:
        lookback = 2

    end_idx = u_idx - 1
    start_idx = max(0, end_idx - lookback + 1)
    if start_idx > end_idx:
        return False

    window = df.iloc[start_idx : end_idx + 1]
    peak_high = float(window["High"].max())
    if peak_high <= 0:
        return False

    close_u = float(df.iloc[u_idx]["Close"])
    drawdown = (peak_high - close_u) / peak_high

    dd_min = float(min(cfg.u_drawdown_min, cfg.u_drawdown_max))
    dd_max = float(max(cfg.u_drawdown_min, cfg.u_drawdown_max))
    return dd_min <= drawdown <= dd_max


def _pre_u_bottom_low(df: pd.DataFrame, u_idx: int, cfg: ScanConfig) -> float | None:
    """Return the reference 'bottom' low before U.

    Definition (as selected): min(Low) over the last `cfg.u_lookback` bars ending at (u_idx-1).
    Note: `u_lookback` is clamped to >= 3 for consistency with the original downtrend logic.
    """
    if u_idx <= 0:
        return None

    prev_idx = u_idx - 1
    bars = int(cfg.u_lookback)
    if bars < 3:
        bars = 3

    start_idx = prev_idx - bars + 1
    if start_idx < 0:
        return None

    return float(df.iloc[start_idx : prev_idx + 1]["Low"].min())


def _u_rule(df: pd.DataFrame, u_idx: int, cfg: ScanConfig) -> bool:
    """U must be the first bullish reversal candle after a selloff.

    U conditions (engineering definition):
    1) U is bullish
     2) Either:
         - drawdown filter: U Close is ~30-50% below the last-1y peak High (configurable), OR
                 - strict downtrend: the 3 bars ending at (U-1) form a downtrend structure (Lower-Low + Lower-High),
                     and bearish_count over the last 3–5 bars (ending at U-1) >= min.
        3) Close_u > pre-U bottom low
             - drawdown mode: pre-U bottom is min(Low) over the last `u_lookback` bars ending at (U-1)
             - downtrend mode: pre-U bottom is min(Low) over the last 3 bars ending at (U-1)
    4) Close_u > High_{u-1} (configurable; previous bar)
    5) Optional strict: Close_u > Close_{u-1}
    """
    if u_idx <= 0:
        return False

    row_u = df.iloc[u_idx]
    if not _is_bullish(row_u):
        return False

    prev_idx = u_idx - 1
    # Enforce "first bullish" right after the downtrend.
    if _is_bullish(df.iloc[prev_idx]):
        return False

    if cfg.u_prev_must_be_bearish and not _is_bearish(df.iloc[prev_idx]):
        return False

    if cfg.u_use_drawdown_filter:
        if not _passes_drawdown_filter(df, u_idx=u_idx, cfg=cfg):
            return False

        lookback_bars = int(cfg.u_lookback)
        if lookback_bars < 3:
            lookback_bars = 3

        start_idx = prev_idx - lookback_bars + 1
        if start_idx < 0:
            return False

        min_low = float(df.iloc[start_idx : prev_idx + 1]["Low"].min())
    else:
        bearish_window_bars = int(cfg.u_lookback)
        if not _is_downtrend(
            df,
            end_idx=prev_idx,
            bars=bearish_window_bars,
            bearish_min=cfg.downtrend_bearish_min,
        ):
            return False

        # U-3 definition (strict downtrend mode): compare to the lowest Low of the last 3 bars ending at (U-1).
        start_idx = prev_idx - 3 + 1
        if start_idx < 0:
            return False
        min_low = float(df.iloc[start_idx : prev_idx + 1]["Low"].min())

    close_u = float(row_u["Close"])
    if not (close_u > min_low):
        return False

    if cfg.u_confirm_close_gt_prev_high:
        prev_high = float(df.iloc[prev_idx]["High"])
        if not (close_u > prev_high):
            return False

    if cfg.u_confirm_close_gt_prev:
        prev_close = float(df.iloc[prev_idx]["Close"])
        if not (close_u > prev_close):
            return False

    return True


def _t_rule(df: pd.DataFrame, t_idx: int) -> bool:
    return _is_bearish(df.iloc[t_idx])


def _e1_rule(df: pd.DataFrame, t_idx: int, e1_idx: int) -> bool:
    row_t = df.iloc[t_idx]
    row_e1 = df.iloc[e1_idx]
    return _is_bullish(row_e1) and float(row_e1["High"]) > float(row_t["High"])


def _is_local_swing_high(df: pd.DataFrame, r_idx: int) -> bool:
    # Requires r-1 and r+1 to exist
    if r_idx <= 0 or r_idx >= len(df) - 1:
        return False

    prev_row = df.iloc[r_idx - 1]
    row = df.iloc[r_idx]
    next_row = df.iloc[r_idx + 1]

    return (
        float(row["High"]) > float(prev_row["High"]) and
        float(row["High"]) > float(next_row["High"]) and
        float(row["Low"]) > float(prev_row["Low"]) and
        float(row["Low"]) > float(next_row["Low"])
    )


def _e2_rule(df: pd.DataFrame, r_idx: int, e2_idx: int) -> bool:
    row_r = df.iloc[r_idx]
    row_e2 = df.iloc[e2_idx]
    return _is_bullish(row_e2) and float(row_e2["High"]) > float(row_r["High"])


def scan_dataframe_for_patterns(
    df: pd.DataFrame,
    ticker: str,
    cfg: ScanConfig,
    scan_start_date: date | None = None,
    output_datetime: bool = False,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Scan one ticker DataFrame.

    Returns: (completed_rows, incomplete_rows, u_only_rows)
    """
    completed: list[dict] = []
    incomplete: list[dict] = []
    u_only: list[dict] = []

    if len(df) < 5:
        return completed, incomplete, u_only

    # Ensure required columns exist
    for col in ("Open", "High", "Low", "Close"):
        if col not in df.columns:
            return completed, incomplete, u_only

    def _fmt_idx(i: int) -> str:
        ts = pd.Timestamp(df.index[int(i)])
        if output_datetime:
            # Keep timezone info if present; ISO is unambiguous for intraday scans.
            return ts.isoformat()
        return ts.date().isoformat()

    lows_arr = df["Low"].astype(float).to_numpy()
    # suffix_min_lows[i] = min(Low[i:])
    suffix_min_lows = np.minimum.accumulate(lows_arr[::-1])[::-1]

    start_u_idx = 0
    if scan_start_date is not None:
        # DatetimeIndex is sorted by preprocessing; use searchsorted for efficiency.
        start_u_idx = int(df.index.searchsorted(pd.Timestamp(scan_start_date), side="left"))

    u_idx = int(start_u_idx)
    while u_idx < len(df) - 2:
        window_end = min(len(df), u_idx + cfg.window_size)
        if (u_idx + 1) >= window_end:
            break

        if not _u_rule(df, u_idx=u_idx, cfg=cfg):
            u_idx += 1
            continue

        reset_low = _pre_u_bottom_low(df, u_idx=u_idx, cfg=cfg)
        if cfg.u_reset_on_new_low and reset_low is None:
            u_idx += 1
            continue

        # Find T: first bearish candle after U (within window).
        t_idx: int | None = None
        reset_triggered = False
        reset_to_idx: int | None = None
        for i in range(u_idx + 1, window_end):
            if cfg.u_reset_on_new_low and reset_low is not None and float(df.iloc[i]["Low"]) < float(reset_low):
                reset_triggered = True
                reset_to_idx = i
                break
            if _t_rule(df, t_idx=i):
                t_idx = i
                break

        if reset_triggered and reset_to_idx is not None:
            u_idx = reset_to_idx + 1
            continue

        if t_idx is None:
            u_only.append(
                {
                    "StockCode": ticker,
                    "U_Date": _fmt_idx(u_idx),
                    "Status": "U_only",
                    "Last_Date": _fmt_idx(-1),
                }
            )
            u_idx += 1
            continue

        # Find E1: first bullish candle after T that breaks above T's High (within window).
        e1_idx: int | None = None
        reset_triggered = False
        reset_to_idx = None
        for j in range(t_idx + 1, window_end):
            if cfg.u_reset_on_new_low and reset_low is not None and float(df.iloc[j]["Low"]) < float(reset_low):
                reset_triggered = True
                reset_to_idx = j
                break
            if _e1_rule(df, t_idx=t_idx, e1_idx=j):
                e1_idx = j
                break

        if reset_triggered and reset_to_idx is not None:
            u_idx = reset_to_idx + 1
            continue

        if e1_idx is None:
            u_only.append(
                {
                    "StockCode": ticker,
                    "U_Date": _fmt_idx(u_idx),
                    "Status": "U_only",
                    "Last_Date": _fmt_idx(-1),
                }
            )
            u_idx += 1
            continue

        # At this point, we have U-T-E1
        # Search for R (local swing high) after E1.
        r_found_idx: int | None = None
        reset_triggered = False
        reset_to_idx = None
        if cfg.r_strict_next:
            r_idx = e1_idx + 1
            if r_idx < window_end:
                if cfg.u_reset_on_new_low and float(df.iloc[r_idx]["Low"]) < float(reset_low):
                    reset_triggered = True
                    reset_to_idx = r_idx
                elif _is_local_swing_high(df, r_idx=r_idx):
                    r_found_idx = r_idx
        else:
            search_r_start = e1_idx + 1
            # r needs r+1 to exist, so cap at (window_end - 1)
            search_r_end = min(window_end - 1, e1_idx + 1 + cfg.max_r_bars)
            for r_idx in range(search_r_start, search_r_end):
                if cfg.u_reset_on_new_low and float(df.iloc[r_idx]["Low"]) < float(reset_low):
                    reset_triggered = True
                    reset_to_idx = r_idx
                    break
                if _is_local_swing_high(df, r_idx=r_idx):
                    r_found_idx = r_idx
                    break

        if reset_triggered and reset_to_idx is not None:
            # New low after U => abandon this setup and continue scanning after the break.
            u_idx = reset_to_idx + 1
            continue

        if r_found_idx is None:
            # If the setup later breaks the pre-U bottom, do NOT keep it in the watchlist.
            if (
                cfg.u_reset_on_new_low
                and reset_low is not None
                and (u_idx + 1) < len(df)
                and float(suffix_min_lows[u_idx + 1]) < float(reset_low)
            ):
                u_idx += 1
                continue

            # Incomplete UTE (no R in horizon)
            incomplete.append(
                {
                    "StockCode": ticker,
                    "U_Date": _fmt_idx(u_idx),
                    "T_Date": _fmt_idx(t_idx),
                    "E1_Date": _fmt_idx(e1_idx),
                    "Status": "UTE_incomplete",
                    "Last_Date": _fmt_idx(-1),
                }
            )
            u_idx += 1
            continue

        # Search for E2 breakout after R.
        e2_found_idx: int | None = None
        search_e2_start = r_found_idx + 1
        search_e2_end = min(window_end, r_found_idx + 1 + cfg.max_e2_bars)

        for e2_idx in range(search_e2_start, search_e2_end):
            if cfg.u_reset_on_new_low and float(df.iloc[e2_idx]["Low"]) < float(reset_low):
                reset_triggered = True
                reset_to_idx = e2_idx
                break
            if _e2_rule(df, r_idx=r_found_idx, e2_idx=e2_idx):
                e2_found_idx = e2_idx
                break

        if reset_triggered and reset_to_idx is not None:
            u_idx = reset_to_idx + 1
            continue

        if e2_found_idx is None:
            if (
                cfg.u_reset_on_new_low
                and reset_low is not None
                and (u_idx + 1) < len(df)
                and float(suffix_min_lows[u_idx + 1]) < float(reset_low)
            ):
                u_idx += 1
                continue

            incomplete.append(
                {
                    "StockCode": ticker,
                    "U_Date": _fmt_idx(u_idx),
                    "T_Date": _fmt_idx(t_idx),
                    "E1_Date": _fmt_idx(e1_idx),
                    "Status": "UTE_incomplete",
                    "Last_Date": _fmt_idx(-1),
                }
            )
            u_idx += 1
            continue

        completed.append(
            {
                "StockCode": ticker,
                "U_Date": _fmt_idx(u_idx),
                "T_Date": _fmt_idx(t_idx),
                "E1_Date": _fmt_idx(e1_idx),
                "R_Date": _fmt_idx(r_found_idx),
                "E2_Date": _fmt_idx(e2_found_idx),
                "Pattern_Complete_Date": _fmt_idx(e2_found_idx),
            }
        )

        u_idx += 1

    # Optional de-dup: remove exact duplicates
    if completed:
        completed = pd.DataFrame(completed).drop_duplicates().to_dict(orient="records")
    if incomplete:
        incomplete = pd.DataFrame(incomplete).drop_duplicates().to_dict(orient="records")
    if u_only:
        u_only = pd.DataFrame(u_only).drop_duplicates().to_dict(orient="records")

    return completed, incomplete, u_only
