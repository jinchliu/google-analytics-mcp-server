"""Invalid configuration must fail before a request can change remote state."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import config
from google_analytics_mcp_server.server import create_server
from google_analytics_mcp_server.tools.generic import ga_create


@pytest.mark.parametrize(
    'key', ['MAX_LIST_ITEMS', 'MAX_ROWS', 'MAX_OUTPUT_BYTES', 'HTTP_TIMEOUT']
)
@pytest.mark.parametrize('value', ['0', '-1', 'abc', '1.5', ''])
def test_bad_limits_stop_startup_and_writes(key, value, monkeypatch, http):
    monkeypatch.setenv('GA_MCP_' + key, value)
    with pytest.raises(ToolError, match=key):
        create_server()
    with pytest.raises(ToolError, match=key):
        ga_create('properties', 'accounts/123', {})
    assert not http.requests


@pytest.mark.parametrize(
    'value,expected',
    [
        ('1', True),
        ('true', True),
        ('YES', True),
        ('0', False),
        ('false', False),
        ('', False),
    ],
)
def test_boolean_settings(value, expected, monkeypatch):
    monkeypatch.setenv('GA_MCP_READ_ONLY', value)
    assert config.read_only() is expected
