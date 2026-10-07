"""Verify actual scope denial with an isolated, verified read-only access token."""

import pytest
import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from mcp.server.mcpserver.exceptions import ToolError
from tests.e2e.helpers import call

from google_analytics_mcp_server import auth, client

pytestmark = [pytest.mark.live, pytest.mark.anyio]
READ_SCOPE = 'https://www.googleapis.com/auth/analytics.readonly'


async def test_change_history_rejects_verified_readonly_token(
    server, property_name, monkeypatch
):
    original = auth.credentials()
    if not isinstance(original, Credentials):
        pytest.skip('This isolated-scope check currently requires user ADC.')
    prop = await call(server, 'ga_get', name=property_name)
    narrow = Credentials(
        token=None,
        refresh_token=original.refresh_token,
        token_uri=original.token_uri,
        client_id=original.client_id,
        client_secret=original.client_secret,
        quota_project_id=original.quota_project_id,
        scopes=[READ_SCOPE],
    )
    try:
        narrow.refresh(Request())
        response = requests.get(
            'https://oauth2.googleapis.com/tokeninfo',
            params={'access_token': narrow.token},
            timeout=30,
        )
    except Exception:
        pytest.fail(
            'Isolated token refresh/inspection failed; no credentials were written.',
            pytrace=False,
        )
    if response.status_code != 200:
        pytest.fail('Google could not verify the isolated token scopes.', pytrace=False)
    granted = set(response.json().get('scope', '').split())
    if granted != {READ_SCOPE}:
        pytest.skip(
            'Google did not issue an exclusively read-only token; a separate grant is required.'
        )
    monkeypatch.setattr(auth, '_credentials', narrow)
    monkeypatch.setattr(client, '_services', {})
    report = await call(
        server,
        'ga_run_report',
        property=property_name,
        metrics=['activeUsers'],
        dimensions=['country'],
        limit=1,
        response_format='json_full',
    )
    assert report['metricHeaders'][0]['name'] == 'activeUsers'
    with pytest.raises(ToolError, match='HTTP 403') as raised:
        await call(
            server,
            'ga_search_change_history',
            account=prop['parent'],
            body={'property': property_name, 'pageSize': 1},
        )
    assert 'scope' in str(raised.value).lower()
    assert 'analytics.edit' in str(raised.value)
