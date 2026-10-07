"""Declarative endpoint and field routes, separate from verification evidence."""

import re
from typing import Any

from google_analytics_mcp_server import catalog, config, policy
from google_analytics_mcp_server.catalog import Method

TRANSPORT_OVERRIDES = frozenset(
    {
        'key',
        'access_token',
        'oauth_token',
        'uploadType',
        'upload_protocol',
        'callback',
    }
)
QUERY_ARGUMENTS = {
    'pageSize': 'limit',
    'pageToken': 'page_token',
    'showDeleted': 'show_deleted',
    'filter': 'filter',
    'names': 'names',
    'updateMask': 'update_mask',
    'calculatedMetricId': 'resource_id',
}


def availability(item: Method) -> dict[str, Any]:
    """Describe current reachability without claiming live API eligibility."""
    feature = 'GA_MCP_ENABLE_CHAT' if item.owner == 'ga_chat' else None
    enabled = (not config.read_only() or policy.is_read(item)) and (
        feature is None or config.enabled('ENABLE_CHAT')
    )
    return {
        'native_enabled': enabled,
        'feature_gate': feature,
        'read_only_eligible': policy.is_read(item),
        'confirmation_required': item.owner in policy.DESTRUCTIVE_OWNERS,
        'raw_enabled': enabled
        and config.enabled('ENABLE_RAW_API')
        and not config.read_only(),
    }


def parameter_route(item: Method, name: str, spec: dict[str, Any]) -> str:
    """Map a business parameter to the corresponding native tool argument."""
    if spec['location'] == 'query':
        return QUERY_ARGUMENTS.get(name, 'raw.params.' + name)
    if item.owner in {
        'ga_start_async_job',
        'ga_submit_user_deletion',
        'ga_get_metadata',
    }:
        return 'property'
    return name


def request_fields(item: Method) -> list[dict[str, Any]]:
    """Enumerate recursive field routes; mark repeated and recursive references."""
    if not item.request_schema:
        return []
    argument = 'patch' if item.owner in {'ga_update', 'ga_update_settings'} else 'body'
    generated = item.owner in {
        'ga_acknowledge_user_data_collection',
        'ga_delete',
        'ga_review_dv360_link_proposal',
    }
    result: list[dict[str, Any]] = []

    def visit(
        spec: dict[str, Any], path: str, ancestors: frozenset[str], writable: bool
    ) -> None:
        writable = writable and not spec.get('readOnly', False)
        if argument == 'patch' and re.search(
            r'\bImmutable\.', spec.get('description', '')
        ):
            writable = False
        entry: dict[str, Any] = {
            'path': path,
            'native': 'generated' if generated else f'{argument}.{path}',
            'raw': f'body.{path}',
            'writable': writable,
        }
        if '$ref' in spec:
            ref = spec['$ref']
            if ref in ancestors:
                entry['recursive_schema'] = ref
                result.append(entry)
                return
            spec = item.schema(ref)
            ancestors = ancestors | {ref}
        if spec.get('type') == 'array':
            visit(spec.get('items', {}), path + '[]', ancestors, writable)
        elif spec.get('properties'):
            for name, child in spec['properties'].items():
                visit(child, f'{path}.{name}' if path else name, ancestors, writable)
        else:
            entry.update({k: spec[k] for k in ('type', 'format', 'enum') if k in spec})
            result.append(entry)

    visit({'$ref': item.request_schema}, '', frozenset(), True)
    return result


def endpoint(item: Method) -> dict[str, Any]:
    """Describe field access and intentional value restrictions for one endpoint."""
    global_params = catalog.document(item.api, item.version).get('parameters', {})
    restrictions = [
        'Google enforces business rules, roles, feature eligibility and preview access.',
        'Read output is bounded by GA_MCP_MAX_OUTPUT_BYTES; no silent row clipping.',
    ]
    if 'updateMask' in item.parameters:
        restrictions.extend(
            [
                'An explicit nonempty updateMask is required.',
                'Native patches require exact writable leaf masks; wildcard and parent-message masks require raw dispatch and confirmation.',
                'Unverified null clearing is rejected by native patches; array semantics are method-specific.',
            ]
        )
    if 'accessBindings' in item.resource and not policy.is_read(item):
        restrictions.append(
            'All binding writes require confirmation; updates require a complete intended roles array, with [] denoting deletion.'
        )
    return {
        'method': item.key,
        'native_tool': item.owner,
        'default_version': catalog.routes()[item.logical_key]['default_version'],
        **availability(item),
        'parameters': {
            name: {'native': parameter_route(item, name, spec), 'raw': f'params.{name}'}
            for name, spec in item.parameters.items()
        },
        'request_fields': request_fields(item),
        'global_query_parameters': {
            name: f'raw.params.{name}'
            for name in global_params
            if name not in TRANSPORT_OVERRIDES
        },
        'restrictions': restrictions,
        'transport': 'ADC authentication and JSON responses; auth/upload overrides are not exposed.',
        'verification': 'Declared routes from pinned discovery; not evidence of offline or live test coverage.',
    }


def ledger() -> dict[str, Any]:
    """Return the versioned ledger and actual configured method availability."""
    entries = [
        {
            'method': item.key,
            'tool': item.owner,
            'default': catalog.routes()[item.logical_key]['default_version']
            == item.version,
            **availability(item),
        }
        for item in catalog.methods()
    ]
    enabled = [entry for entry in entries if entry['native_enabled']]
    return {
        'logical_methods': len(catalog.routes()),
        'versioned_endpoints': len(entries),
        'enabled_logical_methods': len(
            {
                (entry['method'].split(':')[0], entry['method'].split(':')[2])
                for entry in enabled
            }
        ),
        'enabled_versioned_endpoints': len(enabled),
        'verification': 'Declared routes, not a test result. Describe a method to inspect its field routes and value restrictions.',
        'methods': entries,
    }
