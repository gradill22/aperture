"""MCP adapter over the backend's tool registry (aperture_api.tools).

    aperture-mcp --transport stdio                         (Claude Code / local clients)
    aperture-mcp --transport streamable-http --port 8001   (compose service, path /mcp)

Needs DATABASE_URL. Tool results are the same compact JSON the in-app agent sees; map actions are
dropped because MCP clients have no map. Times are UTC unless an argument carries an offset.
"""

import argparse
import inspect
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from aperture_api import queries, tools
from aperture_api.db import make_engine
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field, ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

INSTRUCTIONS = (
    f"Aperture offline OSINT tools. {tools.DATASET} Resolve place names with search_entities "
    f"(feature_id) before calling geofence_alert. Times are UTC unless given with an offset."
)


@dataclass
class State:
    engine: AsyncEngine


@asynccontextmanager
async def lifespan(_: MCPServer) -> AsyncIterator[State]:
    engine = make_engine()
    try:
        yield State(engine)
    finally:
        await engine.dispose()


def adapter(tool: tools.Tool):
    """An MCP tool function whose signature mirrors tool.input_model's fields.

    MCPServer derives the argument schema from inspect.signature, so each pydantic field becomes a
    keyword-only parameter carrying its type, constraints and description.
    """

    async def run(ctx: Context[State, Any], **kwargs: Any) -> str:
        state: State = ctx.request_context.lifespan_context
        try:
            async with state.engine.connect() as conn:
                result = await tools.call_tool(conn, tool.name, kwargs, tools.ToolContext("UTC"))
        except ValidationError as exc:
            raise ToolError(f"invalid arguments: {exc}") from exc
        except (LookupError, queries.BadRequest) as exc:
            raise ToolError(str(exc)) from exc
        except DBAPIError as exc:
            raise ToolError(str(exc.orig).splitlines()[0]) from exc
        return json.dumps(result.data, separators=(",", ":"), default=str)

    params = [inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context)]
    for name, field in tool.input_model.model_fields.items():
        # Built at runtime from the model, so not a static type.
        annotation = Annotated[
            (field.annotation, *field.metadata, Field(description=field.description))  # type: ignore[name-defined]
        ]
        default = inspect.Parameter.empty if field.is_required() else field.default
        params.append(
            inspect.Parameter(
                name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation, default=default
            )
        )
    run.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    run.__name__ = tool.name
    return run


def build_server() -> MCPServer:
    server = MCPServer("aperture", instructions=INSTRUCTIONS, lifespan=lifespan)
    for tool in tools.REGISTRY.values():
        server.add_tool(
            adapter(tool), name=tool.name, description=tool.description, structured_output=False
        )
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    server = build_server()
    if args.transport == "stdio":
        server.run("stdio")
        return
    # DNS-rebinding protection: only accept Host/Origin values we serve under (compose DNS name,
    # the nginx edge, localhost). Comma-separated overrides via env.
    hosts = os.environ.get("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,mcp-server:*")
    origins = os.environ.get("MCP_ALLOWED_ORIGINS", "http://localhost:*,http://127.0.0.1:*")
    server.run(
        "streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path="/mcp",
        # Plain JSON responses and no session handshake, so curl can drive it in the gate.
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts.split(","),
            allowed_origins=origins.split(","),
        ),
    )


if __name__ == "__main__":
    main()
