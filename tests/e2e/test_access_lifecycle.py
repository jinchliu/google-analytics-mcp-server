"""Property-only role lifecycle with independent account admin and restoration."""

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from tests.e2e.access import build_access_plan
from tests.e2e.helpers import call
from tests.e2e.sandbox import Sandbox

pytestmark = [pytest.mark.live, pytest.mark.live_write, pytest.mark.anyio]


async def find_binding(server, parent, principal):
    arguments = {
        'resource': 'accessBindings',
        'parent': parent,
        'limit': 100,
        'response_format': 'json_full',
        'api_version': 'v1alpha',
    }
    matched = []
    for _ in range(20):
        for attempt in range(3):
            try:
                page = await call(server, 'ga_list', **arguments)
                break
            except ToolError as error:
                if 'Network request to' not in str(error) or attempt == 2:
                    raise
                await asyncio.sleep(1)
        matched.extend(
            item
            for item in page.get('accessBindings', [])
            if item.get('user', '').casefold() == principal.casefold()
        )
        arguments = page['_mcp']['next_arguments']
        if not arguments:
            assert len(matched) <= 1
            return matched[0] if matched else None
    pytest.fail('Access inventory is incomplete; no mutation is permitted.')


async def test_property_access_lifecycle_and_restore(
    server, property_name, pytestconfig
):
    plan_path = pytestconfig.getoption('--sandbox-plan')
    journal_path = pytestconfig.getoption('--sandbox-journal')
    if not plan_path or not journal_path:
        pytest.fail(
            'The approved access lifecycle requires its preview plan and journal.'
        )
    runner = Sandbox(
        Path(plan_path),
        Path(journal_path),
        property_name,
        plan_factory=build_access_plan,
    )
    plan = runner.plan
    prop = await call(server, 'ga_get', name=property_name)
    account = prop['parent']
    inherited = await find_binding(server, account, plan['principal'])
    assert inherited and 'predefinedRoles/admin' in inherited['roles']
    original = await find_binding(server, property_name, plan['principal'])
    assert original and original['name'] == plan['binding']
    assert set(original['roles']) == set(plan['roles'])
    case = 'property_access_lifecycle'
    runner.begin(case)
    runner.note(
        case, original=original, independent_account_administrator=inherited['name']
    )

    async def require_roles(expected):
        current = await find_binding(server, property_name, plan['principal'])
        if expected is None:
            assert current is None
        else:
            assert current and set(current['roles']) == set(expected)
        runner.note(case, observed_roles=expected)
        return current

    try:
        await runner.execute(server, case, 'delete_original')
        await require_roles(None)
        created = await runner.execute(server, case, 'create_viewer')
        current = await require_roles(['predefinedRoles/viewer'])
        assert current['name'] == created['name']
        replacements = {'$access': current['name']}
        await runner.execute(server, case, 'replace_roles', **replacements)
        await require_roles(['predefinedRoles/analyst'])
        await runner.execute(server, case, 'clear_roles', **replacements)
        await require_roles(None)
        created = await runner.execute(server, case, 'batch_create')
        current = await require_roles(['predefinedRoles/viewer'])
        assert current['name'] == created['accessBindings'][0]['name']
        replacements = {'$access': current['name']}
        await runner.execute(server, case, 'batch_update', **replacements)
        await require_roles(['predefinedRoles/analyst'])
        await runner.execute(server, case, 'batch_delete', **replacements)
        await require_roles(None)
    finally:
        current = await find_binding(server, property_name, plan['principal'])
        if current is None:
            await runner.execute(server, case, 'restore_create')
        elif set(current['roles']) != set(original['roles']):
            await runner.execute(
                server, case, 'restore_update', **{'$access': current['name']}
            )
        restored = await require_roles(original['roles'])
        account_after = await find_binding(server, account, plan['principal'])
        assert account_after == inherited
        runner.note(
            case,
            restored=True,
            binding=restored['name'],
            account_binding_unchanged=True,
        )
