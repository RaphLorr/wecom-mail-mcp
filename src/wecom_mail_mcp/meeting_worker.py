"""One bounded reconciliation pass, intended for an operator-managed systemd timer."""

import argparse
import asyncio
import json
import os

from .config import load_settings
from .meeting_plans import MeetingPlans, PlanStore
from .wecom import WeComMailClient


async def run():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--openclaw-config",
        help="Read existing wecom-mail MCP environment; never print secrets",
    )
    args = parser.parse_args()
    if args.openclaw_config:
        with open(args.openclaw_config) as f:
            config = json.load(f)
        env = config["mcp"]["servers"]["wecom-mail"].get("env", {})
        os.environ.update({k: str(v) for k, v in env.items()})
    settings = load_settings()
    store = PlanStore(settings.wecom_meeting_store, settings.wecom_corp_id)
    client = WeComMailClient(settings)
    try:
        result = await MeetingPlans(
            store, client, settings.wecom_room_advance_days
        ).tick()
        print(
            json.dumps(result)
        )  # Counts only; never attendee lists, secrets or API responses.
    finally:
        await client.aclose()
        store.close()


if __name__ == "__main__":
    asyncio.run(run())
