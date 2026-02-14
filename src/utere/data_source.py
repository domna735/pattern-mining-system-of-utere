from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf


def _basic_clean_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Basic preprocessing:
    - ensure DatetimeIndex sorted
    - drop rows with NaN in OHLC
    - drop duplicate timestamps
    - remove obvious bad bars (High < Low, or Open/Close outside [Low, High])
    """
    needed = ["Open", "High", "Low", "Close"]
    df = df.dropna(subset=needed).sort_index()
    df = df[~df.index.duplicated(keep="first")]

    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    open_ = df["Open"].astype(float)
    close = df["Close"].astype(float)

    valid = (high >= low) & (open_ >= low) & (open_ <= high) & (close >= low) & (close <= high)
    df = df.loc[valid]
    return df


def fetch_ohlc_yfinance(ticker: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV from yfinance.

    Returns DataFrame with columns: Open, High, Low, Close, Volume and a DatetimeIndex.
    """
    df = yf.download(
        tickers=ticker,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )

    if df is None or len(df) == 0:
        return pd.DataFrame()

    # yfinance sometimes returns columns with lower-case or multi-index depending on params.
    if isinstance(df.columns, pd.MultiIndex):
        # Flatten if needed (e.g., when ticker returns multiindex)
        df.columns = [c[0] for c in df.columns]

    needed = ["Open", "High", "Low", "Close"]
    for col in needed:
        if col not in df.columns:
            return pd.DataFrame()

    # Keep Volume if present (optional)
    keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep_cols]

    return _basic_clean_ohlc(df)


