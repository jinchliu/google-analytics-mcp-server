"""Explicitly selected, bounded read-only requests against a user-chosen property.

No create, update, deletion, access change, job or chat operation is exercised.
Responses are asserted in memory; credentials and Analytics results are not saved.
"""

from datetime import UTC, datetime, timedelta

import pytest
from tests.e2e.helpers import call

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_account_navigation(server):
    result = await call(
        server,
        'ga_list',
        resource='accountSummaries',
        limit=1,
        response_format='json_full',
    )
    assert isinstance(result.get('accountSummaries', []), list)
    assert result['_mcp']['count'] <= 1
    continuation = result['_mcp']['next_arguments']
    if continuation:
        next_page = await call(server, 'ga_list', **continuation)
        assert next_page['_mcp']['count'] <= 1
        first_names = {row['name'] for row in result.get('accountSummaries', [])}
        assert not first_names & {
            row['name'] for row in next_page.get('accountSummaries', [])
        }


async def test_property_and_stream_navigation(server, property_name):
    result = await call(server, 'ga_get', name=property_name)
    assert result['name'] == property_name
    streams = await call(
        server,
        'ga_list',
        resource='dataStreams',
        parent=property_name,
        limit=2,
        response_format='json_full',
    )
    assert all(
        row['name'].startswith(property_name + '/dataStreams/')
        for row in streams.get('dataStreams', [])
    )


@pytest.mark.parametrize('universal', [False, True])
async def test_metadata(server, property_name, universal):
    result = await call(
        server,
        'ga_get_metadata',
        property='0' if universal else property_name,
        search='activeUsers',
        kind='metrics',
        response_format='json',
    )
    assert any(metric['apiName'] == 'activeUsers' for metric in result['metrics'])


async def test_compatibility(server, property_name, monkeypatch):
    # This API returns the complete field compatibility catalog, even for a pair.
    monkeypatch.setenv('GA_MCP_MAX_OUTPUT_BYTES', '1048576')
    result = await call(
        server,
        'ga_check_compatibility',
        property=property_name,
        body={
            'dimensions': [{'name': 'country'}],
            'metrics': [{'name': 'activeUsers'}],
        },
    )
    assert result['metricCompatibilities']


@pytest.mark.parametrize('version', ['v1beta', 'v1alpha'])
async def test_standard_report(server, property_name, version):
    result = await call(
        server,
        'ga_run_report',
        property=property_name,
        dimensions=['country'],
        metrics=['activeUsers'],
        date_ranges=[{'startDate': '7daysAgo', 'endDate': 'yesterday'}],
        limit=2,
        response_format='json_full',
        api_version=version,
    )
    assert result['_mcp']['api_version'] == version
    assert len(result.get('rows', [])) <= 2
    assert result['metricHeaders'][0]['name'] == 'activeUsers'


async def test_realtime_report(server, property_name):
    result = await call(
        server,
        'ga_run_realtime_report',
        property=property_name,
        metrics=['activeUsers'],
        limit=2,
        response_format='json_full',
    )
    assert result['_mcp']['continuation']['mode'] == 'none'


async def test_pivot_report(server, property_name):
    result = await call(
        server,
        'ga_run_pivot_report',
        property=property_name,
        body={
            'dateRanges': [{'startDate': '7daysAgo', 'endDate': 'yesterday'}],
            'dimensions': [{'name': 'country'}],
            'metrics': [{'name': 'activeUsers'}],
            'pivots': [{'fieldNames': ['country'], 'limit': '2'}],
        },
        response_format='json_full',
    )
    assert result['_mcp']['continuation']['mode'] == 'per_pivot'


async def test_batch_report(server, property_name):
    result = await call(
        server,
        'ga_batch_run_reports',
        property=property_name,
        body={'requests': [{'metrics': [{'name': 'activeUsers'}], 'limit': '2'}]},
        response_format='json_full',
    )
    assert len(result['reports']) == 1


async def test_cohort_report(server, property_name):
    day = (datetime.now(UTC).date() - timedelta(days=14)).isoformat()
    result = await call(
        server,
        'ga_run_report',
        property=property_name,
        body={
            'dimensions': [{'name': 'cohort'}, {'name': 'cohortNthDay'}],
            'metrics': [{'name': 'cohortActiveUsers'}],
            'cohortSpec': {
                'cohorts': [
                    {
                        'name': 'smoke',
                        'dimension': 'firstSessionDate',
                        'dateRange': {'startDate': day, 'endDate': day},
                    }
                ],
                'cohortsRange': {'granularity': 'DAILY', 'endOffset': 1},
            },
            'limit': '2',
        },
        response_format='json_full',
    )
    assert len(result.get('rows', [])) <= 2


async def test_funnel_report(server, property_name):
    result = await call(
        server,
        'ga_run_funnel_report',
        property=property_name,
        body={
            'dateRanges': [{'startDate': '7daysAgo', 'endDate': 'yesterday'}],
            'funnel': {
                'steps': [
                    {
                        'name': 'Page view',
                        'filterExpression': {
                            'funnelEventFilter': {'eventName': 'page_view'}
                        },
                    }
                ]
            },
            'limit': '2',
        },
        response_format='json_full',
    )
    assert 'funnelTable' in result
    assert result['_mcp']['continuation']['mode'] == 'none'
