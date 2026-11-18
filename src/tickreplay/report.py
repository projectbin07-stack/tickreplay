"""Self-contained HTML report: summary table, equity curve and drawdown as inline SVG.

The file references nothing external - no CDN, fonts, scripts or images - so it can be
opened offline and archived next to the data that produced it. A few lines of inline
JavaScript add a hover read-out; the charts and the data table work without it.
"""

from __future__ import annotations

import html
import json
import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from tickreplay import __version__
from tickreplay.engine import RunResult
from tickreplay.metrics import Metrics, drawdown_series

DISCLAIMER = (
    "Simulated results from research/education software. They depend entirely on the "
    "input data and on simplified fee, slippage and fill assumptions. Nothing here is "
    "financial advice or evidence that a strategy is profitable."
)

_MAX_POINTS = 600
_W, _H = 880, 250
_ML, _MR, _MT, _MB = 70, 16, 12, 28

_CSS = """
:root{color-scheme:light;--surface:#fcfcfb;--panel:#ffffff;--text:#0b0b0b;--text2:#52514e;
--grid:#e4e3df;--axis:#b9b8b2;--s1:#2a78d6;--s2:#eb6834;--warn-bg:#fff4d6;--warn-bd:#c98500}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--surface:#1a1a19;--panel:#222221;
--text:#f0efec;--text2:#c3c2b7;--grid:#34342f;--axis:#5c5b55;--s1:#3987e5;--s2:#d95926;
--warn-bg:#3a2f10;--warn-bd:#c98500}}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--text);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
.sub{color:var(--text2);margin:0 0 16px}
.notice{border:1px solid var(--warn-bd);background:var(--warn-bg);border-radius:6px;
padding:10px 12px;margin:12px 0}
.notice strong{display:block}
table{border-collapse:collapse;width:100%;background:var(--panel)}
th,td{text-align:left;padding:5px 10px;border-bottom:1px solid var(--grid);
font-variant-numeric:tabular-nums}
th{color:var(--text2);font-weight:500}td.num,th.num{text-align:right}
td{overflow-wrap:anywhere}th{white-space:nowrap}.narrow{max-width:520px}
.chart{position:relative;background:var(--panel);border:1px solid var(--grid);border-radius:6px;
padding:8px}
svg{display:block;width:100%;height:auto}
svg text{fill:var(--text2);font-size:11px}
.gridline{stroke:var(--grid);stroke-width:1}.axisline{stroke:var(--axis);stroke-width:1}
.line1{fill:none;stroke:var(--s1);stroke-width:2;stroke-linejoin:round}
.line2{fill:none;stroke:var(--s2);stroke-width:2;stroke-linejoin:round}
.area2{fill:var(--s2);opacity:.18}
.cross{stroke:var(--axis);stroke-width:1;visibility:hidden}
.tip{position:absolute;pointer-events:none;background:var(--panel);border:1px solid var(--axis);
border-radius:4px;padding:4px 8px;font-size:12px;white-space:nowrap;visibility:hidden}
.tip b{display:block;font-size:13px}
details{margin-top:8px}summary{cursor:pointer;color:var(--text2)}
.scroll{max-height:320px;overflow:auto}
footer{color:var(--text2);margin-top:32px;font-size:12px}
"""

_JS = """
(function(){
var D=JSON.parse(document.getElementById('series').textContent);
function wire(id,key,fmt){
 var box=document.getElementById(id);if(!box)return;
 var svg=box.querySelector('svg'),cross=box.querySelector('.cross'),tip=box.querySelector('.tip');
 function move(ev){
  var r=svg.getBoundingClientRect(),x=(ev.clientX-r.left)/r.width*D.w,best=0,bd=1e18;
  for(var i=0;i<D.x.length;i++){var d=Math.abs(D.x[i]-x);if(d<bd){bd=d;best=i;}}
  cross.setAttribute('x1',D.x[best]);cross.setAttribute('x2',D.x[best]);
  cross.style.visibility='visible';
  tip.textContent='';var b=document.createElement('b');b.textContent=fmt(D[key][best]);
  tip.appendChild(b);tip.appendChild(document.createTextNode(D.t[best]));
  var px=D.x[best]/D.w*r.width;
  tip.style.left=Math.min(Math.max(px+12,0),r.width-150)+'px';tip.style.top='12px';
  tip.style.visibility='visible';
 }
 function leave(){cross.style.visibility='hidden';tip.style.visibility='hidden';}
 svg.addEventListener('pointermove',move);svg.addEventListener('pointerleave',leave);
}
wire('equity-chart','e',function(v){return v.toLocaleString(undefined,
 {minimumFractionDigits:2,maximumFractionDigits:2});});
wire('dd-chart','d',function(v){return (-v*100).toFixed(2)+'%';});
})();
"""


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def fmt_pct(value: float | None) -> str:
    """Format a fraction as a percentage, or ``n/a``."""
    return "n/a" if value is None else f"{value * 100:.2f}%"


