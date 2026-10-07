"""Central method and payload policy, shared by native and raw tools."""

import re
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import config
from google_analytics_mcp_server.catalog import Method

READ_OWNERS = frozenset(
    {
        'ga_list',
        'ga_get',
        'ga_query',
        'ga_run_report',
        'ga_run_realtime_report',
        'ga_run_pivot_report',
        'ga_batch_run_reports',
        'ga_run_funnel_report',
        'ga_check_compatibility',
        'ga_get_metadata',
        'ga_run_access_report',
        'ga_search_change_history',
        'ga_describe_schema',
    }
)
JOB_OWNERS = frozenset({'ga_start_async_job', 'ga_chat'})
DESTRUCTIVE_OWNERS = frozenset(
    {
        'ga_delete',
        'ga_update_settings',
        'ga_manage_access_bindings',
        'ga_submit_user_deletion',
        'ga_acknowledge_user_data_collection',
        'ga_review_dv360_link_proposal',
    }
)


def is_read(item: Method) -> bool:
    """Classify reads by reviewed ownership, including read-only POST methods."""
    return item.owner in READ_OWNERS


def broad_mask(item: Method, mask: str) -> bool:
    """Recognize wildcard and message masks at any nesting depth."""
    if '*' in mask:
        return True
    if not item.request_schema:
        return False
    for path in mask.split(','):
        spec = item.schema(item.request_schema)
        field: dict[str, Any] = {}
        for part in path.strip().split('.'):
            camel = re.sub(r'_([a-z])', lambda match: match[1].upper(), part)
            field = spec.get('properties', {}).get(camel, {})
            spec = item.schema(field['$ref']) if '$ref' in field else field
        if '$ref' in field or field.get('type') == 'object':
            return True
    return False


def guard(
    item: Method, params: dict[str, Any], body: dict[str, Any] | None, confirm: bool
) -> None:
    """Reject disallowed calls before credentials, service creation or HTTP."""
    if item.owner == 'ga_chat' and not config.enabled('ENABLE_CHAT'):
        raise ToolError('Chat is disabled. Set GA_MCP_ENABLE_CHAT=1 to enable it.')
    if config.read_only() and not is_read(item):
        raise ToolError(f'{item.id} is unavailable in strict read-only mode.')
    mask = params.get('updateMask')
    if 'updateMask' in item.parameters and (
        not isinstance(mask, str) or not mask.strip()
    ):
        raise ToolError(
            'Supply an explicit updateMask; implicit whole-resource updates are not supported.'
        )
    requires_confirmation = item.owner in DESTRUCTIVE_OWNERS or (
        isinstance(mask, str) and broad_mask(item, mask)
    )
    if requires_confirmation and not confirm:
        raise ToolError(
            f'{item.id} requires confirm=true after user approval of the operation and target.'
        )
    if 'accessBindings' in item.resource and not is_read(item):
        validate_access_targets(item, params, body)


def validate_access_targets(
    item: Method, params: dict[str, Any], body: dict[str, Any] | None
) -> None:
    """Validate single/batch binding targets and explicit complete role sets."""
    payload = body or {}
    parent = params.get('parent')
    records = payload.get('requests') if item.name.startswith('batch') else [payload]
    if item.name.startswith('batch'):
        if not isinstance(records, list) or not 1 <= len(records) <= 1000:
            raise ToolError('Access-binding batches require 1-1000 official requests.')
    for record in records or []:
        if not isinstance(record, dict):
            raise ToolError('Each binding request must be an object.')
        binding = record.get('accessBinding', record)
        if not isinstance(binding, dict):
            raise ToolError('accessBinding must be an object.')
        for target in (record.get('name'), binding.get('name'), params.get('name')):
            if target is None:
                continue
            if not isinstance(target, str) or not re.fullmatch(
                r'(accounts|properties)/[^/]+/accessBindings/[^/]+', target
            ):
                raise ToolError(
                    'Binding names must be full accounts/.../accessBindings/... or properties/.../accessBindings/... names.'
                )
            if parent and not target.startswith(f'{parent}/accessBindings/'):
                raise ToolError('Every binding target must belong to the batch parent.')
            if params.get('name') and target != params['name']:
                raise ToolError('Binding body name differs from the target name.')
        if record.get('parent') not in (None, parent):
            raise ToolError('Every binding request parent must match the batch parent.')
        if (
            item.name in {'create', 'patch', 'batchCreate', 'batchUpdate'}
            and 'roles' not in binding
        ):
            raise ToolError(
                'Supply the complete intended roles list; an empty list deletes an existing binding.'
            )
        if item.name in {'batchUpdate', 'batchDelete'} and not (
            record.get('name') or binding.get('name')
        ):
            raise ToolError(
                'Each update/delete request requires the full binding name.'
            )
