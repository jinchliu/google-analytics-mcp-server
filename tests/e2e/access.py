"""Explicitly approved lifecycle of the current user's property access binding."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from tests.e2e.sandbox import build_plan, dry_run


def build_access_plan(
    property_name: str,
    audience: str,
    principal: str,
    binding: str,
    roles: list[str],
    run_id: str,
) -> dict[str, Any]:
    plan = build_plan(property_name, audience, principal, binding, roles, run_id)

    def spec(operation, **arguments):
        return {
            'tool': 'ga_manage_access_bindings',
            'arguments': {
                'operation': operation,
                'confirm': True,
                'api_version': 'v1alpha',
                **arguments,
            },
        }

    viewer = {'user': principal, 'roles': ['predefinedRoles/viewer']}
    analyst = {'user': principal, 'roles': ['predefinedRoles/analyst']}
    original = {'user': principal, 'roles': roles}
    plan['calls'] = {
        'delete_original': spec('delete', name=binding),
        'create_viewer': spec('create', parent=property_name, body=viewer),
        'replace_roles': spec('update', name='$access', body=analyst),
        'clear_roles': spec(
            'update', name='$access', body={'user': principal, 'roles': []}
        ),
        'batch_create': spec(
            'batch_create',
            parent=property_name,
            body={
                'requests': [{'accessBinding': viewer}],
            },
        ),
        'batch_update': spec(
            'batch_update',
            parent=property_name,
            body={
                'requests': [{'accessBinding': {'name': '$access', **analyst}}],
            },
        ),
        'batch_delete': spec(
            'batch_delete',
            parent=property_name,
            body={
                'requests': [{'name': '$access'}],
            },
        ),
        'restore_create': spec('create', parent=property_name, body=original),
        'restore_update': spec('update', name='$access', body=original),
    }
    plan['access'] = (
        'User explicitly approved changing/restoring this property binding only.'
    )
    plan['cleanup'] = (
        'In finally, restore the original property roles and verify account Administrator remains.'
    )
    plan['job_retention'] = 'No jobs or sessions are created.'
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.inventory.read_text())
    binding = data['property_binding'][0]
    plan = build_access_plan(
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
        f'Offline access preview passed: {len(plan["dry_run"]["requests"])} requests; {args.output}'
    )


if __name__ == '__main__':
    main()
