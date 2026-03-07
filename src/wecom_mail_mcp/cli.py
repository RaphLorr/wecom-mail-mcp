from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from .config import Settings, load_settings
from .errors import WeComMailError
from .server import create_server
from .wecom import WeComMailClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wecom-mail-mcp",
        description="MCP server that sends email through the official WeCom mail API.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport. Defaults to WECOM_MCP_TRANSPORT or stdio.",
    )
    parser.add_argument(
        "--host",
        help="Bind host for SSE or streamable-http mode. Defaults to WECOM_MCP_HOST.",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Bind port for SSE or streamable-http mode. Defaults to WECOM_MCP_PORT.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level. Defaults to WECOM_LOG_LEVEL.",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate env vars and fetch the current WeCom app mailbox, then exit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    overrides: dict[str, object] = {}
    if args.transport:
        overrides["wecom_mcp_transport"] = args.transport
    if args.host:
        overrides["wecom_mcp_host"] = args.host
    if args.port is not None:
        overrides["wecom_mcp_port"] = args.port
    if args.log_level:
        overrides["wecom_log_level"] = args.log_level

    try:
        settings = load_settings(**overrides)
    except WeComMailError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.check_config:
        return asyncio.run(check_config(settings))

    server = create_server(settings)
    server.run(transport=settings.wecom_mcp_transport)
    return 0


async def check_config(settings: Settings) -> int:
    client = WeComMailClient(settings)
    try:
        mailbox = await client.get_mailbox_info(force_refresh=True)
    except WeComMailError as exc:
        print(f"Configuration check failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await client.aclose()

    result = {
        "ok": True,
        "provider": "wecom",
        "sender_email": mailbox.email,
        "alias_list": mailbox.alias_list,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
