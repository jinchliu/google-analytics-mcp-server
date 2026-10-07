"""Bounded observation of Google's documented short-page continuation behavior."""

import pytest
from tests.e2e.helpers import call

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_change_history_short_page_with_continuation(
    server, property_name, record_property
):
    prop = await call(server, 'ga_get', name=property_name)
    queries = [
        {'property': property_name, 'pageSize': 5},
        {'property': property_name, 'pageSize': 5, 'resourceType': ['DATA_STREAM']},
        {'property': property_name, 'pageSize': 5, 'action': ['DELETED']},
    ]
    inspected = 0
    for body in queries:
        arguments = {
            'account': prop['parent'],
            'body': body,
            'response_format': 'json_full',
        }
        seen = set()
        for _ in range(6):
            page = await call(server, 'ga_search_change_history', **arguments)
            inspected += 1
            events = page.get('changeHistoryEvents', [])
            identifiers = {event['id'] for event in events}
            assert not seen & identifiers
            seen.update(identifiers)
            following = page['_mcp']['next_arguments']
            if not following:
                break
            assert {
                key: value
                for key, value in following['body'].items()
                if key != 'pageToken'
            } == body
            if len(events) < body['pageSize']:
                resumed = await call(server, 'ga_search_change_history', **following)
                assert not seen & {
                    event['id'] for event in resumed.get('changeHistoryEvents', [])
                }
                record_property('requested_page_size', body['pageSize'])
                record_property('returned_items', len(events))
                record_property('continuation_present', True)
                record_property(
                    'resumed_items', len(resumed.get('changeHistoryEvents', []))
                )
                record_property('inspected_pages', inspected)
                return
            arguments = following
    pytest.skip(
        f'No short/empty page with a token observed in {inspected} bounded pages; offline edge coverage remains applicable.'
    )
