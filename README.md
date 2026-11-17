# Agentic Weather Data Pipeline (Google ADK + GCS)

An end-to-end **agentic data engineering pipeline** built with Google's
[Agent Development Kit (ADK)](https://google.github.io/adk-docs/). A team of ADK
agents ingests daily weather data for six Indiana cities from the public
[Open-Meteo API](https://open-meteo.com/), transforms and aggregates it, stores
raw and processed data in **Google Cloud Storage**, and publishes an **HTML
dashboard**.

## Architecture

```
                    weather_pipeline  (ADK SequentialAgent - orchestrator)
                                    |
   +----------------+---------------+-----------------+------------------+
   |                |                                 |                  |
ingestion_agent  processing_agent              storage_agent      reporting_agent
 (LlmAgent)       (LlmAgent)                    (LlmAgent)         (LlmAgent)
   |                |                                 |                  |
fetch_weather_   process_weather_            store_data_in_gcs   build_html_report
data()           data()                             |                  |
   |                |                               v                  v
Open-Meteo API   clean / derive / aggregate   gs://<bucket>/weather/  output/report.html
                                              <run_id>/raw.json       (+ copy in GCS)
                                              processed.json
                                              city_summary.csv

            Shared ADK session state carries raw_data -> processed_data -> URIs
```

* **Agent-driven, not one script.** A `SequentialAgent` orchestrates four
  `LlmAgent`s. Each agent reads the previous agent's report (via `output_key`
  and `{state}` templating in its instructions), decides whether to proceed,
  chooses tool arguments (e.g., the ingestion agent parses "last 30 days" from
  the user prompt), calls its tool, and validates the result.
* **State, not prompts, carries the data.** Large payloads move through
  `tool_context.state`; the LLM only sees compact summaries, which keeps token
  cost low and avoids the model altering data.

| Step | Agent | Tool | What it does |
|---|---|---|---|
| Ingest | `ingestion_agent` | `fetch_weather_data(past_days)` | Calls Open-Meteo for each city: daily high/low temp (F), precipitation (in), max wind (mph) |
| Process | `processing_agent` | `process_weather_data()` | Flattens JSON to daily rows; **filters** out forecast days and rows with nulls; **derives** temp range and a wet-day flag (>= 0.04 in); **groups/aggregates** per city (avg high/low, extremes, total precip, wet days, max wind); **ranks** cities by avg high |
| Store | `storage_agent` | `store_data_in_gcs()` | Uploads `raw.json`, `processed.json`, `city_summary.csv` to `gs://$GCS_BUCKET/weather/<run_id>/` |
| Visualize | `reporting_agent` | `build_html_report(headline)` | Writes an LLM-generated headline + KPI tiles, bar charts, a daily trend line chart and a summary table to `output/report.html` (also uploaded to GCS) |

## Project layout

```
.
├── main.py                       # CLI runner (InMemoryRunner)
├── weather_pipeline/
│   ├── __init__.py
│   ├── agent.py                  # ADK agents (root_agent)
│   ├── tools.py                  # ingestion / processing / storage / report tools
│   └── report_template.html      # Chart.js dashboard template
├── tests/test_tools.py           # offline tests (mocked API, local storage)
├── requirements.txt
├── .env.example                  # copy to .env - never commit secrets
└── .gitignore
```

## Setup

Prerequisites: Python 3.10+, a Gemini API key, and a Google Cloud project with
the `gcloud` CLI installed.

```bash
git clone <this-repo-url>
cd adk-weather-pipeline
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

1. **Gemini key** - create one at <https://aistudio.google.com/apikey> and put it
   in `.env` as `GOOGLE_API_KEY`.
2. **Cloud Storage** - authenticate and create a bucket (bucket names are global,
   so pick a unique one):

   ```bash
   gcloud auth application-default login
   gcloud config set project YOUR_PROJECT_ID
   gcloud storage buckets create gs://YOUR_UNIQUE_BUCKET --location=us-central1
   ```

   Set `GOOGLE_CLOUD_PROJECT` and `GCS_BUCKET` in `.env`.

No credentials are stored in the repo: the Gemini key lives in the git-ignored
`.env`, and GCS uses Application Default Credentials.

## Run

```bash
python main.py                                   # last 14 days (default)
python main.py "Run the pipeline for the last 30 days"
```

Console output shows each agent calling its tool and reporting back. Then open
`output/report.html` in a browser, and verify the upload with:

```bash
gcloud storage ls -r gs://YOUR_UNIQUE_BUCKET/weather/
```

**Interactive ADK dev UI** (optional): `adk web` from the repo root, select
`weather_pipeline`, and chat with it to watch the event trace for every agent
and tool call.

**Offline mode**: set `STORAGE_MODE=local` in `.env` to skip GCS and write only
to `./output/` (still needs the Gemini key).

## Tests

```bash
python -m pytest -q
```

The tests mock the Open-Meteo API and use local storage, so they need no network,
GCP credentials or LLM key. They check filtering (forecast/null rows dropped),
aggregation, ranking, storage output and HTML rendering.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_API_KEY` | - | Gemini API key (required) |
| `ADK_MODEL` | `gemini-2.5-flash` | Model used by every agent |
| `GCS_BUCKET` | - | Target bucket (required unless `STORAGE_MODE=local`) |
| `GOOGLE_CLOUD_PROJECT` | gcloud default | GCP project for the storage client |
| `STORAGE_MODE` | `gcs` | `gcs` or `local` |
| `CITIES` | 6 Indiana cities | JSON override, e.g. `{"Chicago": [41.88, -87.63]}` |
| `OUTPUT_DIR` | `output` | Local output folder |

See `Future_Improvements.docx` (submitted on Canvas) for planned enhancements.
