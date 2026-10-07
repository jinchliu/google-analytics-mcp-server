"""Slim fields follow discovery and report summaries preserve important data."""

import pytest

from google_analytics_mcp_server import catalog, projection


@pytest.mark.parametrize(
    'item',
    [m for m in catalog.methods() if m.owner == 'ga_list'],
    ids=lambda item: item.key,
)
def test_slim_fields_exist_in_selected_response_schema(item):
    collection = item.resource.split('.')[-1]
    schema = item.schema(item.response_schema)['properties'][catalog.items_key(item)][
        'items'
    ]
    for path in projection.SLIM_FIELDS.get(collection, ()):
        current = schema
        for part in path.split('.'):
            if '$ref' in current:
                current = item.schema(current['$ref'])
            assert part in current.get('properties', {}), (item.key, path)
            current = current['properties'][part]


def test_account_without_properties_remains_visible():
    assert (
        projection.resources(
            'accountSummaries',
            [
                {
                    'name': 'accountSummaries/1',
                    'account': 'accounts/1',
                    'displayName': 'Empty',
                }
            ],
        )[0]['accountName']
        == 'Empty'
    )


def test_secrets_are_not_in_slim_listings():
    row = {
        'name': 'properties/1/dataStreams/2/measurementProtocolSecrets/3',
        'displayName': 'Example',
        'secretValue': 'synthetic-secret',
    }
    assert projection.resources('measurementProtocolSecrets', [row]) == [
        {'name': row['name'], 'displayName': 'Example'}
    ]


def test_report_precision_headers_and_aggregates():
    response = {
        'dimensionHeaders': [{'name': 'dateRange'}, {'name': 'country'}],
        'metricHeaders': [{'name': 'activeUsers', 'type': 'TYPE_INTEGER'}],
        'rows': [
            {
                'dimensionValues': [{'value': 'range_1'}, {'value': '001'}],
                'metricValues': [{'value': '9007199254740993'}],
            }
        ],
        'rowCount': 1,
        'totals': [{'metricValues': [{'value': '9007199254740993'}]}],
        'metadata': {
            'subjectToThresholding': True,
            'samplingMetadatas': [
                {'samplesReadCount': '100', 'samplingSpaceSize': '1000'}
            ],
        },
    }
    result = projection.report(response)
    assert result['rows'] == [['range_1', '001', '9007199254740993']]
    assert result['columns'] == ['dateRange', 'country', 'activeUsers']
    assert result['totals'] == response['totals']
    assert result['metadata'] == response['metadata']
    assert result['metric_types'] == ['TYPE_INTEGER']


@pytest.mark.parametrize('key', ['audienceExport', 'audienceList'])
def test_audience_row_projection(key):
    result = projection.report(
        {
            key: {'dimensions': [{'dimensionName': 'deviceId'}]},
            'audienceRows': [{'dimensionValues': [{'value': '0001'}]}],
        }
    )
    assert result['columns'] == ['deviceId']
    assert result['rows'] == [['0001']]
