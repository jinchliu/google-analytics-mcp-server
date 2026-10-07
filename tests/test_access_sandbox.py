"""Offline restoration checks, including ambiguous failures after each mutation."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.e2e import sandbox
from tests.e2e import test_access_lifecycle as live
from tests.e2e.access import build_access_plan


@pytest.mark.anyio
@pytest.mark.parametrize(
    'fail_operation',
    [
        None,
        'delete',
        'create',
        'update',
        'clear',
        'batch_create',
        'batch_update',
        'batch_delete',
    ],
)
async def test_access_lifecycle_restores_original_after_each_failure(
    fail_operation, tmp_path, monkeypatch
):
    property_name = 'properties/123'
    original = {
        'name': f'{property_name}/accessBindings/456',
        'user': 'test@example.invalid',
        'roles': ['predefinedRoles/admin'],
    }
    inherited = {**original, 'name': 'accounts/789/accessBindings/456'}
    plan = build_access_plan(
        property_name,
        f'{property_name}/audiences/111',
        original['user'],
        original['name'],
        original['roles'],
        'abc123abc123',
    )
    plan['dry_run'] = await sandbox.dry_run(plan)
    assert len(plan['dry_run']['requests']) == 9
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan))
    journal_path = tmp_path / 'journal.json'
    state = {'binding': deepcopy(original), 'failed': False, 'writes': []}

    async def fake_call(server, tool_name, **arguments):
        if tool_name == 'ga_get':
            return {'name': property_name, 'parent': 'accounts/789'}
        if tool_name == 'ga_list':
            binding = (
                inherited if arguments['parent'] == 'accounts/789' else state['binding']
            )
            return {
                'accessBindings': [deepcopy(binding)] if binding else [],
                '_mcp': {'next_arguments': None},
            }
        assert tool_name == 'ga_manage_access_bindings'
        assert arguments.get('parent', property_name) == property_name
        assert arguments.get('name', original['name']) == original['name']
        operation = arguments['operation']
        body = arguments.get('body', {})
        key = 'clear' if operation == 'update' and body['roles'] == [] else operation
        state['writes'].append(key)
        if operation in {'delete', 'batch_delete'} or key == 'clear':
            state['binding'] = None
            result = {}
        else:
            value = (
                body['requests'][0]['accessBinding']
                if operation.startswith('batch_')
                else body
            )
            state['binding'] = {**original, 'roles': deepcopy(value['roles'])}
            result = (
                {'accessBindings': [deepcopy(state['binding'])]}
                if operation.startswith('batch_')
                else deepcopy(state['binding'])
            )
        if key == fail_operation and not state['failed']:
            state['failed'] = True
            raise OSError('Synthetic connection lost after mutation')
        return result

    monkeypatch.setattr(live, 'call', fake_call)
    monkeypatch.setattr(sandbox, 'call', fake_call)
    config = SimpleNamespace(
        getoption=lambda name: {
            '--sandbox-plan': str(path),
            '--sandbox-journal': str(journal_path),
        }[name]
    )
    if fail_operation:
        with pytest.raises(OSError, match='after mutation'):
            await live.test_property_access_lifecycle_and_restore(
                None, property_name, config
            )
    else:
        await live.test_property_access_lifecycle_and_restore(
            None, property_name, config
        )
    assert state['binding'] == original
    recorded = json.loads(journal_path.read_text())['cases'][
        'property_access_lifecycle'
    ]['events']
    assert recorded[-1]['restored'] is True
    assert recorded[-1]['account_binding_unchanged'] is True
