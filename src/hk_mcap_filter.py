from __future__ import annotations

import argparse
import csv
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf


@dataclass(frozen=True)
class McapConfig:
    min_mcap: int = 10_000_000_000  # HKD 10B
    input_tickers: str = "tickers_hk_all.txt"
    out: str = "tickers_hk_市值_100億以上.txt"
    cache: str = "data/hk_mcap_cache.csv"
    max_retries: int = 3
    backoff_seconds: float = 2.0
    pause_seconds: float = 0.15


def _read_tickers(path: Path) -> list[str]:
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
    out: list[str] = []
    for line in lines:
        s = line.strip().lstrip("\ufeff")
        if not s or s.startswith("#"):
            continue
        out.append(_normalize_hk(s))
    return out


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sleep_backoff(attempt: int, base_seconds: float) -> None:
    jitter = random.uniform(0.0, 0.3)
    delay = base_seconds * (2 ** max(0, attempt - 1)) + jitter
    time.sleep(delay)


def _load_existing_cache(cache_path: Path) -> dict[str, dict]:
    if not cache_path.exists():
        return {}

    existing: dict[str, dict] = {}
    try:
        with cache_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                t = (row.get("Ticker") or "").strip()
                if not t:
                    continue
                existing[t] = row
    except Exception:
        return {}

    return existing


def _append_cache_row(cache_path: Path, row: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = (not cache_path.exists()) or cache_path.stat().st_size == 0
    with cache_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Currency", "MarketCap", "FetchedAt"],
        )
        if needs_header:
            writer.writeheader()
        writer.writerow(row)


def _fetch_mcap_one(ticker: str, cfg: McapConfig) -> tuple[int | None, str | None]:
    last_err: Exception | None = None
    for attempt in range(1, cfg.max_retries + 1):
        try:
            t = yf.Ticker(ticker)

            # Prefer fast_info: usually lighter.
            fi = getattr(t, "fast_info", None)
            if fi:
                mcap = fi.get("marketCap")
                cur = fi.get("currency")
                if isinstance(mcap, (int, float)) and mcap and mcap > 0:
                    return int(mcap), (str(cur) if cur else None)

            info = t.get_info()
            mcap = info.get("marketCap")
            cur = info.get("currency")
            if isinstance(mcap, (int, float)) and mcap and mcap > 0:
                return int(mcap), (str(cur) if cur else None)

            return None, (str(cur) if cur else None)
        except Exception as e:
            last_err = e
            if attempt >= cfg.max_retries:
                break
            _sleep_backoff(attempt, cfg.backoff_seconds)
        finally:
            time.sleep(max(0.0, float(cfg.pause_seconds)))

    _ = last_err
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Filter HK tickers by market cap using yfinance (writes tickers_hk_市值_100億以上.txt by default)."
    )
    parser.add_argument("--in", dest="input_tickers", type=str, default="tickers_hk_all.txt", help="Input tickers file")
    parser.add_argument("--out", type=str, default="tickers_hk_市值_100億以上.txt", help="Output filtered tickers file")
    parser.add_argument(
        "--min-mcap",
        type=int,
        default=10_000_000_000,
        help="Minimum market cap (default: 100億 = 10,000,000,000)",
    )
    parser.add_argument("--cache", type=str, default="data/hk_mcap_cache.csv", help="Cache CSV to allow resume")
    parser.add_argument("--max-retries", type=int, default=3, help="Retries per ticker")
    parser.add_argument("--backoff-seconds", type=float, default=2.0, help="Exponential backoff base seconds")
    parser.add_argument("--pause-seconds", type=float, default=0.15, help="Pause between tickers (rate-limit friendly)")

    args = parser.parse_args()

    cfg = McapConfig(
        min_mcap=int(args.min_mcap),
        input_tickers=str(args.input_tickers),
        out=str(args.out),
        cache=str(args.cache),
        max_retries=int(args.max_retries),
        backoff_seconds=float(args.backoff_seconds),
        pause_seconds=float(args.pause_seconds),
    )

    tickers = _read_tickers(Path(cfg.input_tickers))
    if not tickers:
        raise SystemExit(f"No tickers found in {cfg.input_tickers}")

    cache_path = Path(cfg.cache)
    existing = _load_existing_cache(cache_path)

    eligible: list[str] = []
    processed = 0
    fetched = 0

    for ticker in tickers:
        processed += 1

        row = existing.get(ticker)
        mcap_val: int | None = None
        cur_val: str | None = None

        if row:
            try:
                mcap_text = (row.get("MarketCap") or "").strip()
                mcap_val = int(float(mcap_text)) if mcap_text else None
            except Exception:
                mcap_val = None
            cur_val = (row.get("Currency") or "").strip() or None

        if mcap_val is None:
            mcap_val, cur_val = _fetch_mcap_one(ticker, cfg=cfg)
            fetched += 1
            _append_cache_row(
                cache_path,
                {
                    "Ticker": ticker,
                    "Currency": cur_val or "",
                    "MarketCap": str(mcap_val or ""),
                    "FetchedAt": _utc_now_iso(),
                },
            )

        if mcap_val is not None and mcap_val >= cfg.min_mcap:
            eligible.append(ticker)

        if processed % 50 == 0:
            print(
                f"Processed {processed}/{len(tickers)} | fetched={fetched} | eligible={len(eligible)} | last={ticker} mcap={mcap_val} {cur_val or ''}"
            )

    eligible = sorted(set(eligible))
    out_path = Path(cfg.out)
    out_path.write_text("\n".join(eligible) + "\n", encoding="utf-8")

    print(f"Eligible tickers (mcap >= {cfg.min_mcap}): {len(eligible)}")
    print(f"Wrote: {out_path}")
    print(f"Cache: {cache_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
