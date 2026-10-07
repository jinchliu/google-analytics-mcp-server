"""Fixed additional sandbox operations for standard-property alpha features."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from tests.e2e.sandbox import build_plan, dry_run


def build_advanced_plan(
    property_name: str,
    audience: str,
    principal: str,
    binding: str,
    roles: list[str],
    run_id: str,
) -> dict[str, Any]:
    """Reuse scope validation while describing only temporary alpha fixtures/chat."""
    plan = build_plan(property_name, audience, principal, binding, roles, run_id)
    calls = {}
    resources = [
        (
            'annotation',
            'reportingDataAnnotations',
            {
                'title': f'MCP sandbox {run_id}',
                'description': 'Temporary API verification.',
                'color': 'BLUE',
                'annotationDate': {'year': 2026, 'month': 10, 'day': 4},
            },
            {'description': 'Updated temporary API verification.'},
        ),
        (
            'metric',
            'calculatedMetrics',
            {
                'displayName': f'MCP sandbox {run_id}',
                'description': 'Temporary API verification.',
                'metricUnit': 'STANDARD',
                'formula': 'eventCount / 2',
            },
            {'formula': 'eventCount / 3'},
        ),
    ]
    for label, resource, body, update in resources:
        calls[f'create_{label}'] = {
            'tool': 'ga_create',
            'arguments': {
                'resource': resource,
                'parent': property_name,
                'body': body,
                'api_version': 'v1alpha',
                **({'resource_id': f'mcp_probe_{run_id}'} if label == 'metric' else {}),
            },
        }
        calls[f'patch_{label}'] = {
            'tool': 'ga_update',
            'arguments': {
                'name': f'${label}',
                'patch': update,
                'api_version': 'v1alpha',
            },
        }
        calls[f'delete_{label}'] = {
            'tool': 'ga_delete',
            'arguments': {
                'name': f'${label}',
                'confirm': True,
                'api_version': 'v1alpha',
            },
        }
    calls['chat_start'] = {
        'tool': 'ga_chat',
        'arguments': {
            'property': property_name,
            'api_version': 'v1alpha',
            'user_query': 'What does the activeUsers metric mean? Answer in one sentence.',
        },
    }
    calls['chat_continue'] = {
        'tool': 'ga_chat',
        'arguments': {
            'property': property_name,
            'api_version': 'v1alpha',
            'session_id': '$session',
            'user_query': 'What is the difference from totalUsers? Answer in one sentence.',
        },
    }
    plan.update(
        {
            'calls': calls,
            'cleanup': 'Delete only the annotation/calculated metric created by this run.',
            'job_retention': 'Chat sessions have no delete method in the pinned API; record their IDs.',
            'access': 'No access-binding mutation is included in this plan.',
        }
    )
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.inventory.read_text())
    binding = data['property_binding'][0]
    plan = build_advanced_plan(
        data['property'],
        data['resources']['audiences']['names'][0],
        data['adc_account'],
        binding['name'],
        binding['roles'],
        uuid4().hex[:12],
    )
    plan['dry_run'] = asyncio.run(dry_run(plan))
    with args.output.open('x') as output:
        json.dump(plan, output, indent=2)
        output.write('\n')
    print(
        f'Offline preview passed: {len(plan["dry_run"]["requests"])} requests; {args.output}'
    )


if __name__ == '__main__':
    main()
