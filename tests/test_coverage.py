"""Every pinned endpoint must execute through both native and raw adapters."""

from urllib.parse import urlsplit

import pytest

from google_analytics_mcp_server import catalog
from google_analytics_mcp_server.tools.meta import ga_call_api
from tests.helpers import call_native, expected_path, scalar, wire_input

ENDPOINTS = catalog.methods()


@pytest.mark.parametrize('item', ENDPOINTS, ids=lambda item: item.key)
def test_native_versioned_dispatch(item, http, full_profile):
    call_native(item)
    assert len(http.requests) == 1
    request = http.requests[0]
    params, body = wire_input(item)
    assert request.method == item.spec['httpMethod']
    assert urlsplit(request.uri).path == expected_path(item, params)
    assert (
        urlsplit(request.uri).netloc == f'{catalog.SERVICES[item.api]}.googleapis.com'
    )
    assert request.headers['user-agent'].startswith('google-analytics-mcp-server/')
    if body is not None:
        for key, value in body.items():
            if key == 'requests' and item.owner == 'ga_batch_run_reports':
                for expected, actual in zip(value, request.body[key], strict=True):
                    assert all(
                        actual[field] == original
                        for field, original in expected.items()
                    )
            else:
                assert request.body[key] == value
    else:
        assert request.body is None
    for name, value in params.items():
        if name not in item.path_parameters:
            assert request.query[name] == (
                [str(v) for v in value] if isinstance(value, list) else [str(value)]
            )


@pytest.mark.parametrize('item', ENDPOINTS, ids=lambda item: item.key)
def test_raw_versioned_dispatch_and_preservation(item, http, full_profile):
    params, body = wire_input(item)
    params.update(fields='name', prettyPrint=False, quotaUser='synthetic-quota-user')
    ga_call_api(item.api, item.version, item.id, params, body, confirm=True)
    assert len(http.requests) == 1
    request = http.requests[0]
    assert request.method == item.spec['httpMethod']
    assert urlsplit(request.uri).path == expected_path(item, params)
    assert request.body == body
    assert request.query['fields'] == ['name']
    assert request.query['prettyPrint'] == ['false']
    assert request.query['quotaUser'] == ['synthetic-quota-user']
    for name, value in params.items():
        if name in item.path_parameters or name in {'prettyPrint'}:
            continue
        assert request.query[name] == (
            [str(v) for v in value] if isinstance(value, list) else [str(value)]
        )


QUERY_CASES = [
    pytest.param(item, name, spec, id=f'{item.key}:{name}')
    for item in ENDPOINTS
    for name, spec in item.parameters.items()
    if spec['location'] == 'query'
]


@pytest.mark.parametrize('item,name,spec', QUERY_CASES)
def test_every_business_query_parameter_is_preserved(
    item, name, spec, http, full_profile
):
    params, body = wire_input(item)
    value = params.get(name, scalar(spec))
    if spec.get('repeated') and not isinstance(value, list):
        value = [value]
    params[name] = value
    ga_call_api(item.api, item.version, item.id, params, body, confirm=True)

    def wire_string(value):
        return str(value).lower() if isinstance(value, bool) else str(value)

    assert http.requests[-1].query[name] == [
        wire_string(v) for v in (value if isinstance(value, list) else [value])
    ]
