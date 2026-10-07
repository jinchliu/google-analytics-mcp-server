"""Opt-in account access checks preserving all existing administrator roles."""

import asyncio
from pathlib import Path

import pytest
from tests.e2e.account_access import AccessSandbox, check_originals, list_bindings

pytestmark = [pytest.mark.live, pytest.mark.live_write, pytest.mark.anyio]


def runner_for(pytestconfig, property_name: str) -> AccessSandbox:
    """Require an explicit account, property, offline preview and durable journal."""
    account = pytestconfig.getoption('--account')
    plan = pytestconfig.getoption('--sandbox-plan')
    journal = pytestconfig.getoption('--sandbox-journal')
    if not account or not plan or not journal:
        pytest.fail(
            'Access tests require --account, --sandbox-plan and --sandbox-journal.'
        )
    account = account if account.startswith('accounts/') else f'accounts/{account}'
    return AccessSandbox(Path(plan), Path(journal), account, property_name)


async def test_account_updates_preserve_original_roles(
    server, property_name, pytestconfig
):
    runner = runner_for(pytestconfig, property_name)
    assert runner.plan['mode'] == 'unchanged'
    await check_originals(server, runner.plan, recipient_absent=True)
    case = 'account_unchanged_roles'
    runner.begin(case)
    runner.note(case, original_bindings=runner.plan['bindings'])
    try:
        for step in ('account_update_original', 'account_batch_update_original'):
            await runner.execute(server, case, step)
            await check_originals(server, runner.plan, recipient_absent=True)
            runner.note(case, verified_step=step, original_bindings_unchanged=True)
    finally:
        await check_originals(server, runner.plan, recipient_absent=True)
        runner.note(case, original_bindings_unchanged=True)


@pytest.mark.parametrize('level', ['account', 'property'])
async def test_temporary_access_lifecycle(server, property_name, pytestconfig, level):
    runner = runner_for(pytestconfig, property_name)
    plan = runner.plan
    assert plan['mode'] == 'temporary'
    await check_originals(server, plan, recipient_absent=True)
    case = f'{level}_access_lifecycle'
    runner.begin(case)
    runner.note(
        case, original_bindings=plan['bindings'], recipient_initially_absent=True
    )
    parent = plan[level]
    placeholder = f'${level}_access'

    async def observe():
        entries = await list_bindings(server, parent)
        matched = [
            entry
            for entry in entries
            if entry['user'].casefold() == plan['test_user'].casefold()
        ]
        assert len(matched) <= 1
        current = matched[0] if matched else None
        runner.note(case, observed_binding=current)
        return current

    async def require_roles(expected):
        for attempt in range(4):
            current = await observe()
            matches = (
                current is None
                if expected is None
                else current is not None and set(current['roles']) == set(expected)
            )
            if matches:
                await check_originals(server, plan, recipient_absent=False)
                return current
            if attempt < 3:
                await asyncio.sleep(1)
        pytest.fail(
            f'Temporary {level} binding did not reach expected roles: {expected}.'
        )

    async def execute(step, current=None):
        replacements = {placeholder: current['name']} if current else {}
        return await runner.execute(server, case, f'{level}_{step}', **replacements)

    try:
        created = await execute('create_single')
        current = await require_roles(['predefinedRoles/viewer'])
        assert created['name'] == current['name']
        await execute('update_single', current)
        current = await require_roles(['predefinedRoles/analyst'])
        await execute('delete_single', current)
        await require_roles(None)

        await execute('create_for_clear')
        current = await require_roles(['predefinedRoles/viewer'])
        await execute('clear_single', current)
        await require_roles(None)

        created = await execute('create_batch')
        current = await require_roles(['predefinedRoles/viewer'])
        assert created['accessBindings'][0]['name'] == current['name']
        await execute('update_batch', current)
        current = await require_roles(['predefinedRoles/analyst'])
        await execute('delete_batch', current)
        await require_roles(None)

        await execute('create_for_batch_clear')
        current = await require_roles(['predefinedRoles/viewer'])
        await execute('clear_batch', current)
        await require_roles(None)
    finally:
        current = await observe()
        if current:
            events = runner.data['cases'][case]['events']
            last_attempt = next(
                event for event in reversed(events) if event.get('phase') == 'before'
            )
            step = last_attempt['step']
            if any(token in step for token in ('delete_', 'clear_', 'cleanup')):
                runner.note(
                    case,
                    cleanup_blocked=True,
                    reason='Do not replay a removal whose outcome is unresolved.',
                )
                pytest.fail(
                    'Removal outcome is unresolved; inspect the journal before further writes.'
                )
            await execute('cleanup', current)
            await require_roles(None)
        await check_originals(server, plan, recipient_absent=True)
        runner.note(case, cleanup_verified=True, original_bindings_unchanged=True)


async def test_retain_viewer_for_user_review(server, property_name, pytestconfig):
    """Leave the expressly requested review grants after both lifecycles pass."""
    runner = runner_for(pytestconfig, property_name)
    plan = runner.plan
    if not plan.get('retain_for_review'):
        pytest.skip('This preview does not request retained review access.')
    runner.require_completed_lifecycles()
    await check_originals(server, plan, recipient_absent=True)
    case = 'access_review'
    runner.begin(case)
    completed = False
    try:
        for level in ('account', 'property'):
            created = await runner.execute(server, case, f'{level}_retain_viewer')
            for attempt in range(4):
                snapshots = await check_originals(server, plan, recipient_absent=False)
                matched = [
                    entry
                    for entry in snapshots[plan[level]]
                    if entry['user'].casefold() == plan['test_user'].casefold()
                ]
                if len(matched) == 1 and matched[0]['roles'] == [
                    'predefinedRoles/viewer'
                ]:
                    break
                if attempt < 3:
                    await asyncio.sleep(1)
            else:
                pytest.fail(f'{level} Viewer grant was not confirmed by readback.')
            assert matched[0]['name'] == created['name']
            runner.note(case, level=level, retained_binding=matched[0])
        completed = True
    finally:
        snapshots = await check_originals(server, plan, recipient_absent=False)
        retained = {
            parent: [
                entry
                for entry in entries
                if entry['user'].casefold() == plan['test_user'].casefold()
            ]
            for parent, entries in snapshots.items()
        }
        verified = all(
            len(entries) == 1 and entries[0]['roles'] == ['predefinedRoles/viewer']
            for entries in retained.values()
        )
        runner.note(
            case,
            review_complete=completed and verified,
            retained_bindings=retained,
            original_bindings_unchanged=True,
        )
        if completed:
            assert verified
