"""Resolve resource names using the selected discovery method's path patterns."""

import re
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import catalog
from google_analytics_mcp_server.config import ApiVersion


def normalize(value: str, collection: str | None = None) -> str:
    """Normalize names; numeric IDs require an unambiguous expected collection."""
    value = value.strip().strip('/')
    if value.startswith(('http:', 'https:', 'G-', 'UA-')):
        raise ToolError(
            'Use an API resource name, not a URL, measurement ID or UA ID. Discover names with ga_list.'
        )
    if value.isdecimal() and collection:
        value = f'{collection}/{value}'
    return value


def property_name(value: str) -> str:
    """Normalize a property locator."""
    value = normalize(value, 'properties')
    if not re.fullmatch(r'properties/[^/]+', value):
        raise ToolError('Expected properties/PROPERTY_ID.')
    return value


def select(
    owner: str,
    *,
    resource: str | None = None,
    locator: str | None = None,
    api_version: ApiVersion = 'auto',
    action: str | None = None,
) -> tuple[catalog.Method, dict[str, Any]]:
    """Select a unique route by collection and locator, retaining wire parameters."""
    options = catalog.candidates(owner, api_version)
    if resource:
        options = [
            m for m in options if resource in {m.resource, m.resource.split('.')[-1]}
        ]
    if action:
        options = [m for m in options if m.name == action]
    matches: list[tuple[catalog.Method, dict[str, Any]]] = []
    for item in options:
        paths = item.path_parameters
        if not paths:
            if locator is None:
                matches.append((item, {}))
            continue
        if locator is None or len(paths) != 1:
            continue
        key, spec = next(iter(paths.items()))
        expected = spec.get('pattern', '')
        prefix = re.match(r'^\^?(accounts|properties)/\[\^/\]\+\$$', expected)
        value = normalize(locator, prefix.group(1) if prefix else None)
        if re.fullmatch(expected, value):
            matches.append((item, {key: value}))
    if len(matches) == 1:
        return matches[0]
    shapes = sorted(
        {p.get('pattern', '') for m in options for p in m.path_parameters.values()}
    )
    detail = (
        ', '.join(shapes)
        if shapes
        else ', '.join(sorted({m.resource for m in options}))
    )
    raise ToolError(
        f'No unique {owner} route for {resource or locator!r} in {api_version}. Expected: {detail or "a supported method/version"}.'
    )
