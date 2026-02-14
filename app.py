from __future__ import annotations

import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import tempfile

import pandas as pd
import streamlit as st


REPO_ROOT = Path(__file__).resolve().parent
OUTPUTS_DIR = REPO_ROOT / "outputs"
RUNTIME_DIR = REPO_ROOT / "runtime_uploads"


@dataclass(frozen=True)
class UiText:
    language: str  # "English" | "中文"

    def t(self, en: str, zh: str) -> str:
        return en if self.language == "English" else zh


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

language = st.sidebar.selectbox("Language / 語言", ["English", "中文"], index=0)
ui = UiText(language=language)

st.title(ui.t("u-t-e-r-e Pattern Mining", "u-t-e-r-e 形態掃描"))
st.caption(ui.t("Rule-based scan. Daily or intraday (one day only).", "規則掃描：日線 或 分鐘圖（只掃同一日）"))

with st.sidebar:
    st.header(ui.t("Run Settings", "運行設定"))

    mode = st.selectbox(ui.t("Mode", "模式"), [ui.t("Daily (1d)", "日線 (1d)"), ui.t("Intraday (minute, one day)", "分鐘圖（只掃同一日）")])

    ticker_files = _list_ticker_files()
    if not ticker_files:
        st.error(ui.t("No tickers*.txt files found in repo root.", "找不到 tickers*.txt（請放到專案根目錄）"))
        st.stop()

    ticker_source = st.radio(
        ui.t("Tickers source", "Ticker 來源"),
        [ui.t("Use existing file", "使用現有檔案"), ui.t("Upload .txt", "上傳 .txt"), ui.t("Paste tickers", "直接貼上")],
        index=0,
    )

    uploaded_file = None
    pasted_text = ""
    tickers_path: Path | None = None

    if ticker_source == ui.t("Use existing file", "使用現有檔案"):
        tickers_path = st.selectbox(
            ui.t("Tickers file", "Ticker 檔案"),
            ticker_files,
            format_func=lambda p: p.name,
        )
    elif ticker_source == ui.t("Upload .txt", "上傳 .txt"):
        uploaded_file = st.file_uploader(
            ui.t("Upload a tickers .txt (one ticker per line)", "上傳 tickers .txt（每行一個 ticker）"),
            type=["txt"],
        )
        if uploaded_file is not None:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            # Normalize to UTF-8 and strip BOM if present.
            raw = uploaded_file.getvalue()
            text = raw.decode("utf-8-sig", errors="replace")
            tmp_path = RUNTIME_DIR / f"uploaded_{uploaded_file.name}"
            tmp_path.write_text(text, encoding="utf-8")
            tickers_path = tmp_path
            st.caption(ui.t(f"Using uploaded file: {tmp_path.name}", f"使用上傳檔案：{tmp_path.name}"))
    else:
        pasted_text = st.text_area(
            ui.t("Paste tickers (one per line)", "貼上 tickers（每行一個）"),
            value="\n".join(["0700.HK", "9988.HK", "3690.HK"]),
            height=140,
        )

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        # Strip comments/blank lines and cap to a reasonable size.
        tickers: list[str] = []
        for line in pasted_text.splitlines():
            s = line.strip().lstrip("\ufeff")
            if not s or s.startswith("#"):
                continue
            tickers.append(s)

        max_tickers = 5000
        if len(tickers) > max_tickers:
            st.warning(ui.t(f"Too many tickers; keeping first {max_tickers}.", f"Ticker 太多；只保留頭 {max_tickers} 個。"))
            tickers = tickers[:max_tickers]

        tmp_path = RUNTIME_DIR / "pasted_tickers.txt"
        tmp_path.write_text("\n".join(tickers) + "\n", encoding="utf-8")
        tickers_path = tmp_path
        st.caption(ui.t(f"Tickers count: {len(tickers)}", f"Ticker 數量：{len(tickers)}"))

    latest_only = st.checkbox(ui.t("Latest-only outputs", "只保留每隻股票最新結果"), value=True)

    if tickers_path is None:
        st.warning(ui.t("Select or provide a tickers file first.", "請先選擇 / 提供 tickers 檔案。"))

    if mode == ui.t("Daily (1d)", "日線 (1d)"):
        period = st.selectbox(ui.t("Period", "Period"), ["1y", "2y", "5y", "max"], index=2)
        window = st.number_input(ui.t("Window (bars)", "Window（K線數）"), min_value=5, max_value=200, value=30, step=1)
        batch_size = st.number_input(ui.t("Batch size", "Batch size"), min_value=1, max_value=200, value=30, step=1)
        scan_start = st.text_input(ui.t("Scan start date (optional)", "掃描開始日期（可選）"), value="")

        refresh_cache = st.checkbox(ui.t("Refresh cache (re-download)", "重新下載（覆蓋快取）"), value=False)
        no_update_cache = st.checkbox(ui.t("Do not top-up cache", "不更新快取（只用現有）"), value=False)
        no_cache = st.checkbox(ui.t("Disable cache (always download)", "不使用快取（每次都下載）"), value=False)

        run_label = ui.t("Run daily scan", "開始日線掃描")

    else:
        day = st.date_input(ui.t("Day", "日期"), value=date.today())
        market = st.selectbox(ui.t("Market", "市場"), ["HK", "US"], index=0)
        interval = st.selectbox(ui.t("Interval", "分鐘間隔"), ["1m", "2m", "5m", "15m", "30m"], index=0)
        window = st.number_input(ui.t("Window (bars)", "Window（K線數）"), min_value=5, max_value=300, value=30, step=1)
        batch_size = st.number_input(ui.t("Batch size", "Batch size"), min_value=1, max_value=200, value=20, step=1)
        out_prefix = st.text_input(ui.t("Output prefix", "輸出檔案前綴"), value="intraday")

        if tickers_path is not None:
            # Intraday is easy to overwhelm; warn early.
            try:
                approx_lines = len(tickers_path.read_text(encoding="utf-8", errors="ignore").splitlines())
                if approx_lines > 200:
                    st.warning(
                        ui.t(
                            "Intraday minute scans should use a SMALL ticker list (e.g., < 200).",
                            "分鐘圖掃描建議用較少 ticker（例如 < 200），否則可能會慢 / 被 Yahoo 限制。",
                        )
                    )
            except Exception:
                pass

        run_label = ui.t("Run intraday scan", "開始分鐘圖掃描")

    run = st.button(run_label, type="primary", disabled=(tickers_path is None))

