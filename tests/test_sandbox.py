"""Offline guard checks for the explicitly opted-in live sandbox runner."""

import json
import os
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.conftest import pytest_collection_modifyitems
from tests.e2e.sandbox import Sandbox, build_plan, dry_run, substitute


@pytest.fixture
def plan():
    return build_plan(
        'properties/123',
        'properties/123/audiences/456',
        'test@example.invalid',
        'properties/123/accessBindings/789',
        ['predefinedRoles/admin'],
        'abc123abc123',
    )


@pytest.mark.parametrize(
    'field,value',
    [
        ('property_name', 'accounts/123'),
        ('audience', 'properties/999/audiences/456'),
        ('binding', 'accounts/123/accessBindings/789'),
        ('binding', 'properties/999/accessBindings/789'),
        ('roles', []),
        ('roles', ['predefinedRoles/viewer']),
        ('principal', ''),
        ('run_id', '../unsafe'),
    ],
)
def test_plan_rejects_unscoped_targets_or_permission_removal(field, value):
    arguments = dict(
        property_name='properties/123',
        audience='properties/123/audiences/456',
        principal='test@example.invalid',
        binding='properties/123/accessBindings/789',
        roles=['predefinedRoles/admin'],
        run_id='abc123abc123',
    )
    arguments[field] = value
    with pytest.raises(ValueError):
        build_plan(**arguments)


@pytest.mark.anyio
async def test_dry_run_uses_real_discovery_with_no_adc_or_network(plan):
    result = await dry_run(plan)
    assert result['network'] is False
    assert len(result['requests']) == 16
    clear_requests = [
        item for item in result['requests'] if item['step'] == 'clear_uri'
    ]
    assert len(clear_requests) == 2
    assert all(
        item['body'] == {'webStreamData': {'defaultUri': ''}} for item in clear_requests
    )
    assert all(
        'updateMask=web_stream_data.default_uri' in item['uri']
        for item in clear_requests
    )
    assert all(
        'example.invalid' in item['body']['user']
        for item in result['requests']
        if item['step'] == 'binding_update'
    )


def test_substitution_is_literal_and_requires_known_placeholders():
    assert substitute({'x': ['$stream', 'literal $stream']}, {'$stream': 'chosen'}) == {
        'x': ['chosen', 'literal $stream'],
    }
    with pytest.raises(KeyError):
        substitute('$missing', {})


@pytest.mark.anyio
async def test_journal_requires_matching_preview_and_refuses_replay(plan, tmp_path):
    path = tmp_path / 'plan.json'
    journal = tmp_path / 'journal.json'
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='preview'):
        Sandbox(path, journal, 'properties/123')
    plan['dry_run'] = await dry_run(plan)
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='target'):
        Sandbox(path, journal, 'properties/999')
    runner = Sandbox(path, journal, 'properties/123')
    runner.begin('example')
    runner.note('example', observation='persisted')
    reloaded = Sandbox(path, journal, 'properties/123')
    assert reloaded.data['cases']['example']['events'][0]['observation'] == 'persisted'
    with pytest.raises(ValueError, match='already attempted'):
        reloaded.begin('example')
    changed = deepcopy(plan)
    changed['calls']['binding_update']['arguments']['body']['roles'] = []
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='fixed operation set'):
        Sandbox(path, journal, 'properties/123')


@pytest.mark.anyio
async def test_runner_never_mutates_preexisting_stream(plan, tmp_path):
    plan['dry_run'] = await dry_run(plan)
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan))
    runner = Sandbox(path, tmp_path / 'journal.json', 'properties/123')
    runner.begin('example')
    with pytest.raises(ValueError, match='created by this case'):
        await runner.execute(
            None,
            'example',
            'delete_stream',
            **{
                '$stream': 'properties/123/dataStreams/999',
                '$version': 'v1beta',
            },
        )
    with pytest.raises(ValueError, match='version'):
        await runner.execute(None, 'example', 'create_stream', **{'$version': 'v9'})


@pytest.mark.parametrize(
    'live,write,expected',
    [
        (False, False, ['offline']),
        (True, False, ['offline', 'read']),
        (True, True, ['offline', 'read', 'write']),
    ],
)
def test_live_write_selection_requires_both_flags(live, write, expected):
    items = [
        SimpleNamespace(
            name=name,
            get_closest_marker=lambda marker, markers=markers: marker in markers,
        )
        for name, markers in (
            ('offline', set()),
            ('read', {'live'}),
            ('write', {'live', 'live_write'}),
        )
    ]
    deselected_items = []
    config = SimpleNamespace(
        getoption=lambda name: {'--live': live, '--live-write': write}[name],
        hook=SimpleNamespace(
            pytest_deselected=lambda items: deselected_items.extend(items)
        ),
    )
    pytest_collection_modifyitems(config, items)
    assert [item.name for item in items] == expected
    assert len(items) + len(deselected_items) == 3


