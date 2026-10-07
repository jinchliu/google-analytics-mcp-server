"""Offline account-access boundaries, recovery and no-replay regression tests."""

import json
import os
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.e2e import account_access, sandbox
from tests.e2e import test_account_access as live
from tests.e2e.account_access import (
    AccessSandbox,
    build_account_access_plan,
    canonical,
    preview,
)

ACCOUNT = 'accounts/123'
PROPERTY = 'properties/456'
OPERATOR = 'operator@example.invalid'
RECIPIENT = 'recipient@example.invalid'


@pytest.fixture
def inputs():
    return {
        'account': ACCOUNT,
        'property_name': PROPERTY,
        'principal': OPERATOR,
        'bindings': {
            parent: [
                {
                    'name': f'{parent}/accessBindings/admin',
                    'user': OPERATOR,
                    'roles': ['predefinedRoles/admin'],
                }
            ]
            for parent in (ACCOUNT, PROPERTY)
        },
        'test_user': RECIPIENT,
        'run_id': 'abc123abc123',
    }


@pytest.mark.parametrize(
    'field,value',
    [
        ('account', PROPERTY),
        ('property_name', ACCOUNT),
        ('account', 'accounts/123/extra'),
        ('property_name', 'properties/456/extra'),
        ('run_id', '../unsafe'),
        ('principal', ''),
        ('test_user', ''),
        ('test_user', OPERATOR),
        ('test_user', OPERATOR.upper()),
    ],
)
def test_access_plan_rejects_invalid_or_operator_targets(inputs, field, value):
    inputs[field] = value
    with pytest.raises(ValueError):
        build_account_access_plan(**inputs)


@pytest.mark.parametrize(
    'problem',
    [
        'missing_parent',
        'missing_admin',
        'non_admin',
        'wrong_parent',
        'duplicate',
        'existing_recipient',
    ],
)
def test_access_plan_requires_complete_safe_snapshots(inputs, problem):
    bindings = inputs['bindings']
    if problem == 'missing_parent':
        del bindings[PROPERTY]
    elif problem == 'missing_admin':
        bindings[ACCOUNT] = []
    elif problem == 'non_admin':
        bindings[ACCOUNT][0]['roles'] = ['predefinedRoles/viewer']
    elif problem == 'wrong_parent':
        bindings[PROPERTY][0]['name'] = f'{ACCOUNT}/accessBindings/wrong'
    elif problem == 'duplicate':
        bindings[ACCOUNT].append(deepcopy(bindings[ACCOUNT][0]))
    else:
        bindings[PROPERTY].append(
            {
                'name': f'{PROPERTY}/accessBindings/preexisting',
                'user': RECIPIENT.upper(),
                'roles': ['predefinedRoles/viewer'],
            }
        )
    with pytest.raises(ValueError):
        build_account_access_plan(**inputs)


@pytest.mark.anyio
@pytest.mark.parametrize('recipient,expected_count', [(None, 2), (RECIPIENT, 22)])
async def test_account_preview_uses_real_discovery_without_network(
    inputs, recipient, expected_count
):
    inputs['test_user'] = recipient
    plan = build_account_access_plan(**inputs)
    result = await preview(plan)
    assert result['network'] is False
    assert len(result['requests']) == expected_count
    assert all(
        request['uri'].startswith('https://analyticsadmin.googleapis.com/v1alpha/')
        for request in result['requests']
    )
    if recipient is None:
        assert [request['method'] for request in result['requests']] == [
            'PATCH',
            'POST',
        ]
        assert result['requests'][0]['body']['roles'] == ['predefinedRoles/admin']
        assert result['requests'][1]['body']['requests'][0]['accessBinding'][
            'roles'
        ] == ['predefinedRoles/admin']
    else:
        for level, parent in (('account', ACCOUNT), ('property', PROPERTY)):
            requests = {
                item['step']: item
                for item in result['requests']
                if item['step'].startswith(level + '_')
            }
            assert all(
                f'/{parent}/accessBindings' in request['uri']
                for request in requests.values()
            )
            assert requests[f'{level}_clear_single']['body']['roles'] == []
            assert (
                requests[f'{level}_clear_batch']['body']['requests'][0][
                    'accessBinding'
                ]['roles']
                == []
            )
            assert requests[f'{level}_create_single']['body']['user'] == RECIPIENT


