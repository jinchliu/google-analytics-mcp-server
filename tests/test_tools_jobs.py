"""Create once, retain real job names and poll without holding the HTTP lock."""

import threading

import pytest

from google_analytics_mcp_server import client
from google_analytics_mcp_server.tools import jobs


@pytest.mark.parametrize(
    'kind,collection,state_path',
    [
        ('report_task', 'reportTasks', 'reportMetadata'),
        ('audience_export', 'audienceExports', None),
        ('audience_list', 'audienceLists', None),
    ],
)
def test_job_create_poll_active(kind, collection, state_path, http, clock):
    name = f'properties/123/{collection}/456'
    http.queue({'name': 'operations/opaque', 'metadata': {'name': name}})
    http.queue(
        {
            'name': name,
            **(
                {state_path: {'state': 'CREATING'}}
                if state_path
                else {'state': 'CREATING'}
            ),
        }
    )
    http.queue(
        {
            'name': name,
            **(
                {state_path: {'state': 'ACTIVE'}} if state_path else {'state': 'ACTIVE'}
            ),
        }
    )
    result = jobs.ga_start_async_job(kind, '123', {}, wait_seconds=10)
    assert result['name'] == name
    assert result['status'] == 'ACTIVE'
    assert [r.method for r in http.requests] == ['POST', 'GET', 'GET']
    assert clock.sleeps == [1.0]


def test_poll_timeout_retains_name_and_never_recreates(http, clock):
    name = 'properties/123/reportTasks/456'
    http.queue({'response': {'name': name}})
    result = jobs.ga_start_async_job('report_task', '123', {}, wait_seconds=2)
    assert result['wait_expired'] is True
    assert result['name'] == name
    assert [r.method for r in http.requests] == ['POST', 'GET', 'GET']


@pytest.mark.parametrize(
    'payload,expected',
    [
        ({'name': 'operations/opaque'}, None),
        ({'metadata': {'name': 'properties/999/reportTasks/456'}}, None),
        (
            {'metadata': [{'name': 'properties/123/reportTasks/456'}]},
            'properties/123/reportTasks/456',
        ),
    ],
)
def test_operation_id_is_never_invented_as_job_name(payload, expected):
    assert jobs.resource_name(payload, 'properties/123', 'reportTasks') == expected


def test_operation_failure_preserves_original_error(http):
    http.queue(
        {'name': 'operations/opaque', 'error': {'code': 9, 'message': 'ineligible'}}
    )
    result = jobs.ga_start_async_job('report_task', '123', {}, wait_seconds=10)
    assert result['status'] == 'FAILED'
    assert result['operation']['error']['code'] == 9
    assert len(http.requests) == 1


def test_poll_error_retains_completed_creation(http, clock):
    http.queue({'metadata': {'name': 'properties/123/reportTasks/456'}})
    http.queue({'error': {'message': 'permission denied'}}, 403)
    result = jobs.ga_start_async_job('report_task', '123', {}, wait_seconds=10)
    assert 'HTTP 403' in result['poll_error']
    assert result['name'] == 'properties/123/reportTasks/456'
    assert len(http.requests) == 2


def test_poll_sleep_releases_transport_lock(http, clock, monkeypatch):
    http.queue({'metadata': {'name': 'properties/123/reportTasks/456'}})
    acquired = []
    original_sleep = clock.sleep

    def sleep(seconds):
        def take_lock():
            success = client._lock.acquire(timeout=1)
            acquired.append(success)
            if success:
                client._lock.release()

        thread = threading.Thread(target=take_lock)
        thread.start()
        thread.join(timeout=2)
        original_sleep(seconds)

    monkeypatch.setattr(jobs.time, 'sleep', sleep)
    jobs.ga_start_async_job('report_task', '123', {}, wait_seconds=1)
    assert acquired == [True]
