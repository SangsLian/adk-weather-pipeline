"""ADK agent definitions for the agentic weather data pipeline.

Architecture
------------
root_agent (SequentialAgent "weather_pipeline")
  1. ingestion_agent   -> fetch_weather_data      (Open-Meteo public API)
  2. processing_agent  -> process_weather_data    (clean / derive / aggregate)
  3. storage_agent     -> store_data_in_gcs       (Google Cloud Storage)
  4. reporting_agent   -> build_html_report       (HTML dashboard)

Each step is an LlmAgent that decides how to call its tool, checks the tool's
result, and reports back. The SequentialAgent orchestrates the order and all
agents share one session state, which is how data flows between steps.

`root_agent` is the name ADK looks for, so this also works with `adk web` and
`adk run weather_pipeline`.
"""

import os

from dotenv import load_dotenv
from google.adk.agents import LlmAgent, SequentialAgent

from .tools import (
    build_html_report,
    fetch_weather_data,
    process_weather_data,
    store_data_in_gcs,
)

load_dotenv()
MODEL = os.getenv("ADK_MODEL", "gemini-2.5-flash")

ingestion_agent = LlmAgent(
    name="ingestion_agent",
    model=MODEL,
    description="Pulls raw daily weather data from the Open-Meteo public API.",
    instruction=(
        "You are the data ingestion agent. Work out how many past days of history the "
        "user asked for (default to 14 if not stated; the maximum is 92) and call "
        "`fetch_weather_data` exactly once with that number. Then report in one or two "
        "sentences which cities were fetched, how many daily records came back, and any "
        "cities that failed. If every city failed, say clearly that the pipeline must stop."
    ),
    tools=[fetch_weather_data],
    output_key="ingestion_report",
)

processing_agent = LlmAgent(
    name="processing_agent",
    model=MODEL,
    description="Cleans, filters and aggregates the raw weather data.",
    instruction=(
        "You are the data processing agent. The ingestion step reported: "
        "{ingestion_report}\n"
        "If ingestion failed, reply 'Skipping processing: no data.' and do nothing else. "
        "Otherwise call `process_weather_data` once. Report how many rows were kept and "
        "dropped, then name the warmest city, the coolest city and the wettest city "
        "(highest total_precip_in) with their numbers."
    ),
    tools=[process_weather_data],
    output_key="processing_report",
)

storage_agent = LlmAgent(
    name="storage_agent",
    model=MODEL,
    description="Persists raw and processed data to Google Cloud Storage.",
    instruction=(
        "You are the storage agent. The processing step reported: {processing_report}\n"
        "If processing was skipped, reply 'Skipping storage.' Otherwise call "
        "`store_data_in_gcs` once and list every URI that was written. If the tool "
        "returns an error, repeat the error message verbatim so the user can fix it."
    ),
    tools=[store_data_in_gcs],
    output_key="storage_report",
)

reporting_agent = LlmAgent(
    name="reporting_agent",
    model=MODEL,
    description="Builds the HTML visualization of the processed data.",
    instruction=(
        "You are the reporting agent. Processing summary: {processing_report}\n"
        "Storage summary: {storage_report}\n"
        "If there is no processed data, reply 'No report generated.' Otherwise write ONE "
        "plain-English headline sentence (under 30 words) capturing the most interesting "
        "comparison across cities, and call `build_html_report` with it. Finish with a "
        "short pipeline summary: records ingested, rows kept, storage location, and the "
        "report path."
    ),
    tools=[build_html_report],
    output_key="final_report",
)

root_agent = SequentialAgent(
    name="weather_pipeline",
    description="End-to-end agentic data pipeline: ingest -> process -> store -> visualize.",
    sub_agents=[ingestion_agent, processing_agent, storage_agent, reporting_agent],
)