async def setup_runner(inputs, tmp_path):
    plan = build_account_access_plan(**inputs)
    plan['dry_run'] = await preview(plan)
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(plan))
    journal_path = tmp_path / 'journal.json'
    config = SimpleNamespace(
        getoption=lambda name: {
            '--account': ACCOUNT,
            '--sandbox-plan': str(plan_path),
            '--sandbox-journal': str(journal_path),
        }[name]
    )
    return plan, plan_path, journal_path, config


@pytest.mark.anyio
async def test_account_journal_checks_targets_digest_and_replay(inputs, tmp_path):
    plan, path, journal, _ = await setup_runner(inputs, tmp_path)
    with pytest.raises(ValueError, match='targets'):
        AccessSandbox(path, journal, 'accounts/999', PROPERTY)
    with pytest.raises(ValueError, match='targets'):
        AccessSandbox(path, journal, ACCOUNT, 'properties/999')
    runner = AccessSandbox(path, journal, ACCOUNT, PROPERTY)
    runner.begin('account_access_lifecycle')
    with pytest.raises(ValueError, match='already attempted'):
        AccessSandbox(path, journal, ACCOUNT, PROPERTY).begin(
            'account_access_lifecycle'
        )
    changed = deepcopy(plan)
    changed['calls']['account_create_single']['arguments']['body']['roles'] = [
        'predefinedRoles/admin'
    ]
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='writes'):
        AccessSandbox(path, journal, ACCOUNT, PROPERTY)
    del plan['dry_run']
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='preview'):
        AccessSandbox(path, journal, ACCOUNT, PROPERTY)


@pytest.mark.anyio
async def test_account_runner_rejects_unowned_cross_parent_or_repeated_writes(
    inputs, tmp_path
):
    _, path, journal, _ = await setup_runner(inputs, tmp_path)
    runner = AccessSandbox(path, journal, ACCOUNT, PROPERTY)
    case = 'account_access_lifecycle'
    runner.begin(case)
    with pytest.raises(ValueError, match='current lifecycle'):
        await runner.execute(None, case, 'property_create_single')
    with pytest.raises(ValueError, match='selected parent'):
        await runner.execute(
            None,
            case,
            'account_delete_single',
            **{'$account_access': f'{PROPERTY}/accessBindings/other'},
        )
    with pytest.raises(ValueError, match='Inspect'):
        await runner.execute(
            None,
            case,
            'account_delete_single',
            **{'$account_access': f'{ACCOUNT}/accessBindings/admin'},
        )
    runner.note(case, step='account_create_single', phase='before')
    with pytest.raises(ValueError, match='already attempted'):
        await runner.execute(None, case, 'account_create_single')


