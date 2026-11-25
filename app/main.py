"""FastAPI web application for Warehouse IT Monitor."""

from __future__ import annotations

import asyncio
import html
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.database import (
    analytics_buckets,
    analytics_summary,
    dashboard_stats,
    init_db,
    list_events,
    list_services,
    query_check_history,
    sync_services,
)
from app.monitor import run_all_checks, validate_service_config
from app.notifications import TelegramSettings, notification_worker
from app.scheduler import CheckScheduler

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "monitor.sqlite3"
DEFAULT_SERVICES_PATH = BASE_DIR / "config" / "services.json"
APP_VERSION = "0.7.0"
PERIODS: dict[str, tuple[timedelta, int]] = {
    "24h": (timedelta(hours=24), 3600),
    "7d": (timedelta(days=7), 6 * 3600),
    "30d": (timedelta(days=30), 24 * 3600),
    "90d": (timedelta(days=90), 3 * 24 * 3600),
}

V050_CATEGORIES = (
    "OK",
    "SLOW_RESPONSE",
    "HTTP_STATUS",
    "TIMEOUT",
    "DNS_ERROR",
    "CONNECTION_REFUSED",
    "CONNECTION_RESET",
    "TLS_ERROR",
    "NETWORK_ERROR",
    "UNKNOWN_ERROR",
    "UNCLASSIFIED",
)


def _read_config(config_path: str | Path) -> dict[str, Any]:
    """Read JSON with or without a UTF-8 BOM (as written by Windows Notepad)."""
    path = Path(config_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in configuration file: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("services"), list):
        raise ValueError("Configuration must contain a 'services' array")
    return raw


def load_services(config_path: str | Path) -> list[dict[str, Any]]:
    raw = _read_config(config_path)
    services = [validate_service_config(item) for item in raw["services"]]
    urls = [item["url"] for item in services]
    if len(urls) != len(set(urls)):
        raise ValueError("Service URLs must be unique")
    return services


def load_scheduler_settings(config_path: str | Path) -> dict[str, Any]:
    """Load and validate the optional scheduler section of the JSON config."""
    raw = _read_config(config_path)
    scheduler = raw.get("scheduler", {})
    if not isinstance(scheduler, dict):
        raise ValueError("Configuration 'scheduler' must be a JSON object")

    enabled = scheduler.get("enabled", True)
    interval_seconds = scheduler.get("interval_seconds", 60)
    if not isinstance(enabled, bool):
        raise ValueError("scheduler.enabled must be true or false")
    if (
        not isinstance(interval_seconds, int)
        or isinstance(interval_seconds, bool)
        or not 5 <= interval_seconds <= 86400
    ):
        raise ValueError("scheduler.interval_seconds must be an integer between 5 and 86400")
    return {"enabled": enabled, "interval_seconds": interval_seconds}


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _period_window(period: str) -> tuple[datetime, datetime, int]:
    if period not in PERIODS:
        raise HTTPException(status_code=422, detail="period must be one of: 24h, 7d, 30d, 90d")
    duration, bucket_seconds = PERIODS[period]
    end_at = datetime.now(timezone.utc)
    return end_at - duration, end_at, bucket_seconds