def test_write_flag_alone_is_an_error():
    config = SimpleNamespace(getoption=lambda name: name == '--live-write')
    with pytest.raises(pytest.UsageError, match='also requires --live'):
        pytest_collection_modifyitems(config, [])


@pytest.mark.anyio
async def test_inventory_filters_binding_and_follows_all_pages(monkeypatch):
    from tests.e2e import inventory as module

    async def fake_call(server, tool_name, **arguments):
        assert os.environ['GA_MCP_READ_ONLY'] == '1'
        if tool_name == 'ga_get':
            return {'displayName': 'Synthetic', 'parent': 'accounts/321'}
        resource = arguments['resource']
        page = {'_mcp': {'next_arguments': None}}
        if resource != 'accessBindings':
            return {**page, resource: [{'name': f'properties/123/{resource}/456'}]}
        if not arguments.get('page_token'):
            return {
                'accessBindings': [{'name': 'other', 'user': 'other@example.invalid'}],
                '_mcp': {'next_arguments': {**arguments, 'page_token': 'next'}},
            }
        return {
            **page,
            'accessBindings': [
                {
                    'name': 'properties/123/accessBindings/789',
                    'user': 'TEST@example.invalid',
                    'roles': ['predefinedRoles/admin'],
                }
            ],
        }

    monkeypatch.setattr(module, 'call', fake_call)
    result = await module.inventory('properties/123', 'test@example.invalid')
    assert len(result['property_binding']) == 1
    assert result['resources']['accessBindings']['count'] == 2
    assert 'other@example.invalid' not in json.dumps(result)


@pytest.mark.anyio
async def test_journal_records_uncertain_outcome_without_retry(
    plan, tmp_path, monkeypatch
):
    from tests.e2e import sandbox as module

    plan['dry_run'] = await dry_run(plan)
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan))
    runner = Sandbox(path, tmp_path / 'journal.json', 'properties/123')
    runner.begin('example')
    calls = []

    async def fail(server, tool_name, **arguments):
        calls.append(tool_name)
        assert runner.data['cases']['example']['events'][-1]['phase'] == 'before'
        raise OSError('Synthetic connection lost after sending create')

    monkeypatch.setattr(module, 'call', fail)
    with pytest.raises(OSError, match='connection lost'):
        await runner.execute(None, 'example', 'create_stream', **{'$version': 'v1beta'})
    assert calls == ['ga_create']
    saved = json.loads(runner.path.read_text())
    assert [event['phase'] for event in saved['cases']['example']['events']] == [
        'before',
        'error',
    ]


@pytest.mark.anyio
async def test_advanced_preview_and_resource_session_ownership(tmp_path):
    from tests.e2e.advanced import build_advanced_plan

    plan = build_advanced_plan(
        'properties/123',
        'properties/123/audiences/456',
        'test@example.invalid',
        'properties/123/accessBindings/789',
        ['predefinedRoles/admin'],
        'abc123abc123',
    )
    plan['dry_run'] = await dry_run(plan)
    assert len(plan['dry_run']['requests']) == 8
    path = tmp_path / 'advanced.json'
    path.write_text(json.dumps(plan))
    runner = Sandbox(
        path,
        tmp_path / 'journal.json',
        'properties/123',
        plan_factory=build_advanced_plan,
    )
    runner.begin('example')
    for step, replacements in (
        (
            'delete_annotation',
            {'$annotation': 'properties/123/reportingDataAnnotations/999'},
        ),
        ('delete_metric', {'$metric': 'properties/123/calculatedMetrics/existing'}),
        ('chat_continue', {'$session': 'preexisting-session'}),
    ):
        with pytest.raises(ValueError, match='created by this case'):
            await runner.execute(None, 'example', step, **replacements)


@pytest.mark.anyio
@pytest.mark.parametrize('listed,error', [(False, False), (True, False), (False, True)])
async def test_deleted_annotation_403_needs_successful_complete_absence_check(
    listed, error, monkeypatch
):
    from mcp.server.mcpserver.exceptions import ToolError

    from tests.e2e import test_sandbox as live

    name = 'properties/123/reportingDataAnnotations/456'
    calls = []

    async def fake_call(server, tool_name, **arguments):
        calls.append(tool_name)
        if tool_name == 'ga_get' or error:
            raise ToolError('HTTP 403: permission denied')
        return {
            'reportingDataAnnotations': [{'name': name}] if listed else [],
            '_mcp': {'next_arguments': None},
        }

    monkeypatch.setattr(live, 'call', fake_call)
    if listed or error:
        with pytest.raises(AssertionError if listed else ToolError):
            await live.assert_deleted(None, name, 'v1alpha')
    else:
        await live.assert_deleted(None, name, 'v1alpha')
    assert calls == ['ga_get', 'ga_list']
