"""Command-line entry point: run the ADK weather pipeline end to end.

Usage:
    python main.py                     # default prompt (14 days)
    python main.py "Use the last 30 days"
"""

import asyncio
import sys
import uuid

from dotenv import load_dotenv

load_dotenv()

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from weather_pipeline.agent import root_agent  # noqa: E402

APP_NAME = "weather_pipeline"
USER_ID = "local_user"


async def run(prompt: str) -> None:
    runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)
    session_id = str(uuid.uuid4())
    await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    async for event in runner.run_async(
        user_id=USER_ID, session_id=session_id, new_message=message
    ):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.function_call:
                print(f"[{event.author}] -> calling tool {part.function_call.name}({dict(part.function_call.args or {})})")
            elif part.function_response:
                print(f"[{event.author}] <- {part.function_response.name} returned")
            elif part.text and part.text.strip():
                print(f"\n[{event.author}]\n{part.text.strip()}\n")

    session = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )
    print("=" * 60)
    print("Report:", session.state.get("report_path", "not generated"))
    for uri in session.state.get("storage_uris", []):
        print("Stored:", uri)


if __name__ == "__main__":
    user_prompt = " ".join(sys.argv[1:]) or (
        "Run the weather pipeline for the last 14 days and build the report."
    )
    asyncio.run(run(user_prompt))
