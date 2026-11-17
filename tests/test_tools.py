"""Offline tests for the pipeline tools (no network, no GCP, no LLM needed).

Run with:  python -m pytest -q
"""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from weather_pipeline import tools


def _fake_payload(offset: float):
    days = [(date.today() - timedelta(days=i)).isoformat() for i in range(5, -2, -1)]  # incl. today+tomorrow
    n = len(days)
    return {"daily": {
        "time": days,
        "temperature_2m_max": [50 + offset + i for i in range(n)],
        "temperature_2m_min": [30 + offset + i for i in range(n - 1)] + [None],
        "precipitation_sum": [0.0, 0.2, 0.0, 0.5, 0.01, 0.0, 0.0],
        "wind_speed_10m_max": [10.0] * n,
    }}


def _ctx():
    return SimpleNamespace(state={})


def _mock_get(url, params, timeout):
    resp = mock.Mock()
    resp.raise_for_status = lambda: None
    resp.json = lambda: _fake_payload(params["latitude"] / 10)
    return resp


def test_full_pipeline_local(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_MODE", "local")
    monkeypatch.setattr(tools, "OUTPUT_DIR", tmp_path)
    ctx = _ctx()
    with mock.patch.object(tools.requests, "get", side_effect=_mock_get):
        r1 = tools.fetch_weather_data(5, ctx)
    assert r1["status"] == "success" and len(r1["cities_fetched"]) == 6

    r2 = tools.process_weather_data(ctx)
    assert r2["status"] == "success"
    assert r2["rows_kept"] == 6 * 5          # today/tomorrow (forecast) dropped
    assert r2["rows_dropped"] == 6 * 2
    ranks = [c["warmth_rank"] for c in r2["city_summary"]]
    assert ranks == list(range(1, 7))
    assert r2["city_summary"][0]["city"] == "South Bend"  # highest latitude in fake data -> warmest
    assert r2["city_summary"][0]["wet_days"] == 2

    r3 = tools.store_data_in_gcs(ctx)
    assert r3["mode"] == "local" and len(r3["uris"]) == 3

    r4 = tools.build_html_report("Test headline <b>", ctx)
    html = (tmp_path / "report.html").read_text()
    assert "Test headline &lt;b&gt;" in html and "__DATA__" not in html


def test_process_without_data():
    assert tools.process_weather_data(_ctx())["status"] == "error"