def install_fake(
    inputs, journal_path, monkeypatch, fail_step=None, failure_mode='after'
):
    state = {'bindings': deepcopy(inputs['bindings']), 'writes': []}

    async def fake_call(server, tool_name, **arguments):
        if tool_name == 'ga_get':
            return {'name': PROPERTY, 'parent': ACCOUNT}
        if tool_name == 'ga_list':
            return {
                'accessBindings': deepcopy(state['bindings'][arguments['parent']]),
                '_mcp': {'next_arguments': None},
            }
        assert tool_name == 'ga_manage_access_bindings'
        data = json.loads(journal_path.read_text())
        case = list(data['cases'])[-1]
        step = next(
            event['step']
            for event in reversed(data['cases'][case]['events'])
            if event.get('phase') == 'before'
        )
        state['writes'].append(step)
        if step == fail_step and failure_mode == 'before':
            raise OSError('Synthetic ambiguous failure before mutation')
        operation = arguments['operation']
        body = arguments.get('body', {})
        parent = (
            arguments.get('parent') or arguments['name'].split('/accessBindings/')[0]
        )
        entries = state['bindings'][parent]
        value = (
            body['requests'][0].get('accessBinding', {})
            if operation.startswith('batch_')
            else body
        )
        if operation in {'delete', 'batch_delete'}:
            target = (
                arguments['name']
                if operation == 'delete'
                else body['requests'][0]['name']
            )
            entries[:] = [entry for entry in entries if entry['name'] != target]
            result = {}
        else:
            target = (
                arguments['name']
                if operation == 'update'
                else value.get('name', f'{parent}/accessBindings/temporary')
            )
            entries[:] = [entry for entry in entries if entry['name'] != target]
            result = {
                'name': target,
                'user': value['user'],
                'roles': deepcopy(value['roles']),
            }
            if value['roles']:
                entries.append(deepcopy(result))
            else:
                result = {}
            if operation.startswith('batch_'):
                result = {'accessBindings': [result]} if result else {}
        if step == fail_step:
            raise OSError('Synthetic ambiguous failure after mutation')
        return result

    monkeypatch.setattr(account_access, 'call', fake_call)
    monkeypatch.setattr(sandbox, 'call', fake_call)
    return state


@pytest.mark.anyio
@pytest.mark.parametrize('level', ['account', 'property'])
@pytest.mark.parametrize(
    'failure',
    [
        None,
        'create_single',
        'update_single',
        'delete_single',
        'create_for_clear',
        'clear_single',
        'create_batch',
        'update_batch',
        'delete_batch',
        'create_for_batch_clear',
        'clear_batch',
    ],
)
async def test_temporary_access_cleanup_after_each_applied_failure(
    inputs, tmp_path, monkeypatch, level, failure
):
    _, _, journal, config = await setup_runner(inputs, tmp_path)
    fail_step = f'{level}_{failure}' if failure else None
    state = install_fake(inputs, journal, monkeypatch, fail_step)
    if failure:
        with pytest.raises(OSError, match='after mutation'):
            await live.test_temporary_access_lifecycle(None, PROPERTY, config, level)
        assert state['writes'].count(fail_step) == 1
    else:
        await live.test_temporary_access_lifecycle(None, PROPERTY, config, level)
        assert len(state['writes']) == 10
    assert state['bindings'] == inputs['bindings']
    events = json.loads(journal.read_text())['cases'][f'{level}_access_lifecycle'][
        'events'
    ]
    assert events[-1]['cleanup_verified'] is True
    assert events[-1]['original_bindings_unchanged'] is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    'failure', ['delete_single', 'clear_single', 'delete_batch', 'clear_batch']
)
async def test_unresolved_removal_is_not_replayed_as_cleanup(
    inputs, tmp_path, monkeypatch, failure
):
    _, _, journal, config = await setup_runner(inputs, tmp_path)
    fail_step = f'account_{failure}'
    state = install_fake(inputs, journal, monkeypatch, fail_step, failure_mode='before')
    with pytest.raises(pytest.fail.Exception, match='unresolved'):
        await live.test_temporary_access_lifecycle(None, PROPERTY, config, 'account')
    assert state['writes'].count(fail_step) == 1
    assert 'account_cleanup' not in state['writes']
    assert state['bindings'][ACCOUNT][0] == inputs['bindings'][ACCOUNT][0]
    assert state['bindings'][PROPERTY] == inputs['bindings'][PROPERTY]
    assert len(state['bindings'][ACCOUNT]) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    'failure', [None, 'account_update_original', 'account_batch_update_original']
)
async def test_unchanged_account_updates_preserve_both_snapshots(
    inputs, tmp_path, monkeypatch, failure
):
    inputs['test_user'] = None
    _, _, journal, config = await setup_runner(inputs, tmp_path)
    state = install_fake(inputs, journal, monkeypatch, failure)
    if failure:
        with pytest.raises(OSError, match='after mutation'):
            await live.test_account_updates_preserve_original_roles(
                None, PROPERTY, config
            )
    else:
        await live.test_account_updates_preserve_original_roles(None, PROPERTY, config)
    assert canonical(state['bindings'][ACCOUNT]) == canonical(
        inputs['bindings'][ACCOUNT]
    )
    assert canonical(state['bindings'][PROPERTY]) == canonical(
        inputs['bindings'][PROPERTY]
    )


