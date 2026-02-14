from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st


REPO_ROOT = Path(__file__).resolve().parent
OUTPUTS_DIR = REPO_ROOT / "outputs"


def _list_ticker_files() -> list[Path]:
    files: list[Path] = []
    for p in REPO_ROOT.glob("tickers*.txt"):
        if p.is_file():
            files.append(p)
    return sorted(files)


def _run_command(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _read_csv_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def _download_button_for_file(path: Path, label: str) -> None:
    if not path.exists() or path.stat().st_size == 0:
        st.caption(f"Missing/empty: {path.relative_to(REPO_ROOT)}")
        return

    data = path.read_bytes()
    st.download_button(
        label=label,
        data=data,
        file_name=path.name,
        mime="text/csv",
    )


st.set_page_config(page_title="u-t-e-r-e Scanner", layout="wide")

st.title("u-t-e-r-e Pattern Mining")
st.caption("Rule-based scan. Daily or intraday (one day only).")

with st.sidebar:
    st.header("Run Settings")

    mode = st.selectbox("Mode", ["Daily (1d)", "Intraday (minute, one day)"])

    ticker_files = _list_ticker_files()
    if not ticker_files:
        st.error("No tickers*.txt files found in repo root.")
        st.stop()

    tickers_path = st.selectbox(
        "Tickers file",
        ticker_files,
        format_func=lambda p: p.name,
    )

    latest_only = st.checkbox("Latest-only outputs", value=True)

    if mode == "Daily (1d)":
        period = st.selectbox("Period", ["1y", "2y", "5y", "max"], index=2)
        window = st.number_input("Window (bars)", min_value=5, max_value=200, value=30, step=1)
        batch_size = st.number_input("Batch size", min_value=1, max_value=200, value=30, step=1)
        scan_start = st.text_input("Scan start date (optional)", value="")

        refresh_cache = st.checkbox("Refresh cache (re-download)", value=False)
        no_update_cache = st.checkbox("Do not top-up cache", value=False)
        no_cache = st.checkbox("Disable cache (always download)", value=False)

        run_label = "Run daily scan"

    else:
        day = st.date_input("Day", value=date.today())
        market = st.selectbox("Market", ["HK", "US"], index=0)
        interval = st.selectbox("Interval", ["1m", "2m", "5m", "15m", "30m"], index=0)
        window = st.number_input("Window (bars)", min_value=5, max_value=300, value=30, step=1)
        batch_size = st.number_input("Batch size", min_value=1, max_value=200, value=20, step=1)
        out_prefix = st.text_input("Output prefix", value="intraday")

        run_label = "Run intraday scan"

    run = st.button(run_label, type="primary")

st.divider()

col1, col2 = st.columns([1, 1])

if run:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    if mode == "Daily (1d)":
        cmd: list[str] = [
            sys.executable,
            "-m",
            "src.main",
            "--tickers",
            str(tickers_path),
            "--period",
            str(period),
            "--interval",
            "1d",
            "--window",
            str(int(window)),
            "--batch-size",
            str(int(batch_size)),
        ]

        if latest_only:
            cmd.append("--latest-only")

        if scan_start.strip():
            cmd += ["--scan-start", scan_start.strip()]

        if refresh_cache:
            cmd.append("--refresh-cache")
        if no_update_cache:
            cmd.append("--no-update-cache")
        if no_cache:
            cmd.append("--no-cache")

        code, stdout, stderr = _run_command(cmd)

        suffix = "_latest" if latest_only else ""
        completed_path = OUTPUTS_DIR / f"completed_patterns{suffix}.csv"
        incomplete_path = OUTPUTS_DIR / f"incomplete_UTE_patterns{suffix}.csv"
        u_path = OUTPUTS_DIR / f"u_signals{suffix}.csv"
        all_path = OUTPUTS_DIR / f"all_patterns{suffix}.csv"

    else:
        cmd = [
            sys.executable,
            "-m",
            "src.minute_scan",
            "--tickers",
            str(tickers_path),
            "--day",
            day.isoformat(),
            "--market",
            str(market),
            "--interval",
            str(interval),
            "--window",
            str(int(window)),
            "--batch-size",
            str(int(batch_size)),
            "--out-prefix",
            str(out_prefix).strip() or "intraday",
        ]

        if latest_only:
            cmd.append("--latest-only")

        code, stdout, stderr = _run_command(cmd)

        suffix = "_latest" if latest_only else ""
        prefix = str(out_prefix).strip() or "intraday"
        completed_path = OUTPUTS_DIR / f"{prefix}_completed_patterns{suffix}.csv"
        incomplete_path = OUTPUTS_DIR / f"{prefix}_incomplete_UTE_patterns{suffix}.csv"
        u_path = OUTPUTS_DIR / f"{prefix}_u_signals{suffix}.csv"
        all_path = OUTPUTS_DIR / f"{prefix}_all_patterns{suffix}.csv"

    with col1:
        if code == 0:
            st.success("Scan finished")
        else:
            st.error(f"Scan failed (exit code {code})")

        st.subheader("Console output")
        st.code(stdout or "(no stdout)")
        if stderr:
            st.subheader("Errors")
            st.code(stderr)

    with col2:
        st.subheader("Outputs")

        for p, label in [
            (completed_path, "Download completed patterns"),
            (incomplete_path, "Download incomplete UTE"),
            (u_path, "Download U-only"),
            (all_path, "Download all patterns"),
        ]:
            _download_button_for_file(p, label)

        st.subheader("Preview (first 50 rows)")
        preview = _read_csv_if_exists(all_path)
        if preview.empty:
            st.info("No rows (or file missing).")
        else:
            st.dataframe(preview.head(50), use_container_width=True)

else:
    st.info("Choose settings on the left, then click Run.")
    st.caption("Tip: intraday minute scans should use a SMALL ticker list to avoid rate limits.")
