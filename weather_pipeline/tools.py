"""Pipeline tools used by the ADK agents.

Each function is a plain Python function that ADK exposes to an LLM agent as a
tool. Large payloads (raw/processed data) are passed between steps through the
shared ADK session state (``tool_context.state``) so the LLM only ever sees
small summaries, not thousands of data points.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.adk.tools import ToolContext

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
]

# Default cities (Indiana). Override with the CITIES env var as JSON.
DEFAULT_CITIES = {
    "Indianapolis": (39.7684, -86.1581),
    "Bloomington": (39.1653, -86.5264),
    "Fort Wayne": (41.0793, -85.1394),
    "Evansville": (37.9716, -87.5711),
    "South Bend": (41.6764, -86.2520),
    "Lafayette": (40.4167, -86.8753),
}

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))


def _cities() -> dict[str, tuple[float, float]]:
    raw = os.getenv("CITIES")
    if raw:
        return {k: tuple(v) for k, v in json.loads(raw).items()}
    return DEFAULT_CITIES


def _run_id(tool_context: ToolContext) -> str:
    run_id = tool_context.state.get("run_id")
    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tool_context.state["run_id"] = run_id
    return run_id


# --------------------------------------------------------------------------- #
# 1. Ingestion
# --------------------------------------------------------------------------- #
def fetch_weather_data(past_days: int, tool_context: ToolContext) -> dict:
    """Fetch daily weather observations for the configured cities from Open-Meteo.

    Args:
        past_days: Number of past days of history to retrieve (1-92).

    Returns:
        A short summary of what was ingested (cities, days, record count).
    """
    past_days = max(1, min(int(past_days), 92))
    raw: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for city, (lat, lon) in _cities().items():
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": ",".join(DAILY_VARS),
            "past_days": past_days,
            "forecast_days": 1,
            "timezone": "America/Indiana/Indianapolis",
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
            "wind_speed_unit": "mph",
        }
        try:
            resp = requests.get(OPEN_METEO_URL, params=params, timeout=30)
            resp.raise_for_status()
            raw[city] = resp.json()
        except requests.RequestException as exc:
            errors[city] = str(exc)

    run_id = _run_id(tool_context)
    tool_context.state["raw_data"] = raw
    tool_context.state["past_days"] = past_days
    records = sum(len(v.get("daily", {}).get("time", [])) for v in raw.values())
    return {
        "status": "success" if raw else "error",
        "run_id": run_id,
        "cities_fetched": list(raw.keys()),
        "cities_failed": errors,
        "daily_records": records,
    }


# --------------------------------------------------------------------------- #
# 2. Processing
# --------------------------------------------------------------------------- #
def process_weather_data(tool_context: ToolContext) -> dict:
    """Clean, filter and aggregate the raw weather data held in session state.

    Transformations:
      * flatten the per-city JSON into tidy daily rows
      * drop future (forecast) days and rows with missing values
      * add derived fields: temperature range and a "wet day" flag (>= 0.04 in)
      * aggregate per city: avg high/low, max high, min low, total precip,
        wet-day count, max wind, and rank cities by average high

    Returns:
        A short summary with per-city aggregates and data-quality counts.
    """
    raw = tool_context.state.get("raw_data")
    if not raw:
        return {"status": "error", "message": "No raw data in state. Run ingestion first."}

    today = datetime.now().strftime("%Y-%m-%d")
    daily_rows, dropped = [], 0
    for city, payload in raw.items():
        d = payload.get("daily", {})
        for i, day in enumerate(d.get("time", [])):
            vals = {v: (d.get(v) or [None] * (i + 1))[i] for v in DAILY_VARS}
            if day >= today or any(x is None for x in vals.values()):
                dropped += 1
                continue
            daily_rows.append(
                {
                    "city": city,
                    "date": day,
                    "high_f": vals["temperature_2m_max"],
                    "low_f": vals["temperature_2m_min"],
                    "precip_in": vals["precipitation_sum"],
                    "wind_mph": vals["wind_speed_10m_max"],
                    "temp_range_f": round(vals["temperature_2m_max"] - vals["temperature_2m_min"], 1),
                    "wet_day": vals["precipitation_sum"] >= 0.04,
                }
            )

    by_city: dict[str, list[dict]] = {}
    for row in daily_rows:
        by_city.setdefault(row["city"], []).append(row)

    summary = []
    for city, rows in by_city.items():
        n = len(rows)
        summary.append(
            {
                "city": city,
                "days": n,
                "avg_high_f": round(sum(r["high_f"] for r in rows) / n, 1),
                "avg_low_f": round(sum(r["low_f"] for r in rows) / n, 1),
                "max_high_f": round(max(r["high_f"] for r in rows), 1),
                "min_low_f": round(min(r["low_f"] for r in rows), 1),
                "avg_range_f": round(sum(r["temp_range_f"] for r in rows) / n, 1),
                "total_precip_in": round(sum(r["precip_in"] for r in rows), 2),
                "wet_days": sum(r["wet_day"] for r in rows),
                "max_wind_mph": round(max(r["wind_mph"] for r in rows), 1),
            }
        )
    summary.sort(key=lambda r: r["avg_high_f"], reverse=True)
    for rank, row in enumerate(summary, start=1):
        row["warmth_rank"] = rank

    processed = {
        "run_id": _run_id(tool_context),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "past_days": tool_context.state.get("past_days"),
        "rows_kept": len(daily_rows),
        "rows_dropped": dropped,
        "city_summary": summary,
        "daily": sorted(daily_rows, key=lambda r: (r["city"], r["date"])),
    }
    tool_context.state["processed_data"] = processed
    return {
        "status": "success",
        "rows_kept": len(daily_rows),
        "rows_dropped": dropped,
        "city_summary": summary,
    }


# --------------------------------------------------------------------------- #
# 3. Storage
# --------------------------------------------------------------------------- #
def _write_local(name: str, data: str) -> Path:
    path = OUTPUT_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return path


def store_data_in_gcs(tool_context: ToolContext) -> dict:
    """Save the raw and processed datasets to Google Cloud Storage.

    Files are written under ``gs://$GCS_BUCKET/weather/<run_id>/`` as
    ``raw.json``, ``processed.json`` and ``city_summary.csv``. A local copy is
    always written to ./output as well. If STORAGE_MODE=local, the GCS upload
    is skipped (useful for offline testing).

    Returns:
        The URIs of the stored objects.
    """
    raw = tool_context.state.get("raw_data")
    processed = tool_context.state.get("processed_data")
    if not raw or not processed:
        return {"status": "error", "message": "Raw/processed data missing. Run earlier steps first."}

    run_id = _run_id(tool_context)
    prefix = f"weather/{run_id}"
    summary = processed["city_summary"]
    cols = list(summary[0].keys()) if summary else []
    csv_text = "\n".join([",".join(cols)] + [",".join(str(r[c]) for c in cols) for r in summary])

    files = {
        f"{prefix}/raw.json": (json.dumps(raw, indent=2), "application/json"),
        f"{prefix}/processed.json": (json.dumps(processed, indent=2), "application/json"),
        f"{prefix}/city_summary.csv": (csv_text, "text/csv"),
    }
    local_paths = [str(_write_local(name, body)) for name, (body, _) in files.items()]

    if os.getenv("STORAGE_MODE", "gcs").lower() == "local":
        tool_context.state["storage_uris"] = local_paths
        return {"status": "success", "mode": "local", "uris": local_paths}

    bucket_name = os.getenv("GCS_BUCKET")
    if not bucket_name:
        return {"status": "error", "message": "GCS_BUCKET env var is not set."}

    from google.cloud import storage  # imported lazily so local mode needs no GCP auth

    client = storage.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT") or None)
    bucket = client.bucket(bucket_name)
    uris = []
    for name, (body, ctype) in files.items():
        bucket.blob(name).upload_from_string(body, content_type=ctype)
        uris.append(f"gs://{bucket_name}/{name}")
    tool_context.state["storage_uris"] = uris
    return {"status": "success", "mode": "gcs", "uris": uris}


# --------------------------------------------------------------------------- #
# 4. Visualization
# --------------------------------------------------------------------------- #
def build_html_report(headline: str, tool_context: ToolContext) -> dict:
    """Render an HTML dashboard (charts + summary table) of the processed data.

    Args:
        headline: A one-sentence plain-English insight to show at the top of
            the page (written by the reporting agent from the city summary).

    Returns:
        The local path of the report (and its GCS URI when uploaded).
    """
    processed = tool_context.state.get("processed_data")
    if not processed:
        return {"status": "error", "message": "No processed data. Run processing first."}

    template = (Path(__file__).parent / "report_template.html").read_text(encoding="utf-8")
    html = (
        template.replace("__HEADLINE__", _escape(headline))
        .replace("__DATA__", json.dumps(processed).replace("</", "<\\/"))
        .replace("__STORAGE__", json.dumps(tool_context.state.get("storage_uris", [])))
    )
    path = _write_local("report.html", html)
    result = {"status": "success", "local_path": str(path.resolve())}

    bucket_name = os.getenv("GCS_BUCKET")
    if os.getenv("STORAGE_MODE", "gcs").lower() != "local" and bucket_name:
        from google.cloud import storage

        name = f"weather/{_run_id(tool_context)}/report.html"
        client = storage.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT") or None)
        client.bucket(bucket_name).blob(name).upload_from_string(html, content_type="text/html")
        result["gcs_uri"] = f"gs://{bucket_name}/{name}"
    tool_context.state["report_path"] = result["local_path"]
    return result


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
