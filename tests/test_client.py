"""Real discovery transport, deterministic retries and actionable failures."""

import pytest
from google.auth.exceptions import RefreshError
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import catalog, client
from tests.helpers import wire_input


@pytest.mark.parametrize(
    'api,identity,status,message,retries',
    [
        ('admin', 'accounts.list', 503, 'backend unavailable', 3),
        ('data', 'properties.runReport', 503, 'backend unavailable', 1),
        ('admin', 'properties.runAccessReport', 500, 'backend unavailable', 1),
        ('admin', 'accounts.list', 429, 'rateLimitExceeded', 5),
        ('admin', 'accounts.list', 403, 'requests per minute', 5),
        ('data', 'properties.runReport', 429, 'concurrent requests exceeded', 3),
        ('data', 'properties.runReport', 429, 'tokens per hour exceeded', 0),
        ('data', 'properties.runReport', 429, 'thresholded requests', 0),
        ('data', 'properties.runReport', 429, 'server error quota', 0),
        ('data', 'properties.runReport', 429, 'unknown quota', 0),
        ('admin', 'properties.runAccessReport', 429, 'tokens per hour exceeded', 0),
        ('admin', 'properties.runAccessReport', 429, 'unknown quota', 0),
        ('admin', 'accounts.list', 400, 'invalid request', 0),
        ('admin', 'accounts.list', 401, 'unauthorized', 0),
        ('admin', 'properties.create', 503, 'backend unavailable', 0),
        ('data', 'properties.reportTasks.create', 503, 'backend unavailable', 0),
        ('data', 'properties.chat', 503, 'backend unavailable', 0),
    ],
)
def test_retry_matrix(
    api, identity, status, message, retries, http, clock, full_profile
):
    item = catalog.method(api, identity)
    params, body = wire_input(item)
    for _ in range(retries + 1):
        http.queue({'error': {'message': message}}, status)
    with pytest.raises(ToolError, match=f'HTTP {status}'):
        client.execute(item, params, body, confirm=True)
    assert len(http.requests) == retries + 1
    assert len(clock.sleeps) == retries
    assert all(
        request.uri == http.requests[0].uri and request.body == body
        for request in http.requests
    )


def test_retry_after_is_honored_without_real_sleep(http, clock):
    http.queue(
        {'error': {'message': 'concurrent requests'}}, 429, **{'retry-after': '4'}
    )
    http.queue({'accounts': []})
    assert client.execute(catalog.method('admin', 'accounts.list')) == {'accounts': []}
    assert clock.sleeps == [4.0]


@pytest.mark.parametrize(
    'status,canonical,message,retries',
    [
        (429, 'RESOURCE_EXHAUSTED', 'Quota exceeded.', 0),
        (429, 'RESOURCE_EXHAUSTED', 'Property tokens per hour exhausted.', 0),
        (429, 'RESOURCE_EXHAUSTED', 'Concurrent request limit exceeded.', 3),
        (429, 'RESOURCE_EXHAUSTED', 'Potentially thresholded requests exhausted.', 0),
        (429, 'RESOURCE_EXHAUSTED', 'Server error quota exhausted.', 0),
        (500, 'INTERNAL', 'Synthetic internal failure.', 1),
        (503, 'UNAVAILABLE', 'Synthetic unavailable backend.', 1),
    ],
)
def test_documented_error_envelope_with_synthetic_quota_messages(
    status, canonical, message, retries, http, clock
):
    # Envelope and categories: developers.google.com/analytics/devguides/reporting/data/v1/errors
    # Messages/values are synthetic, not recordings of exhausted production quotas.
    item = catalog.method('data', 'properties.runReport')
    params, body = wire_input(item)
    for _ in range(retries + 1):
        http.queue(
            {'error': {'code': status, 'status': canonical, 'message': message}}, status
        )
    with pytest.raises(ToolError, match=f'HTTP {status}'):
        client.execute(item, params, body)
    assert len(http.requests) == retries + 1
    assert len(clock.sleeps) == retries


