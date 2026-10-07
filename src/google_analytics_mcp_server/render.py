"""MCP result assembly, truthful continuation hints and output budgets."""

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from google_analytics_mcp_server import config, contracts, projection
from google_analytics_mcp_server.catalog import Method
from google_analytics_mcp_server.config import ResponseFormat


def json_text(value: Any) -> str:
    """Serialize compact JSON while retaining Unicode and integer precision."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def cell(value: Any) -> str:
    """Escape untrusted API values for a Markdown table cell."""
    text = (
        json_text(value)
        if isinstance(value, (list, dict, bool))
        else str(value if value is not None else '')
    )
    return (
        text.replace('\\', '\\\\')
        .replace('|', '\\|')
        .replace('\r', ' ')
        .replace('\n', ' ')
    )


def table(rows: list[dict[str, Any]]) -> str:
    """Render rows without clipping their count."""
    if not rows:
        return '(none)'
    columns = list(dict.fromkeys(key for row in rows for key in row))
    return grid(columns, [[row.get(c) for c in columns] for row in rows])


def grid(columns: list[str], rows: list[list[Any]]) -> str:
    """Render ordered report columns without losing duplicate column labels."""
    if not columns:
        return json_text(rows) if rows else '(none)'
    return '\n'.join(
        [
            '| ' + ' | '.join(cell(c) for c in columns) + ' |',
            '|' + '|'.join('---' for _ in columns) + '|',
            *['| ' + ' | '.join(cell(value) for value in row) + ' |' for row in rows],
        ]
    )


def result(payload: dict[str, Any], *, markdown: str | None = None) -> CallToolResult:
    """Apply a combined text/structured-content read budget before returning."""
    content = markdown if markdown is not None else json_text(payload)
    structured = payload if markdown is None else None
    size = len(content.encode()) + (
        len(json_text(payload).encode()) if structured is not None else 0
    )
    cap = config.positive_int('MAX_OUTPUT_BYTES', 65536)
    if size > cap:
        raise ToolError(
            f'Read result is {size} bytes, over GA_MCP_MAX_OUTPUT_BYTES={cap}. No records or cursor were returned. Narrow the query, choose a compact format, start a new query with a smaller limit, or raise the budget. Keep continuation parameters unchanged where the API requires it.'
        )
    return CallToolResult(
        content=[TextContent(type='text', text=content)], structured_content=structured
    )


def entity(
    response: dict[str, Any], item: Method, *, mutating: bool = False
) -> dict[str, Any]:
    """Preserve complete read entities and successful write outcomes."""
    cap = config.positive_int('MAX_OUTPUT_BYTES', 65536)
    # MCPServer renders a plain dict as indented text plus structured content.
    size = len(json.dumps(response, ensure_ascii=False, indent=2).encode()) + len(
        json_text(response).encode()
    )
    if size <= cap:
        return response
    if not mutating:
        raise ToolError(
            f'{item.id} returned {size} output bytes, exceeding GA_MCP_MAX_OUTPUT_BYTES={cap}; raise the budget for this complete entity.'
        )
    names: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'name', 'target'} and isinstance(child, str):
                    names.append(child)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(response)
    summary = {
        'request_completed': True,
        'method': item.key,
        'resource_names': list(dict.fromkeys(names)),
        'response_truncated': True,
        'response_bytes': size,
        'message': 'The API call completed. Do not repeat the mutation. Use the retained identifiers to follow up; the full response exceeded the display budget.',
    }
    for key in (
        'success',
        'status',
        'error',
        'poll_error',
        'wait_expired',
        'next_action',
        'sessionId',
        'accountTicketId',
        'terms_of_service_url',
    ):
        if key in response:
            summary[key] = response[key]
    operation = response.get('operation')
    if isinstance(operation, dict) and operation.get('error'):
        summary['error'] = operation['error']
    return summary


def list_result(
    item: Method,
    response: dict[str, Any],
    key: str,
    fmt: ResponseFormat,
    next_arguments: dict[str, Any],
) -> CallToolResult:
    """Return one intact upstream page and its exact continuation information."""
    rows = response.get(key, [])
    token = response.get('nextPageToken')
    metadata = {
        'count': len(rows),
        'has_more': bool(token),
        'next_page_token': token,
        'api_version': item.version,
        'next_arguments': {
            **next_arguments,
            'page_token': token,
            'api_version': item.version,
        }
        if token
        else None,
    }
    if fmt == 'json_full':
        return result({**response, '_mcp': metadata})
    slim = projection.resources(item.resource.split('.')[-1], rows)
    payload = {'items': slim, **metadata}
    text = f'{len(rows)} upstream items ({item.version})\n\n{table(slim)}\n\n{json_text(metadata)}'
    return result(payload, markdown=text if fmt == 'markdown' else None)


def continuation(
    item: Method, body: dict[str, Any], response: dict[str, Any]
) -> dict[str, Any]:
    """Describe only pagination mechanisms actually accepted by the method."""
    fields = (
        item.schema(item.request_schema).get('properties', {})
        if item.request_schema
        else {}
    )
    if 'requests' in body:
        from google_analytics_mcp_server.catalog import method

        name = 'runPivotReport' if item.name == 'batchRunPivotReports' else 'runReport'
        child = method('data', f'properties.{name}', item.version)
        return {
            'reports': [
                continuation(child, req, res)
                for req, res in zip(
                    body['requests'], response.get('reports', []), strict=False
                )
            ]
        }
    if 'pivots' in fields:
        headers = response.get('pivotHeaders', [])
        pivots = []
        for index, pivot in enumerate(body.get('pivots', [])):
            header = headers[index] if index < len(headers) else {}
            count = len(header.get('pivotDimensionHeaders', []))
            next_offset = (
                contracts.integer(pivot.get('offset', 0), 'pivot.offset') + count
            )
            pivots.append(
                {
                    'pivot_index': index,
                    'next_offset': next_offset
                    if count and next_offset < int(header.get('rowCount', next_offset))
                    else None,
                }
            )
        return {'mode': 'per_pivot', 'pivots': pivots}
    rows = response.get('rows', response.get('audienceRows', []))
    if 'offset' in fields:
        offset = contracts.integer(body.get('offset', 0), 'offset')
        next_offset = offset + len(rows)
        total = response.get('rowCount')
        more = len(rows) > 0 and (total is None or next_offset < int(total))
        return {
            'mode': 'offset',
            'returned_rows': len(rows),
            'row_count': total,
            'next_offset': next_offset if more else None,
        }
    if item.name == 'runFunnelReport':
        return {
            'mode': 'none',
            'subreports': {
                key: {
                    'returned_rows': len(response.get(key, {}).get('rows', [])),
                    'metadata': response.get(key, {}).get('metadata', {}),
                }
                for key in ('funnelTable', 'funnelVisualization')
            },
            'hint': 'Funnel reports have no offset pagination. Narrow filters or increase the limit; inspect each subreport metadata for truncation.',
        }
    if 'limit' in fields:
        total = response.get('rowCount')
        return {
            'mode': 'none',
            'returned_rows': len(rows) if 'rows' in response else None,
            'row_count': total,
            'truncated': int(total) > len(rows) if total is not None else None,
            'hint': 'No offset pagination. Narrow filters or increase the limit.',
        }
    return {'mode': 'none'}


def report_result(
    item: Method, body: dict[str, Any], response: dict[str, Any], fmt: ResponseFormat
) -> CallToolResult:
    """Render report families with version-specific paging and available quotas."""
    metadata: dict[str, Any] = {
        'api_version': item.version,
        'continuation': continuation(item, body, response),
    }
    if 'reports' in response:
        metadata['quota'] = [
            r.get('propertyQuota', r.get('quota', 'unavailable'))
            for r in response['reports']
        ]
    else:
        metadata['quota'] = response.get(
            'propertyQuota', response.get('quota', 'unavailable')
        )
    payload = {
        **(response if fmt == 'json_full' else projection.report(response)),
        '_mcp': metadata,
    }
    if fmt != 'markdown':
        return result(payload)
    return result(payload, markdown=report_markdown(payload))


def report_markdown(payload: dict[str, Any]) -> str:
    """Render nested reports and labelled aggregates while keeping metadata."""
    parts: list[str] = []
    other = dict(payload)
    if 'reports' in other:
        parts.extend(
            f'### Report {index + 1}\n\n{report_markdown(child)}'
            for index, child in enumerate(other.pop('reports'))
        )
    for name in ('funnelTable', 'funnelVisualization'):
        if name in other:
            parts.append(f'### {name}\n\n{report_markdown(other.pop(name))}')
    if 'columns' in other:
        columns = other.pop('columns')
        parts.append(grid(columns, other.pop('rows')))
        for name in ('totals', 'minimums', 'maximums'):
            if name in other:
                rows = [projection.row_values(row) for row in other.pop(name)]
                parts.append(f'{name}\n\n{grid(columns, rows)}')
    if other:
        parts.append(json_text(other))
    return '\n\n'.join(parts)
