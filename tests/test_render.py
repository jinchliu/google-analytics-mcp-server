"""Truthful pagination, context budgets and retained mutation outcomes."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import catalog, render


@pytest.mark.parametrize('response_format', ['markdown', 'json', 'json_full'])
def test_empty_metric_only_report_can_omit_headers(response_format):
    # Shape observed in a live empty report; values below are synthetic.
    response = {
        'kind': 'analyticsData#runReport',
        'metadata': {'currencyCode': 'USD', 'timeZone': 'Etc/UTC'},
        'propertyQuota': {'tokensPerHour': {'consumed': 1, 'remaining': 99}},
    }
    result = render.report_result(
        catalog.method('data', 'properties.runReport'),
        {'metrics': [{'name': 'activeUsers'}], 'limit': '1'},
        response,
        response_format,
    )
    assert not result.is_error
    if response_format == 'markdown':
        assert result.structured_content is None
        assert 'tokensPerHour' in result.content[0].text
        return
    payload = result.structured_content
    assert payload['_mcp']['quota'] == response['propertyQuota']
    assert payload['_mcp']['continuation']['returned_rows'] == 0
    assert payload['_mcp']['continuation']['next_offset'] is None


@pytest.mark.parametrize(
    'offset,rows,total,next_offset',
    [(0, 2, 10, 2), (2, 1, 10, 3), (0, 0, 10, None), (2, 2, 4, None)],
)
def test_report_continuation_uses_actual_rows(offset, rows, total, next_offset):
    item = catalog.method('data', 'properties.runReport')
    result = render.continuation(
        item,
        {'offset': str(offset), 'limit': '100'},
        {'rows': [{}] * rows, 'rowCount': total},
    )
    assert result['next_offset'] == next_offset


def test_realtime_and_funnel_never_offer_offset():
    realtime = render.continuation(
        catalog.method('data', 'properties.runRealtimeReport'),
        {'limit': 1},
        {'rows': [{}], 'rowCount': 10},
    )
    assert realtime['mode'] == 'none'
    assert realtime['truncated'] is True
    funnel = render.continuation(
        catalog.method('data', 'properties.runFunnelReport'),
        {},
        {'funnelTable': {'rows': [{}]}},
    )
    assert funnel['mode'] == 'none'
    assert funnel['subreports']['funnelTable']['returned_rows'] == 1


def test_pivot_continuation_is_per_pivot():
    result = render.continuation(
        catalog.method('data', 'properties.runPivotReport'),
        {'pivots': [{'offset': '4'}, {'offset': '0'}]},
        {
            'pivotHeaders': [
                {'rowCount': 10, 'pivotDimensionHeaders': [{}, {}]},
                {'rowCount': 1, 'pivotDimensionHeaders': [{}]},
            ]
        },
    )
    assert result == {
        'mode': 'per_pivot',
        'pivots': [
            {'pivot_index': 0, 'next_offset': 6},
            {'pivot_index': 1, 'next_offset': None},
        ],
    }


@pytest.mark.parametrize('quota', [None, {'tokensPerDay': {'remaining': 12}}])
def test_access_quota_and_unavailable_are_distinct(quota):
    response = {'quota': quota} if quota is not None else {}
    result = render.report_result(
        catalog.method('admin', 'properties.runAccessReport'), {}, response, 'json'
    )
    assert result.structured_content['_mcp']['quota'] == (
        quota if quota is not None else 'unavailable'
    )


def test_markdown_escaping_and_duplicate_columns():
    text = render.grid(['same', 'same'], [['a|b\nc', '001']])
    assert '| same | same |' in text
    assert 'a\\|b c' in text
    assert '001' in text


def test_large_read_fails_without_returning_cursor(monkeypatch):
    monkeypatch.setenv('GA_MCP_MAX_OUTPUT_BYTES', '100')
    item = catalog.method('admin', 'accounts.list')
    with pytest.raises(ToolError, match='No records or cursor'):
        render.list_result(
            item,
            {
                'accounts': [{'name': 'accounts/1', 'displayName': 'x' * 100}],
                'nextPageToken': 'synthetic-token',
            },
            'accounts',
            'json',
            {},
        )


def test_large_mutation_retains_outcome_and_identifiers(monkeypatch):
    monkeypatch.setenv('GA_MCP_MAX_OUTPUT_BYTES', '100')
    response = {
        'name': 'properties/123',
        'data': 'x' * 1000,
        'sessionId': 'synthetic-session',
        'accountTicketId': 'ticket',
        'operation': {
            'name': 'operations/456',
            'error': {'code': 9, 'message': 'failed'},
        },
    }
    output = render.entity(
        response, catalog.method('admin', 'properties.create'), mutating=True
    )
    assert output['request_completed'] is True
    assert output['resource_names'] == ['properties/123', 'operations/456']
    assert output['sessionId'] == 'synthetic-session'
    assert output['accountTicketId'] == 'ticket'
    assert output['error']['code'] == 9
    with pytest.raises(ToolError, match='output bytes'):
        render.entity(response, catalog.method('admin', 'properties.get'))
