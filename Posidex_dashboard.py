from __future__ import annotations

import hashlib
import json
import re
import base64
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from data_loader_tv import load_all

st.set_page_config(page_title="Festival TV", page_icon="📺", layout="wide")

#DEFAULT_SHEET_ID = "1U5echBzVt1l_8f8osQB_WvMflsWqeQx9lCR2uim4v3E"
#DEFAULT_GID = "1990713097"

DEFAULT_SHEET_ID = "1vZUZfWK8pQw-YSQLUO_lVC_652l51kdPBwY7abTzK9s"
DEFAULT_GID = "0"

# ============================================================
# COMPANY LOGO
# Keep the logo image in the same folder as this dashboard.
# Change only this filename if you want to use another logo.
# ============================================================
LOGO_PATH = Path(__file__).resolve().parent / "posidex.gif"

with open(LOGO_PATH, "rb") as f:
    LOGO_BASE64 = base64.b64encode(f.read()).decode("utf-8")

qp = st.query_params
SCALE = float(qp.get("scale", 1.0))
REFRESH = int(qp.get("refresh", 60))
SHEET_ID = qp.get("sid", DEFAULT_SHEET_ID)
GID = qp.get("gid", DEFAULT_GID)

LINE_COLORS = [
    "#D62728", "#1F77B4", "#2CA02C", "#9467BD", "#FF7F0E",
    "#17BECF", "#8C564B", "#E377C2", "#BCBD22", "#7F7F7F",
    "#1F4E79", "#A23E48", "#4B0082", "#008080", "#C55A11",
]

GOLD = "#E8A200"
LAVENDER = "#B3A2C7"
GREEN = "#375623"
ORANGE = "#F39C12"
PEAK_BLUE = "#2874A6"

# Cumulative race duration is based on the number of available hours.
# It is fast in the morning, but never too fast later in the day.
RACE_MIN_MS = 15000
RACE_PER_HOUR_MS = 2000
RACE_MAX_MS = 45000
RACE_HOLD_MS = 5000

LOWER_ANIMATION_MS = 1800


st.markdown(
    """
<style>
[data-testid="stHeader"],
[data-testid="stSidebar"],
[data-testid="stToolbar"],
[data-testid="stStatusWidget"],
#MainMenu,
footer,
.stDeployButton {
    display:none !important;
}

.block-container {
    padding:0 !important;
    margin:0 !important;
    max-width:100vw !important;
}

html,
body,
.stApp {
    overflow:hidden !important;
    background:#fff;
}

::-webkit-scrollbar {
    display:none;
}

[data-testid="stIFrame"],
iframe[title="st.iframe"] {
    position:fixed;
    inset:0;
    width:100vw !important;
    height:100vh !important;
    border:none !important;
    z-index:9999 !important;
}
</style>
""",
    unsafe_allow_html=True,
)


def _numeric(value: Any):
    try:
        if pd.isna(value):
            return None

        value = float(value)

        return value if pd.notna(value) else None

    except (TypeError, ValueError):
        return None


def _n(value: float) -> str:
    return f"{int(round(float(value))):,}"


def prepare_hourly(
    hourly: pd.DataFrame,
    clients: list[str],
):
    h = hourly.copy()

    h["Hour"] = pd.to_numeric(
        h["Hour"],
        errors="coerce",
    )

    h = (
        h.dropna(subset=["Hour"])
        .sort_values("Hour")
        .reset_index(drop=True)
    )

    observed_hours = {
        int(x)
        for x in h["Hour"].tolist()
    }

    max_observed_hour = (
        max(observed_hours)
        if observed_hours
        else -1
    )

    latest_by_client: dict[str, int] = {}
    missing: list[str] = []

    # A blank hour is NOT zero.
    # Each client stops at its first missing hour.
    for client in clients:

        latest = -1
        first_missing = None

        for hour in range(
            max_observed_hour + 1
        ):

            if hour not in observed_hours:
                first_missing = hour
                break

            row = h.loc[
                h["Hour"] == hour,
                client,
            ]

            value = (
                _numeric(row.iloc[0])
                if not row.empty
                else None
            )

            if value is None:
                first_missing = hour
                break

            latest = hour

        latest_by_client[client] = latest

        if first_missing is not None:
            missing.append(
                f"{client} — Hour {first_missing} not updated"
            )

    return (
        h,
        latest_by_client,
        missing,
    )


def add_title(
    fig,
    text,
    x,
    y,
    size,
):
    fig.add_annotation(
        text=f"<b>{text}</b>",
        x=x,
        y=y,
        xref="paper",
        yref="paper",
        xanchor="center",
        showarrow=False,
        font=dict(
            size=size,
            color="#333333",
        ),
    )


