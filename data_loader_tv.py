from __future__ import annotations

import io
import re

import pandas as pd
import streamlit as st


def _clean(cell) -> str:
    return str(cell).replace("\ufeff", "").replace("\xa0", " ").strip()


def _num(cell):
    s = _clean(cell).replace(",", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _is_int_cell(cell) -> bool:
    return bool(re.fullmatch(r"\d{1,2}", _clean(cell)))


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def _fetch_csv(url: str) -> str:
    import requests

    candidates = [url]
    m_sid = re.search(r"/spreadsheets/d/([\w-]+)", url)
    m_gid = re.search(r"[?&]gid=(\d+)", url)
    if m_sid and m_gid:
        sid, gid = m_sid.group(1), m_gid.group(1)
        candidates += [
            f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&gid={gid}",
            f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}",
        ]

    errors = []
    for u in candidates:
        try:
            resp = requests.get(u, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            body = resp.text.lstrip("\ufeff \r\n\t")
            low = body[:400].lower()
            if resp.status_code != 200:
                errors.append(f"HTTP {resp.status_code}")
            elif low.startswith("<!doctype") or "<html" in low:
                errors.append("got HTML (sheet not shared publicly?)")
            elif body.strip():
                return body
            else:
                errors.append("empty response")
        except Exception as e:
            errors.append(str(e))

    raise RuntimeError(
        "Google Sheets fetch failed → " + " · ".join(errors[:3]) +
        "\n👉 Fix: Share → General access → 'Anyone with the link' (Viewer)."
    )


def _looks_like_header(row_vals) -> bool:
    filled = [_clean(v) for v in row_vals if _clean(v)]
    if len(filled) < 3:
        return False
    numeric = sum(1 for v in filled if _num(v) is not None)
    return numeric < len(filled) / 2


def _nearest_header_above(grid: pd.DataFrame, r: int) -> dict[int, str]:
    for i in range(r - 1, -1, -1):
        if _looks_like_header(grid.iloc[i]):
            return {j: _clean(v) for j, v in enumerate(grid.iloc[i]) if _clean(v)}
    return {}


def _find_hour_headers(grid: pd.DataFrame) -> list[int]:
    hits = []
    for i in range(len(grid)):
        for v in grid.iloc[i]:
            if re.match(r"^hours?\b", _clean(v).lower()):
                hits.append(i)
                break
    return hits


def _pick_real_hour_header(grid: pd.DataFrame, candidates: list[int]) -> tuple[int, int]:
    for h in candidates:
        h_col = next(j for j, v in enumerate(grid.iloc[h]) if re.match(r"^hours?\b", _clean(v).lower()))
        data_rows = sum(1 for i in range(h + 1, min(h + 30, len(grid))) if _is_int_cell(grid.iat[i, h_col]))
        if data_rows >= 3:
            return h, h_col
    raise ValueError("Only empty 'HOUR' template rows found — no hourly data beneath any of them.")


def load_workbook(url: str) -> dict:
    grid = pd.read_csv(io.StringIO(_fetch_csv(url)), header=None, dtype=str).fillna("")
    ncols = grid.shape[1]
    h_idx, h_col = _pick_real_hour_header(grid, _find_hour_headers(grid))

    canon: dict[str, str] = {}
    for j, v in enumerate(grid.iloc[h_idx]):
        name = _clean(v)
        if name and j != h_col and not re.match(r"^hours?\b", name.lower()):
            canon[_norm(name)] = name

    def remap(raw_header: dict[int, str], row_vals) -> dict[str, float]:
        out = {}
        for j, raw_name in raw_header.items():
            n = _norm(raw_name)
            if not n or n.startswith("hour"):
                continue
            target = canon.get(n)
            if target is None:
                for cn, disp in canon.items():
                    if cn.startswith(n) or n.startswith(cn):
                        target = disp
                        break
            if target and j < ncols:
                v = _num(row_vals[j]) if j < len(row_vals) else None
                if v is not None:
                    out[target] = v
        return out

    client_cols = [
        (j, canon[_norm(v)])
        for j, v in enumerate(grid.iloc[h_idx])
        if _clean(v) and j != h_col and _norm(v) in canon
    ]

    rows = []
    for i in range(h_idx + 1, len(grid)):
        hv = _clean(grid.iat[i, h_col]) if h_col < ncols else ""
        if _is_int_cell(hv) and int(hv) <= 23:
            row = {"Hour": int(hv)}
            for j, name in client_cols:
                row[name] = _num(grid.iat[i, j]) if j < ncols else None
            rows.append(row)

    if not rows:
        raise ValueError("'Hour' header found but no numeric hour rows beneath it.")

    hourly = pd.DataFrame(rows).sort_values("Hour").reset_index(drop=True)

    # TV-specific labels. In particular, only the exact Avg Processing Time(ms)
    # metric is accepted; the plain Avg Processing Time row is intentionally ignored.
    LABELS = [
        ("surge_pct", lambda s: s.startswith("festive surge %")),
        ("surge_abs", lambda s: s == "festive surge"),
        ("daily_avg", lambda s: s.startswith("daily average")),
        ("avg_ms", lambda s: re.sub(r"\s+", "", s) == "avgprocessingtime(ms)"),
        ("peak", lambda s: s.startswith("highest")),
        ("total", lambda s: s.startswith("total request count")),
        ("completed", lambda s: s.startswith("completed count")),
    ]

    summary: dict[str, dict[str, float]] = {}
    for i in range(len(grid)):
        cells = [_clean(v) for v in grid.iloc[i]]
        label = next((c for c in cells if c), "")
        if not label or _looks_like_header(grid.iloc[i]):
            continue
        key_l = label.lower()
        key = next((k for k, test in LABELS if test(key_l)), None)
        if key is None:
            continue
        vals = remap(_nearest_header_above(grid, i), grid.iloc[i])
        if vals:
            summary[key] = vals

    return {"hourly": hourly, "summary": summary, "fetched_at": pd.Timestamp.now()}


def load_all(url: str):
    wb = load_workbook(url)
    s = wb["summary"]
    return (
        wb["hourly"],
        s.get("avg_ms", {}),
        s.get("daily_avg", {}),
        s.get("surge_abs", {}),
        s.get("peak", {}),
        s.get("total", {}),
        wb["fetched_at"],
    )
