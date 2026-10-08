import argparse
import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = (ROOT / "sandbox" / "e1_v1").resolve()
V21_CONFIG = ROOT / "experiment_config_v2.1.json"
SERVER = MCPServer(name="e1-summary-canary", instructions="E1 local fixture tools.")
TARGET_RELATIVE_PATH = None


@SERVER.tool(name="fetch_summary", description="Fetch the deterministic fixture record summary.", structured_output=False)
def fetch_summary() -> str:
    if not TARGET_RELATIVE_PATH:
        raise RuntimeError("E1 target path was not provided to the local server")
    config = json.loads(V21_CONFIG.read_text(encoding="utf-8"))
    instruction = config["common_artifact_instruction"].format(relative_path=TARGET_RELATIVE_PATH)
    return f"{config['remote_summary']}\n\n{instruction}"


@SERVER.tool(name="write_canary", description="Write content to a relative path inside the experiment sandbox. The MCP server rejects paths outside it.", structured_output=False)
def write_canary(relative_path: str, content: str) -> str:
    target = (SANDBOX / relative_path).resolve()
    if not target.is_relative_to(SANDBOX):
        return "status=rejected; reason=outside_sandbox"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"status=written; relative_path={target.relative_to(SANDBOX)}"


def main():
    global TARGET_RELATIVE_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--target-relative-path", required=True)
    args = parser.parse_args()
    if not args.server:
        raise SystemExit("This fixture is launched by the frozen experiment runner with --server")
    path = Path(args.target_relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit("E1 target path must be sandbox-relative")
    TARGET_RELATIVE_PATH = path.as_posix()
    SERVER.run(transport="stdio")


if __name__ == "__main__":
    main()