def build_analytics_payload(
    db_path: str | Path,
    *,
    period: str,
    service_id: int | None = None,
) -> dict[str, Any]:
    """Return sample-based availability and fixed-width time buckets."""
    start_dt, end_dt, bucket_seconds = _period_window(period)
    start_at, end_at = _utc_iso(start_dt), _utc_iso(end_dt)
    summary = analytics_summary(
        db_path, start_at=start_at, end_at=end_at, service_id=service_id
    )
    aggregated = analytics_buckets(
        db_path,
        start_at=start_at,
        end_at=end_at,
        bucket_seconds=bucket_seconds,
        service_id=service_id,
    )
    by_epoch = {item["bucket_epoch"]: item for item in aggregated}
    start_epoch = int(start_dt.timestamp())
    first_bucket = (start_epoch // bucket_seconds) * bucket_seconds
    end_epoch = int(end_dt.timestamp())
    buckets: list[dict[str, Any]] = []
    for epoch in range(first_bucket, end_epoch + bucket_seconds, bucket_seconds):
        if epoch > end_epoch:
            break
        row = by_epoch.get(epoch)
        if row is None:
            total = up = down = 0
            avg_latency = None
        else:
            total = row["total_checks"]
            up = row["up_checks"]
            down = row["down_checks"]
            avg_latency = row["avg_up_response_time_ms"]
        buckets.append(
            {
                "bucket_start": _utc_iso(datetime.fromtimestamp(epoch, timezone.utc)),
                "total_checks": total,
                "up_checks": up,
                "down_checks": down,
                "availability_pct": round(up / total * 100, 2) if total else None,
                "avg_up_response_time_ms": avg_latency,
            }
        )

    return {
        "period": period,
        "service_id": service_id,
        "start_at": start_at,
        "end_at": end_at,
        "bucket_seconds": bucket_seconds,
        "method": "sample_based",
        "method_description": "Erfolgreiche UP-Prüfungen geteilt durch alle abgeschlossenen UP- und DOWN-Prüfungen; keine kontinuierliche Betriebszeit.",
        "summary": summary,
        "buckets": buckets,
    }


PAGE_CSS = """
<style>
:root { color-scheme:light; --ink:#182230; --muted:#667085; --line:#e4e7ec; --bg:#f5f7fa; --navy:#132338; --green:#067647; --red:#b42318; --amber:#b54708; --blue:#175cd3; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 Inter,Segoe UI,Arial,sans-serif; }
header { background:var(--navy); color:#fff; padding:25px max(22px,calc((100% - 1150px)/2)); }
header .eyebrow { color:#b6c6d9; font-size:12px; letter-spacing:.12em; text-transform:uppercase; }
h1 { margin:5px 0 2px; font-size:25px; font-weight:650; }
header p { margin:0; color:#d7e1ec; }
main { max-width:1150px; margin:26px auto; padding:0 20px 36px; }
.topline { display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:18px; }
button { background:var(--blue); border:0; border-radius:8px; color:white; padding:10px 15px; font-weight:600; cursor:pointer; }
button:hover { background:#1849a9; }
button:disabled { opacity:.55; cursor:wait; }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:13px; margin:18px 0 25px; }
.card,.panel { background:white; border:1px solid var(--line); border-radius:12px; }
.card { padding:16px 18px; }
.card .label { color:var(--muted); font-size:13px; }
.card .value { font-size:25px; font-weight:700; margin-top:4px; }
.panel { padding:18px; margin-top:18px; }
h2 { margin:0 0 13px; font-size:17px; }
table { width:100%; border-collapse:collapse; }
th,td { padding:10px 9px; text-align:left; border-bottom:1px solid #eef0f3; vertical-align:top; }
th { color:var(--muted); font-size:12px; font-weight:600; }
tr:last-child td { border-bottom:0; }
.name { font-weight:650; }
.url { font-size:12px; color:var(--muted); overflow-wrap:anywhere; }
.status { display:inline-flex; align-items:center; gap:7px; font-size:12px; font-weight:700; border-radius:99px; padding:4px 9px; }
.status:before { content:''; width:7px; height:7px; border-radius:50%; background:currentColor; }
.up { color:var(--green); background:#ecfdf3; }
.down { color:var(--red); background:#fef3f2; }
.wait { color:var(--amber); background:#fffaeb; }
.muted { color:var(--muted); }
.event { display:flex; gap:12px; padding:10px 0; border-bottom:1px solid #eef0f3; }
.event:last-child { border-bottom:0; }
.event time { color:var(--muted); font-size:12px; min-width:155px; }
.schedule { display:flex; align-items:center; gap:12px; flex-wrap:wrap; color:var(--muted); font-size:13px; }
.schedule strong { color:var(--ink); }
.error { color:var(--red); font-size:12px; margin-top:3px; }
footer { color:var(--muted); font-size:12px; margin-top:18px; }
#message,.hint { color:var(--muted); font-size:13px; }
.filters { display:flex; flex-wrap:wrap; align-items:end; gap:10px; margin:10px 0 16px; }
.filters label { display:flex; flex-direction:column; gap:4px; color:var(--muted); font-size:12px; font-weight:600; }
select { min-width:125px; border:1px solid #d0d5dd; border-radius:8px; padding:9px 10px; background:white; color:var(--ink); font:inherit; }
.chart-wrap { width:100%; overflow:hidden; }
.chart { display:block; width:100%; height:auto; min-height:170px; }
.chart-empty { fill:var(--muted); font-size:14px; }
.legend { display:flex; gap:16px; flex-wrap:wrap; color:var(--muted); font-size:12px; margin-top:4px; }
.legend span:before { content:''; display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:6px; background:var(--legend-color); }
.pagination { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:12px; }
.pagination button { padding:7px 12px; }
.warn { color:var(--amber); font-weight:600; }
@media(max-width:760px) { .stats { grid-template-columns:repeat(2,1fr); } table { display:block; overflow-x:auto; white-space:nowrap; } .event { display:block; } .event time { display:block; margin-bottom:3px; } .filters label,select { width:100%; } .filters button { width:100%; } }
</style>
"""


CATEGORY_LABELS = {
    "OK": "In Ordnung", "SLOW_RESPONSE": "Langsame Antwort",
    "HTTP_STATUS": "HTTP-Status", "TIMEOUT": "Zeitüberschreitung",
    "DNS_ERROR": "DNS-Fehler", "CONNECTION_REFUSED": "Verbindung abgelehnt",
    "CONNECTION_RESET": "Verbindung zurückgesetzt", "TLS_ERROR": "TLS-Fehler",
    "NETWORK_ERROR": "Netzwerkfehler", "UNKNOWN_ERROR": "Unbekannter Fehler",
    "UNCLASSIFIED": "Nicht klassifiziert",
}
SEVERITY_LABELS = {
    "WARNING": "Warnung", "INFO": "Information", "ERROR": "Fehler",
    "CRITICAL": "Kritisch", "UNCLASSIFIED": "Nicht klassifiziert",
}


def _category_label(value: str) -> str:
    return CATEGORY_LABELS.get(value, value)


def _severity_label(value: str) -> str:
    return SEVERITY_LABELS.get(value, value)


def _service_options(services: list[dict[str, Any]]) -> str:
    options = ['<option value="">Alle Dienste</option>']
    for service in services:
        options.append(
            f'<option value="{int(service["id"])}">{html.escape(service["name"])}</option>'
        )
    return "".join(options)


def render_dashboard(db_path: str | Path, scheduler_status: dict[str, Any] | None = None) -> str:
    services = list_services(db_path)
    events = list_events(db_path, 15)
    stats = dashboard_stats(db_path)
    scheduler_status = scheduler_status or {
        "enabled": False,
        "interval_seconds": 60,
        "next_check_at": None,
        "is_running": False,
    }
    schedule_summary = (
        f"Automatische Prüfungen alle {scheduler_status['interval_seconds']} Sekunden"
        if scheduler_status.get("enabled") else "Automatische Prüfungen deaktiviert"
    )
    scheduler_state = "Prüfung läuft" if scheduler_status.get("is_running") else schedule_summary

    rows: list[str] = []
    for item in services:
        status = item["last_status"]
        if status == "UP":
            status_html = '<span class="status up">UP</span>'
        elif status == "DOWN":
            status_html = '<span class="status down">DOWN</span>'
        else:
            status_html = '<span class="status wait">NICHT GEPRÜFT</span>'
        error_html = f'<div class="error">{html.escape(item["last_error_message"] or "")}</div>' if item["last_error_message"] else ""
        checked = html.escape(item["last_checked_at"] or "—")
        latency = f'{item["last_response_time_ms"]:.0f} ms' if item["last_response_time_ms"] is not None else "—"
        category = _category_label(item["last_result_category"] or "UNCLASSIFIED")
        severity = _severity_label(item["last_severity"] or "UNCLASSIFIED")
        severity_html = (
            f'<span class="warn">{html.escape(severity)}</span>'
            if item["last_severity"] == "WARNING" else html.escape(severity)
        )
        rows.append(
            "<tr>"
            f'<td><div class="name">{html.escape(item["name"])}</div><div class="url">{html.escape(item["url"])}</div></td>'
            f"<td>{status_html}{error_html}</td>"
            f'<td>{"TCP" if item["check_type"] == "tcp" else "HTTP"}</td>'
            f"<td>{html.escape(category)}</td>"
            f"<td>{severity_html}</td>"
            f"<td>{latency}</td><td>{checked}</td></tr>"
        )
    rows_html = "".join(rows) or '<tr><td colspan="7" class="muted">Keine Dienste konfiguriert. Füge einen autorisierten Dienst zu config/services.json hinzu.</td></tr>'

    event_html: list[str] = []
    for event in events:
        cls = "up" if event["new_status"] == "UP" else "down"
        event_html.append(
            '<div class="event">'
            f'<time>{html.escape(event["occurred_at"])}</time>'
            f'<div><span class="status {cls}">{html.escape(event["new_status"])}</span> '
            f'<strong>{html.escape(event["service_name"])}</strong>'
            f'<div class="muted">{html.escape(event["details"])}</div></div></div>'
        )
    events_html = "".join(event_html) or '<p class="muted">Noch keine Statusänderungen vorhanden. Starte die erste Prüfung, um einen Eintrag zu erstellen.</p>'
    service_options = _service_options(services)

    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Warehouse IT Monitor</title>{PAGE_CSS}</head>
<body>
<header><div class="eyebrow">Lernprojekt · v{APP_VERSION}</div><h1>Warehouse IT Monitor</h1><p>Verfügbarkeitsübersicht der ausdrücklich konfigurierten IT-Dienste</p></header>
<main>
  <div class="topline"><div><strong>Infrastrukturübersicht</strong><div class="schedule"><span id="scheduler-state">{html.escape(scheduler_state)}</span><span>·</span><span>Nächste Prüfung: <strong id="next-check">—</strong></span></div></div>
    <div><button id="run">Prüfungen jetzt ausführen</button> <span id="message" aria-live="polite"></span></div></div>
  <section class="stats">
    <div class="card"><div class="label">Konfigurierte Dienste</div><div class="value" id="overview-total">{stats['total']}</div></div>
    <div class="card"><div class="label">UP</div><div class="value" id="overview-up" style="color:var(--green)">{stats['up']}</div></div>
    <div class="card"><div class="label">DOWN</div><div class="value" id="overview-down" style="color:var(--red)">{stats['down']}</div></div>
    <div class="card"><div class="label">Nicht geprüft</div><div class="value" id="overview-unchecked">{stats['unchecked']}</div></div>
  </section>
  <section class="panel"><h2>Dienste</h2><table><thead><tr><th>Dienst</th><th>Status</th><th>Typ</th><th>Kategorie</th><th>Schweregrad</th><th>Antwortzeit</th><th>Zuletzt geprüft (UTC)</th></tr></thead><tbody id="services-body">{rows_html}</tbody></table></section>

  <section class="panel">
    <h2>Analysen</h2>
    <div class="filters">
      <label>Dienst<select id="analytics-service">{service_options}</select></label>
      <label>Period<select id="analytics-period"><option value="24h">Letzte 24 Stunden</option><option value="7d" selected>Letzte 7 Tage</option><option value="30d">Letzte 30 Tage</option><option value="90d">Letzte 90 Tage</option></select></label>
      <button id="load-analytics">Analysen aktualisieren</button>
    </div>
    <div class="stats">
      <div class="card"><div class="label">Abgeschlossene Prüfungen</div><div class="value" id="metric-total">—</div></div>
      <div class="card"><div class="label">Beobachtete Verfügbarkeit (Prüfungen)</div><div class="value" id="metric-availability">—</div></div>
      <div class="card"><div class="label">Erfolgreiche Prüfungen (UP)</div><div class="value" id="metric-up">—</div></div>
      <div class="card"><div class="label">Durchschnittliche Antwortzeit (nur UP)</div><div class="value" id="metric-latency">—</div></div>
    </div>
    <div class="hint" id="analytics-method">Die Verfügbarkeit wird anhand abgeschlossener Prüfungen berechnet und nicht als kontinuierliche Betriebszeit.</div>
    <div class="chart-wrap"><h3>Durchschnittliche Antwortzeit im Zeitverlauf</h3><svg id="latency-chart" class="chart" viewBox="0 0 800 240" role="img" aria-label="Diagramm der durchschnittlichen Antwortzeit"></svg></div>
    <div class="legend"><span style="--legend-color:#175cd3">Durchschnittliche Antwortzeit (nur erfolgreiche Prüfungen)</span></div>
    <div class="chart-wrap"><h3>Erfolgreiche Prüfungen im Zeitverlauf</h3><svg id="availability-chart" class="chart" viewBox="0 0 800 200" role="img" aria-label="Diagramm erfolgreicher Prüfungen"></svg></div>
    <div class="legend"><span style="--legend-color:#067647">UP</span><span style="--legend-color:#b42318">DOWN</span></div>
  </section>

  <section class="panel">
    <h2>Prüfungsverlauf</h2>
    <div class="filters">
      <label>Dienst<select id="history-service">{service_options}</select></label>
      <label>Period<select id="history-period"><option value="24h">Letzte 24 Stunden</option><option value="7d" selected>Letzte 7 Tage</option><option value="30d">Letzte 30 Tage</option><option value="90d">Letzte 90 Tage</option></select></label>
      <label>Status<select id="history-status"><option value="">Alle Statuswerte</option><option value="UP">UP</option><option value="DOWN">DOWN</option></select></label>
      <label>Kategorie<select id="history-category"><option value="">Alle Kategorien</option><option value="OK">OK</option><option value="SLOW_RESPONSE">Langsame Antwort</option><option value="HTTP_STATUS">HTTP-Status</option><option value="TIMEOUT">Timeout</option><option value="DNS_ERROR">DNS</option><option value="CONNECTION_REFUSED">Verbindung abgelehnt</option><option value="CONNECTION_RESET">Verbindung zurückgesetzt</option><option value="TLS_ERROR">TLS</option><option value="NETWORK_ERROR">Netzwerkfehler</option><option value="UNKNOWN_ERROR">Unbekannter Fehler</option><option value="UNCLASSIFIED">Altbestand / nicht klassifiziert</option></select></label>
      <label>Typ<select id="history-type"><option value="">Alle Typen</option><option value="http">HTTP</option><option value="tcp">TCP</option></select></label>
      <button id="load-history">Filter anwenden</button>
    </div>
    <div class="hint" id="history-summary">Verlauf wird geladen …</div>
    <table><thead><tr><th>Prüfzeit (UTC)</th><th>Dienst</th><th>Typ</th><th>Status</th><th>Kategorie</th><th>Schweregrad</th><th>HTTP</th><th>Antwortzeit</th><th>Details</th></tr></thead><tbody id="history-body"><tr><td colspan="9" class="muted">Wird geladen …</td></tr></tbody></table>
    <div class="pagination"><button id="history-prev" disabled>Zurück</button><span class="muted" id="history-page">Seite 1</span><button id="history-next" disabled>Weiter</button></div>
  </section>

  <section class="panel"><h2>Statusänderungen</h2><div id="events-list">{events_html}</div></section>
  <footer>Lokaler Lernprototyp. Die Verfügbarkeit basiert auf Stichproben. Konfiguriere nur Systeme, die dir gehören oder zu deren Überwachung du berechtigt bist. Stelle dieses Dashboard nicht öffentlich bereit.</footer>
</main>
<script>
const button = document.getElementById('run');
const message = document.getElementById('message');
button.addEventListener('click', async () => {{
  button.disabled = true; message.textContent = 'Prüfung läuft …';
  try {{
    const response = await fetch('/api/checks/run', {{method:'POST'}});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const result = await response.json();
    message.textContent = result.checked + ' Dienst(e) geprüft. Dashboard wird aktualisiert …';
    await refreshData();
    message.textContent = result.checked + ' Dienst(e) geprüft.';
    button.disabled = false;
  }} catch (error) {{
    message.textContent = 'Prüfung fehlgeschlagen: ' + error.message;
    button.disabled = false;
  }}
}});

function addSvg(parent, tag, attrs, textValue) {{
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs || {{}})) node.setAttribute(key, String(value));
  if (textValue !== undefined) node.textContent = String(textValue);
  parent.appendChild(node);
  return node;
}}
function drawEmpty(svg, label) {{
  svg.replaceChildren();
  addSvg(svg, 'text', {{x:400, y:112, 'text-anchor':'middle', class:'chart-empty'}}, label);
}}
function drawLatencyChart(buckets) {{
  const svg = document.getElementById('latency-chart'); svg.replaceChildren();
  const points = buckets.map((b,i) => ({{i, value:b.avg_up_response_time_ms}})).filter(p => p.value !== null);
  if (!points.length) return drawEmpty(svg, 'Keine erfolgreichen Prüfungen in diesem Zeitraum');
  const left=58, right=780, top=18, bottom=192, max=Math.max(10,...points.map(p=>p.value))*1.15;
  for (let i=0;i<=4;i++) {{
    const y=bottom-(bottom-top)*i/4;
    addSvg(svg,'line',{{x1:left,y1:y,x2:right,y2:y,stroke:'#e4e7ec','stroke-width':1}});
    addSvg(svg,'text',{{x:left-9,y:y+4,'text-anchor':'end',fill:'#667085','font-size':11}}, Math.round(max*i/4)+' ms');
  }}
  const xFor = i => buckets.length<=1 ? (left+right)/2 : left+(right-left)*i/(buckets.length-1);
  const yFor = value => bottom-(Math.max(0,value)/max)*(bottom-top);
  const coords=points.map(p=>xFor(p.i)+','+yFor(p.value)).join(' ');
  addSvg(svg,'polyline',{{points:coords,fill:'none',stroke:'#175cd3','stroke-width':3,'stroke-linejoin':'round','stroke-linecap':'round'}});
  for (const p of points) addSvg(svg,'circle',{{cx:xFor(p.i),cy:yFor(p.value),r:4,fill:'#175cd3'}});
  const labels=[0,Math.floor((buckets.length-1)/2),buckets.length-1];
  for (const i of [...new Set(labels)]) {{
    if (buckets[i]) addSvg(svg,'text',{{x:xFor(i),y:218,'text-anchor':'middle',fill:'#667085','font-size':11}},new Date(buckets[i].bucket_start).toLocaleDateString('de-DE'));
  }}
}}
function drawAvailabilityChart(buckets) {{
  const svg=document.getElementById('availability-chart'); svg.replaceChildren();
  if (!buckets.some(b=>b.total_checks>0)) return drawEmpty(svg,'Keine Prüfungsdaten in diesem Zeitraum');
  const left=52,right=780,top=18,bottom=150;
  for (const pct of [0,25,50,75,100]) {{
    const y=bottom-(bottom-top)*pct/100;
    addSvg(svg,'line',{{x1:left,y1:y,x2:right,y2:y,stroke:'#e4e7ec','stroke-width':1}});
    addSvg(svg,'text',{{x:left-9,y:y+4,'text-anchor':'end',fill:'#667085','font-size':11}},pct+'%');
  }}
  const slot=(right-left)/buckets.length, barWidth=Math.max(2,slot*.62);
  buckets.forEach((b,i)=>{{
    if (!b.total_checks) return;
    const x=left+i*slot+(slot-barWidth)/2;
    const upHeight=(bottom-top)*(b.up_checks/b.total_checks);
    const downHeight=(bottom-top)-upHeight;
    addSvg(svg,'rect',{{x,y:top,width:barWidth,height:downHeight,fill:'#b42318',rx:2}});
    addSvg(svg,'rect',{{x,y:top+downHeight,width:barWidth,height:upHeight,fill:'#067647',rx:2}});
  }});
  const labels=[0,Math.floor((buckets.length-1)/2),buckets.length-1];
  for (const i of [...new Set(labels)]) {{
    if (buckets[i]) addSvg(svg,'text',{{x:left+i*slot+slot/2,y:178,'text-anchor':'middle',fill:'#667085','font-size':11}},new Date(buckets[i].bucket_start).toLocaleDateString('de-DE'));
  }}
}}
async function loadAnalytics() {{
  const service=document.getElementById('analytics-service').value;
  const period=document.getElementById('analytics-period').value;
  const params=new URLSearchParams({{period}}); if(service) params.set('service_id',service);
  const response=await fetch('/api/analytics?'+params.toString(),{{cache:'no-store'}});
  if(!response.ok) throw new Error('Analyse-HTTP-Fehler: '+response.status);
  const data=await response.json(), s=data.summary;
  document.getElementById('metric-total').textContent=s.total_checks;
  document.getElementById('metric-availability').textContent=s.availability_pct===null?'—':s.availability_pct.toFixed(2)+'%';
  document.getElementById('metric-up').textContent=s.up_checks+' / '+s.down_checks+' DOWN';
  document.getElementById('metric-latency').textContent=s.avg_up_response_time_ms===null?'—':s.avg_up_response_time_ms.toFixed(1)+' ms';
  document.getElementById('analytics-method').textContent='Erfolgreiche Prüfungen (UP) / alle abgeschlossenen Prüfungen. '+s.total_checks+' Stichproben; diese Kennzahl ist keine kontinuierliche Betriebszeit und füllt keine Lücken zwischen Prüfungen.';
  drawLatencyChart(data.buckets); drawAvailabilityChart(data.buckets);
}}
function categoryLabel(value) {{
  const labels={{OK:'In Ordnung',SLOW_RESPONSE:'Langsame Antwort',HTTP_STATUS:'HTTP-Status',TIMEOUT:'Zeitüberschreitung',DNS_ERROR:'DNS-Fehler',CONNECTION_REFUSED:'Verbindung abgelehnt',CONNECTION_RESET:'Verbindung zurückgesetzt',TLS_ERROR:'TLS-Fehler',NETWORK_ERROR:'Netzwerkfehler',UNKNOWN_ERROR:'Unbekannter Fehler',UNCLASSIFIED:'Nicht klassifiziert'}};
  return labels[value]||value;
}}
function severityLabel(value) {{
  const labels={{WARNING:'Warnung',INFO:'Information',ERROR:'Fehler',CRITICAL:'Kritisch',UNCLASSIFIED:'Nicht klassifiziert'}};
  return labels[value]||value;
}}
let historyOffset=0, historyTotal=0; const historyLimit=50;
async function loadHistory() {{
  const service=document.getElementById('history-service').value;
  const period=document.getElementById('history-period').value;
  const status=document.getElementById('history-status').value;
  const category=document.getElementById('history-category').value;
  const type=document.getElementById('history-type').value;
  const params=new URLSearchParams({{period,limit:String(historyLimit),offset:String(historyOffset)}});
  if(service) params.set('service_id',service); if(status) params.set('status',status); if(category) params.set('category',category); if(type) params.set('check_type',type);
  const response=await fetch('/api/history?'+params.toString(),{{cache:'no-store'}});
  if(!response.ok) throw new Error('Verlaufs-HTTP-Fehler: '+response.status);
  const data=await response.json(); historyTotal=data.total;
  const body=document.getElementById('history-body'); body.replaceChildren();
  if(!data.items.length) {{
    const row=document.createElement('tr'), cell=document.createElement('td'); cell.colSpan=9; cell.className='muted'; cell.textContent='Keine Prüfeinträge entsprechen diesen Filtern.'; row.appendChild(cell); body.appendChild(row);
  }} else {{
    for(const item of data.items) {{
      const row=document.createElement('tr');
      const values=[new Date(item.checked_at).toLocaleString('de-DE'),item.service_name,(item.check_type||'http').toUpperCase(),item.status,categoryLabel(item.result_category),severityLabel(item.severity),item.status_code??'—',Number(item.response_time_ms).toFixed(1)+' ms',item.error_message||'—'];
      values.forEach((value,index)=>{{ const cell=document.createElement('td'); cell.textContent=String(value); if(index===3) cell.className=item.status==='UP'?'up':'down'; if(index===5&&item.severity==='WARNING') cell.className='warn'; row.appendChild(cell); }});
      body.appendChild(row);
    }}
  }}
  const first=historyTotal===0?0:historyOffset+1, last=Math.min(historyOffset+data.items.length,historyTotal);
  document.getElementById('history-summary').textContent='Zeige '+first+'–'+last+' von '+historyTotal+' passenden Prüfungen';
  document.getElementById('history-page').textContent='Seite '+(Math.floor(historyOffset/historyLimit)+1);
  document.getElementById('history-prev').disabled=historyOffset===0;
  document.getElementById('history-next').disabled=historyOffset+historyLimit>=historyTotal;
}}
async function refreshOverview() {{
  const response=await fetch('/api/services',{{cache:'no-store'}});
  if(!response.ok) throw new Error('Dienste-HTTP-Fehler: '+response.status);
  const services=await response.json(), body=document.getElementById('services-body');
  body.replaceChildren();
  let up=0, down=0, unchecked=0;
  for(const service of services) {{
    const row=document.createElement('tr'), name=document.createElement('div'), url=document.createElement('div');
    name.className='name'; name.textContent=service.name; url.className='url'; url.textContent=service.url;
    const first=document.createElement('td'); first.append(name,url);
    const statusCell=document.createElement('td'), badge=document.createElement('span');
    const status=service.last_status;
    if(status==='UP') {{ up++; badge.className='status up'; badge.textContent='UP'; }}
    else if(status==='DOWN') {{ down++; badge.className='status down'; badge.textContent='DOWN'; }}
    else {{ unchecked++; badge.className='status wait'; badge.textContent='NICHT GEPRÜFT'; }}
    statusCell.appendChild(badge);
    if(service.last_error_message) {{ const err=document.createElement('div'); err.className='error'; err.textContent=service.last_error_message; statusCell.appendChild(err); }}
    const latency=document.createElement('td'); latency.textContent=service.last_response_time_ms===null?'—':Number(service.last_response_time_ms).toFixed(0)+' ms';
    const checked=document.createElement('td'); checked.textContent=service.last_checked_at||'—';
    const category=document.createElement('td'); category.textContent=categoryLabel(service.last_result_category||'UNCLASSIFIED');
    const severity=document.createElement('td'); severity.textContent=severityLabel(service.last_severity||'UNCLASSIFIED');
    const type=document.createElement('td'); type.textContent=(service.check_type||'http').toUpperCase();
    if(service.last_severity==='WARNING') severity.className='warn';
    row.append(first,statusCell,type,category,severity,latency,checked); body.appendChild(row);
  }}
  if(!services.length) {{ const row=document.createElement('tr'),cell=document.createElement('td'); cell.colSpan=7; cell.className='muted'; cell.textContent='Keine Dienste konfiguriert.'; row.appendChild(cell); body.appendChild(row); }}
  document.getElementById('overview-total').textContent=services.length;
  document.getElementById('overview-up').textContent=up;
  document.getElementById('overview-down').textContent=down;
  document.getElementById('overview-unchecked').textContent=unchecked;
}}
async function refreshEvents() {{
  const response=await fetch('/api/events',{{cache:'no-store'}});
  if(!response.ok) throw new Error('Ereignis-HTTP-Fehler: '+response.status);
  const events=await response.json(), container=document.getElementById('events-list'); container.replaceChildren();
  if(!events.length) {{ const p=document.createElement('p'); p.className='muted'; p.textContent='Noch keine Statusänderungen vorhanden.'; container.appendChild(p); return; }}
  for(const event of events) {{
    const line=document.createElement('div'), time=document.createElement('time'), body=document.createElement('div'), badge=document.createElement('span'), strong=document.createElement('strong'), details=document.createElement('div');
    line.className='event'; time.textContent=event.occurred_at; badge.className=event.new_status==='UP'?'status up':'status down'; badge.textContent=event.new_status; strong.textContent=event.service_name; details.className='muted'; details.textContent=event.details;
    body.append(badge,document.createTextNode(' '),strong,details); line.append(time,body); container.appendChild(line);
  }}
}}
async function refreshData() {{
  try {{ await refreshOverview(); await refreshEvents(); await loadAnalytics(); await loadHistory(); }}
  catch(error) {{ document.getElementById('analytics-method').textContent='Daten konnten nicht aktualisiert werden: '+error.message; }}
}}
document.getElementById('load-analytics').addEventListener('click',loadAnalytics);
document.getElementById('load-history').addEventListener('click',()=>{{historyOffset=0;loadHistory();}});
document.getElementById('history-prev').addEventListener('click',()=>{{historyOffset=Math.max(0,historyOffset-historyLimit);loadHistory();}});
document.getElementById('history-next').addEventListener('click',()=>{{if(historyOffset+historyLimit<historyTotal){{historyOffset+=historyLimit;loadHistory();}}}});
refreshData();

