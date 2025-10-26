# app/orchestrator/mcp_bridge.py
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import os
import shutil

def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)

def _call_or_value(attr):
    try:
        return attr() if callable(attr) else attr
    except Exception:
        return attr

def _content_to_plain(result) -> Dict[str, Any]:
    """
    Convert MCP CallToolResult -> plain dict.
    Prefer JSON payloads, then text; expose tool errors.
    """
    try:
        if getattr(result, "is_error", False):
            msg = getattr(result, "message", None) or getattr(result, "error", None) or "tool_error"
            return {"error": str(msg)}

        content = getattr(result, "content", None) or []
        for item in content:
            if hasattr(item, "json"):
                val = _call_or_value(getattr(item, "json"))
                if isinstance(val, (dict, list, str, int, float, bool)) or val is None:
                    return val if isinstance(val, dict) else {"json": val}
                return {"json": str(val)}
            if hasattr(item, "text"):
                val = _call_or_value(getattr(item, "text"))
                return {"text": val if isinstance(val, str) else str(val)}
        return {
            "content": [
                _call_or_value(getattr(i, "json", getattr(i, "text", str(i))))
                for i in content
            ]
        }
    except Exception as e:
        return {"error": f"failed_to_coerce_mcp_result: {e!s}"}

class MCPBroker:
    def __init__(self,
                 command: Optional[str] = None,
                 args: Optional[List[str]] = None):
        # Prefer uv if present; fall back to python
        if command is None:
            command = "uv" if _which("uv") else "python"
        if args is None:
            if command == "uv":
                args = ["run", "python", "-m", "app.calendar_tool.server_mcp"]
            else:
                args = ["-m", "app.calendar_tool.server_mcp"]

        self.params = StdioServerParameters(command=command, args=args)
        self._stack = AsyncExitStack()
        self.session: Optional[ClientSession] = None

    async def __aenter__(self):
        read, write = await self._stack.enter_async_context(stdio_client(self.params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.aclose()

    async def call(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        assert self.session is not None
        res = await self.session.call_tool(tool, args)
        # ... keep your _content_to_plain logic here ...
        return {"ok": True, **_content_to_plain(res)}
