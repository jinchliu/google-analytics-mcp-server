"""Real report errors, access reports and bounded pagination round trips."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from tests.e2e.helpers import call

pytestmark = [pytest.mark.live, pytest.mark.anyio]


@pytest.mark.parametrize('api_version', ['v1beta', 'v1alpha'])
async def test_property_access_report_quota(server, property_name, api_version):
    result = await call(
        server,
        'ga_run_access_report',
        entity=property_name,
        body={
            'dateRanges': [{'startDate': '7daysAgo', 'endDate': 'yesterday'}],
            'dimensions': [{'dimensionName': 'dataApiQuotaCategory'}],
            'metrics': [{'metricName': 'dataApiQuotaPropertyTokensConsumed'}],
        },
        limit=2,
        api_version=api_version,
        response_format='json_full',
    )
    assert (
        result['metricHeaders'][0]['metricName'] == 'dataApiQuotaPropertyTokensConsumed'
    )
    assert len(result.get('rows', [])) <= 2
    assert isinstance(result.get('quota'), dict)
    assert result['_mcp']['quota'] == result['quota']


async def test_account_access_report_scoped_to_property(server, property_name):
    prop = await call(server, 'ga_get', name=property_name)
    result = await call(
        server,
        'ga_run_access_report',
        entity=prop['parent'],
        body={
            'dateRanges': [{'startDate': '7daysAgo', 'endDate': 'yesterday'}],
            'dimensions': [{'dimensionName': 'accessedPropertyId'}],
            'metrics': [{'metricName': 'dataApiQuotaPropertyTokensConsumed'}],
            'dimensionFilter': {
                'accessFilter': {
                    'fieldName': 'accessedPropertyId',
                    'stringFilter': {
                        'matchType': 'EXACT',
                        'value': property_name.split('/')[1],
                    },
                }
            },
        },
        limit=2,
        response_format='json_full',
    )
    assert result['_mcp']['quota'] == 'unavailable'
    assert all(
        row['dimensionValues'][0]['value'] == property_name.split('/')[1]
        for row in result.get('rows', [])
    )


async def test_change_history_is_scoped_and_pageable(server, property_name):
    prop = await call(server, 'ga_get', name=property_name)
    first = await call(
        server,
        'ga_search_change_history',
        account=prop['parent'],
        body={'property': property_name, 'pageSize': 1},
        response_format='json_full',
    )
    assert len(first.get('changeHistoryEvents', [])) <= 1
    continuation = first['_mcp']['next_arguments']
    if continuation:
        assert continuation['body']['property'] == property_name
        second = await call(server, 'ga_search_change_history', **continuation)
        first_ids = {event['id'] for event in first.get('changeHistoryEvents', [])}
        assert not first_ids & {
            event['id'] for event in second.get('changeHistoryEvents', [])
        }


@pytest.mark.parametrize('field', ['dimensions', 'metrics'])
async def test_google_invalid_field_error_keeps_actionable_hint(
    server, property_name, field
):
    arguments = {'property': property_name, 'metrics': ['activeUsers'], 'limit': 1}
    arguments[field] = ['__ga_mcp_invalid_field__']
    with pytest.raises(ToolError, match='HTTP 400') as raised:
        await call(server, 'ga_run_report', **arguments)
    assert 'ga_get_metadata' in str(raised.value)
    assert 'ga_check_compatibility' in str(raised.value)


@pytest.mark.parametrize('api_version', ['v1beta', 'v1alpha'])
async def test_standard_report_offset_round_trip(server, property_name, api_version):
    arguments = {
        'property': property_name,
        'body': {
            'dateRanges': [{'startDate': '365daysAgo', 'endDate': 'yesterday'}],
            'dimensions': [{'name': 'date'}, {'name': 'eventName'}],
            'metrics': [{'name': 'eventCount'}],
            'orderBys': [
                {'dimension': {'dimensionName': 'date'}},
                {'dimension': {'dimensionName': 'eventName'}},
            ],
        },
        'api_version': api_version,
        'response_format': 'json_full',
    }
    baseline = await call(server, 'ga_run_report', **arguments, limit=2)
    if len(baseline.get('rows', [])) < 2:
        pytest.skip(
            'The selected property has fewer than two historical rows; live offset paging remains unverified.'
        )
    first = await call(server, 'ga_run_report', **arguments, limit=1)
    offset = first['_mcp']['continuation']['next_offset']
    assert offset == 1
    second = await call(server, 'ga_run_report', **arguments, limit=1, offset=offset)
    assert first['rows'] + second['rows'] == baseline['rows']


async def test_pivot_offset_round_trip(server, property_name):
    body = {
        'dateRanges': [{'startDate': '365daysAgo', 'endDate': 'yesterday'}],
        'dimensions': [{'name': 'eventName'}],
        'metrics': [{'name': 'eventCount'}],
        'pivots': [
            {
                'fieldNames': ['eventName'],
                'limit': '2',
                'orderBys': [{'dimension': {'dimensionName': 'eventName'}}],
            }
        ],
    }
    baseline = await call(
        server,
        'ga_run_pivot_report',
        property=property_name,
        body=body,
        response_format='json_full',
    )
    if len(baseline.get('rows', [])) < 2:
        pytest.skip(
            'The selected property has fewer than two pivot rows; live pivot paging remains unverified.'
        )
    body['pivots'][0]['limit'] = '1'
    first = await call(
        server,
        'ga_run_pivot_report',
        property=property_name,
        body=body,
        response_format='json_full',
    )
    offset = first['_mcp']['continuation']['pivots'][0]['next_offset']
    assert offset == 1
    body['pivots'][0]['offset'] = str(offset)
    second = await call(
        server,
        'ga_run_pivot_report',
        property=property_name,
        body=body,
        response_format='json_full',
    )
    assert first['rows'] + second['rows'] == baseline['rows']


async def test_audience_list_page_token_round_trip(server, property_name):
    arguments = {
        'resource': 'audiences',
        'parent': property_name,
        'response_format': 'json_full',
        'api_version': 'v1alpha',
    }
    baseline = await call(server, 'ga_list', **arguments, limit=2)
    if len(baseline.get('audiences', [])) < 2:
        pytest.skip(
            'At least two existing audiences are needed to verify live page-token continuation.'
        )
    first = await call(server, 'ga_list', **arguments, limit=1)
    continuation = first['_mcp']['next_arguments']
    assert continuation is not None
    second = await call(server, 'ga_list', **continuation)
    assert first['audiences'] + second['audiences'] == baseline['audiences']