@pytest.mark.anyio
@pytest.mark.parametrize('changed', ['parent', 'roles', 'recipient', 'unrelated_user'])
async def test_live_preflight_refuses_changes_since_snapshot(
    inputs, tmp_path, monkeypatch, changed
):
    _, _, journal, config = await setup_runner(inputs, tmp_path)
    state = install_fake(inputs, journal, monkeypatch)
    if changed == 'parent':
        original_call = account_access.call

        async def wrong_parent(server, tool_name, **arguments):
            if tool_name == 'ga_get':
                return {'parent': 'accounts/999'}
            return await original_call(server, tool_name, **arguments)

        monkeypatch.setattr(account_access, 'call', wrong_parent)
    elif changed == 'roles':
        state['bindings'][ACCOUNT][0]['roles'] = ['predefinedRoles/viewer']
    else:
        state['bindings'][ACCOUNT].append(
            {
                'name': f'{ACCOUNT}/accessBindings/existing',
                'user': RECIPIENT
                if changed == 'recipient'
                else 'other@example.invalid',
                'roles': ['predefinedRoles/viewer'],
            }
        )
    with pytest.raises(AssertionError):
        await live.test_temporary_access_lifecycle(None, PROPERTY, config, 'account')
    assert not state['writes']


@pytest.mark.anyio
async def test_account_inventory_is_read_only_complete_and_scoped(inputs, monkeypatch):
    pages = []

    async def fake_call(server, tool_name, **arguments):
        assert os.environ['GA_MCP_READ_ONLY'] == '1'
        if tool_name == 'ga_get':
            return {'name': arguments['name'], 'parent': ACCOUNT}
        assert tool_name == 'ga_list'
        parent = arguments['parent']
        assert parent in {ACCOUNT, PROPERTY}
        pages.append((parent, arguments.get('page_token')))
        if 'page_token' not in arguments:
            return {
                'accessBindings': [],
                '_mcp': {'next_arguments': {**arguments, 'page_token': 'next'}},
            }
        return {
            'accessBindings': deepcopy(inputs['bindings'][parent]),
            '_mcp': {'next_arguments': None},
        }

    monkeypatch.setattr(account_access, 'call', fake_call)
    result = await account_access.inventory(ACCOUNT, PROPERTY, OPERATOR)
    assert result['read_only'] is True
    assert result['bindings'] == inputs['bindings']
    assert len(pages) == 4
    assert os.environ.get('GA_MCP_READ_ONLY') != '1'


@pytest.mark.anyio
async def test_account_inventory_rejects_incomplete_pagination(monkeypatch):
    calls = []

    async def fake_call(server, tool_name, **arguments):
        calls.append(arguments)
        return {'accessBindings': [], '_mcp': {'next_arguments': arguments}}

    monkeypatch.setattr(account_access, 'call', fake_call)
    with pytest.raises(ValueError, match='Incomplete'):
        await account_access.list_bindings(None, ACCOUNT)
    assert len(calls) == 20


def test_retention_requires_a_selected_recipient(inputs):
    inputs.update(test_user=None, retain_for_review=True)
    with pytest.raises(ValueError, match='selected test recipient'):
        build_account_access_plan(**inputs)


@pytest.mark.anyio
async def test_retention_preview_adds_only_two_fixed_viewer_grants(inputs):
    plan = build_account_access_plan(**inputs, retain_for_review=True)
    result = await preview(plan)
    assert len(result['requests']) == 24
    for level, request in zip(
        ('account', 'property'), result['requests'][-2:], strict=True
    ):
        assert request['step'] == f'{level}_retain_viewer'
        assert request['method'] == 'POST'
        assert f'/{plan[level]}/accessBindings?' in request['uri']
        assert request['body'] == {
            'user': RECIPIENT,
            'roles': ['predefinedRoles/viewer'],
        }