def fetch_ohlc_yfinance_since(ticker: str, start: str, interval: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV from yfinance starting from a date (YYYY-MM-DD).

    Returns DataFrame with columns: Open, High, Low, Close, Volume and a DatetimeIndex.
    """
    df = yf.download(
        tickers=ticker,
        start=start,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )

    if df is None or len(df) == 0:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    needed = ["Open", "High", "Low", "Close"]
    for col in needed:
        if col not in df.columns:
            return pd.DataFrame()

    keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep_cols]
    return _basic_clean_ohlc(df)


@dataclass(frozen=True)
class FetchConfig:
    period: str = "5y"
    interval: str = "1d"
    cache_dir: str = "data/cache"
    use_cache: bool = True
    update_cache: bool = True
    refresh_cache: bool = False
    batch_size: int = 30
    max_retries: int = 3
    backoff_seconds: float = 2.0
    pause_between_batches: float = 0.2


def _merge_cached_with_delta(cached: pd.DataFrame, delta: pd.DataFrame) -> pd.DataFrame:
    if cached is None or cached.empty:
        return delta
    if delta is None or delta.empty:
        return cached
    out = pd.concat([cached, delta], axis=0)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return _basic_clean_ohlc(out)


def _cached_last_index(cached: pd.DataFrame) -> pd.Timestamp | None:
    if cached is None or cached.empty:
        return None
    try:
        ts = pd.to_datetime(cached.index.max())
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts


def _compute_safe_delta_start_str(last_timestamps: list[pd.Timestamp]) -> str | None:
    """Return a safe YYYY-MM-DD start string for delta downloads, or None.

    yfinance errors if `start` is after its implicit `end` (now). This happens
    when cached data already contains the most recent day, so `last + 1 day`
    becomes tomorrow.
    """
    starts: list[pd.Timestamp] = []
    for ts in last_timestamps:
        if ts is None:
            continue
        # Ensure tz-naive timestamps for consistent comparisons.
        if getattr(ts, "tzinfo", None) is not None:
            try:
                ts = ts.tz_convert(None)
            except Exception:
                ts = ts.tz_localize(None)
        starts.append(ts.normalize())
    if not starts:
        return None

    start_ts = min(starts) + pd.Timedelta(days=1)
    now_ts = pd.Timestamp.now(tz="UTC")
    try:
        now_ts = now_ts.tz_convert(None)
    except Exception:
        now_ts = now_ts.tz_localize(None)
    if getattr(start_ts, "tzinfo", None) is not None:
        try:
            start_ts = start_ts.tz_convert(None)
        except Exception:
            start_ts = start_ts.tz_localize(None)
    # If start is in the future (or later than now), there is nothing to fetch.
    if start_ts > now_ts:
        return None

    return start_ts.strftime("%Y-%m-%d")


def _cache_path(cache_dir: str, ticker: str, period: str, interval: str) -> Path:
    safe_ticker = ticker.replace("/", "-").replace("\\", "-").replace(":", "-")
    safe_period = period.replace("/", "-")
    safe_interval = interval.replace("/", "-")
    return Path(cache_dir) / f"{safe_ticker}__{safe_period}__{safe_interval}.csv"


def _read_cached_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    # Normalize index name for later code that expects DatetimeIndex.
    df.index = pd.to_datetime(df.index)
    return df


def _write_cached_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=True)


def _sleep_backoff(attempt: int, base_seconds: float) -> None:
    # Exponential backoff with a little jitter.
    jitter = random.uniform(0.0, 0.3)
    delay = base_seconds * (2 ** max(0, attempt - 1)) + jitter
    time.sleep(delay)


def _extract_ticker_frame(download_df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Extract a single-ticker OHLCV frame from a multi-ticker yfinance download."""
    if download_df is None or len(download_df) == 0:
        return pd.DataFrame()

    df = download_df
    if isinstance(df.columns, pd.MultiIndex):
        # Possible shapes:
        # 1) columns like ("Open", "0700.HK") ... ("Close", "0700.HK")
        # 2) columns like ("0700.HK", "Open") ...
        level0 = set(df.columns.get_level_values(0))
        level1 = set(df.columns.get_level_values(1))

        if ticker in level1:
            # (field, ticker)
            try:
                out = df.xs(ticker, axis=1, level=1, drop_level=True)
            except Exception:
                return pd.DataFrame()
            return out

        if ticker in level0:
            # (ticker, field)
            try:
                out = df.xs(ticker, axis=1, level=0, drop_level=True)
            except Exception:
                return pd.DataFrame()
            return out

        return pd.DataFrame()

    # Single-ticker already
    return df


def fetch_many_ohlc_yfinance(tickers: list[str], cfg: FetchConfig) -> dict[str, pd.DataFrame]:
    """Fetch multiple tickers with batching + cache + retries.

    Returns mapping ticker -> cleaned OHLC(V) DataFrame.
    """
    results: dict[str, pd.DataFrame] = {}
    remaining: list[str] = []

    # 1) Load from cache if possible (optionally top-up cache)
    cached_for_update: dict[str, pd.DataFrame] = {}
    cached_last: dict[str, pd.Timestamp] = {}
    for ticker in tickers:
        cache_path = _cache_path(cfg.cache_dir, ticker, cfg.period, cfg.interval)
        if cfg.use_cache and (not cfg.refresh_cache) and cache_path.exists():
            try:
                cached = _read_cached_csv(cache_path)
                cached = _basic_clean_ohlc(cached)
                if not cached.empty:
                    if cfg.update_cache:
                        cached_for_update[ticker] = cached
                        last_ts = _cached_last_index(cached)
                        if last_ts is not None:
                            cached_last[ticker] = last_ts
                    else:
                        results[ticker] = cached
                    continue
            except Exception:
                # Cache might be corrupted; re-fetch.
                pass
        remaining.append(ticker)

    # Optional: top-up cached tickers with missing newest bars
    if cfg.use_cache and (not cfg.refresh_cache) and cfg.update_cache and cached_for_update:
        # Download from the earliest last cached date (+1 day) to cover all.
        start_str = _compute_safe_delta_start_str(list(cached_last.values()))
        if start_str:
            batch_df: pd.DataFrame | None = None
            for attempt in range(1, cfg.max_retries + 1):
                try:
                    batch_df = yf.download(
                        tickers=" ".join(list(cached_for_update.keys())),
                        start=start_str,
                        interval=cfg.interval,
                        auto_adjust=False,
                        progress=False,
                        threads=False,
                        group_by="column",
                    )
                    break
                except Exception:
                    if attempt >= cfg.max_retries:
                        batch_df = None
                        break
                    _sleep_backoff(attempt, cfg.backoff_seconds)

            for ticker, cached in cached_for_update.items():
                delta = _extract_ticker_frame(batch_df, ticker=ticker) if batch_df is not None else pd.DataFrame()
                if not delta.empty:
                    keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in delta.columns]
                    delta = _basic_clean_ohlc(delta[keep_cols]) if keep_cols else pd.DataFrame()

                merged = _merge_cached_with_delta(cached, delta)
                if not merged.empty:
                    results[ticker] = merged
                    _write_cached_csv(_cache_path(cfg.cache_dir, ticker, cfg.period, cfg.interval), merged)
                else:
                    results[ticker] = cached
        else:
            # Could not determine last date; fall back to cached as-is.
            for ticker, cached in cached_for_update.items():
                results[ticker] = cached

    if not remaining:
        return results

    # 2) Batch fetch the rest
    for batch_start in range(0, len(remaining), max(1, cfg.batch_size)):
        batch = remaining[batch_start: batch_start + cfg.batch_size]

        batch_df: pd.DataFrame | None = None
        for attempt in range(1, cfg.max_retries + 1):
            try:
                batch_df = yf.download(
                    tickers=" ".join(batch),
                    period=cfg.period,
                    interval=cfg.interval,
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                    group_by="column",
                )
                break
            except Exception:
                if attempt >= cfg.max_retries:
                    batch_df = None
                    break
                _sleep_backoff(attempt, cfg.backoff_seconds)

        if batch_df is None or len(batch_df) == 0:
            # Fallback: try per-ticker downloads so one failure doesn't kill the whole batch.
            for ticker in batch:
                df = pd.DataFrame()
                for attempt in range(1, cfg.max_retries + 1):
                    try:
                        df = fetch_ohlc_yfinance(ticker=ticker, period=cfg.period, interval=cfg.interval)
                        break
                    except Exception:
                        if attempt >= cfg.max_retries:
                            df = pd.DataFrame()
                            break
                        _sleep_backoff(attempt, cfg.backoff_seconds)

                df = _basic_clean_ohlc(df) if not df.empty else df
                if not df.empty:
                    results[ticker] = df
                    if cfg.use_cache:
                        _write_cached_csv(_cache_path(cfg.cache_dir, ticker, cfg.period, cfg.interval), df)
            time.sleep(max(0.0, cfg.pause_between_batches))
            continue

        for ticker in batch:
            single = _extract_ticker_frame(batch_df, ticker=ticker)
            if single.empty:
                continue

            # Keep only expected columns and basic-clean.
            keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in single.columns]
            single = single[keep_cols]
            single = _basic_clean_ohlc(single)
            if single.empty:
                continue

            results[ticker] = single
            if cfg.use_cache:
                _write_cached_csv(_cache_path(cfg.cache_dir, ticker, cfg.period, cfg.interval), single)

        time.sleep(max(0.0, cfg.pause_between_batches))

    return results


