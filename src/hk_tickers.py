from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import urlretrieve

import pandas as pd

HKEX_LIST_URL = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"


def _download_hkex_list(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    urlretrieve(HKEX_LIST_URL, dest)  # noqa: S310 (trusted public source)


def _load_hkex_list(path: Path) -> pd.DataFrame:
    # HKEX file has 2 metadata rows; headers start at row index 2.
    df = pd.read_excel(path, sheet_name=0, header=2)
    return df


def _is_equity_stock(row: pd.Series) -> bool:
    cat = str(row.get("Category", "")).strip()
    sub = str(row.get("Sub-Category", "")).strip()

    if cat != "Equity":
        return False

    # Keep listed equities (Main Board + GEM). Exclude ETFs, debt, derivatives, etc.
    return sub.startswith("Equity Securities")


def _format_yahoo_hk(code: str) -> str | None:
    code = str(code).strip()
    if not code:
        return None

    # HKEX stock codes are numeric; ensure digits.
    if not code.isdigit():
        return None

    return f"{code.zfill(4)}.HK"


def generate_hk_tickers(xlsx_path: Path) -> list[str]:
    df = _load_hkex_list(xlsx_path)

    required_cols = {"Stock Code", "Category", "Sub-Category"}
    if not required_cols.issubset(set(df.columns)):
        missing = sorted(required_cols - set(df.columns))
        raise RuntimeError(f"Unexpected HKEX XLSX format. Missing columns: {missing}")

    df = df[df.apply(_is_equity_stock, axis=1)]

    tickers: list[str] = []
    for code in df["Stock Code"].tolist():
        t = _format_yahoo_hk(code)
        if t is not None:
            tickers.append(t)

    # De-dup + sort
    tickers = sorted(set(tickers))
    return tickers


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate full HK stock ticker list (Yahoo format like 0700.HK)")
    parser.add_argument("--out", type=str, default="tickers_hk_all.txt", help="Output ticker list file")
    parser.add_argument(
        "--xlsx",
        type=str,
        default="data/ListOfSecurities.xlsx",
        help="Path to HKEX ListOfSecurities.xlsx (will download if missing)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-download of the HKEX list even if the XLSX exists",
    )

    args = parser.parse_args()

    xlsx_path = Path(args.xlsx)
    if args.refresh or not xlsx_path.exists():
        _download_hkex_list(xlsx_path)

    tickers = generate_hk_tickers(xlsx_path)

    out_path = Path(args.out)
    out_path.write_text("\n".join(tickers) + "\n", encoding="utf-8")

    print(f"HK tickers generated: {len(tickers)}")
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
