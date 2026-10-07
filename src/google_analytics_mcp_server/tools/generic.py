"""Resource navigation and ordinary configuration operations."""

import re
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from google_analytics_mcp_server import catalog, client, contracts, names, render
from google_analytics_mcp_server.config import ApiVersion, ResponseFormat
from google_analytics_mcp_server.registry import tool


def reject_bindings(resource: str) -> None:
    """Keep every binding mutation on the confirmed dedicated tool."""
    if 'accessBindings' in resource:
        raise ToolError('Use ga_manage_access_bindings for all access-binding changes.')


@tool()
def ga_list(
    resource: str,
    parent: str | None = None,
    filter: str | None = None,
    show_deleted: bool = False,
    names: list[str] | None = None,
    response_format: ResponseFormat = 'markdown',
    page_token: str | None = None,
    limit: int = 100,
    *,
    api_version: ApiVersion = 'auto',
) -> CallToolResult:
    """List one intact API page, or batch-get named access bindings.

    Args:
        resource: Collection, e.g. accountSummaries, properties, dataStreams,
            customDimensions, audiences, accessBindings or reportTasks.
        parent: Full parent name. properties accepts an accounts/N parent filter.
        filter: Official filter, where supported; overrides the properties parent filter.
        show_deleted: Include trashed resources only where supported.
        names: Full binding names for accessBindings.batchGet.
        response_format: markdown, compact json, or json_full with all fields.
        page_token: Upstream token; retain the original query and limit.
        limit: Requested page size, default 100, bounded by configuration and API.
        api_version: auto selects a reviewed default; explicit versions never fall back.

    Returns:
        Items, count, resolved version and exact continuation arguments.
    """
    # The module name avoids shadowing by the public names argument.
    from google_analytics_mcp_server import names as locators

    action = 'batchGet' if names is not None else 'list'
    property_filter = resource in {'properties', 'admin:properties'}
    selected_resource = 'properties' if property_filter else resource
    item, params = locators.select(
        'ga_list',
        resource=selected_resource,
        locator=None if property_filter else parent,
        api_version=api_version,
        action=action,
    )
    size = contracts.page_size(item, limit)
    if property_filter and parent is not None and filter is None:
        account = locators.normalize(parent, 'accounts')
        if not re.fullmatch(r'accounts/[^/]+', account):
            raise ToolError('A properties parent must be accounts/ACCOUNT_ID.')
        filter = f'parent:{account}'
    if filter is not None:
        params['filter'] = filter
    if show_deleted:
        params['showDeleted'] = True
    if names is not None:
        if not 1 <= len(names) <= min(limit, 1000):
            raise ToolError('names must contain 1..min(limit,1000) full binding names.')
        expected_parent = params.get('parent', '')
        pattern = re.escape(f'{expected_parent}/accessBindings/') + r'[^/]+'
        if any(not re.fullmatch(pattern, name) for name in names):
            raise ToolError('Every binding name must belong to the supplied parent.')
        if page_token is not None:
            raise ToolError('batchGet does not support page_token.')
        params['names'] = names
    elif 'pageSize' in item.parameters:
        params['pageSize'] = size
    if page_token is not None:
        params['pageToken'] = page_token
    response = client.execute(item, params)
    key = catalog.items_key(item)
    if 'pageSize' not in item.parameters and len(response.get(key, [])) > limit:
        raise ToolError(
            f'This method has no page-size control and returned more than {limit} items. Increase limit; no records have been discarded or cursor advanced.'
        )
    next_args = {
        'resource': resource,
        'parent': parent,
        'filter': filter,
        'show_deleted': show_deleted,
        'limit': limit,
        'response_format': response_format,
    }
    return render.list_result(item, response, key, response_format, next_args)


@tool()
def ga_get(name: str, *, api_version: ApiVersion = 'auto') -> dict[str, Any]:
    """Get a full resource, settings singleton, quota snapshot or existing job.

    Args:
        name: Exact name copied from ga_list; metadata uses ga_get_metadata.
        api_version: Reviewed default or explicit published version.
    """
    if name.rstrip('/').endswith('/metadata'):
        raise ToolError('Use ga_get_metadata for dimension and metric metadata.')
    item, params = names.select('ga_get', locator=name, api_version=api_version)
    return render.entity(client.execute(item, params), item)


