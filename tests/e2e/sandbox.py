"""Preview fixed sandbox operations offline and journal opt-in live execution."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import socket
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import google.auth
import httplib2
from googleapiclient.discovery import build_from_document
from tests.e2e.helpers import call
from tests.helpers import RecordingHttp

from google_analytics_mcp_server import catalog, client
from google_analytics_mcp_server.server import create_server


def build_plan(
    property_name: str,
    audience: str,
    principal: str,
    binding: str,
    roles: list[str],
    run_id: str,
) -> dict[str, Any]:
    """Build only temporary configuration, bounded jobs and unchanged-role writes."""
    if not re.fullmatch(r'properties/[0-9]+', property_name):
        raise ValueError('A numeric property resource name is required.')
    if not re.fullmatch(re.escape(property_name) + r'/audiences/[0-9]+', audience):
        raise ValueError('Audience must belong to the selected property.')
    if not re.fullmatch(re.escape(property_name) + r'/accessBindings/[^/]+', binding):
        raise ValueError('Binding must belong to the selected property.')
    if not principal or 'predefinedRoles/admin' not in roles:
        raise ValueError('These tests require an existing administrator binding.')
    if not re.fullmatch(r'[a-z0-9]{12}', run_id):
        raise ValueError('run_id must be twelve lowercase letters or digits.')
    stream_body = {
        'displayName': f'MCP sandbox {run_id}',
        'type': 'WEB_DATA_STREAM',
        'webStreamData': {'defaultUri': 'https://example.invalid'},
    }
    rule_body = {
        'displayName': f'MCP sandbox {run_id} rule',
        'eventConditions': [
            {'field': 'event_name', 'comparisonType': 'EQUALS', 'value': 'mcp_unused'}
        ],
        'parameterMutations': [
            {'parameter': 'mcp_probe_one', 'parameterValue': 'one'},
            {'parameter': 'mcp_probe_two', 'parameterValue': 'two'},
        ],
    }
    calls = {
        'create_stream': {
            'tool': 'ga_create',
            'arguments': {
                'resource': 'dataStreams',
                'parent': property_name,
                'body': stream_body,
                'api_version': '$version',
            },
        },
        'patch_uri': {
            'tool': 'ga_update',
            'arguments': {
                'name': '$stream',
                'patch': {'webStreamData': {'defaultUri': 'https://updated.invalid'}},
                'api_version': '$version',
            },
        },
        'clear_uri': {
            'tool': 'ga_update',
            'arguments': {
                'name': '$stream',
                'patch': {'webStreamData': {'defaultUri': None}},
                'update_mask': 'web_stream_data.default_uri',
                'api_version': '$version',
            },
        },
        'delete_stream': {
            'tool': 'ga_delete',
            'arguments': {
                'name': '$stream',
                'confirm': True,
                'api_version': '$version',
            },
        },
        'create_rule': {
            'tool': 'ga_create',
            'arguments': {
                'resource': 'eventEditRules',
                'parent': '$stream',
                'body': rule_body,
                'api_version': 'v1alpha',
            },
        },
        'patch_rule': {
            'tool': 'ga_update',
            'arguments': {
                'name': '$rule',
                'patch': {'parameterMutations': [rule_body['parameterMutations'][0]]},
                'api_version': 'v1alpha',
            },
        },
        'delete_rule': {
            'tool': 'ga_delete',
            'arguments': {'name': '$rule', 'confirm': True, 'api_version': 'v1alpha'},
        },
        'binding_update': {
            'tool': 'ga_manage_access_bindings',
            'arguments': {
                'operation': 'update',
                'name': binding,
                'body': {'user': principal, 'roles': roles},
                'confirm': True,
                'api_version': 'v1alpha',
            },
        },
        'binding_batch_update': {
            'tool': 'ga_manage_access_bindings',
            'arguments': {
                'operation': 'batch_update',
                'parent': property_name,
                'body': {
                    'requests': [
                        {
                            'accessBinding': {
                                'name': binding,
                                'user': principal,
                                'roles': roles,
                            }
                        }
                    ]
                },
                'confirm': True,
                'api_version': 'v1alpha',
            },
        },
    }
    for kind, version in (
        ('report_task', 'v1alpha'),
        ('audience_export', 'v1beta'),
        ('audience_list', 'v1alpha'),
    ):
        body = (
            {
                'reportDefinition': {
                    'dateRanges': [{'startDate': '7daysAgo', 'endDate': 'yesterday'}],
                    'dimensions': [{'name': 'country'}],
                    'metrics': [{'name': 'activeUsers'}],
                    'limit': '2',
                }
            }
            if kind == 'report_task'
            else {
                'audience': audience,
                'dimensions': [{'dimensionName': 'isAdsPersonalizationAllowed'}],
            }
        )
        calls[kind] = {
            'tool': 'ga_start_async_job',
            'arguments': {
                'kind': kind,
                'property': property_name,
                'body': body,
                'wait_seconds': 0,
                'api_version': version,
            },
        }
    return {
        'property': property_name,
        'audience': audience,
        'principal': principal,
        'binding': binding,
        'roles': roles,
        'run_id': run_id,
        'calls': calls,
        'cleanup': 'Delete only streams/rules created by this run, in finally blocks.',
        'job_retention': 'Jobs have no delete endpoint in the pinned APIs; record names for follow-up.',
        'access': 'Preserve the exact existing role set. No binding create or delete.',
    }


def digest(plan: dict[str, Any]) -> str:
    """Fingerprint the fixed inputs and operations, excluding dry-run output."""
    data = {key: value for key, value in plan.items() if key != 'dry_run'}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def substitute(value: Any, replacements: dict[str, str]) -> Any:
    """Resolve complete placeholder strings without evaluating arbitrary templates."""
    if isinstance(value, dict):
        return {key: substitute(child, replacements) for key, child in value.items()}
    if isinstance(value, list):
        return [substitute(child, replacements) for child in value]
    if isinstance(value, str) and value.startswith('$'):
        return replacements[value]
    return value


async def dry_run(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate every planned mutation through real discovery without ADC/network."""
    transport = RecordingHttp()
    services = {
        (api, version): build_from_document(
            json.dumps(catalog.document(api, version)), http=transport
        )
        for api in catalog.SERVICES
        for version in catalog.VERSIONS
    }
    stream = f'{plan["property"]}/dataStreams/0'
    requests = []
    with (
        patch.object(client, '_services', services),
        patch.object(
            google.auth, 'default', side_effect=AssertionError('ADC forbidden')
        ),
        patch.object(
            httplib2.Http, 'request', side_effect=AssertionError('Network forbidden')
        ),
        patch.object(
            socket.socket, 'connect', side_effect=AssertionError('Network forbidden')
        ),
        patch.dict(os.environ, {'GA_MCP_READ_ONLY': '0', 'GA_MCP_ENABLE_CHAT': '1'}),
    ):
        server = create_server()
        for version in ('v1beta', 'v1alpha'):
            replacements = {
                '$version': version,
                '$stream': stream,
                '$rule': f'{stream}/eventEditRules/0',
                '$annotation': f'{plan["property"]}/reportingDataAnnotations/0',
                '$metric': f'{plan["property"]}/calculatedMetrics/mcp_probe_{plan["run_id"]}',
                '$session': 'offline-session',
                '$access': plan['binding'],
            }
            for step, spec in plan['calls'].items():
                if version == 'v1alpha' and '$version' not in json.dumps(spec):
                    continue
                await call(
                    server, spec['tool'], **substitute(spec['arguments'], replacements)
                )
                request = transport.requests[-1]
                requests.append(
                    {
                        'step': step,
                        'method': request.method,
                        'uri': request.uri,
                        'body': request.body,
                    }
                )
    return {'digest': digest(plan), 'network': False, 'requests': requests}


