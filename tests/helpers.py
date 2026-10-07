"""Synthetic request drivers and a recording transport for real discovery clients.

These inputs verify adapter wiring, not Google's undocumented business rules.
All identifiers and responses in this module are synthetic.
"""

import copy
import json
import re
from collections import deque
from dataclasses import dataclass
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httplib2

import google_analytics_mcp_server.tools  # noqa: F401
from google_analytics_mcp_server import catalog
from google_analytics_mcp_server.catalog import Method
from google_analytics_mcp_server.registry import REGISTRY


@dataclass
class Request:
    """An actual HTTP request produced by google-api-python-client."""

    uri: str
    method: str
    body: Any
    headers: dict[str, str]

    @property
    def query(self) -> dict[str, list[str]]:
        return parse_qs(urlsplit(self.uri).query, keep_blank_values=True)


class RecordingHttp:
    """Record wire requests and return queued, synthetic HTTP responses."""

    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.responses: deque[Any] = deque()

    def queue(self, payload: Any = None, status: int = 200, **headers: str) -> None:
        self.responses.append((status, payload if payload is not None else {}, headers))

    def request(self, uri, method='GET', body=None, headers=None, **kwargs):
        self.requests.append(
            Request(
                uri, method, json.loads(body) if body else None, dict(headers or {})
            )
        )
        response = self.responses.popleft() if self.responses else (200, {}, {})
        if isinstance(response, Exception):
            raise response
        status, payload, extra = response
        return httplib2.Response(
            {'status': str(status), 'content-type': 'application/json', **extra}
        ), json.dumps(payload).encode()


class FakeClock:
    """Advance retry/poll time deterministically without waiting."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def path_params(item: Method) -> dict[str, str]:
    """Produce concrete resource names from pinned path patterns."""
    return {
        key: spec['pattern'].removeprefix('^').removesuffix('$').replace('[^/]+', '123')
        for key, spec in item.path_parameters.items()
    }


def scalar(spec: dict[str, Any]) -> Any:
    """Choose a schema-shaped sentinel without inventing live resource IDs."""
    if 'enum' in spec:
        return spec['enum'][-1]
    if spec.get('format') in {'int64', 'uint64'}:
        return '7'
    return {'string': 'test-value', 'integer': 7, 'number': 1.5, 'boolean': False}.get(
        spec.get('type'), {}
    )


def leaf_patch(
    item: Method, schema: str | None = None, seen: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Choose one writable leaf, avoiding output identifiers and cycles."""
    schema = schema or item.request_schema
    assert schema is not None
    for name, spec in item.schema(schema).get('properties', {}).items():
        if (
            name == 'name'
            or spec.get('readOnly')
            or 'Immutable.' in spec.get('description', '')
        ):
            continue
        if '$ref' in spec:
            if spec['$ref'] in seen:
                continue
            child = leaf_patch(item, spec['$ref'], seen | {schema})
            if child:
                return {name: child}
        elif spec.get('type') == 'array':
            return {name: []}
        else:
            return {name: scalar(spec)}
    return {}


def example_body(item: Method) -> dict[str, Any] | None:
    if not item.request_schema:
        return None
    examples = json.loads(
        files('google_analytics_mcp_server')
        .joinpath('examples', f'{item.api}.json')
        .read_text()
    )
    for example in examples.get(catalog.short_schema(item.request_schema), []):
        if item.version in example['api_versions']:
            return copy.deepcopy(example['body'])
    return leaf_patch(item)


