"""Confirmation and feature gates must stop requests before HTTP or ADC."""

import copy

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import catalog, client, policy
from google_analytics_mcp_server.registry import REGISTRY
from google_analytics_mcp_server.tools import generic, meta
from tests.helpers import native_arguments, wire_input

SENSITIVE = [
    item for item in catalog.methods() if item.owner in policy.DESTRUCTIVE_OWNERS
]
MUTATIONS = [item for item in catalog.methods() if not policy.is_read(item)]
BINDINGS = [
    item for item in catalog.methods() if item.owner == 'ga_manage_access_bindings'
]


@pytest.mark.parametrize('item', SENSITIVE, ids=lambda item: item.key)
@pytest.mark.parametrize('route', ['native', 'raw'])
def test_every_sensitive_operation_requires_confirmation(
    item, route, http, full_profile
):
    with pytest.raises(ToolError, match='confirm=true'):
        if route == 'native':
            args = native_arguments(item)
            args['confirm'] = False
            REGISTRY[item.owner].fn(**args)
        else:
            params, body = wire_input(item)
            meta.ga_call_api(item.api, item.version, item.id, params, body)
    assert http.requests == []


@pytest.mark.parametrize('item', MUTATIONS, ids=lambda item: item.key)
def test_read_only_blocks_every_mutation_before_http(
    item, http, full_profile, monkeypatch
):
    monkeypatch.setenv('GA_MCP_READ_ONLY', '1')
    params, body = wire_input(item)
    with pytest.raises(ToolError, match='read-only'):
        client.execute(item, params, body, confirm=True)
    assert http.requests == []


def test_all_twelve_binding_writes_have_dedicated_confirmed_owner():
    assert len(BINDINGS) == 12
    assert {item.name for item in BINDINGS} == {
        'create',
        'patch',
        'delete',
        'batchCreate',
        'batchUpdate',
        'batchDelete',
    }


@pytest.mark.parametrize('operation', ['create', 'update', 'delete'])
def test_generic_mutations_reject_access_bindings(operation, http):
    name = 'properties/123/accessBindings/456'
    with pytest.raises(ToolError, match='ga_manage_access_bindings'):
        if operation == 'create':
            generic.ga_create(
                'properties.accessBindings', 'properties/123', {'roles': []}
            )
        elif operation == 'update':
            generic.ga_update(name, {'roles': []})
        else:
            generic.ga_delete(name, confirm=True)
    assert not http.requests


@pytest.mark.parametrize('item', BINDINGS, ids=lambda item: item.key)
def test_binding_cross_parent_targets_are_rejected(item, http):
    params, body = wire_input(item)
    if item.name in {'delete', 'batchDelete'}:
        if item.name == 'delete':
            params['name'] = 'properties/123'
        else:
            body['requests'][0]['name'] = 'properties/999/accessBindings/456'
    elif item.name.startswith('batch'):
        body['requests'][0]['accessBinding']['name'] = (
            'properties/999/accessBindings/456'
        )
    else:
        body['name'] = 'properties/999/accessBindings/456'
    with pytest.raises(ToolError, match='Binding|binding'):
        client.execute(item, params, body, confirm=True)
    assert not http.requests


@pytest.mark.parametrize('name', ['create', 'patch', 'batchCreate', 'batchUpdate'])
def test_missing_complete_roles_is_rejected(name, http):
    item = catalog.method('admin', f'properties.accessBindings.{name}')
    params, body = wire_input(item)
    body = copy.deepcopy(body)
    binding = body['requests'][0]['accessBinding'] if name.startswith('batch') else body
    binding.pop('roles')
    with pytest.raises(ToolError, match='complete intended roles'):
        client.execute(item, params, body, confirm=True)
    assert not http.requests


@pytest.mark.parametrize('mask', ['*', 'webStreamData', 'web_stream_data'])
def test_raw_broad_masks_require_confirmation(mask, http, full_profile):
    params = {'name': 'properties/123/dataStreams/456', 'updateMask': mask}
    body = {'webStreamData': {'defaultUri': ''}}
    with pytest.raises(ToolError, match='confirm=true'):
        meta.ga_call_api(
            'admin', 'v1beta', 'properties.dataStreams.patch', params, body
        )
    assert not http.requests
    meta.ga_call_api(
        'admin', 'v1beta', 'properties.dataStreams.patch', params, body, confirm=True
    )
    assert http.requests[-1].query['updateMask'] == [mask]


@pytest.mark.parametrize('mask', [None, '', '   '])
def test_raw_patch_never_implies_whole_resource_update(mask, http, full_profile):
    params = {'name': 'properties/123'}
    if mask is not None:
        params['updateMask'] = mask
    with pytest.raises(ToolError, match='explicit updateMask'):
        meta.ga_call_api(
            'admin',
            'v1beta',
            'properties.patch',
            params,
            {'displayName': 'Example'},
            confirm=True,
        )
    assert not http.requests


def test_feature_flags_apply_to_direct_and_raw_dispatch(http, monkeypatch):
    item = catalog.method('data', 'properties.chat')
    with pytest.raises(ToolError, match='disabled'):
        client.execute(item, {'property': 'properties/123'}, {'userQuery': 'hello'})
    with pytest.raises(ToolError, match='disabled'):
        meta.ga_call_api('admin', 'v1beta', 'accounts.list')
    monkeypatch.setenv('GA_MCP_ENABLE_RAW_API', '1')
    with pytest.raises(ToolError, match='Chat is disabled'):
        meta.ga_call_api(
            'data',
            'v1alpha',
            'properties.chat',
            {'property': 'properties/123'},
            {'userQuery': 'hello'},
        )
    assert not http.requests