def iter_many_ohlc_yfinance(tickers: list[str], cfg: FetchConfig):
    """Yield (ticker, DataFrame) with batching + cache + retries.

    This is the memory-friendly version for large universes.
    """
    remaining: list[str] = []

    # 1) Load cached (optionally top-up) and track what still needs full download
    cached_for_update: dict[str, pd.DataFrame] = {}
    cached_last: dict[str, pd.Timestamp] = {}
    for ticker in tickers:
        cache_path = _cache_path(cfg.cache_dir, ticker, cfg.period, cfg.interval)
        if cfg.use_cache and (not cfg.refresh_cache) and cache_path.exists():
            try:
                cached = _read_cached_csv(cache_path)
                cached = _basic_clean_ohlc(cached)
                if not cached.empty:
                    if cfg.update_cache:
                        cached_for_update[ticker] = cached
                        last_ts = _cached_last_index(cached)
                        if last_ts is not None:
                            cached_last[ticker] = last_ts
                    else:
                        yield ticker, cached
                    continue
            except Exception:
                pass
        remaining.append(ticker)

    # 1b) Top-up cached tickers in batches, then yield them
    if cfg.use_cache and (not cfg.refresh_cache) and cfg.update_cache and cached_for_update:
        cached_tickers = list(cached_for_update.keys())
        for batch_start in range(0, len(cached_tickers), max(1, cfg.batch_size)):
            batch = cached_tickers[batch_start: batch_start + cfg.batch_size]
            start_str = _compute_safe_delta_start_str([cached_last.get(t) for t in batch])
            if start_str:
                batch_df: pd.DataFrame | None = None
                for attempt in range(1, cfg.max_retries + 1):
                    try:
                        batch_df = yf.download(
                            tickers=" ".join(batch),
                            start=start_str,
                            interval=cfg.interval,
                            auto_adjust=False,
                            progress=False,
                            threads=False,
                            group_by="column",
                        )
                        break
                    except Exception:
                        if attempt >= cfg.max_retries:
                            batch_df = None
                            break
                        _sleep_backoff(attempt, cfg.backoff_seconds)
            else:
                batch_df = None

            for ticker in batch:
                cached = cached_for_update[ticker]
                delta = _extract_ticker_frame(batch_df, ticker=ticker) if batch_df is not None else pd.DataFrame()
                if not delta.empty:
                    keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in delta.columns]
                    delta = _basic_clean_ohlc(delta[keep_cols]) if keep_cols else pd.DataFrame()

                merged = _merge_cached_with_delta(cached, delta)
                if not merged.empty and cfg.use_cache:
                    _write_cached_csv(_cache_path(cfg.cache_dir, ticker, cfg.period, cfg.interval), merged)
                yield ticker, (merged if not merged.empty else cached)

            time.sleep(max(0.0, cfg.pause_between_batches))

    if not remaining:
        return

    # 2) Batch fetch the rest
    for batch_start in range(0, len(remaining), max(1, cfg.batch_size)):
        batch = remaining[batch_start: batch_start + cfg.batch_size]

        batch_df: pd.DataFrame | None = None
        for attempt in range(1, cfg.max_retries + 1):
            try:
                batch_df = yf.download(
                    tickers=" ".join(batch),
                    period=cfg.period,
                    interval=cfg.interval,
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                    group_by="column",
                )
                break
            except Exception:
                if attempt >= cfg.max_retries:
                    batch_df = None
                    break
                _sleep_backoff(attempt, cfg.backoff_seconds)

        if batch_df is None or len(batch_df) == 0:
            # Fallback: per-ticker so one failure doesn't kill the whole batch.
            for ticker in batch:
                df = pd.DataFrame()
                for attempt in range(1, cfg.max_retries + 1):
                    try:
                        df = fetch_ohlc_yfinance(ticker=ticker, period=cfg.period, interval=cfg.interval)
                        break
                    except Exception:
                        if attempt >= cfg.max_retries:
                            df = pd.DataFrame()
                            break
                        _sleep_backoff(attempt, cfg.backoff_seconds)

                df = _basic_clean_ohlc(df) if not df.empty else df
                if not df.empty:
                    if cfg.use_cache:
                        _write_cached_csv(_cache_path(cfg.cache_dir, ticker, cfg.period, cfg.interval), df)
                    yield ticker, df

            time.sleep(max(0.0, cfg.pause_between_batches))
            continue

        for ticker in batch:
            single = _extract_ticker_frame(batch_df, ticker=ticker)
            if single.empty:
                continue

            keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in single.columns]
            single = single[keep_cols]
            single = _basic_clean_ohlc(single)
            if single.empty:
                continue

            if cfg.use_cache:
                _write_cached_csv(_cache_path(cfg.cache_dir, ticker, cfg.period, cfg.interval), single)
            yield ticker, single

        time.sleep(max(0.0, cfg.pause_between_batches))
