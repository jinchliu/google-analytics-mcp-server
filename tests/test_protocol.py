"""Real MCP registration and stdio handshake with ADC deliberately disabled."""

import os
import sys
import tomllib
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import auth, capabilities, package_version, policy
from google_analytics_mcp_server.registry import REGISTRY
from google_analytics_mcp_server.server import (
    INSTRUCTIONS,
    SERVER_DESCRIPTION,
    SERVER_NAME,
    create_server,
)

PROFILES = [
    (read_only, chat, raw)
    for read_only in (False, True)
    for chat in (False, True)
    for raw in (False, True)
]


@pytest.mark.anyio
@pytest.mark.parametrize('read_only,chat,raw', PROFILES)
async def test_actual_registration_and_annotations(read_only, chat, raw, monkeypatch):
    for key, value in [
        ('READ_ONLY', read_only),
        ('ENABLE_CHAT', chat),
        ('ENABLE_RAW_API', raw),
    ]:
        monkeypatch.setenv('GA_MCP_' + key, str(int(value)))
    tools = await create_server().list_tools()
    expected = (
        set(policy.READ_OWNERS)
        if read_only
        else set(REGISTRY)
        - ({'ga_chat'} if not chat else set())
        - ({'ga_call_api'} if not raw else set())
    )
    assert {tool.name for tool in tools} == expected
    assert len(tools) == (13 if read_only else 26 + chat + raw)
    for tool in tools:
        annotations = tool.annotations
        assert annotations.read_only_hint is (tool.name in policy.READ_OWNERS)
        assert annotations.open_world_hint is (tool.name != 'ga_describe_schema')
        assert annotations.idempotent_hint is (tool.name in policy.READ_OWNERS)
        assert annotations.destructive_hint is (
            REGISTRY[tool.name].tier not in {'read', 'job'}
        )
        assert len(tool.description.encode()) < 2048
        assert tool.input_schema['type'] == 'object'
    ledger = capabilities.ledger()
    if not read_only:
        assert ledger['enabled_versioned_endpoints'] == 247 + chat
        assert ledger['enabled_logical_methods'] == 190 + chat
    assert auth._credentials is None


def test_server_identity_and_instruction_budget():
    project = tomllib.loads(Path('pyproject.toml').read_text())['project']
    assert SERVER_NAME == project['name']
    assert SERVER_DESCRIPTION == project['description']
    assert package_version() == project['version']
    assert 0 < len(INSTRUCTIONS.encode()) < 2048


@pytest.mark.anyio
async def test_sdk_tool_validation_and_errors_do_not_call_http(http):
    server = create_server()
    with pytest.raises(ToolError):
        await server.call_tool('ga_get', {'name': 'G-NOT-A-PROPERTY'})
    with pytest.raises(ToolError, match='confirm=true'):
        await server.call_tool('ga_delete', {'name': 'properties/123'})
    with pytest.raises(ToolError):
        await server.call_tool(
            'ga_run_report', {'property': '123', 'api_version': 'v2'}
        )
    assert not http.requests


@pytest.mark.anyio
@pytest.mark.parametrize('read_only,chat,raw', PROFILES)
async def test_stdio_startup_tool_listing_and_schema_without_adc(
    read_only, chat, raw, tmp_path
):
    script = """
import google.auth
import httplib2
import socket
def forbidden(*args, **kwargs):
    raise AssertionError('ADC or network used during offline protocol test')
google.auth.default = forbidden
httplib2.Http.request = forbidden
socket.socket.connect = forbidden
from google_analytics_mcp_server import main
main()
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(('GA_MCP_', 'GOOGLE_'))
    }
    env.update(
        {
            'GOOGLE_APPLICATION_CREDENTIALS': str(tmp_path / 'missing-adc.json'),
            'CLOUDSDK_CONFIG': str(tmp_path),
            'GA_MCP_READ_ONLY': str(int(read_only)),
            'GA_MCP_ENABLE_CHAT': str(int(chat)),
            'GA_MCP_ENABLE_RAW_API': str(int(raw)),
        }
    )
    parameters = StdioServerParameters(
        command=sys.executable, args=['-c', script], env=env, cwd=tmp_path
    )
    with anyio.fail_after(20), (tmp_path / 'stderr.log').open('w+') as stderr:
        async with (
            stdio_client(parameters, errlog=stderr) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            initialized = await session.initialize()
            assert initialized.server_info.name == SERVER_NAME
            assert initialized.server_info.version == package_version()
            assert initialized.instructions == INSTRUCTIONS
            result = await session.list_tools()
            assert len(result.tools) == (13 if read_only else 26 + chat + raw)
            described = await session.call_tool(
                'ga_describe_schema', {'name': 'data:v1alpha:RunReportRequest'}
            )
            assert not described.is_error
            assert 'conversionSpec' in str(described)
            invalid = await session.call_tool('ga_get', {'name': 'G-NOT-A-PROPERTY'})
            assert invalid.is_error
            assert 'resource name' in str(invalid)
        stderr.seek(0)
        assert 'Failed to parse' not in stderr.read()
