"""Offline schema help and opt-in raw versioned dispatch."""

import json
from importlib.resources import files
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import (
    capabilities,
    catalog,
    client,
    config,
    policy,
    render,
)
from google_analytics_mcp_server.config import ApiVersion
from google_analytics_mcp_server.registry import tool


@tool()
def ga_describe_schema(
    name: str,
    api: Literal['admin', 'data'] | None = None,
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Describe an API schema/method offline, without credentials or quota.

    Args:
        name: Schema (Audience, RunReportRequest), method (properties.runReport),
            or qualified identity such as admin:v1alpha:Audience. Use catalog to
            list all versioned methods and their native tools.
        api: Disambiguates shared short names such as NumericValue.
        api_version: Reviewed default or explicit version for schema fields.
    """
    if name == 'catalog':
        return capabilities.ledger()
    result = catalog.describe(name, api, api_version)
    if 'method' in result:
        service, version, identity = result['method'].split(':')
        item = catalog.method(service, identity, version)
        result['capabilities'] = capabilities.endpoint(item)
        example_schema = item.request_schema
        selected = item.version
    else:
        example_schema = result['name']
        service = result['api']
        selected = result['api_version']
    short = catalog.short_schema(example_schema or '')
    example = files('google_analytics_mcp_server').joinpath(
        'examples', f'{service}.json'
    )
    if example.is_file():
        examples = json.loads(example.read_text('utf-8')).get(short, [])
        result['examples'] = [
            entry for entry in examples if selected in entry['api_versions']
        ]
    return result


@tool(tier='mixed', feature='ENABLE_RAW_API')
def ga_call_api(
    api: Literal['admin', 'data'],
    api_version: Literal['v1beta', 'v1alpha'],
    method: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Call any pinned versioned method, preserving official fields and parameters.

    Requires GA_MCP_ENABLE_RAW_API=1. All method policies still apply, including
    chat enablement, access changes and destructive confirmation. No convenience
    defaults are injected. Explicit broad update masks also require confirmation.
    Args:
        api: admin or data.
        api_version: Explicit v1beta or v1alpha.
        method: Dotted discovery ID, e.g. properties.runReport.
        params: Official path/query names, including global query controls.
        body: Complete official request body, when supported.
        confirm: True only after user approval of an operation requiring it.
    """
    if not config.enabled('ENABLE_RAW_API') or config.read_only():
        raise ToolError('Raw dispatch is disabled by the current server profile.')
    item = catalog.method(api, method, api_version)
    return render.entity(
        client.execute(item, params, body, confirm=confirm),
        item,
        mutating=not policy.is_read(item),
    )
