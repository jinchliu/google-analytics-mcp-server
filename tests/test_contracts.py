"""Versioned validation, leaf masks, report defaults and row limits."""

import copy

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import catalog, contracts
from google_analytics_mcp_server.tools import admin, generic, reports


@pytest.mark.parametrize('value', [-(2**63), 2**63 - 1, '9007199254740993', 0])
def test_int64_wire_values_remain_exact_strings(value):
    item = catalog.method('data', 'properties.runReport')
    assert contracts.validate(
        value, {'type': 'string', 'format': 'int64'}, item
    ) == str(int(value))


@pytest.mark.parametrize('value', [True, 1.1, '1.1', 2**63, -(2**63) - 1, None])
def test_invalid_int64_is_rejected(value):
    with pytest.raises(ToolError):
        contracts.validate(
            value,
            {'type': 'string', 'format': 'int64'},
            catalog.method('data', 'properties.runReport'),
        )


@pytest.mark.parametrize(
    'spec,value',
    [
        ({'type': 'boolean'}, 1),
        ({'type': 'integer'}, True),
        ({'type': 'number'}, float('nan')),
        ({'type': 'number'}, float('inf')),
        ({'type': 'array'}, {}),
        ({'type': 'object'}, []),
        ({'type': 'string'}, 12),
        ({'enum': ['A'], 'type': 'string'}, 'B'),
        ({'type': 'integer', 'minimum': '1'}, 0),
        ({'type': 'integer', 'maximum': '5'}, 6),
    ],
)
def test_invalid_schema_values_fail(spec, value):
    with pytest.raises(ToolError):
        contracts.validate(value, spec, catalog.method('data', 'properties.runReport'))


def test_nested_mask_and_documented_uri_clear_preserve_siblings(http):
    patch = {'webStreamData': {'defaultUri': None}, 'displayName': 'Example'}
    original = copy.deepcopy(patch)
    generic.ga_update('properties/123/dataStreams/456', patch)
    request = http.requests[0]
    assert request.body == {
        'webStreamData': {'defaultUri': ''},
        'displayName': 'Example',
    }
    assert request.query['updateMask'] == ['webStreamData.defaultUri,displayName']
    assert 'measurementId' not in request.body['webStreamData']
    assert patch == original


@pytest.mark.parametrize(
    'patch,mask',
    [
        ({}, None),
        ({'webStreamData': {}}, None),
        ({'displayName': None}, None),
        ({'type': 'WEB_DATA_STREAM'}, None),
        ({'createTime': 'now'}, None),
        ({'unknown': 'x'}, None),
        ({'webStreamData': {'defaultUri': 'https://example.com'}}, 'webStreamData'),
        ({'displayName': 'x'}, '*'),
        ({'displayName': 'x'}, 'displayName,timeZone'),
    ],
)
def test_unsafe_native_patches_fail_before_http(patch, mask, http):
    with pytest.raises(ToolError):
        generic.ga_update('properties/123/dataStreams/456', patch, mask)
    assert not http.requests


def test_explicit_snake_mask_is_preserved(http):
    generic.ga_update(
        'properties/123/dataStreams/456',
        {'webStreamData': {'defaultUri': ''}},
        'web_stream_data.default_uri',
    )
    assert http.requests[0].query['updateMask'] == ['web_stream_data.default_uri']


def test_false_zero_empty_and_repeated_values_are_not_dropped(http):
    generic.ga_update(
        'properties/123/googleAdsLinks/456', {'adsPersonalizationEnabled': False}
    )
    assert http.requests[-1].body == {'adsPersonalizationEnabled': False}
    generic.ga_update(
        'properties/123/bigQueryLinks/456', {'exportStreams': []}, api_version='v1alpha'
    )
    assert http.requests[-1].body == {'exportStreams': []}


def test_body_shortcut_conflicts_are_errors_and_inputs_are_unchanged(http):
    body = {'metrics': [{'name': 'activeUsers'}], 'limit': '7'}
    original = copy.deepcopy(body)
    with pytest.raises(ToolError, match='both in body'):
        reports.ga_run_report('123', body=body, limit=7)
    assert body == original
    assert not http.requests


def test_default_report_fields_and_metric_objects(http):
    reports.ga_run_report(
        '123', metrics=['activeUsers'], dimensions=[{'name': 'country'}]
    )
    assert http.requests[-1].body == {
        'metrics': [{'name': 'activeUsers'}],
        'dimensions': [{'name': 'country'}],
        'dateRanges': [{'startDate': '28daysAgo', 'endDate': 'yesterday'}],
        'limit': '100',
        'offset': '0',
        'returnPropertyQuota': True,
    }


def test_cohort_omits_dates_and_rejects_explicit_dates(http):
    body = {'metrics': [{'name': 'cohortActiveUsers'}], 'cohortSpec': {'cohorts': []}}
    reports.ga_run_report('123', body=body)
    assert 'dateRanges' not in http.requests[-1].body
    with pytest.raises(ToolError, match='omit dateRanges'):
        reports.ga_run_report('123', body=body, date_ranges=[])
    assert len(http.requests) == 1


def test_alpha_fields_require_exact_version(http):
    body = {
        'metrics': [{'name': 'activeUsers'}],
        'conversionSpec': {
            'conversionActions': ['conversionActions/1234'],
            'attributionModel': 'DATA_DRIVEN',
        },
    }
    with pytest.raises(ToolError, match='v1beta'):
        reports.ga_run_report('123', body=body)
    assert not http.requests
    reports.ga_run_report('123', body=body, api_version='v1alpha')
    assert http.requests[-1].body['conversionSpec'] == body['conversionSpec']


@pytest.mark.parametrize(
    'entity,quota', [('accounts/123', False), ('properties/123', True)]
)
def test_access_quota_is_entity_specific(entity, quota, http):
    admin.ga_run_access_report(entity, {})
    assert http.requests[-1].body['returnEntityQuota'] is quota
    assert 'returnPropertyQuota' not in http.requests[-1].body


def test_account_quota_true_is_rejected(http):
    with pytest.raises(ToolError, match='returnEntityQuota=false'):
        admin.ga_run_access_report('accounts/123', {'returnEntityQuota': True})
    assert not http.requests


@pytest.mark.parametrize('method', ['runRealtimeReport', 'runFunnelReport'])
def test_reports_without_offset_reject_it(method, http):
    fn = (
        reports.ga_run_realtime_report
        if method == 'runRealtimeReport'
        else reports.ga_run_funnel_report
    )
    with pytest.raises(ToolError, match='offset'):
        fn('123', body={'offset': '1'})
    assert not http.requests


def test_pivot_and_batch_budgets_are_aggregate(http, monkeypatch):
    monkeypatch.setenv('GA_MCP_MAX_ROWS', '100')
    with pytest.raises(ToolError, match='exceed'):
        reports.ga_run_pivot_report(
            '123', {'pivots': [{'limit': '20'}, {'limit': '6'}]}
        )
    with pytest.raises(ToolError, match='exceed'):
        reports.ga_batch_run_reports(
            '123', {'requests': [{'limit': '60'}, {'limit': '60'}]}
        )
    with pytest.raises(ToolError, match='match'):
        reports.ga_batch_run_reports(
            '123', {'requests': [{'property': 'properties/999', 'limit': '1'}]}
        )
    assert not http.requests


@pytest.mark.parametrize('requests', [[], [{}] * 6, [None]])
def test_batch_shape_is_validated(requests, http):
    with pytest.raises(ToolError):
        reports.ga_batch_run_reports('123', {'requests': requests})
    assert not http.requests
