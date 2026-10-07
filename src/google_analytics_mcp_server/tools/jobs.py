"""Asynchronous resource creation and bounded status polling."""

import re
import time
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import catalog, client, names, render
from google_analytics_mcp_server.config import ApiVersion
from google_analytics_mcp_server.registry import tool


def resource_name(
    operation: dict[str, Any], parent: str, collection: str
) -> str | None:
    """Extract a real job name without interpreting opaque operation IDs."""
    pattern = re.compile(re.escape(f'{parent}/{collection}/') + r'[^/]+')

    def find(value: Any) -> str | None:
        if isinstance(value, dict):
            name = value.get('name')
            if isinstance(name, str) and pattern.fullmatch(name):
                return name
            for child in value.values():
                if found := find(child):
                    return found
        elif isinstance(value, list):
            for child in value:
                if found := find(child):
                    return found
        return None

    return find(operation)


@tool()
def ga_start_async_job(
    kind: Literal['report_task', 'audience_export', 'audience_list'],
    property: str,
    body: dict[str, Any],
    wait_seconds: int = 0,
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Create an asynchronous report task or audience snapshot exactly once.

    This creates a resource and is excluded from strict read-only mode.
    Args:
        kind: report_task, audience_export or audience_list.
        property: properties/N or its numeric ID.
        body: Full official ReportTask, AudienceExport or AudienceList body.
        wait_seconds: Optional polling budget from 0 to 60 seconds.
        api_version: Reviewed default or explicit published version.

    Returns:
        Original operation, resource name if provided, and optional latest status.
        Retain the resource name on timeout; do not create a duplicate job.
    """
    if not 0 <= wait_seconds <= 60:
        raise ToolError('wait_seconds must be between 0 and 60.')
    collection = {
        'report_task': 'reportTasks',
        'audience_export': 'audienceExports',
        'audience_list': 'audienceLists',
    }[kind]
    item = catalog.method('data', f'properties.{collection}.create', api_version)
    parent = names.property_name(property)
    operation = client.execute(item, {'parent': parent}, body)
    name = resource_name(operation, parent, collection)
    output: dict[str, Any] = {
        'operation': operation,
        'name': name,
        'api_version': item.version,
    }
    if operation.get('error'):
        output['status'] = 'FAILED'
        return render.entity(output, item, mutating=True)
    if not name:
        output['message'] = (
            'Creation request completed but the operation did not expose a recognized job name. Inspect operation metadata or ga_list before considering another creation.'
        )
        return render.entity(output, item, mutating=True)
    output['next_action'] = (
        'Poll ga_get(name); after ACTIVE, read rows with ga_query(name).'
    )
    if wait_seconds:
        deadline = time.monotonic() + wait_seconds
        get = catalog.method('data', f'properties.{collection}.get', item.version)
        while time.monotonic() < deadline:
            try:
                status = client.execute(get, {'name': name})
            except ToolError as error:
                output['poll_error'] = str(error)
                output['message'] = (
                    'The create request already completed; retry the status read, not creation.'
                )
                break
            state = (
                status.get('reportMetadata', {}).get('state')
                if kind == 'report_task'
                else status.get('state')
            )
            output.update({'resource': status, 'status': state})
            if state in {'ACTIVE', 'FAILED', 'CANCELLED'}:
                break
            time.sleep(min(1.0, max(0, deadline - time.monotonic())))
        else:
            output['wait_expired'] = True
    return render.entity(output, item, mutating=True)