def test_large_retry_after_returns_error_instead_of_blocking(http, clock):
    http.queue({'error': {'message': 'rate limit'}}, 429, **{'retry-after': '90'})
    with pytest.raises(ToolError):
        client.execute(catalog.method('admin', 'accounts.list'))
    assert not clock.sleeps
    assert len(http.requests) == 1


@pytest.mark.parametrize(
    'status,message,hint',
    [
        (403, 'insufficient scope', 'Required scopes'),
        (403, 'SERVICE_DISABLED', 'Enable analyticsadmin'),
        (403, 'permission denied', 'role'),
        (400, 'bad metric', 'ga_get_metadata'),
        (404, 'missing target', 'ga_list'),
        (429, 'unknown quota', 'not necessarily at a whole-hour'),
    ],
)
def test_actionable_errors(status, message, hint, http):
    http.queue({'error': {'message': message}}, status)
    with pytest.raises(ToolError, match=hint):
        client.execute(catalog.method('admin', 'accounts.list'))


@pytest.mark.parametrize(
    'error,hint',
    [
        (OSError('timeout'), 'outcome may be unknown'),
        (RefreshError('expired'), 'credentials could not refresh'),
    ],
)
def test_mutation_transport_errors_do_not_replay(error, hint, http):
    item = catalog.method('admin', 'properties.create')
    http.responses.append(error)
    with pytest.raises(ToolError, match=hint):
        client.execute(item, {}, {'parent': 'accounts/123'})
    assert len(http.requests) == 1


@pytest.mark.parametrize(
    'params',
    [
        {'unknown': 1},
        {'key': 'not-a-real-key'},
        {'access_token': 'not-a-token'},
        {'alt': 'media'},
        {'pageSize': True},
    ],
)
def test_invalid_parameters_block_http(params, http):
    with pytest.raises(ToolError):
        client.execute(catalog.method('admin', 'accounts.list'), params)
    assert not http.requests


def test_required_paths_and_bodyless_methods_are_checked(http):
    with pytest.raises(ToolError, match='requires parameter'):
        client.execute(catalog.method('admin', 'properties.get'))
    with pytest.raises(ToolError, match='does not accept a body'):
        client.execute(catalog.method('admin', 'accounts.list'), body={})
    assert not http.requests


def test_lazy_services_are_cached_per_api_version(monkeypatch):
    from unittest.mock import Mock

    from google.auth.credentials import AnonymousCredentials

    from google_analytics_mcp_server import auth
    from tests.helpers import RecordingHttp

    credentials = Mock(return_value=AnonymousCredentials())
    authorize = Mock(return_value=RecordingHttp())
    monkeypatch.setattr(auth, 'credentials', credentials)
    monkeypatch.setattr(client, 'AuthorizedHttp', authorize)
    for version in ('v1beta', 'v1alpha'):
        item = catalog.method('admin', 'accounts.list', version)
        client.execute(item)
        client.execute(item)
    assert len(client._services) == 2
    assert authorize.call_count == credentials.call_count == 2


def test_transport_lock_is_held_during_http(http, monkeypatch):
    import threading

    original = http.request
    observed = []

    def recording(*args, **kwargs):
        def probe():
            acquired = client._lock.acquire(timeout=0.02)
            observed.append(acquired)
            if acquired:
                client._lock.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(timeout=2)
        return original(*args, **kwargs)

    monkeypatch.setattr(http, 'request', recording)
    client.execute(catalog.method('admin', 'accounts.list'))
    assert observed == [False]


@pytest.mark.parametrize('header,delay', [('invalid', 1.0), ('-2', 0.0), ('2.5', 2.5)])
def test_retry_after_invalid_and_numeric_values(header, delay, http, clock):
    http.queue({'error': {'message': 'rate limit'}}, 429, **{'retry-after': header})
    client.execute(catalog.method('admin', 'accounts.list'))
    assert clock.sleeps == [delay]