@tool()
def ga_create(
    resource: str,
    parent: str,
    body: dict[str, Any],
    resource_id: str | None = None,
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Create a configuration resource with the complete official JSON body.

    Args:
        resource: Collection such as keyEvents, properties or customDimensions.
        parent: Full account/property/stream parent name.
        body: Official camelCase body; inspect ga_describe_schema first.
        resource_id: calculatedMetricId, only for calculatedMetrics.
        api_version: Reviewed default or explicit version.
    """
    reject_bindings(resource)
    property_create = resource == 'properties'
    item, params = names.select(
        'ga_create',
        resource=resource,
        locator=None if property_create else parent,
        api_version=api_version,
    )
    payload = dict(body)
    if property_create:
        account = names.normalize(parent, 'accounts')
        if not re.fullmatch(r'accounts/[^/]+', account):
            raise ToolError('A new property requires an accounts/ACCOUNT_ID parent.')
        if payload.get('parent') not in (None, account):
            raise ToolError('body.parent differs from the supplied parent.')
        payload['parent'] = account
    if resource_id is not None:
        params['calculatedMetricId'] = resource_id
    return render.entity(client.execute(item, params, payload), item, mutating=True)


@tool()
def ga_update(
    name: str,
    patch: dict[str, Any],
    update_mask: str | None = None,
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Patch configuration using precise leaf masks, without a read-merge-write.

    Args:
        name: Full resource name. Access writes use ga_manage_access_bindings.
        patch: Writable camelCase leaf changes. Unverified null clears are rejected.
        update_mask: Optional explicit leaf mask matching exactly the patch.
        api_version: Reviewed default or explicit version.
    """
    reject_bindings(name)
    item, params = names.select('ga_update', locator=name, api_version=api_version)
    body, mask = contracts.patch_body(item, patch, update_mask)
    params['updateMask'] = mask
    return render.entity(client.execute(item, params, body), item, mutating=True)


@tool()
def ga_delete(
    name: str, confirm: bool = False, *, api_version: ApiVersion = 'auto'
) -> dict[str, Any]:
    """Delete or archive a resource after explicit user confirmation.

    Accounts/properties are trashed; audiences/custom definitions are archived.
    Args:
        name: Full resource name, discovered before deletion.
        confirm: True only after user approval of this target and operation.
        api_version: Reviewed default or explicit version.
    """
    reject_bindings(name)
    item, params = names.select('ga_delete', locator=name, api_version=api_version)
    response = client.execute(
        item, params, {} if item.request_schema else None, confirm=confirm
    )
    outcome = {'success': True, 'target': name, 'operation': item.name, **response}
    if item.resource in {'accounts', 'properties'}:
        outcome['message'] = (
            'Moved to trash. Restore through the Analytics UI before the service retention period expires.'
        )
    elif item.name == 'archive':
        outcome['message'] = 'Archived. This archive cannot be undone through this API.'
    return render.entity(outcome, item, mutating=True)


@tool()
def ga_query(
    name: str,
    offset: int | None = None,
    limit: int | None = None,
    response_format: ResponseFormat = 'markdown',
    body: dict[str, Any] | None = None,
    *,
    api_version: ApiVersion = 'auto',
) -> CallToolResult:
    """Read rows from an existing audience export/list or completed report task.

    Args:
        name: Existing job resource name; inspect ga_get for readiness.
        offset: Starting row; omit for the first page.
        limit: Row count, default 100. Conflicts with body.limit are rejected.
        response_format: markdown, json or json_full.
        body: Complete official query request, optional.
        api_version: Reviewed default or explicit version.
    """
    item, params = names.select('ga_query', locator=name, api_version=api_version)
    payload = contracts.report_body(
        item, contracts.merge_body(body, offset=offset, limit=limit), params
    )
    return render.report_result(
        item, payload, client.execute(item, params, payload), response_format
    )