let lastAutoCheckAt;
async function refreshSchedulerStatus() {{
  try {{
    const response = await fetch('/api/scheduler', {{cache:'no-store'}});
    if (!response.ok) return;
    const status = await response.json();
    const state = document.getElementById('scheduler-state');
    const next = document.getElementById('next-check');
    state.textContent = status.is_running ? 'Prüfung läuft' : (status.enabled ? 'Automatische Prüfungen alle ' + status.interval_seconds + ' Sekunden' : 'Automatische Prüfungen deaktiviert');
    if (lastAutoCheckAt === undefined) {{ lastAutoCheckAt = status.last_auto_check_at; }}
    else if (status.last_auto_check_at !== lastAutoCheckAt) {{ lastAutoCheckAt = status.last_auto_check_at; await refreshData(); }}
    if (!status.enabled || !status.next_check_at) {{ next.textContent = '—'; return; }}
    const due = new Date(status.next_check_at);
    const seconds = Math.max(0, Math.ceil((due.getTime() - Date.now()) / 1000));
    const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
    const ss = String(seconds % 60).padStart(2, '0');
    next.textContent = due.toLocaleTimeString('de-DE') + ' (' + mm + ':' + ss + ')';
  }} catch (_error) {{ /* Keep the dashboard usable if status polling is interrupted. */ }}
}}
refreshSchedulerStatus(); setInterval(refreshSchedulerStatus,1000);
</script>
</body></html>"""


def create_app(db_path: str | Path | None = None, services_path: str | Path | None = None) -> FastAPI:
    database_path = Path(db_path or os.getenv("WITMON_DB_PATH", DEFAULT_DB_PATH))
    config_path = Path(services_path or os.getenv("WITMON_SERVICES_PATH", DEFAULT_SERVICES_PATH))
    scheduler = CheckScheduler(
        database_path,
        enabled=True,
        interval_seconds=60,
        check_runner=run_all_checks,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db(database_path)
        services = load_services(config_path)
        scheduler_settings = load_scheduler_settings(config_path)
        telegram_settings = TelegramSettings.from_env()
        sync_services(database_path, services)
        scheduler.enabled = scheduler_settings["enabled"]
        scheduler.interval_seconds = scheduler_settings["interval_seconds"]
        await scheduler.start()
        worker_task = None
        if telegram_settings.enabled:
            worker_task = asyncio.create_task(
                notification_worker(database_path, telegram_settings),
                name="warehouse-it-monitor-telegram-worker",
            )
        try:
            yield
        finally:
            if worker_task is not None:
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass
            await scheduler.stop()

    application = FastAPI(
        title="Warehouse IT Monitor",
        version=APP_VERSION,
        description="Ein Lernprojekt zur Überwachung ausdrücklich konfigurierter HTTP-Dienste.",
        lifespan=lifespan,
    )

    @application.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(render_dashboard(database_path, scheduler.status()))

    @application.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "warehouse-it-monitor", "version": APP_VERSION}

    @application.get("/api/services")
    def api_services() -> list[dict[str, Any]]:
        return list_services(database_path)

    @application.get("/api/events")
    def api_events() -> list[dict[str, Any]]:
        return list_events(database_path)

    @application.get("/api/scheduler")
    def api_scheduler() -> dict[str, Any]:
        return scheduler.status()

    @application.get("/api/analytics")
    def api_analytics(period: str = "7d", service_id: int | None = None) -> dict[str, Any]:
        if service_id is not None and service_id < 1:
            raise HTTPException(status_code=422, detail="service_id must be a positive integer")
        return build_analytics_payload(database_path, period=period, service_id=service_id)

    @application.get("/api/history")
    def api_history(
        period: str = "7d",
        service_id: int | None = None,
        status: str | None = None,
        category: str | None = None,
        check_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if period not in PERIODS:
            raise HTTPException(status_code=422, detail="period must be one of: 24h, 7d, 30d, 90d")
        if service_id is not None and service_id < 1:
            raise HTTPException(status_code=422, detail="service_id must be a positive integer")
        if status not in (None, "UP", "DOWN"):
            raise HTTPException(status_code=422, detail="status must be UP or DOWN")
        if category is not None and category not in V050_CATEGORIES:
            raise HTTPException(
                status_code=422,
                detail="category must be one of: " + ", ".join(V050_CATEGORIES),
            )
        if check_type not in (None, "http", "tcp"):
            raise HTTPException(status_code=422, detail="check_type must be http or tcp")
        if not 1 <= limit <= 100:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
        if not 0 <= offset <= 100000:
            raise HTTPException(status_code=422, detail="offset must be between 0 and 100000")
        start_dt, end_dt, _ = _period_window(period)
        start_at, end_at = _utc_iso(start_dt), _utc_iso(end_dt)
        items, total = query_check_history(
            database_path,
            service_id=service_id,
            status=status,
            category=category,
            check_type=check_type,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
            offset=offset,
        )
        return {"period": period, "items": items, "total": total, "limit": limit, "offset": offset}

    @application.post("/api/checks/run")
    async def api_run_checks() -> dict[str, Any]:
        try:
            results = await scheduler.run_manual()
        except Exception as exc:  # Return an understandable API error without leaking a traceback.
            raise HTTPException(status_code=500, detail="Dienstprüfungen konnten nicht abgeschlossen werden") from exc
        return {
            "checked": len(results),
            "up": sum(1 for item in results if item["status"] == "UP"),
            "down": sum(1 for item in results if item["status"] == "DOWN"),
            "results": results,
        }

    return application


app = create_app()
