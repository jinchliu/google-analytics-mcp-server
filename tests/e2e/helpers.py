"""Call the current MCP server while retaining structured results."""

from typing import Any

from mcp.server.mcpserver import MCPServer


async def call(server: MCPServer, tool_name: str, **arguments: Any) -> dict[str, Any]:
    """Execute a registered tool and require a successful structured result."""
    result = await server.call_tool(tool_name, arguments)
    assert not result.is_error
    assert result.structured_content is not None
    return result.structured_content