def fmt_ratio(value: float | None) -> str:
    """Format a unitless ratio, or ``n/a`` / ``inf``."""
    if value is None:
        return "n/a"
    if math.isinf(value):
        return "inf"
    return f"{value:.2f}"


def fmt_money(value: Decimal) -> str:
    """Format a money amount with thousands separators and two decimals."""
    return f"{value:,.2f}"


def fmt_duration(value: timedelta) -> str:
    """Format a duration as days and hours."""
    hours = int(value.total_seconds() // 3600)
    return f"{hours // 24}d {hours % 24}h"


def metric_rows(result: RunResult, metrics: Metrics) -> list[tuple[str, str]]:
    """Label/value pairs shown in both the CLI summary and the HTML report."""
    return [
        ("Initial equity", fmt_money(result.config.initial_cash)),
        ("Final equity", fmt_money(result.final_equity)),
        ("Total return", fmt_pct(metrics.total_return)),
        ("CAGR", fmt_pct(metrics.cagr)),
        ("Volatility (annualised)", fmt_pct(metrics.volatility)),
        ("Sharpe", fmt_ratio(metrics.sharpe)),
        ("Sortino", fmt_ratio(metrics.sortino)),
        ("Max drawdown", fmt_pct(metrics.max_drawdown)),
        ("Max drawdown duration", fmt_duration(metrics.max_drawdown_duration)),
        ("Round trips", str(metrics.trades)),
        ("Win rate", fmt_pct(metrics.win_rate)),
        ("Profit factor", fmt_ratio(metrics.profit_factor)),
        ("Exposure (time in market)", fmt_pct(metrics.exposure)),
        ("Turnover (notional / mean equity)", fmt_ratio(metrics.turnover)),
        ("Fills", str(len(result.fills))),
        ("Orders rejected by risk", str(len(result.rejections))),
        ("Daily-loss halts", str(len(result.halts))),
        ("Fees paid", fmt_money(result.fees_paid)),
        ("Realised P&L (gross)", fmt_money(result.realised_pnl)),
        ("Unrealised P&L", fmt_money(result.unrealised_pnl)),
    ]


def _decimate(n: int, limit: int) -> list[int]:
    """Evenly spaced indices into ``range(n)``, always including first and last."""
    if n <= limit:
        return list(range(n))
    step = (n - 1) / (limit - 1)
    return sorted({round(i * step) for i in range(limit)})


def _nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + (abs(lo) or 1.0) * 0.01
    raw = (hi - lo) / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw)
    first = math.ceil(lo / step) * step
    ticks: list[float] = []
    value = first
    while value <= hi + step * 1e-9:
        ticks.append(0.0 if abs(value) < step * 1e-9 else value)
        value += step
    return ticks


def _chart(
    xs: Sequence[float],
    values: Sequence[float],
    stamps: Sequence[datetime],
    *,
    css: str,
    label: str,
    tick_format: str,
    area: bool,
) -> str:
    lo, hi = min(values), max(values)
    if area:
        hi = 0.0
    if hi <= lo:
        pad = abs(lo) * 0.01 or 1.0
        lo, hi = lo - pad, hi + (0.0 if area else pad)
    plot_h = _H - _MT - _MB

    def y(v: float) -> float:
        return _MT + (hi - v) / (hi - lo) * plot_h

    parts = [
        f'<svg viewBox="0 0 {_W} {_H}" role="img" aria-label="{_esc(label)}">',
    ]
    for tick in _nice_ticks(lo, hi):
        ty = y(tick)
        parts.append(
            f'<line class="gridline" x1="{_ML}" x2="{_W - _MR}" y1="{ty:.1f}" y2="{ty:.1f}"/>'
            f'<text x="{_ML - 6}" y="{ty + 4:.1f}" text-anchor="end">'
            f"{_esc(format(tick, tick_format))}</text>"
        )
    for i in _decimate(len(xs), 5):
        anchor = "start" if i == 0 else ("end" if i == len(xs) - 1 else "middle")
        parts.append(
            f'<text x="{xs[i]:.1f}" y="{_H - 8}" text-anchor="{anchor}">'
            f"{_esc(stamps[i].strftime('%Y-%m-%d'))}</text>"
        )
    points = " ".join(f"{x:.1f},{y(v):.1f}" for x, v in zip(xs, values, strict=True))
    if area:
        base = y(0.0)
        parts.append(
            f'<polygon class="area2" points="{xs[0]:.1f},{base:.1f} {points} '
            f'{xs[-1]:.1f},{base:.1f}"/>'
        )
    parts.append(f'<polyline class="{css}" points="{points}"/>')
    parts.append(
        f'<line class="axisline" x1="{_ML}" x2="{_W - _MR}" y1="{_H - _MB}" y2="{_H - _MB}"/>'
        f'<line class="cross" x1="0" x2="0" y1="{_MT}" y2="{_H - _MB}"/>'
        "</svg>"
    )
    return "".join(parts)


