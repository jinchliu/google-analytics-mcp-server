"""Public navigation tools retain complete pages and exact continuation inputs."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server.tools import admin, generic, reports


def test_short_and_empty_pages_preserve_tokens_and_query(http):
    http.queue({'properties': [{'name': 'properties/1'}], 'nextPageToken': 'token-1'})
    http.queue({'properties': [], 'nextPageToken': 'token-2'})
    http.queue({'properties': [{'name': 'properties/2'}]})
    first = generic.ga_list(
        'properties',
        'accounts/123',
        limit=20,
        show_deleted=True,
        response_format='json_full',
        api_version='v1alpha',
    ).structured_content
    assert first['_mcp']['count'] == 1
    second = generic.ga_list(**first['_mcp']['next_arguments']).structured_content
    assert second['_mcp']['count'] == 0
    assert second['_mcp']['has_more'] is True
    third = generic.ga_list(**second['_mcp']['next_arguments']).structured_content
    assert third['_mcp']['has_more'] is False
    assert first['properties'] + third['properties'] == [
        {'name': 'properties/1'},
        {'name': 'properties/2'},
    ]
    for request in http.requests:
        assert request.query['filter'] == ['parent:accounts/123']
        assert request.query['pageSize'] == ['20']
        assert request.query['showDeleted'] == ['true']
        assert '/v1alpha/' in request.uri
    assert http.requests[1].query['pageToken'] == ['token-1']
    assert http.requests[2].query['pageToken'] == ['token-2']


def test_explicit_property_filter_takes_precedence(http):
    generic.ga_list('properties', 'accounts/123', filter='ancestor:accounts/456')
    assert http.requests[-1].query['filter'] == ['ancestor:accounts/456']


def test_collection_specific_page_caps(http):
    generic.ga_list(
        'measurementProtocolSecrets', 'properties/123/dataStreams/456', limit=100
    )
    assert http.requests[-1].query['pageSize'] == ['10']
    generic.ga_list('accessBindings', 'properties/123', limit=1000)
    assert http.requests[-1].query['pageSize'] == ['500']


def test_collection_without_page_control_is_not_sliced(http):
    http.queue({'accessBindings': [{'name': 'one'}, {'name': 'two'}]})
    with pytest.raises(ToolError, match='no page-size control'):
        generic.ga_list(
            'accessBindings',
            'properties/123',
            names=['properties/123/accessBindings/1'],
            limit=1,
        )
    assert len(http.requests) == 1


def test_batch_get_validates_all_parents_and_no_page_token(http):
    with pytest.raises(ToolError, match='belong'):
        generic.ga_list(
            'accessBindings', 'properties/123', names=['accounts/123/accessBindings/1']
        )
    with pytest.raises(ToolError, match='page_token'):
        generic.ga_list(
            'accessBindings',
            'properties/123',
            names=['properties/123/accessBindings/1'],
            page_token='token',
        )
    assert not http.requests


def test_create_property_parent_is_preserved_and_conflict_rejected(http):
    generic.ga_create('properties', '123', {'displayName': 'Example'})
    assert http.requests[-1].body == {
        'parent': 'accounts/123',
        'displayName': 'Example',
    }
    with pytest.raises(ToolError, match='differs'):
        generic.ga_create('properties', '123', {'parent': 'accounts/999'})
    assert len(http.requests) == 1


@pytest.mark.parametrize(
    'name,operation,verb',
    [
        ('properties/123', 'delete', 'DELETE'),
        ('properties/123/audiences/456', 'archive', 'POST'),
    ],
)
def test_delete_versus_archive(name, operation, verb, http):
    result = generic.ga_delete(name, confirm=True)
    assert result['success'] is True
    assert result['operation'] == operation
    assert http.requests[-1].method == verb


def test_change_history_empty_page_continues_with_exact_body(http):
    http.queue({'nextPageToken': 'history-token'})
    result = admin.ga_search_change_history(
        '123', {'pageSize': 10, 'property': 'properties/123'}, response_format='json'
    ).structured_content
    args = result['_mcp']['next_arguments']
    assert args['body'] == {
        'pageSize': 10,
        'property': 'properties/123',
        'pageToken': 'history-token',
    }
    assert args['api_version'] == 'v1alpha'


def test_metadata_filtering_and_full_response(http):
    response = {
        'name': 'properties/0/metadata',
        'dimensions': [
            {'apiName': 'country', 'customDefinition': False},
            {'apiName': 'customEvent:campaign', 'customDefinition': True},
        ],
        'metrics': [{'apiName': 'activeUsers'}],
    }
    http.queue(response)
    result = reports.ga_get_metadata(
        '0',
        search='CAMPAIGN',
        custom_only=True,
        kind='dimensions',
        response_format='json',
    ).structured_content
    assert result['dimensions'] == response['dimensions'][1:]
    assert 'metrics' not in result
    http.queue(response)
    assert (
        reports.ga_get_metadata(
            '0', search='absent', response_format='json_full'
        ).structured_content
        == response
    )


def test_account_ticket_retains_follow_up_url(http):
    http.queue({'accountTicketId': 'synthetic/ticket'})
    result = admin.ga_provision_account_ticket({})
    assert result['terms_of_service_url'].endswith('/synthetic%2Fticket')
    assert result['accountTicketId'] == 'synthetic/ticket'
