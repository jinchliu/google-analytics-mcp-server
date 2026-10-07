"""Real MCP SDK client, subprocess stdio transport and bounded live Google reads."""

import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from google_analytics_mcp_server.server import SERVER_NAME

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_stdio_client_live_reads_and_error_transport(property_name, tmp_path):
    env = {
        key: value for key, value in os.environ.items() if not key.startswith('GA_MCP_')
    }
    env['GA_MCP_READ_ONLY'] = '1'
    parameters = StdioServerParameters(
        command=sys.executable,
        args=['-c', 'from google_analytics_mcp_server import main; main()'],
        env=env,
        cwd=Path.cwd(),
    )
    with anyio.fail_after(180), (tmp_path / 'stdio.log').open('w+') as stderr:
        async with (
            stdio_client(parameters, errlog=stderr) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            initialized = await session.initialize()
            assert initialized.server_info.name == SERVER_NAME
            tools = await session.list_tools()
            assert len(tools.tools) == 13
            prop = await session.call_tool('ga_get', {'name': property_name})
            assert not prop.is_error
            assert prop.structured_content['name'] == property_name
            report = await session.call_tool(
                'ga_run_report',
                {
                    'property': property_name,
                    'metrics': ['activeUsers'],
                    'dimensions': ['country'],
                    'limit': 1,
                    'response_format': 'json_full',
                },
            )
            assert not report.is_error
            assert (
                report.structured_content['metricHeaders'][0]['name'] == 'activeUsers'
            )
            invalid = await session.call_tool(
                'ga_run_report',
                {
                    'property': property_name,
                    'metrics': ['__mcp_invalid_metric__'],
                    'limit': 1,
                },
            )
            assert invalid.is_error
            assert 'HTTP 400' in str(invalid.content)
            assert 'ga_get_metadata' in str(invalid.content)
        stderr.seek(0)
        assert 'Failed to parse' not in stderr.read()