class Sandbox:
    """Keep one durable journal and refuse automatic replay of started cases."""

    def __init__(
        self,
        plan_path: Path,
        journal_path: Path,
        property_name: str,
        plan_factory: Callable[..., dict[str, Any]] = build_plan,
    ):
        self.plan = json.loads(plan_path.read_text())
        expected = plan_factory(
            self.plan['property'],
            self.plan['audience'],
            self.plan['principal'],
            self.plan['binding'],
            self.plan['roles'],
            self.plan['run_id'],
        )
        if self.plan['property'] != property_name or digest(self.plan) != digest(
            expected
        ):
            raise ValueError(
                'Sandbox plan does not match the target or fixed operation set.'
            )
        if self.plan.get('dry_run', {}).get('digest') != digest(expected):
            raise ValueError('Run the offline sandbox preview before live writes.')
        self.path = journal_path
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if self.data['digest'] != digest(expected):
                raise ValueError(
                    'Journal belongs to another plan; inspect existing outcomes.'
                )
        else:
            self.data = {
                'digest': digest(expected),
                'property': property_name,
                'cases': {},
            }
            with self.path.open('x') as output:
                json.dump(self.data, output, indent=2)

    def flush(self) -> None:
        temporary = self.path.with_suffix('.writing')
        with temporary.open('w') as output:
            json.dump(self.data, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)

    def begin(self, case: str) -> None:
        if case in self.data['cases']:
            raise ValueError(
                'Case already attempted; inspect journal before any new mutation.'
            )
        self.data['cases'][case] = {'events': []}
        self.flush()

    def note(self, case: str, **evidence: Any) -> None:
        self.data['cases'][case]['events'].append(
            {
                'time': datetime.now(UTC).isoformat(),
                **evidence,
            }
        )
        self.flush()

    async def execute(
        self, server, case: str, step: str, **replacements: str
    ) -> dict[str, Any]:
        spec = self.plan['calls'][step]
        owned = {
            event['outcome'].get('name')
            for event in self.data['cases'][case]['events']
            if event.get('phase') == 'after'
            and self.plan['calls'][event['step']]['tool'] == 'ga_create'
        }
        sessions = {
            event['outcome'].get('sessionId')
            for event in self.data['cases'][case]['events']
            if event.get('phase') == 'after'
        }
        for key, value in replacements.items():
            if (
                key in {'$stream', '$rule', '$annotation', '$metric'}
                and value not in owned
            ):
                raise ValueError('Only resources created by this case may be mutated.')
            if key == '$session' and value not in sessions:
                raise ValueError(
                    'Only a session created by this case may be continued.'
                )
            if key == '$version' and value not in {'v1alpha', 'v1beta'}:
                raise ValueError('Unexpected API version.')
        arguments = substitute(spec['arguments'], replacements)
        self.note(case, step=step, phase='before', arguments=arguments)
        try:
            result = await call(server, spec['tool'], **arguments)
        except Exception as error:
            self.note(case, step=step, phase='error', error=str(error))
            raise
        self.note(
            case,
            step=step,
            phase='after',
            outcome={
                key: result[key]
                for key in (
                    'name',
                    'target',
                    'success',
                    'status',
                    'operation',
                    'sessionId',
                    'accessBindings',
                )
                if key in result
            },
        )
        return result


def main() -> None:
    """Create a reviewable dry run from an explicit read-only inventory file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    binding = inventory['property_binding'][0]
    plan = build_plan(
        inventory['property'],
        inventory['resources']['audiences']['names'][0],
        inventory['adc_account'],
        binding['name'],
        deepcopy(binding['roles']),
        uuid4().hex[:12],
    )
    plan['dry_run'] = asyncio.run(dry_run(plan))
    with args.output.open('x') as output:
        json.dump(plan, output, indent=2)
        output.write('\n')
    print(
        f'Offline dry run passed: {len(plan["dry_run"]["requests"])} requests; {args.output}'
    )


if __name__ == '__main__':
    main()
