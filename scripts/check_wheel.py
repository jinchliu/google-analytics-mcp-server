"""Check a built wheel in an isolated environment with Google access disabled.

Run after uv build:
    uv run --isolated --no-project --with dist/*.whl python -I scripts/check_wheel.py
"""

import asyncio
import importlib.metadata
import json
import os
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch


async def check() -> None:
    """Check installed resources, entry points and offline tool registration."""
    import google_analytics_mcp_server as package
    from google_analytics_mcp_server import catalog
    from google_analytics_mcp_server.server import create_server

    source = Path(__file__).resolve().parents[1] / 'src'
    assert not Path(package.__file__).resolve().is_relative_to(source), (
        'The clean-wheel check imported the source checkout instead of the wheel.'
    )
    for key in tuple(os.environ):
        if key.startswith('GA_MCP_'):
            os.environ.pop(key)
    root = files(package)
    for api in catalog.SERVICES:
        for version in catalog.VERSIONS:
            assert catalog.document(api, version)['schemas']
        assert json.loads(root.joinpath('examples', f'{api}.json').read_text())
    assert len(catalog.methods()) == 248
    server = create_server()
    assert len(await server.list_tools()) == 26
    schema = await server.call_tool(
        'ga_describe_schema', {'name': 'data:v1alpha:RunReportRequest'}
    )
    assert not schema.is_error
    distribution = importlib.metadata.distribution('google-analytics-mcp-server')
    assert any(
        entry.name == 'google-analytics-mcp-server' and entry.group == 'console_scripts'
        for entry in distribution.entry_points
    )
    print('Installed wheel: imports, discovery, examples, entry point and 26 tools OK.')


if __name__ == '__main__':
    with (
        patch('google.auth.default', side_effect=AssertionError('ADC is forbidden')),
        patch('httplib2.Http.request', side_effect=AssertionError('HTTP is forbidden')),
        patch(
            'socket.socket.connect', side_effect=AssertionError('Network is forbidden')
        ),
    ):
        asyncio.run(check())