def wire_input(item: Method) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return explicit inputs for each raw endpoint, including sensitive actions."""
    params: dict[str, Any] = path_params(item)
    body = example_body(item)
    if 'updateMask' in item.parameters:
        body = leaf_patch(item)

        def leaves(value, prefix=''):
            return [
                path
                for key, child in value.items()
                for path in (
                    leaves(child, prefix + key + '.')
                    if isinstance(child, dict)
                    else [prefix + key]
                )
            ]

        params['updateMask'] = ','.join(leaves(body))
    if 'filter' in item.parameters and item.resource == 'properties':
        params['filter'] = 'parent:accounts/123'
    if 'calculatedMetricId' in item.parameters:
        params['calculatedMetricId'] = 'test_metric'
    if item.name == 'batchGet':
        params['names'] = [f'{params["parent"]}/accessBindings/456']
    if item.owner == 'ga_manage_access_bindings':
        parent = params.get(
            'parent', params.get('name', '').split('/accessBindings/')[0]
        )
        binding = {'user': 'analyst@example.com', 'roles': ['predefinedRoles/viewer']}
        if item.name == 'create':
            body = binding
        elif item.name == 'patch':
            body = {'name': params['name'], 'roles': []}
        elif item.name == 'batchCreate':
            body = {'requests': [{'parent': parent, 'accessBinding': binding}]}
        elif item.name == 'batchUpdate':
            body = {
                'requests': [
                    {
                        'accessBinding': {
                            'name': f'{parent}/accessBindings/456',
                            'roles': [],
                        }
                    }
                ]
            }
        elif item.name == 'batchDelete':
            body = {'requests': [{'name': f'{parent}/accessBindings/456'}]}
    if item.owner == 'ga_run_access_report':
        body['returnEntityQuota'] = params['entity'].startswith('properties/')
    if item.owner == 'ga_batch_run_reports':
        child = catalog.method(
            'data',
            'properties.'
            + (
                'runPivotReport' if item.name == 'batchRunPivotReports' else 'runReport'
            ),
            item.version,
        )
        body = {'requests': [example_body(child)]}
    if item.owner == 'ga_query':
        body = {'offset': '0', 'limit': '7'}
    if item.owner == 'ga_submit_user_deletion':
        body = {'userId': 'synthetic-user'}
    if (
        item.owner in {'ga_delete', 'ga_review_dv360_link_proposal'}
        and item.request_schema
    ):
        body = {}
    if item.owner == 'ga_chat':
        body = {'userQuery': 'How many users?', 'returnPropertyQuota': True}
    if item.owner == 'ga_acknowledge_user_data_collection':
        from google_analytics_mcp_server.tools.admin import ACKNOWLEDGEMENT

        body = {'acknowledgement': ACKNOWLEDGEMENT}
    return params, body


def native_arguments(item: Method) -> dict[str, Any]:
    """Exercise public tool signatures rather than calling client.execute directly."""
    params, body = wire_input(item)
    locators = path_params(item)
    locator = next(iter(locators.values()), None)
    args: dict[str, Any] = {'api_version': item.version}
    owner = item.owner
    if owner == 'ga_list':
        args.update(resource=item.resource, parent=locator, response_format='json_full')
        if item.resource == 'properties':
            args['parent'] = 'accounts/123'
        if item.name == 'batchGet':
            args['names'] = params['names']
    elif owner == 'ga_get':
        args['name'] = locator
    elif owner == 'ga_create':
        args.update(resource=item.resource, parent=locator or 'accounts/123', body=body)
        if 'calculatedMetricId' in params:
            args['resource_id'] = params['calculatedMetricId']
    elif owner in {'ga_update', 'ga_update_settings'}:
        args.update(name=locator, patch=body)
    elif owner == 'ga_delete':
        args['name'] = locator
    elif owner == 'ga_manage_access_bindings':
        args['operation'] = {
            'patch': 'update',
            'batchCreate': 'batch_create',
            'batchUpdate': 'batch_update',
            'batchDelete': 'batch_delete',
        }.get(item.name, item.name)
        args.update(locators)
        if body is not None:
            args['body'] = body
    elif owner == 'ga_get_metadata':
        args.update(property='123', response_format='json_full')
    elif owner == 'ga_run_access_report':
        args.update(entity=locator, body=body)
    elif owner == 'ga_search_change_history':
        args.update(account=locator, body=body)
    elif owner == 'ga_review_dv360_link_proposal':
        args.update(name=locator, operation=item.name)
    elif owner == 'ga_acknowledge_user_data_collection':
        args['property'] = locator
    elif owner == 'ga_start_async_job':
        args.update(
            property=locator,
            body=body,
            kind={
                'reportTasks': 'report_task',
                'audienceExports': 'audience_export',
                'audienceLists': 'audience_list',
            }[item.resource.split('.')[-1]],
        )
    elif owner == 'ga_query':
        args.update(name=locator, body=body)
    elif owner == 'ga_reorder_event_edit_rules':
        args.update(parent=locator, body=body)
    elif owner in {
        'ga_provision_account_ticket',
        'ga_create_rollup_property',
        'ga_provision_subproperty',
    }:
        args['body'] = body
    else:
        args.update(property=locator, body=body)
        if owner == 'ga_batch_run_reports':
            args['pivot'] = item.name == 'batchRunPivotReports'
    from google_analytics_mcp_server.policy import DESTRUCTIVE_OWNERS

    if owner in DESTRUCTIVE_OWNERS:
        args['confirm'] = True
    return args


def call_native(item: Method):
    return REGISTRY[item.owner].fn(**native_arguments(item))


def expected_path(item: Method, params: dict[str, Any]) -> str:
    """Resolve the discovery URI template independently of the Google client."""
    from urllib.parse import quote

    path = re.sub(
        r'\{(\+?)([^}]+)\}',
        lambda match: quote(str(params[match[2]]), safe='/' if match[1] else ''),
        item.spec['path'],
    )
    return '/' + catalog.document(item.api, item.version).get('servicePath', '') + path