def _table(rows: Sequence[tuple[str, str]], head: tuple[str, str] | None = None) -> str:
    out = ["<table>"]
    if head:
        out.append(f'<tr><th>{_esc(head[0])}</th><th class="num">{_esc(head[1])}</th></tr>')
    out.extend(f'<tr><th>{_esc(k)}</th><td class="num">{_esc(v)}</td></tr>' for k, v in rows)
    out.append("</table>")
    return "".join(out)


def render_report(
    result: RunResult,
    metrics: Metrics,
    *,
    title: str = "tickreplay report",
    data_source: str = "",
    data_notes: Sequence[str] = (),
) -> str:
    """Render one run as a complete HTML document."""
    curve = result.equity_curve
    if not curve:
        raise ValueError("cannot render a report for an empty equity curve")
    keep = _decimate(len(curve), _MAX_POINTS)
    stamps = [curve[i].ts for i in keep]
    equity = [float(curve[i].equity) for i in keep]
    all_dd = drawdown_series([float(p.equity) for p in curve])
    drawdown = [-all_dd[i] * 100 for i in keep]

    t0 = stamps[0].timestamp()
    span = (stamps[-1].timestamp() - t0) or 1.0
    xs = [_ML + (s.timestamp() - t0) / span * (_W - _ML - _MR) for s in stamps]

    labels = [s.strftime("%Y-%m-%d %H:%M UTC") for s in stamps]
    series = {
        "w": _W,
        "x": [round(x, 1) for x in xs],
        "t": labels,
        "e": [round(v, 2) for v in equity],
        "d": [round(all_dd[i], 6) for i in keep],
    }
    series_json = json.dumps(series, separators=(",", ":")).replace("<", "\\u003c")

    cfg = result.config
    config_rows = [
        ("Mode", result.mode.value),
        ("Strategy", result.strategy_name),
        *((f"Parameter: {k}", v) for k, v in result.strategy_params.items()),
        ("Data source", data_source or "n/a"),
        ("First sample", curve[0].ts.strftime("%Y-%m-%d %H:%M UTC")),
        ("Last sample", curve[-1].ts.strftime("%Y-%m-%d %H:%M UTC")),
        ("Samples", str(len(curve))),
        ("Fee model", f"{cfg.fees.fixed} fixed + {cfg.fees.rate_bps} bps"),
        ("Slippage model", repr(cfg.slippage)),
        ("Max participation", str(cfg.max_participation)),
        ("Signal allocation", str(cfg.signal_allocation)),
        ("Risk limits", repr(cfg.risk)),
        ("Result digest (SHA-256)", result.digest()),
    ]
    sample_rows = "".join(
        f'<tr><td>{_esc(labels[j])}</td><td class="num">{equity[j]:,.2f}</td>'
        f'<td class="num">{drawdown[j]:.2f}%</td></tr>'
        for j in _decimate(len(keep), 200)
    )
    notes = "".join(f"<div>{_esc(n)}</div>" for n in data_notes)
    notes_block = (
        f'<div class="notice"><strong>Data note</strong>{notes}</div>' if data_notes else ""
    )
    eq_svg = _chart(
        xs,
        equity,
        stamps,
        css="line1",
        label="Equity curve",
        tick_format=",.0f",
        area=False,
    )
    dd_svg = _chart(
        xs,
        drawdown,
        stamps,
        css="line2",
        label="Drawdown from running peak, percent",
        tick_format=".1f",
        area=True,
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{_esc(title)}</h1>"
        f'<p class="sub">{_esc(result.strategy_name)} &middot; {_esc(result.mode.value)} mode'
        f" &middot; generated by tickreplay {_esc(__version__)}</p>"
        f'<div class="notice"><strong>Simulation only</strong>{_esc(DISCLAIMER)}</div>'
        f"{notes_block}"
        "<h2>Equity</h2>"
        f'<div class="chart" id="equity-chart">{eq_svg}<div class="tip"></div></div>'
        "<h2>Drawdown (%)</h2>"
        f'<div class="chart" id="dd-chart">{dd_svg}<div class="tip"></div></div>'
        '<details><summary>Show sampled data table</summary><div class="scroll"><table>'
        '<tr><th>Time</th><th class="num">Equity</th><th class="num">Drawdown</th></tr>'
        f"{sample_rows}</table></div></details>"
        '<section class="narrow"><h2>Summary</h2>'
        f"{_table(metric_rows(result, metrics))}</section>"
        f"<section><h2>Run configuration</h2>{_table(config_rows)}</section>"
        f"<footer>{_esc(DISCLAIMER)}</footer>"
        f'<script type="application/json" id="series">{series_json}</script>'
        f"<script>{_JS}</script>"
        "</main></body></html>\n"
    )
