"""Process configuration, evaluated without loading Google credentials."""

import os
from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError

Version = Literal['v1beta', 'v1alpha']
ApiVersion = Literal['auto', Version]
ResponseFormat = Literal['markdown', 'json', 'json_full']


def enabled(name: str) -> bool:
    """Read a boolean GA_MCP setting."""
    return os.environ.get(f'GA_MCP_{name}', '').lower() in {'1', 'true', 'yes'}


def positive_int(name: str, default: int) -> int:
    """Read a positive configuration value with an actionable error."""
    try:
        value = int(os.environ.get(f'GA_MCP_{name}', str(default)))
        if value > 0:
            return value
    except ValueError:
        pass
    raise ToolError(f'GA_MCP_{name} must be a positive integer.')


def read_only() -> bool:
    """Whether the strict read-only profile is active."""
    return enabled('READ_ONLY')


def validate_limits() -> None:
    """Reject invalid configuration before any request can change remote state."""
    for key, default in (
        ('MAX_LIST_ITEMS', 1000),
        ('MAX_ROWS', 10000),
        ('MAX_OUTPUT_BYTES', 65536),
        ('HTTP_TIMEOUT', 60),
    ):
        positive_int(key, default)
