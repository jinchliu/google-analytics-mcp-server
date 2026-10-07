"""Additional opt-in alpha lifecycles with persistent evidence and cleanup."""

from pathlib import Path

import pytest
from tests.e2e.advanced import build_advanced_plan
from tests.e2e.helpers import call
from tests.e2e.sandbox import Sandbox
from tests.e2e.test_sandbox import assert_deleted

from google_analytics_mcp_server import auth, client
from google_analytics_mcp_server.server import create_server

pytestmark = [pytest.mark.live, pytest.mark.live_write, pytest.mark.anyio]


@pytest.fixture
def advanced(pytestconfig, property_name):
    plan = pytestconfig.getoption('--sandbox-plan')
    journal = pytestconfig.getoption('--sandbox-journal')
    if not plan or not journal:
        pytest.fail(
            'Advanced live writes require --sandbox-plan and --sandbox-journal.'
        )
    return Sandbox(
        Path(plan), Path(journal), property_name, plan_factory=build_advanced_plan
    )


@pytest.mark.parametrize(
    'label,collection',
    [
        ('annotation', 'reportingDataAnnotations'),
        ('metric', 'calculatedMetrics'),
    ],
)
async def test_additional_alpha_resource_lifecycle(
    server, advanced, property_name, label, collection
):
    case = f'alpha_{label}'
    advanced.begin(case)
    created = await advanced.execute(server, case, f'create_{label}')
    name = created['name']
    assert name.startswith(f'{property_name}/{collection}/')
    replacements = {f'${label}': name}
    try:
        before = await call(server, 'ga_get', name=name, api_version='v1alpha')
        await advanced.execute(server, case, f'patch_{label}', **replacements)
        after = await call(server, 'ga_get', name=name, api_version='v1alpha')
        if label == 'annotation':
            assert after['description'] == 'Updated temporary API verification.'
            assert after['annotationDate'] == before['annotationDate']
            assert after['title'] == before['title']
        else:
            assert after['formula'] == 'eventCount / 3'
            assert after['displayName'] == before['displayName']
            assert after['metricUnit'] == before['metricUnit']
        advanced.note(case, readback=True, siblings_preserved=True)
    finally:
        await advanced.execute(server, case, f'delete_{label}', **replacements)
        await assert_deleted(server, name, 'v1alpha')
        advanced.note(case, cleanup=name, verified_absent=True)


async def test_chat_starts_and_continues_session(advanced, monkeypatch):
    monkeypatch.setenv('GA_MCP_ENABLE_CHAT', '1')
    monkeypatch.setattr(auth, '_credentials', None)
    monkeypatch.setattr(client, '_services', {})
    server = create_server()
    case = 'chat_session'
    advanced.begin(case)
    started = await advanced.execute(server, case, 'chat_start')
    session_id = started['sessionId']
    assert session_id and started['blocks']
    continued = await advanced.execute(
        server, case, 'chat_continue', **{'$session': session_id}
    )
    assert continued['sessionId'] == session_id
    assert continued['blocks']
    advanced.note(case, sessionId=session_id, continued=True)