@pytest.mark.anyio
async def test_review_writes_require_retention_preview(inputs, tmp_path):
    _, path, journal, _ = await setup_runner(inputs, tmp_path)
    runner = AccessSandbox(path, journal, ACCOUNT, PROPERTY)
    runner.begin('access_review')
    with pytest.raises(ValueError, match='does not authorize'):
        await runner.execute(None, 'access_review', 'account_retain_viewer')


@pytest.mark.anyio
@pytest.mark.parametrize(
    'problem',
    [
        'missing_account',
        'missing_property',
        'error',
        'missing_step',
        'cleanup_unverified',
        'originals_unverified',
    ],
)
async def test_retention_refuses_incomplete_or_failed_lifecycles(
    inputs, tmp_path, monkeypatch, problem
):
    inputs['retain_for_review'] = True
    _, path, journal, config = await setup_runner(inputs, tmp_path)
    state = install_fake(inputs, journal, monkeypatch)
    for level in ('account', 'property'):
        await live.test_temporary_access_lifecycle(None, PROPERTY, config, level)
    runner = AccessSandbox(path, journal, ACCOUNT, PROPERTY)
    cases = runner.data['cases']
    if problem.startswith('missing_') and problem != 'missing_step':
        del cases[f'{problem.removeprefix("missing_")}_access_lifecycle']
    else:
        events = cases['account_access_lifecycle']['events']
        if problem == 'error':
            events.insert(-1, {'phase': 'error'})
        elif problem == 'missing_step':
            events[:] = [
                event
                for event in events
                if event.get('step') != 'account_update_single'
            ]
        elif problem == 'cleanup_unverified':
            events[-1]['cleanup_verified'] = False
        else:
            events[-1]['original_bindings_unchanged'] = False
    runner.flush()
    with pytest.raises(ValueError, match='Both access lifecycles'):
        await live.test_retain_viewer_for_user_review(None, PROPERTY, config)
    assert len(state['writes']) == 20
    assert state['bindings'] == inputs['bindings']


@pytest.mark.anyio
@pytest.mark.parametrize(
    'failure,failure_mode,retained_count',
    [
        (None, 'after', 2),
        ('account_retain_viewer', 'before', 0),
        ('account_retain_viewer', 'after', 1),
        ('property_retain_viewer', 'before', 1),
        ('property_retain_viewer', 'after', 2),
    ],
)
async def test_review_retains_access_and_never_replays_ambiguous_grants(
    inputs, tmp_path, monkeypatch, failure, failure_mode, retained_count
):
    inputs['retain_for_review'] = True
    _, path, journal, config = await setup_runner(inputs, tmp_path)
    state = install_fake(inputs, journal, monkeypatch, failure, failure_mode)
    for level in ('account', 'property'):
        await live.test_temporary_access_lifecycle(None, PROPERTY, config, level)
    if failure:
        with pytest.raises(OSError, match='ambiguous failure'):
            await live.test_retain_viewer_for_user_review(None, PROPERTY, config)
    else:
        await live.test_retain_viewer_for_user_review(None, PROPERTY, config)
    retained = []
    for parent, entries in state['bindings'].items():
        retained.extend(entry for entry in entries if entry['user'] == RECIPIENT)
        assert canonical(
            [entry for entry in entries if entry['user'] != RECIPIENT]
        ) == canonical(inputs['bindings'][parent])
    assert len(retained) == retained_count
    assert all(entry['roles'] == ['predefinedRoles/viewer'] for entry in retained)
    assert len(state['writes']) == len(set(state['writes']))
    events = json.loads(journal.read_text())['cases']['access_review']['events']
    assert events[-1]['review_complete'] is (failure is None)
    with pytest.raises(ValueError, match='already attempted'):
        AccessSandbox(path, journal, ACCOUNT, PROPERTY).begin('access_review')
    if failure is None:
        with pytest.raises(ValueError, match='already attempted'):
            await AccessSandbox(path, journal, ACCOUNT, PROPERTY).execute(
                None, 'access_review', 'account_retain_viewer'
            )