def build_page(
    hourly,
    avg_ms,
    daily_avg,
    festive_surge,
    peaks,
    surge_pct,
    ts,
) -> str:

    fs = lambda n: max(
        7,
        int(n * SCALE),
    )

    clients = [
        c
        for c in hourly.columns
        if c != "Hour"
    ]

    (
        h,
        latest_by_client,
        missing,
    ) = prepare_hourly(
        hourly,
        clients,
    )

    active = [
        c
        for c in clients
        if latest_by_client[c] >= 0
    ]

    if h.empty or not active:
        return (
            "<div style='padding:40px;"
            "font:700 28px Arial'>"
            "Waiting for hourly data…"
            "</div>"
        )

    max_latest = max(
        latest_by_client[c]
        for c in active
    )

    h = h[
        h["Hour"] <= max_latest
    ].copy()

    # ============================================================
    # CUMULATIVE DATA
    # ============================================================

    series = {}
    final_cumulative = {}

    for client in active:

        latest = latest_by_client[client]

        part = h[
            h["Hour"] <= latest
        ][
            ["Hour", client]
        ]

        cumulative = []
        running = 0.0

        for value in part[client]:

            value = _numeric(value)

            if value is None:
                break

            running += value
            cumulative.append(running)

        hours = [
            int(x)
            for x in part["Hour"].iloc[
                :len(cumulative)
            ]
        ]

        series[client] = {
            "hours": hours,
            "cumulative": cumulative,
            "latest": latest,
        }

        final_cumulative[client] = running

    main_ymax = max(
        1.0,
        max(
            final_cumulative.values()
        ) * 1.08,
    )

    # ============================================================
    # LOWER DATA
    # ============================================================

    avg_clients = [
        c
        for c in clients
        if _numeric(avg_ms.get(c)) is not None
    ]

    avg_values = [
        float(avg_ms[c])
        for c in avg_clients
    ]

    peak_clients = [
        c
        for c in clients
        if _numeric(peaks.get(c)) is not None
    ]

    peak_values = [
        float(peaks[c])
        for c in peak_clients
    ]

    daily_clients = [
        c
        for c in clients
        if _numeric(daily_avg.get(c)) is not None
    ]

    daily_values = [
        float(daily_avg[c])
        for c in daily_clients
    ]

    festive_clients = [
        c
        for c in clients
        if _numeric(
            festive_surge.get(c)
        ) is not None
    ]

    festive_values = [
        float(festive_surge[c])
        for c in festive_clients
    ]

    pct_clients = [
        c
        for c in clients
        if _numeric(
            surge_pct.get(c)
        ) is not None
    ]

    pct_values = [
        float(surge_pct[c])
        for c in pct_clients
    ]

    avg_ymax = max(
        1.0,
        (
            max(avg_values)
            if avg_values
            else 1.0
        ) * 1.25,
    )

    peak_ymax = max(
        1.0,
        (
            max(peak_values)
            if peak_values
            else 1.0
        ) * 1.20,
    )

    daily_ymax = max(
        1.0,
        max(
            daily_values
            + festive_values
            + [0.0]
        ) * 1.18,
    )

    pct_abs = max(
        [0.0]
        + [
            abs(x)
            for x in pct_values
        ]
    )

    pct_limit = max(
        1.0,
        pct_abs * 1.25,
    )

    # ============================================================
    # FIGURE
    # ============================================================

    fig = go.Figure()

    # ============================================================
    # CUMULATIVE CLIENT RACE
    #
    # This is intentionally a horizontal race rather than a
    # conventional multi-line chart. Each client gets its own
    # lane, so low-volume clients remain visible even when one
    # client has millions of hits.
    # ============================================================

    race_hours = sorted(
        {
            int(x)
            for x in h["Hour"].tolist()
            if int(x) <= max_latest
        }
    )

    # Use all active clients. A client that has stopped updating
    # keeps its last cumulative value in later frames.
    race_states = {}

    for frame_hour in race_hours:

        frame_values = {}

        for client in active:

            s = series[client]

            values = [
                value
                for hour, value in zip(
                    s["hours"],
                    s["cumulative"],
                )
                if hour <= frame_hour
            ]

            frame_values[client] = (
                float(values[-1])
                if values
                else 0.0
            )

        # Highest cumulative value appears at the top.
        ranked = sorted(
            active,
            key=lambda c: (
                frame_values[c],
                c,
            ),
            reverse=True,
        )

        race_states[frame_hour] = {
            "clients": ranked,
            "values": [
                frame_values[c]
                for c in ranked
            ],
        }

    first_state = race_states[race_hours[0]]

    # Actual cumulative race bars.
    # Keep each client as a stable trace identity and use numeric Y positions.
    # This lets Plotly continuously interpolate both bar length (X) and rank
    # position (Y) during a single race timeline.
    first_rank = {c: i for i, c in enumerate(first_state["clients"])}
    first_values_by_client = dict(
        zip(first_state["clients"], first_state["values"])
    )
    fig.add_bar(
        x=[float(first_values_by_client[c]) for c in active],
        y=[float(first_rank[c]) for c in active],
        orientation="h",
        marker_color=[
            LINE_COLORS[
                active.index(c) % len(LINE_COLORS)
            ]
            for c in active
        ],
        text=[
            _n(first_values_by_client[c])
            for c in active
        ],
        texttemplate="<b>\u00A0\u00A0%{text}</b>",
        textfont=dict(
            size=fs(42),
            family="Arial Black, Arial, sans-serif",
            color="#222222",
        ),
        textposition="outside",
        cliponaxis=False,
        showlegend=False,
        xaxis="x",
        yaxis="y",
        customdata=active,
        hovertemplate=(
            "<b>%{customdata}</b><br>"
            "Cumulative hits: %{x:,}"
            "<extra></extra>"
        ),
        width=0.72,
    )

    # ============================================================
    # LOWER CHARTS
    # ============================================================

    avg_idx = 1
    peak_idx = 2
    daily_idx = 3
    festive_idx = 4
    pct_idx = 5

    fig.add_bar(
        x=avg_clients,
        y=[0.0 for _ in avg_values],
        name="Average Processing Time(ms)",
        marker_color=GOLD,
        text=["" for _ in avg_values],
        texttemplate="<b>%{text}</b>",
        textposition="outside",
        textfont=dict(
        size=fs(16),
        family="Arial Black, Arial, sans-serif",
        color="#222222",
        ),
        cliponaxis=False,
        xaxis="x2",
        yaxis="y2",
        showlegend=False,
        customdata=avg_values,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Average processing time: "
            "%{customdata:.2f} ms"
            "<extra></extra>"
        ),
    )

    fig.add_bar(
        x=peak_clients,
        y=[0.0 for _ in peak_values],
        name="Peak Hits / Hour",
        marker_color=PEAK_BLUE,
        text=["" for _ in peak_values],
        texttemplate="<b>%{text}</b>",
        textposition="outside",
        textfont=dict(
        size=fs(12),
        family="Arial Black, Arial, sans-serif",
        color="#222222",
        ),
        cliponaxis=False,
        xaxis="x3",
        yaxis="y3",
        showlegend=False,
        customdata=peak_values,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Peak hourly hits: "
            "%{customdata:,}"
            "<extra></extra>"
        ),
    )

    fig.add_bar(
        x=daily_clients,
        y=[0.0 for _ in daily_values],
        name="Daily Average",
        marker_color=LAVENDER,
        text=["" for _ in daily_values],
        texttemplate="<b>%{text}</b>",
        textposition="outside",
        textfont=dict(
        size=fs(16),
        family="Arial Black, Arial, sans-serif",
        color="#222222",
        ),
        cliponaxis=False,
        xaxis="x4",
        yaxis="y4",
        showlegend=False,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Daily average: %{y:,}"
            "<extra></extra>"
        ),
    )

    fig.add_scatter(
        x=festive_clients,
        y=[0.0 for _ in festive_values],
        name="Festive Surge",
        mode="lines",
        marker=dict(size=0),
        line=dict(
            color=GREEN,
            width=max(1.4, 2.2 * SCALE),
        ),
        xaxis="x4",
        yaxis="y4",
        showlegend=False,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Festive surge total: %{y:,}"
            "<extra></extra>"
        ),
    )

    fig.add_scatter(
        x=pct_clients,
        y=[0.0 for _ in pct_values],
        name="Festive Surge %",
        mode="lines",
        marker=dict(size=0),
        line=dict(
            color=ORANGE,
            width=max(1.2, 1.8 * SCALE),
        ),
        xaxis="x4",
        yaxis="y5",
        showlegend=False,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Festive surge: %{y:.1f}%"
            "<extra></extra>"
        ),
    )

    # ============================================================
    # LAYOUT
    # ============================================================

    bottom_y = [0.0, 0.15]

    fig.update_layout(
        paper_bgcolor="white",
        plot_bgcolor="white",
        height=1000,
        margin=dict(l=125, r=70, t=105, b=50),
        font=dict(
            family="Arial, sans-serif",
            color="#333333",
        ),
        showlegend=False,

        # Main race: horizontal cumulative scale.
        xaxis=dict(
            domain=[0.18, 0.935],
            anchor="y",
            range=[
                0,
                max(1.0, max(first_state["values"] or [0.0]) * 1.18),
            ],
            tickfont=dict(size=fs(11)),
            gridcolor="#EEEEEE",
            zeroline=False,
            tickformat=",",
        ),

        # Main race lanes. Numeric Y positions are required so Plotly can
        # smoothly interpolate rank changes instead of snapping categories.
        yaxis=dict(
            domain=[0.300, 0.885],
            anchor="x",
            range=[len(first_state["clients"]) - 0.5, -0.5],
            tickmode="array",
            tickvals=list(range(len(first_state["clients"]))),
            ticktext=["" for _ in first_state["clients"]],
            tickfont=dict(
                size=fs(20),
                family="Arial Black, Arial, sans-serif",
            ),
            ticklabelstandoff=18,
            showticklabels=False,
            showgrid=False,
            zeroline=False,
        ),

        xaxis2=dict(
            domain=[0.055, 0.325],
            anchor="y2",
            type="category",
            tickmode="array",
            tickvals=avg_clients,
            ticktext=avg_clients,
            tickangle=-30,
            tickfont=dict(
                size=fs(16),
                family="Arial Black, Arial, sans-serif",
            ),
            title="Average Processing Time",
            title_font=dict(size=fs(20),
                           family="Arial Black, Arial, sans-serif",
                           ),
            title_standoff=25,
            showgrid=False,
            linecolor="#CCCCCC",
            automargin=True,
        ),

        yaxis2=dict(
            domain=bottom_y,
            anchor="x2",
            range=[0, avg_ymax],
            title="ms",
            title_standoff=5,
            tickfont=dict(size=fs(16)),
            gridcolor="#EEEEEE",
            zeroline=False,
        ),

        xaxis3=dict(
            domain=[0.365, 0.635],
            anchor="y3",
            type="category",
            tickmode="array",
            tickvals=peak_clients,
            ticktext=peak_clients,
            tickangle=-30,
            tickfont=dict(
                size=fs(16),
                family="Arial Black, Arial, sans-serif",
            ),
            title="Peak Hits / Hour",
            title_font=dict(size=fs(16),
                           family="Arial Black, Arial, sans-serif",
                           ),
            title_standoff=25,
            showgrid=False,
            linecolor="#CCCCCC",
            automargin=True,
        ),

        yaxis3=dict(
            domain=bottom_y,
            anchor="x3",
            range=[0, peak_ymax],
            title="Hits",
            title_standoff=5,
            tickfont=dict(size=fs(16)),
            gridcolor="#EEEEEE",
            zeroline=False,
        ),

        xaxis4=dict(
            domain=[0.675, 0.945],
            anchor="y4",
            type="category",
            tickmode="array",
            tickvals=daily_clients,
            ticktext=daily_clients,
            tickangle=-30,
            tickfont=dict(
                size=fs(16),
                family="Arial Black, Arial, sans-serif",
            ),
            title="Daily Avg vs Festive Surge",
            title_font=dict(size=fs(16),
                           family="Arial Black, Arial, sans-serif",
                           ),
            title_standoff=25,
            showgrid=False,
            linecolor="#CCCCCC",
            automargin=True,
            
        ),

        yaxis4=dict(
            domain=bottom_y,
            anchor="x4",
            range=[0, daily_ymax],
            title="Hits",
            title_standoff=5,
            tickfont=dict(size=fs(20)),
            gridcolor="#EEEEEE",
            zeroline=False,
        ),

        yaxis5=dict(
            overlaying="y4",
            side="right",
            range=[
                (
                    -pct_limit
                    if min(pct_values + [0]) < 0
                    else 0
                ),
                pct_limit,
            ],
            showgrid=False,
            tickfont=dict(size=fs(16)),
            title="%",
            title_standoff=5,
        ),
    )

    # ============================================================
    # TITLES
    # ============================================================

    add_title(
        fig,
        "Total Request Count",
        0.5,
        0.975,
        fs(20),
    )

   

    fig.add_annotation(
        text=(
            '<b style="color:#B3A2C7">'
            '■ Daily Average'
            '</b>'
            '&nbsp;&nbsp;'
            '<b style="color:#375623">'
            '━ Festive Surge'
            '</b>'
            '&nbsp;&nbsp;'
            '<b style="color:#F39C12">'
            '━ Festive Surge %'
            '</b>'
        ),
        x=0.95,
        y=0.21,
        xref="paper",
        yref="paper",
        xanchor="center",
        showarrow=False,
        font=dict(
            size=fs(17),
            color="#555555",
        ),
    )

    # ============================================================
    # RACE INFORMATION
    # ============================================================

    fig.add_annotation(
        text=(
            f"<b>Hour {race_hours[0]} → Hour {max_latest}</b>" # Hour 0 to Hour 1 text on Total count graph
        ),
        x=0.5,
        y=0.925,
        xref="paper",
        yref="paper",
        xanchor="center",
        showarrow=False,
        font=dict(
            size=fs(20),
            color="#555555",
        ),
    )

    # ============================================================
    # RACE TIMELINE METADATA
    # ============================================================

    # The race is rendered by a single requestAnimationFrame clock in the
    # browser. Plotly named-frame animation is deliberately NOT used here:
    # it can jump to the final frame instead of visibly interpolating the
    # cumulative bars in an embedded Streamlit iframe.
    race_timeline = []

    for frame_hour in race_hours:
        state = race_states[frame_hour]
        state_map = dict(zip(state["clients"], state["values"]))
        values_by_client = {
            c: float(state_map.get(c, 0.0))
            for c in active
        }
        ranked = sorted(
            active,
            key=lambda c: (values_by_client[c], c),
            reverse=True,
        )
        rank_by_client = {c: i for i, c in enumerate(ranked)}
        race_timeline.append(
            {
                "hour": int(frame_hour),
                "values": [values_by_client[c] for c in active],
                "ranks": [rank_by_client[c] for c in active],
                "order": ranked,
            }
        )

    # Keep the initial tick labels visible before JavaScript starts.
    initial_order = race_timeline[0]["order"]
    initial_labels = [
        f"{'★ ' if i == 0 else ''}{c}"
        for i, c in enumerate(initial_order)
    ]
    fig.update_layout(
        yaxis=dict(
            tickvals=list(range(len(initial_order))),
            ticktext=initial_labels,
            tickfont=dict(
                size=fs(15),
                family="Arial Black, Arial, sans-serif",
            ),
            ticklabelstandoff=18,
            showticklabels=True,
            automargin=True,
        )
    )

    # ============================================================
    # SIGNATURE
    # ============================================================

    signature = hashlib.md5(
        json.dumps(
            {
                "hourly": h.fillna(
                    "NA"
                ).to_dict("records"),
                "clients": active,
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()

    # ============================================================
    # PLOTLY HTML
    # ============================================================

    html = fig.to_html(
        full_html=False,
        include_plotlyjs="inline",
        default_width="100%",
        default_height="950px",
        config={
            "displayModeBar": False,
            "responsive": True,
        },
    )

    match = re.search(
        r'<div id="([^"]+)" class="plotly-graph-div"',
        html,
    )

    if not match:
        return html

    plot_id = match.group(1)


    js_daily = json.dumps(
        {
            c: float(daily_avg[c])
            for c in daily_clients
        }
    )

    js_festive = json.dumps(
        {
            c: float(festive_surge[c])
            for c in festive_clients
        }
    )

    js_cross = json.dumps(
        [
            c
            for c in daily_clients
            if (
                c in festive_clients
                and float(
                    festive_surge[c]
                )
                >
                float(
                    daily_avg[c]
                )
            )
        ]
    )

    js_clients = json.dumps(
        active
    )

    # ============================================================
    # ANIMATION JAVASCRIPT
    # ============================================================

    race_hours_js = json.dumps(race_hours)

    race_signature = json.dumps(
        signature
    )

    race_duration_js = f"""
    const AVAILABLE_HOURS = {len(race_hours)};
    const RACE_DURATION_MS = Math.max(
        {RACE_MIN_MS},
        Math.min(
            {RACE_MAX_MS},
            AVAILABLE_HOURS * {RACE_PER_HOUR_MS}
        )
    );
    const RACE_HOLD_MS = {RACE_HOLD_MS};
    const LOWER_ANIMATION_MS = {LOWER_ANIMATION_MS};
    const raceHours = {race_hours_js};
    const signature = {race_signature};
    const daily = {json.dumps({c: float(daily_avg[c]) for c in daily_clients})};
    const festive = {json.dumps({c: float(festive_surge[c]) for c in festive_clients})};
    const crossingClients = {json.dumps([c for c in daily_clients if c in festive_clients and float(festive_surge[c]) > float(daily_avg[c])])};
    """

    # ============================================================
    # CLIENT-SIDE ANIMATION
    # ============================================================

    # This script intentionally uses a plain template instead of a Python
    # f-string so JavaScript braces remain untouched and easy to audit.
    extra_script = r"""
<script>
(function() {
  const plot = document.getElementById(__PLOT_ID__);
  if (!plot) return;

  const RACE_DURATION_MS = __RACE_DURATION__;
  const LOWER_ANIMATION_MS = __LOWER_ANIMATION__;
  const raceTimeline = __RACE_TIMELINE__;
  const maxLatest = __MAX_LATEST__;
  const activeClients = __ACTIVE_CLIENTS__;

  const AVG_FINAL = __AVG_FINAL__;
  const PEAK_FINAL = __PEAK_FINAL__;
  const DAILY_FINAL = __DAILY_FINAL__;
  const FESTIVE_FINAL = __FESTIVE_FINAL__;
  const PCT_FINAL = __PCT_FINAL__;

  const daily = __DAILY_MAP__;
  const festive = __FESTIVE_MAP__;
  const clients = __CLIENTS__;

  const ease = t => t * t * (3 - 2 * t);
  function formatSurgeCount(value) {
    const n = Number(value);

    if (Math.abs(n) >= 100000) {
        return (n / 100000).toFixed(2) + 'L';
    }

    if (Math.abs(n) >= 1000) {
        return (n / 1000).toFixed(2) + 'K';
    }

    return Math.round(n).toLocaleString('en-IN');
}
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  // ------------------------------------------------------------
  // Session-safe clap state
  // ------------------------------------------------------------
  function getSessionStore() {
    try { return window.top.sessionStorage; } catch (e) {}
    try { return window.parent.sessionStorage; } catch (e) {}
    try { return window.sessionStorage; } catch (e) {}
    return null;
  }

  function clapSound() {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      const ctx = new AC();
      const now = ctx.currentTime;
      for (let i = 0; i < 4; i++) {
        const n = Math.floor(ctx.sampleRate * 0.075);
        const buffer = ctx.createBuffer(1, n, ctx.sampleRate);
        const data = buffer.getChannelData(0);
        for (let j = 0; j < n; j++) {
          data[j] = (Math.random() * 2 - 1) * Math.exp(-j / (n * 0.11));
        }
        const source = ctx.createBufferSource();
        const gain = ctx.createGain();
        source.buffer = buffer;
        source.connect(gain);
        gain.connect(ctx.destination);
        gain.gain.value = 0.15;
        source.start(now + i * 0.065);
      }
      setTimeout(() => { try { ctx.close(); } catch (e) {} }, 900);
    } catch (e) {}
  }

  function showClap(client) {
    const el = document.createElement('div');
    el.textContent = `👏 ${client} crossed Daily Average`;
    el.style.cssText =
      'position:fixed;left:50%;top:43%;transform:translate(-50%,-50%);' +
      'z-index:99999;background:rgba(255,255,255,.97);' +
      'border:1px solid #375623;border-radius:9px;padding:7px 15px;' +
      'font:bold 16px Arial,sans-serif;color:#375623;' +
      'box-shadow:0 3px 12px rgba(0,0,0,.16);pointer-events:none;' +
      'opacity:0;transition:opacity 140ms ease;';
    document.body.appendChild(el);
    requestAnimationFrame(() => { el.style.opacity = '1'; });
    setTimeout(() => {
      el.style.opacity = '0';
      setTimeout(() => { try { el.remove(); } catch (e) {} }, 180);
    }, 850);
  }

  const clapStore = getSessionStore();
  const crossed = {};
  clients.forEach(c => { crossed[c] = false; });

  // ------------------------------------------------------------
  // LOWER CHARTS — genuinely driven by the rendered Plotly values
  // ------------------------------------------------------------
  function markCrossings(dailyNow, festiveNow) {
    if (!clapStore) return;

    clients.forEach(client => {
      const d = Number(dailyNow[client] || 0);
      const f = Number(festiveNow[client] || 0);
      if (!(d > 0 && f >= d)) return;
      if (crossed[client]) return;

      const key = 'posidex_festive_clap_' + client;
      try {
        if (clapStore.getItem(key) === '1') {
          crossed[client] = true;
          return;
        }
      } catch (e) { return; }

      // This check happens after Plotly.restyle has completed for this frame.
      // Therefore it uses the actual values just rendered into the chart.
      try { clapStore.setItem(key, '1'); } catch (e) { return; }
      crossed[client] = true;
      clapSound();
      showClap(client);
    });
  }

  async function animateLowerCharts() {
    if (!window.Plotly || !plot._fullLayout) return;

    const frames = 60;
    const stepMs = LOWER_ANIMATION_MS / frames;
    const zero = n => Array(n).fill(0);

    function progressiveLine(values, progress) {
      const n = values.length;
      if (!n) return [];
      if (n === 1) return [values[0] * progress];
      const pos = progress * (n - 1);
      return values.map((value, i) => {
        if (i < Math.floor(pos)) return value;
        if (i > Math.ceil(pos)) return null;
        const local = Math.max(0, Math.min(1, pos - i));
        return value * ease(local);
      });
    }

    async function renderLower(avg, peak, dailyVals, festiveVals, pctVals, final) {
      await Plotly.restyle(
        plot,
        {
          y: [avg, peak, dailyVals, festiveVals, pctVals],
          text: [
            avg.map(v => Number(v).toFixed(2)),
            peak.map(v => Math.round(Number(v)).toLocaleString('en-IN')),
            // dailyVals.map(v => Math.round(Number(v)).toLocaleString('en-IN')),
            dailyVals.map(v => formatSurgeCount(v)),
            zero(FESTIVE_FINAL.length),
            zero(PCT_FINAL.length)
          ]
        },
        [1, 2, 3, 4, 5]
      );

      // Read exactly what is now rendered in Plotly's data model.
      const rendered = plot.data || [];
      const dailyTrace = rendered[3] && rendered[3].y ? Array.from(rendered[3].y) : [];
      const festiveTrace = rendered[4] && rendered[4].y ? Array.from(rendered[4].y) : [];
      const dailyNow = {};
      const festiveNow = {};
      dailyTrace.forEach((v, i) => {
        const name = plot.data[3].x[i];
        dailyNow[name] = Number(v) || 0;
      });
      festiveTrace.forEach((v, i) => {
        const name = plot.data[4].x[i];
        festiveNow[name] = Number(v) || 0;
      });
      markCrossings(dailyNow, festiveNow);
    }

    await renderLower(
      zero(AVG_FINAL.length),
      zero(PEAK_FINAL.length),
      zero(DAILY_FINAL.length),
      zero(FESTIVE_FINAL.length),
      zero(PCT_FINAL.length),
      false
    );

    for (let step = 1; step <= frames; step++) {
      const raw = step / frames;
      const t = ease(raw);

      await renderLower(
        AVG_FINAL.map(v => v * t),
        PEAK_FINAL.map(v => v * t),
        DAILY_FINAL.map(v => v * t),
        FESTIVE_FINAL.map(v => v * t),
        PCT_FINAL.map(v => v * t),
        step === frames
      );

      if (step < frames) await sleep(stepMs);
    }
  }

  // ------------------------------------------------------------
  // RACE — one real-time animation clock
  // ------------------------------------------------------------
  // ------------------------------------------------------------
  // OVERTAKE NOTIFICATION
  // Single-slot notification: never stacks multiple boxes.
  // If several overtakes happen while one is visible, only the latest
  // pending overtake is shown after the current one fades out.
  // ------------------------------------------------------------
  let overtakeElement = null;
  let overtakeBusy = false;
  let pendingOvertake = null;

  const OVERTAKE_SHOW_MS = 900;
  const OVERTAKE_FADE_MS = 160;
  const OVERTAKE_GAP_MS = 220;

  function getOvertakeElement() {
    if (overtakeElement && document.body.contains(overtakeElement)) {
      return overtakeElement;
    }

    overtakeElement = document.createElement('div');
    overtakeElement.style.cssText =
      'position:fixed;left:50%;top:39%;transform:translate(-50%,-50%);' +
      'z-index:99999;background:rgba(255,255,255,.97);' +
      'border:1px solid #F39C12;border-radius:9px;padding:6px 13px;' +
      'font:bold 16px Arial,sans-serif;color:#7A4E00;' +
      'box-shadow:0 3px 12px rgba(0,0,0,.16);pointer-events:none;' +
      'opacity:0;transition:opacity 160ms ease;white-space:nowrap;';

    document.body.appendChild(overtakeElement);
    return overtakeElement;
  }

  function showOvertake(client, fromRank, toRank) {
    // Replace any pending notification rather than allowing a pile-up.
    pendingOvertake = {
      client,
      fromRank,
      toRank
    };

    if (overtakeBusy) return;

    playNextOvertake();
  }

  function playNextOvertake() {
    if (overtakeBusy || !pendingOvertake) return;

    const event = pendingOvertake;
    pendingOvertake = null;
    overtakeBusy = true;

    const el = getOvertakeElement();
    el.textContent =
      `⚡ OVERTAKE  ${event.client}  #${event.fromRank} → #${event.toRank}`;

    // Reset transition state and show the same single DOM element.
    el.style.transition = 'none';
    el.style.opacity = '0';

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        el.style.transition = `opacity ${OVERTAKE_FADE_MS}ms ease`;
        el.style.opacity = '1';
      });
    });

    setTimeout(() => {
      el.style.opacity = '0';

      setTimeout(() => {
        overtakeBusy = false;

        // If another overtake happened while this was visible, show only
        // the latest one after a short breathing gap.
        if (pendingOvertake) {
          setTimeout(playNextOvertake, OVERTAKE_GAP_MS);
        }
      }, OVERTAKE_FADE_MS);
    }, OVERTAKE_SHOW_MS);
  }

  function updateHourText(hour) {
    const annotations = Array.from(plot.querySelectorAll('.annotation'));

    const target = annotations.find(el => {
      const value = (el.textContent || '').trim();
      return /^Hour\s+\d+\s*→\s*Hour\s+\d+$/i.test(value);
    });

    if (!target) return;

    const text = target.querySelector('text');
    if (text) {
      text.textContent =
        `Hour ${raceTimeline[0].hour} → Hour ${Math.max(
          raceTimeline[0].hour,
          Math.min(maxLatest, hour)
        )}`;
    }
  }

  function updateRaceTickLabels(order) {
    const labels = order.map((client, i) => `${i === 0 ? '★ ' : ''}${client}`);
    Plotly.relayout(plot, {
      'yaxis.tickvals': order.map((_, i) => i),
      'yaxis.ticktext': labels,
      'yaxis.showticklabels': true
    });
  }

  // ------------------------------------------------------------
  // Moving racer heads / subtle trails
  // ------------------------------------------------------------
  function ensureRaceOverlay() {
    let layer = document.getElementById('posidex-race-head-layer');
    if (layer) return layer;
    layer = document.createElement('div');
    layer.id = 'posidex-race-head-layer';
    layer.style.cssText =
      'position:absolute;inset:0;pointer-events:none;z-index:20;overflow:visible;';
    plot.parentElement.style.position = 'relative';
    plot.parentElement.appendChild(layer);
    return layer;
  }

  function updateRaceHeads() {
    const layer = ensureRaceOverlay();
    const points = Array.from(plot.querySelectorAll('.subplot.xy .barlayer .trace:first-child .point'));
    const visible = [];

    points.forEach((point, i) => {
      const rect = point.querySelector('path, rect');
      if (!rect) return;
      const r = rect.getBoundingClientRect();
      const pr = plot.getBoundingClientRect();
      const x = r.right - pr.left;
      const y = r.top + r.height / 2 - pr.top;
      if (!isFinite(x) || !isFinite(y) || r.width < 1 || r.height < 1) return;
      const bar = point.querySelector('path, rect');
      const fill = bar ? (bar.getAttribute('fill') || bar.style.fill || '') : '';
      visible.push({x, y, fill});
    });

    for (let i = 0; i < visible.length; i++) {
      let trail = layer.children[i * 2];
      let dot = layer.children[i * 2 + 1];

      if (!trail) {
        trail = document.createElement('div');
        trail.style.cssText =
          'position:absolute;height:3px;border-radius:3px;transform:translateY(-50%);' +
          'opacity:.22;filter:blur(.2px);';
        layer.appendChild(trail);
      }
      if (!dot) {
        dot = document.createElement('div');
        dot.style.cssText =
          'position:absolute;width:10px;height:10px;border-radius:50%;' +
          'transform:translate(-50%,-50%);box-shadow:0 0 0 3px rgba(255,255,255,.78),0 2px 8px rgba(0,0,0,.22);';
        layer.appendChild(dot);
      }

      const p = visible[i];
      const trailWidth = 20;
      trail.style.left = Math.max(0, p.x - trailWidth) + 'px';
      trail.style.top = p.y + 'px';
      trail.style.width = trailWidth + 'px';
      if (p.fill) trail.style.background = p.fill;

      dot.style.left = p.x + 'px';
      dot.style.top = p.y + 'px';
      if (p.fill) dot.style.background = p.fill;
    }

    while (layer.children.length > visible.length * 2) {
      layer.lastElementChild.remove();
    }

    if (!window.__posidexRaceHeadsRunning) {
      window.__posidexRaceHeadsRunning = true;
      const loop = () => {
        if (!document.body.contains(plot)) {
          window.__posidexRaceHeadsRunning = false;
          return;
        }
        updateRaceHeads();
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    }
  }

  function styleRaceLabels() {
    const labels = Array.from(plot.querySelectorAll('.annotation text'));
    labels.forEach(label => { label.style.fontWeight = '700'; });
  }

  function playRace() {
    if (!window.Plotly || !plot._fullLayout) {
      setTimeout(playRace, 100);
      return;
    }
    if (!raceTimeline.length || raceTimeline.length === 1) {
      updateRaceHeads();
      return;
    }

    const transitions = Math.max(1, raceTimeline.length - 1);
    const raceDuration = RACE_DURATION_MS;
    const start = performance.now();
    let lastOrder = raceTimeline[0].order.slice();
    let lastLabelKey = lastOrder.join("\u001f");
    let lastPaint = 0;

    const interpolate = (a, b, t) => a + (b - a) * t;

    function tick(now) {
      const raw = Math.max(0, Math.min(1, (now - start) / raceDuration));
      const eased = ease(raw);
      const segmentFloat = eased * (raceTimeline.length - 1);
      const seg = Math.min(raceTimeline.length - 2, Math.floor(segmentFloat));
      const local = raceTimeline.length <= 1 ? 1 : Math.max(0, Math.min(1, segmentFloat - seg));
      const localEased = ease(local);
      const a = raceTimeline[seg];
      const b = raceTimeline[Math.min(seg + 1, raceTimeline.length - 1)];

      const values = a.values.map((v, i) => interpolate(v, b.values[i], localEased));
      const ranks = a.ranks.map((v, i) => interpolate(v, b.ranks[i], localEased));

      // Only paint at ~30 FPS. The browser clock remains continuous, so the
      // animation duration never depends on request volume.
      if (now - lastPaint >= 30 || raw >= 1) {
        lastPaint = now;

        const currentOrder = activeClients.slice().sort((x, y) => {
          const ix = activeClients.indexOf(x);
          const iy = activeClients.indexOf(y);
          const dv = values[iy] - values[ix];
          return Math.abs(dv) < 0.001 ? x.localeCompare(y) : dv;
        });

        if (currentOrder.length === lastOrder.length) {
          for (let i = 0; i < currentOrder.length; i++) {
            const oldRank = lastOrder.indexOf(currentOrder[i]);
            if (oldRank > i) {
              showOvertake(currentOrder[i], oldRank + 1, i + 1);
              break;
            }
          }
        }
        lastOrder = currentOrder.slice();

        Plotly.restyle(plot, {
          x: [values],
          y: [ranks],
          text: [values.map(v => Math.round(v).toLocaleString('en-IN'))]
        }, [0]);

        const leader = Math.max(...values, 1);
        const frameHour = Math.min(
          maxLatest,
          Math.round(interpolate(a.hour, b.hour, local))
        );

        Plotly.relayout(plot, {
          'xaxis.range': [0, leader * 1.18],
          'yaxis.range': [activeClients.length - 0.5, -0.5]
        });
        updateHourText(frameHour);
        const labelKey = currentOrder.join("\u001f");
        if (labelKey !== lastLabelKey) {
          lastLabelKey = labelKey;
          updateRaceTickLabels(currentOrder);
        }
        updateRaceHeads();
      }

      if (raw < 1) {
        requestAnimationFrame(tick);
      } else {
        // Guarantee the exact final state.
        const finalState = raceTimeline[raceTimeline.length - 1];
        Plotly.restyle(plot, {
          x: [finalState.values],
          y: [finalState.ranks],
          text: [finalState.values.map(v => Math.round(v).toLocaleString('en-IN'))]
        }, [0]);
        Plotly.relayout(plot, {
          'xaxis.range': [0, Math.max(...finalState.values, 1) * 1.18]
        });
        updateHourText(finalState.hour);
        updateRaceTickLabels(finalState.order);
        updateRaceHeads();
      }
    }

    updateRaceHeads();
    requestAnimationFrame(tick);
  }

  // ------------------------------------------------------------
  // Start BOTH animations together.
  // ------------------------------------------------------------
  async function start() {
    if (!plot._fullLayout) {
      setTimeout(start, 100);
      return;
    }

    styleRaceLabels();
    updateRaceHeads();

    // Do not await either animation before starting the other.
    // The lower charts and cumulative race therefore animate simultaneously.
    const lowerPromise = animateLowerCharts();
    playRace();
    await lowerPromise;
  }

  setTimeout(start, 180);
})();
</script>
"""

    extra_script = (
        extra_script
        .replace("__PLOT_ID__", json.dumps(plot_id))
        .replace("__RACE_DURATION__", str(max(RACE_MIN_MS, min(RACE_MAX_MS, max(1, len(race_hours) - 1) * RACE_PER_HOUR_MS))))
        .replace("__LOWER_ANIMATION__", str(LOWER_ANIMATION_MS))
        .replace("__RACE_TIMELINE__", json.dumps(race_timeline))
        .replace("__MAX_LATEST__", str(max_latest))
        .replace("__ACTIVE_CLIENTS__", json.dumps(active))
        .replace("__AVG_FINAL__", json.dumps([float(v) for v in avg_values]))
        .replace("__PEAK_FINAL__", json.dumps([float(v) for v in peak_values]))
        .replace("__DAILY_FINAL__", json.dumps([float(v) for v in daily_values]))
        .replace("__FESTIVE_FINAL__", json.dumps([float(v) for v in festive_values]))
        .replace("__PCT_FINAL__", json.dumps([float(v) for v in pct_values]))
        .replace("__DAILY_MAP__", json.dumps({c: float(daily_avg[c]) for c in daily_clients}))
        .replace("__FESTIVE_MAP__", json.dumps({c: float(festive_surge[c]) for c in festive_clients}))
        .replace("__CLIENTS__", json.dumps(sorted(set(daily_clients) & set(festive_clients))))
    )


    updated = (
        pd.Timestamp(ts).strftime(
            "%d %b %Y %I:%M:%S %p"
        )
        if ts is not None
        else "—"
    )

    # ============================================================
    # HEADER
    # ============================================================

    header = f"""
<div style="
    position:fixed;
    top:0;
    left:0;
    width:100%;
    height:68px;
    z-index:1000;

    display:flex;
    align-items:center;
    justify-content:center;

    background:
        linear-gradient(
            90deg,
            #6A1B9A 0%,
            #1565C0 28%,
            #00897B 52%,
            #F9A825 76%,
            #E53935 100%
        );

    box-shadow:
        0 3px 12px
        rgba(0,0,0,.18);

    font-family:Arial,sans-serif;
">

  <!-- COMPANY LOGO -->
  <div style="
      position:absolute;
      left:18px;
      top:50%;
      transform:translateY(-50%);
      height:75px;
      width:78px;
      display:flex;
      align-items:center;
      justify-content:center;
  ">
      <img
          src="data:image/gif;base64,{LOGO_BASE64}"
          style="
              height:75px;
              width:auto;
              object-fit:contain;
              display:block;
          "
      >
  </div>

  <!-- DASHBOARD TITLE -->
  <div style="
      color:white;
      font-size:{fs(50)}px;
      font-weight:800;
      letter-spacing:1px;
      text-shadow:
          0 2px 4px
          rgba(0,0,0,.25);
  ">
      🎉 Posidex Festive Dashboard 🎉
  </div>

  <div style="
      position:absolute;
      right:14px;
      bottom:9px;

      color:
          rgba(255,255,255,.95);

      font-size:{fs(8)}px;
      font-weight:700;
      white-space:nowrap;
  ">
      Updated {updated}
      &nbsp;|&nbsp;
      Data through Hour {max_latest}
  </div>

</div>
"""

    # ============================================================
    # WARNING
    # Pending-hour alert intentionally removed from the TV dashboard.
    # ============================================================

    return (
        header
        + html
        + extra_script
    )


def render_tv():

    # Fresh CSV URL on every fragment refresh.
    # Prevents stale Google CSV data.
    url = (
        f"https://docs.google.com/"
        f"spreadsheets/d/{SHEET_ID}/"
        f"export?format=csv"
        f"&gid={GID}"
        f"&_ts={int(pd.Timestamp.now().timestamp())}"
    )

    try:

        (
            hourly,
            avg_ms,
            daily,
            festive,
            _peak_summary,
            _totals,
            ts,
        ) = load_all(url)

    except Exception as exc:

        st.error(
            "⚠️ Could not load Google Sheets data."
            f"\n\n`{exc}`"
        )

        return

    # ============================================================
    # PEAK HITS / HOUR
    # ============================================================

    peaks = {}

    for client in hourly.columns:

        if client == "Hour":
            continue

        values = pd.to_numeric(
            hourly[client],
            errors="coerce",
        )

        peaks[client] = (
            float(values.max())
            if values.notna().any()
            else None
        )

    # ============================================================
    # FESTIVE SURGE %
    # ============================================================

    surge_pct = {}

    for client in hourly.columns:

        if client == "Hour":
            continue

        d = _numeric(
            daily.get(client)
        )

        f = _numeric(
            festive.get(client)
        )

        if (
            d not in (None, 0)
            and f is not None
        ):

            surge_pct[client] = round(
                (
                    (f - d)
                    / d
                    * 100
                ),
                1,
            )

        else:

            surge_pct[client] = 0.0

    # ============================================================
    # RENDER
    # ============================================================

    components.html(
        build_page(
            hourly,
            avg_ms,
            daily,
            festive,
            peaks,
            surge_pct,
            ts,
        ),
        height=1000,
        scrolling=False,
    )


# ============================================================
# AUTO REFRESH
# ============================================================

if REFRESH > 0:

    render_tv = st.fragment(
        run_every=f"{REFRESH}s"
    )(render_tv)


render_tv()