st.divider()

col1, col2 = st.columns([1, 1])

if run:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    if mode == ui.t("Daily (1d)", "日線 (1d)"):
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
            st.success(ui.t("Scan finished", "完成掃描"))
        else:
            st.error(ui.t(f"Scan failed (exit code {code})", f"掃描失敗（exit code {code}）"))

        st.subheader(ui.t("Console output", "Console 輸出"))
        st.code(stdout or "(no stdout)")
        if stderr:
            st.subheader(ui.t("Errors", "錯誤"))
            st.code(stderr)

    with col2:
        st.subheader(ui.t("Outputs", "輸出檔案"))

        for p, label in [
            (completed_path, ui.t("Download completed patterns", "下載：完整形態")),
            (incomplete_path, ui.t("Download incomplete UTE", "下載：未完成 UTE")),
            (u_path, ui.t("Download U-only", "下載：只有 U")),
            (all_path, ui.t("Download all patterns", "下載：全部結果")),
        ]:
            _download_button_for_file(p, label)

        st.subheader(ui.t("Preview (first 50 rows)", "預覽（頭 50 行）"))
        preview = _read_csv_if_exists(all_path)
        if preview.empty:
            st.info(ui.t("No rows (or file missing).", "冇結果（或者檔案不存在）。"))
        else:
            st.dataframe(preview.head(50), use_container_width=True)

else:
    st.info(ui.t("Choose settings on the left, then click Run.", "喺左邊選好設定，然後按 Run。"))
    st.caption(ui.t("Tip: intraday minute scans should use a SMALL ticker list to avoid rate limits.", "提示：分鐘圖掃描請用較少 ticker，避免 Yahoo 限制。"))
