"""
MCP Client — teb as an MCP client for external tool orchestration.

While teb/mcp_server.py exposes teb *as* an MCP server (so AI tools can
call into teb), this module lets teb *call out* to external MCP servers.

When a task requires "deploy to Vercel" or "create a GitHub repo", the
executor discovers the appropriate MCP server from the integration catalog
and invokes its tools.

This makes teb the orchestration layer on top of the entire MCP ecosystem —
every new MCP server that anyone publishes becomes a tool teb can use.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from teb import config, security

logger = logging.getLogger(__name__)

# Timeout for MCP tool calls
_MCP_TIMEOUT = int(config.EXECUTOR_TIMEOUT)


# ─── Data Types ───────────────────────────────────────────────────────────────

@dataclass
class MCPToolDef:
    """A tool definition from an MCP server."""
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    server_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "server_url": self.server_url,
        }


@dataclass
class MCPCallResult:
    """Result of calling an MCP tool."""
    success: bool
    content: Any = None
    error: Optional[str] = None
    server_url: str = ""
    tool_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "content": self.content,
            "error": self.error,
            "server_url": self.server_url,
            "tool_name": self.tool_name,
        }


@dataclass
class MCPServer:
    """A registered MCP server that teb can call."""
    name: str
    url: str
    description: str = ""
    tools: List[MCPToolDef] = field(default_factory=list)
    auth_header: str = ""
    auth_value: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "description": self.description,
            "tools": [t.to_dict() for t in self.tools],
            "tool_count": len(self.tools),
        }


# ─── Server Registry ─────────────────────────────────────────────────────────

_registered_servers: Dict[str, MCPServer] = {}


def register_mcp_server(
    name: str,
    url: str,
    description: str = "",
    auth_header: str = "",
    auth_value: str = "",
) -> MCPServer:
    """Register an external MCP server for teb to use."""
    if not security.is_safe_url(url):
        raise ValueError(f"URL '{url}' targets a private or disallowed address")

    server = MCPServer(
        name=name,
        url=url.rstrip("/"),
        description=description,
        auth_header=auth_header,
        auth_value=auth_value,
    )
    _registered_servers[name] = server
    logger.info("Registered MCP server: %s at %s", name, url)
    return server


def unregister_mcp_server(name: str) -> bool:
    """Remove an MCP server from the registry."""
    if name in _registered_servers:
        del _registered_servers[name]
        return True
    return False


def list_mcp_servers() -> List[MCPServer]:
    """List all registered MCP servers."""
    return list(_registered_servers.values())


def get_mcp_server(name: str) -> Optional[MCPServer]:
    """Get a specific registered MCP server by name."""
    return _registered_servers.get(name)


# ─── Tool Discovery ──────────────────────────────────────────────────────────

def discover_tools(server_name: str) -> List[MCPToolDef]:
    """Discover available tools from an MCP server.

    Calls the server's tools/list endpoint and caches the result.
    """
    server = _registered_servers.get(server_name)
    if not server:
        logger.warning("MCP server '%s' not registered", server_name)
        return []

    try:
        headers = {}
        if server.auth_header and server.auth_value:
            headers[server.auth_header] = server.auth_value

        with httpx.Client(timeout=_MCP_TIMEOUT) as client:
            resp = client.post(
                f"{server.url}/mcp/tools/list",
                headers=headers,
                json={},
            )
            resp.raise_for_status()
            data = resp.json()

        tools = []
        for tool_data in data.get("tools", []):
            tool = MCPToolDef(
                name=tool_data.get("name", ""),
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
                server_url=server.url,
            )
            tools.append(tool)

        server.tools = tools
        logger.info("Discovered %d tools from MCP server '%s'", len(tools), server_name)
        return tools

    except httpx.HTTPStatusError as exc:
        logger.warning("Failed to discover tools from %s: HTTP %d", server_name, exc.response.status_code)
        return []
    except httpx.RequestError as exc:
        logger.warning("Failed to connect to MCP server %s: %s", server_name, exc)
        return []
    except Exception as exc:
        logger.warning("Unexpected error discovering tools from %s: %s", server_name, exc)
        return []


def find_tool(tool_name: str) -> Optional[tuple]:
    """Find a tool across all registered MCP servers.

    Returns (server, tool) tuple if found, None otherwise.
    """
    for server in _registered_servers.values():
        for tool in server.tools:
            if tool.name == tool_name:
                return (server, tool)
    return None


def find_tools_for_task(task_description: str) -> List[MCPToolDef]:
    """Find MCP tools that might be relevant for a task description.

    Uses keyword matching against tool names and descriptions.
    """
    keywords = set(task_description.lower().split())
    results = []

    for server in _registered_servers.values():
        for tool in server.tools:
            tool_words = set(
                (tool.name + " " + tool.description).lower().split()
            )
            overlap = keywords & tool_words
            if len(overlap) >= 2 or tool.name.lower() in task_description.lower():
                results.append(tool)

    return results


# ─── Tool Execution ──────────────────────────────────────────────────────────

def call_tool(
    server_name: str,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> MCPCallResult:
    """Call a tool on an external MCP server.

    This is the core function that lets teb orchestrate external tools
    through the MCP protocol.
    """
    server = _registered_servers.get(server_name)
    if not server:
        return MCPCallResult(
            success=False,
            error=f"MCP server '{server_name}' not registered",
            tool_name=tool_name,
        )

    if not security.is_safe_url(server.url):
        return MCPCallResult(
            success=False,
            error=f"URL '{server.url}' targets a private or disallowed address",
            server_url=server.url,
            tool_name=tool_name,
        )

    try:
        headers = {"Content-Type": "application/json"}
        if server.auth_header and server.auth_value:
            headers[server.auth_header] = server.auth_value

        payload = {
            "tool": tool_name,
            "arguments": arguments or {},
        }

        with httpx.Client(timeout=_MCP_TIMEOUT) as client:
            resp = client.post(
                f"{server.url}/mcp/tools/call",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        return MCPCallResult(
            success=True,
            content=data.get("result", data),
            server_url=server.url,
            tool_name=tool_name,
        )

    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.text[:500]
        except Exception:
            pass
        return MCPCallResult(
            success=False,
            error=f"HTTP {exc.response.status_code}: {error_body}",
            server_url=server.url,
            tool_name=tool_name,
        )
    except httpx.TimeoutException:
        return MCPCallResult(
            success=False,
            error=f"Timeout calling {tool_name} on {server_name}",
            server_url=server.url,
            tool_name=tool_name,
        )
    except httpx.RequestError as exc:
        return MCPCallResult(
            success=False,
            error=f"Connection error: {exc}",
            server_url=server.url,
            tool_name=tool_name,
        )


# ─── Batch Operations ────────────────────────────────────────────────────────

def call_tools_sequence(
    calls: List[Dict[str, Any]],
) -> List[MCPCallResult]:
    """Execute a sequence of MCP tool calls, stopping on first failure.

    Each call dict: {"server": "name", "tool": "tool_name", "arguments": {...}}
    """
    results = []
    for call in calls:
        result = call_tool(
            server_name=call["server"],
            tool_name=call["tool"],
            arguments=call.get("arguments", {}),
        )
        results.append(result)
        if not result.success:
            logger.warning(
                "MCP sequence halted: %s/%s failed: %s",
                call["server"], call["tool"], result.error,
            )
            break
    return results
