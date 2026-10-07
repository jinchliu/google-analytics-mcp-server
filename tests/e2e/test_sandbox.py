"""Explicit sandbox lifecycles; every configuration fixture is cleaned in finally."""

import asyncio
import re
import time

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from tests.e2e.helpers import call

pytestmark = [pytest.mark.live, pytest.mark.live_write, pytest.mark.anyio]


async def assert_deleted(server, name, version):
    try:
        await call(server, 'ga_get', name=name, api_version=version)
    except ToolError as error:
        if 'HTTP 404' in str(error):
            return
        if '/reportingDataAnnotations/' not in name or 'HTTP 403' not in str(error):
            raise
        # This API can hide deleted annotation names behind PERMISSION_DENIED.
        # A successful complete parent listing must independently prove absence.
        arguments = {
            'resource': 'reportingDataAnnotations',
            'parent': name.split('/reportingDataAnnotations/')[0],
            'limit': 100,
            'response_format': 'json_full',
            'api_version': version,
        }
        for _ in range(20):
            page = await call(server, 'ga_list', **arguments)
            assert name not in {
                item['name'] for item in page.get('reportingDataAnnotations', [])
            }
            arguments = page['_mcp']['next_arguments']
            if not arguments:
                return
        pytest.fail('Annotation absence could not be verified within the page budget.')
    else:
        pytest.fail(f'Deleted resource is still readable: {name}')


@pytest.mark.parametrize('api_version', ['v1beta', 'v1alpha'])
async def test_temporary_stream_patch_clear_and_repeated_fields(
    server, sandbox, property_name, api_version
):
    case = f'configuration_{api_version}'
    sandbox.begin(case)
    stream = rule = None
    replacements = {'$version': api_version}
    try:
        created = await sandbox.execute(server, case, 'create_stream', **replacements)
        stream = created['name']
        assert re.fullmatch(re.escape(property_name) + r'/dataStreams/[0-9]+', stream)
        replacements['$stream'] = stream
        original = await call(server, 'ga_get', name=stream, api_version=api_version)
        measurement_id = original['webStreamData']['measurementId']
        for step in ('patch_uri', 'clear_uri'):
            await sandbox.execute(server, case, step, **replacements)
            current = await call(server, 'ga_get', name=stream, api_version=api_version)
            expected_uri = 'https://updated.invalid' if step == 'patch_uri' else ''
            assert current['webStreamData'].get('defaultUri', '') == expected_uri
            assert current['webStreamData']['measurementId'] == measurement_id
            assert current['displayName'] == original['displayName']
            sandbox.note(case, observation=step, readback=True, siblings_preserved=True)

        created_rule = await sandbox.execute(
            server, case, 'create_rule', **replacements
        )
        rule = created_rule['name']
        assert re.fullmatch(re.escape(stream) + r'/eventEditRules/[0-9]+', rule)
        replacements['$rule'] = rule
        before = await call(server, 'ga_get', name=rule, api_version='v1alpha')
        assert len(before['parameterMutations']) == 2
        await sandbox.execute(server, case, 'patch_rule', **replacements)
        after = await call(server, 'ga_get', name=rule, api_version='v1alpha')
        assert after['parameterMutations'] == before['parameterMutations'][:1]
        assert after['eventConditions'] == before['eventConditions']
        assert after['displayName'] == before['displayName']
        sandbox.note(case, observation='repeated_field_replaced', readback=True)
    finally:
        errors = []
        for step, name, version in (
            ('delete_rule', rule, 'v1alpha'),
            ('delete_stream', stream, api_version),
        ):
            if name:
                try:
                    await sandbox.execute(server, case, step, **replacements)
                    await assert_deleted(server, name, version)
                    sandbox.note(case, cleanup=name, verified_absent=True)
                except Exception as error:
                    sandbox.note(case, cleanup=name, error=str(error))
                    errors.append(error)
        if errors:
            raise ExceptionGroup(
                'Sandbox cleanup requires attention; inspect journal.', errors
            )


async def test_existing_administrator_single_and_batch_update(server, sandbox):
    case = 'access_unchanged_roles'
    sandbox.begin(case)
    plan = sandbox.plan
    for step in ('binding_update', 'binding_batch_update'):
        before = await call(
            server, 'ga_get', name=plan['binding'], api_version='v1alpha'
        )
        assert before['user'].casefold() == plan['principal'].casefold()
        assert set(before['roles']) == set(plan['roles'])
        assert 'predefinedRoles/admin' in before['roles']
        await sandbox.execute(server, case, step)
        after = await call(
            server, 'ga_get', name=plan['binding'], api_version='v1alpha'
        )
        assert after['user'] == before['user']
        assert set(after['roles']) == set(before['roles'])
        sandbox.note(case, observation=step, readback=True, roles_unchanged=True)
    result = await call(
        server,
        'ga_list',
        resource='accessBindings',
        parent=plan['property'],
        names=[plan['binding']],
        response_format='json_full',
        api_version='v1alpha',
    )
    assert result['accessBindings'][0]['name'] == plan['binding']


@pytest.mark.parametrize('kind', ['report_task', 'audience_export', 'audience_list'])
async def test_async_create_get_query(server, sandbox, property_name, kind):
    case = f'job_{kind}'
    sandbox.begin(case)
    started = await sandbox.execute(server, case, kind)
    collection = {
        'report_task': 'reportTasks',
        'audience_export': 'audienceExports',
        'audience_list': 'audienceLists',
    }[kind]
    name = started['name']
    assert name and name.startswith(f'{property_name}/{collection}/'), started
    version = started['api_version']
    deadline = time.monotonic() + 180
    while True:
        current = await call(server, 'ga_get', name=name, api_version=version)
        state = (
            current.get('reportMetadata', {}).get('state')
            if kind == 'report_task'
            else current.get('state')
        )
        sandbox.note(case, name=name, state=state)
        assert state not in {'FAILED', 'CANCELLED'}, current
        if state == 'ACTIVE':
            break
        if time.monotonic() >= deadline:
            pytest.fail(
                f'Job is still {state}: {name}. Poll this name; do not recreate.'
            )
        await asyncio.sleep(5)
    queried = await call(
        server,
        'ga_query',
        name=name,
        limit=1,
        response_format='json_full',
        api_version=version,
    )
    rows = queried.get('rows', queried.get('audienceRows', []))
    assert len(rows) <= 1
    if kind == 'report_task':
        assert queried['metricHeaders'][0]['name'] == 'activeUsers'
    else:
        assert (
            queried['audienceList' if kind == 'audience_list' else 'audienceExport'][
                'name'
            ]
            == name
        )
    sandbox.note(case, name=name, queried=True, returned_rows=len(rows))
