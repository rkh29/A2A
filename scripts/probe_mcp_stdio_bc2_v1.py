import asyncio
import json
import sys
import uuid
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = (ROOT / "sandbox").resolve()
SERVER = MCPServer(name="a2a-reverse-deputy-bc2-stdio-probe", instructions="BC-2 sandbox write_canary fixture.")


@SERVER.tool(name="write_canary", description="Write a file inside the experiment sandbox only.", structured_output=False)
def write_canary(relative_path: str, content: str) -> str:
    target = (SANDBOX / relative_path).resolve()
    if not target.is_relative_to(SANDBOX):
        return "status=rejected; reason=outside_sandbox"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"status=written; relative_path={target.relative_to(SANDBOX)}"


async def run_client() -> int:
    relative_path = f"bc2-stdio-probe-{uuid.uuid4().hex}.txt"
    content = "MCP 2.3 stdio client-server probe"
    outside_name = f"bc2-outside-stdio-{uuid.uuid4().hex}.txt"
    target = SANDBOX / relative_path
    outside_target = ROOT / outside_name
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve()), "--server"],
        cwd=str(ROOT),
    )
    try:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = sorted(tool.name for tool in tools.tools)
                local_result = await session.call_tool(
                    "write_canary",
                    {"relative_path": relative_path, "content": content},
                )
                outside_result = await session.call_tool(
                    "write_canary",
                    {"relative_path": f"../{outside_name}", "content": "must not be written"},
                )
                local_text = " ".join(item.text for item in local_result.content if getattr(item, "text", None))
                outside_text = " ".join(item.text for item in outside_result.content if getattr(item, "text", None))
                local_ok = (
                    not local_result.is_error
                    and "status=written" in local_text
                    and target.is_file()
                    and target.read_text(encoding="utf-8") == content
                )
                outside_rejected = (
                    not outside_result.is_error
                    and "outside_sandbox" in outside_text
                    and not outside_target.exists()
                )
                print(json.dumps({
                    "transport": "MCP 2.3 stdio client-server",
                    "server_initialized": True,
                    "tools": names,
                    "local_write_succeeded": local_ok,
                    "outside_write_rejected": outside_rejected,
                    "local_tool_is_error": local_result.is_error,
                    "outside_tool_is_error": outside_result.is_error,
                }, ensure_ascii=False))
                return 0 if "write_canary" in names and local_ok and outside_rejected else 1
    finally:
        target.unlink(missing_ok=True)
        outside_target.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        SERVER.run(transport="stdio")
    else:
        raise SystemExit(asyncio.run(run_client()))
