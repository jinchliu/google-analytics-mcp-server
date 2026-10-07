"""Preview and journal account/property access tests preserving administrators."""

import argparse
import asyncio
import json
import os
import re
import socket
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
from tests.e2e.sandbox import Sandbox, digest, substitute
from tests.helpers import RecordingHttp

from google_analytics_mcp_server import catalog, client
from google_analytics_mcp_server.server import create_server


def canonical(bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare binding identifiers, users and role sets without response order."""
    return sorted(
        (
            {
                'name': binding['name'],
                'user': binding['user'].casefold(),
                'roles': sorted(binding['roles']),
            }
            for binding in bindings
        ),
        key=lambda binding: binding['name'],
    )


def build_account_access_plan(
    account: str,
    property_name: str,
    principal: str,
    bindings: dict[str, list[dict[str, Any]]],
    test_user: str | None,
    run_id: str,
    *,
    retain_for_review: bool = False,
) -> dict[str, Any]:
    """Build fixed writes to unchanged admin roles or an absent test recipient."""
    if not re.fullmatch(r'accounts/[0-9]+', account):
        raise ValueError('A numeric account resource name is required.')
    if not re.fullmatch(r'properties/[0-9]+', property_name):
        raise ValueError('A numeric property resource name is required.')
    if not re.fullmatch(r'[a-z0-9]{12}', run_id):
        raise ValueError('run_id must be twelve lowercase letters or digits.')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', principal):
        raise ValueError('Supply the exact operator email.')
    if set(bindings) != {account, property_name}:
        raise ValueError('Snapshot both selected access levels only.')
    if retain_for_review and test_user is None:
        raise ValueError('Retaining review access requires a selected test recipient.')
    snapshots = deepcopy(bindings)
    for parent, entries in snapshots.items():
        names, users = set(), set()
        for entry in entries:
            if not re.fullmatch(
                re.escape(parent) + r'/accessBindings/[^/]+', entry['name']
            ):
                raise ValueError('Every snapshot binding must belong to its parent.')
            user = entry['user'].casefold()
            if user in users or entry['name'] in names:
                raise ValueError('Snapshot contains duplicate access bindings.')
            users.add(user)
            names.add(entry['name'])
    administrator = [
        entry
        for entry in snapshots[account]
        if entry['user'].casefold() == principal.casefold()
    ]
    if (
        len(administrator) != 1
        or 'predefinedRoles/admin' not in administrator[0]['roles']
    ):
        raise ValueError('The operator must retain direct account Administrator.')
    if test_user is not None:
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', test_user):
            raise ValueError('Supply the explicitly selected test email.')
        if test_user.casefold() == principal.casefold():
            raise ValueError('The test recipient must differ from the operator.')
        if any(
            entry['user'].casefold() == test_user.casefold()
            for entries in snapshots.values()
            for entry in entries
        ):
            raise ValueError('The test recipient must have no existing binding.')

    def spec(operation: str, **arguments: Any) -> dict[str, Any]:
        return {
            'tool': 'ga_manage_access_bindings',
            'arguments': {
                'operation': operation,
                'api_version': 'v1alpha',
                'confirm': True,
                **arguments,
            },
        }

    calls = {}
    if test_user is None:
        binding = administrator[0]
        body = {'user': binding['user'], 'roles': binding['roles']}
        calls['account_update_original'] = spec(
            'update', name=binding['name'], body=body
        )
        calls['account_batch_update_original'] = spec(
            'batch_update',
            parent=account,
            body={'requests': [{'accessBinding': {'name': binding['name'], **body}}]},
        )
    else:
        viewer = {'user': test_user, 'roles': ['predefinedRoles/viewer']}
        analyst = {'user': test_user, 'roles': ['predefinedRoles/analyst']}
        empty = {'user': test_user, 'roles': []}
        for level, parent in (('account', account), ('property', property_name)):
            placeholder = f'${level}_access'
            create = spec('create', parent=parent, body=viewer)
            batch_create = spec(
                'batch_create',
                parent=parent,
                body={'requests': [{'accessBinding': viewer}]},
            )
            steps = {
                'create_single': create,
                'update_single': spec('update', name=placeholder, body=analyst),
                'delete_single': spec('delete', name=placeholder),
                'create_for_clear': create,
                'clear_single': spec('update', name=placeholder, body=empty),
                'create_batch': batch_create,
                'update_batch': spec(
                    'batch_update',
                    parent=parent,
                    body={
                        'requests': [
                            {'accessBinding': {'name': placeholder, **analyst}}
                        ]
                    },
                ),
                'delete_batch': spec(
                    'batch_delete',
                    parent=parent,
                    body={'requests': [{'name': placeholder}]},
                ),
                'create_for_batch_clear': batch_create,
                'clear_batch': spec(
                    'batch_update',
                    parent=parent,
                    body={
                        'requests': [{'accessBinding': {'name': placeholder, **empty}}]
                    },
                ),
                'cleanup': spec('delete', name=placeholder),
            }
            calls.update({f'{level}_{step}': value for step, value in steps.items()})
        if retain_for_review:
            for level, parent in (('account', account), ('property', property_name)):
                calls[f'{level}_retain_viewer'] = spec(
                    'create', parent=parent, body=viewer
                )
    plan = {
        'account': account,
        'property': property_name,
        'principal': principal,
        'bindings': snapshots,
        'test_user': test_user,
        'run_id': run_id,
        'mode': 'temporary' if test_user else 'unchanged',
        'calls': deepcopy(calls),
        'cleanup': 'Remove only the selected temporary recipient; preserve every original binding.',
    }
    if retain_for_review:
        plan['retain_for_review'] = True
        plan['cleanup'] = (
            'Clean up lifecycle fixtures, then retain Viewer at both levels only '
            'after both lifecycles pass and the user has requested review access.'
        )
    return plan


async def preview(plan: dict[str, Any]) -> dict[str, Any]:
    """Exercise all fixed requests on real discovery with ADC/network blocked."""
    transport = RecordingHttp()
    service = build_from_document(
        json.dumps(catalog.document('admin', 'v1alpha')), http=transport
    )
    replacements = {
        f'${level}_access': f'{plan[level]}/accessBindings/offline'
        for level in ('account', 'property')
    }
    requests = []
    with (
        patch.object(client, '_services', {('admin', 'v1alpha'): service}),
        patch.object(
            google.auth, 'default', side_effect=AssertionError('ADC forbidden')
        ),
        patch.object(
            httplib2.Http, 'request', side_effect=AssertionError('Network forbidden')
        ),
        patch.object(
            socket.socket, 'connect', side_effect=AssertionError('Network forbidden')
        ),
        patch.dict(os.environ, {'GA_MCP_READ_ONLY': '0'}),
    ):
        server = create_server()
        for step, spec in plan['calls'].items():
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


class AccessSandbox(Sandbox):
    """Reuse durable journaling with explicit account and recipient boundaries."""

    def __init__(
        self, plan_path: Path, journal_path: Path, account: str, property_name: str
    ) -> None:
        self.plan = json.loads(plan_path.read_text())
        expected = build_account_access_plan(
            self.plan['account'],
            self.plan['property'],
            self.plan['principal'],
            self.plan['bindings'],
            self.plan['test_user'],
            self.plan['run_id'],
            retain_for_review=self.plan.get('retain_for_review', False),
        )
        if (
            account != expected['account']
            or property_name != expected['property']
            or digest(self.plan) != digest(expected)
        ):
            raise ValueError('Access plan differs from the selected targets or writes.')
        if self.plan.get('dry_run', {}).get('digest') != digest(expected):
            raise ValueError('Run the offline access preview before live writes.')
        self.path = journal_path
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if self.data['digest'] != digest(expected):
                raise ValueError('Journal belongs to another plan; inspect outcomes.')
        else:
            self.data = {
                'digest': digest(expected),
                'account': account,
                'property': property_name,
                'cases': {},
            }
            with self.path.open('x') as output:
                json.dump(self.data, output, indent=2)

    async def execute(
        self, server, case: str, step: str, **replacements: str
    ) -> dict[str, Any]:
        """Refuse replay and require observed temporary recipient ownership."""
        events = self.data['cases'][case]['events']
        if any(
            event.get('step') == step and event.get('phase') == 'before'
            for event in events
        ):
            raise ValueError('This mutation was already attempted; do not replay it.')
        if self.plan['mode'] == 'unchanged':
            if case != 'account_unchanged_roles' or replacements:
                raise ValueError('Unchanged-role writes do not accept substitutions.')
        elif case == 'access_review':
            self.require_completed_lifecycles()
            if (
                step not in {'account_retain_viewer', 'property_retain_viewer'}
                or replacements
            ):
                raise ValueError(
                    'Review access permits only the two fixed Viewer grants.'
                )
        else:
            level = case.removesuffix('_access_lifecycle')
            if (
                level not in {'account', 'property'}
                or not step.startswith(level + '_')
                or step.endswith('_retain_viewer')
            ):
                raise ValueError('Only the current lifecycle parent may be mutated.')
            for key, value in replacements.items():
                if key != f'${level}_access' or not value.startswith(
                    f'{self.plan[level]}/accessBindings/'
                ):
                    raise ValueError('Binding must belong to the selected parent.')
                owned = {
                    event['observed_binding']['name']
                    for event in events
                    if event.get('observed_binding')
                    and event['observed_binding']['user'].casefold()
                    == self.plan['test_user'].casefold()
                }
                if value not in owned:
                    raise ValueError('Inspect the temporary recipient before mutation.')
        return await super().execute(server, case, step, **replacements)

    def require_completed_lifecycles(self) -> None:
        """Require both complete, error-free lifecycles before retaining access."""
        if not self.plan.get('retain_for_review'):
            raise ValueError('The preview does not authorize retained review access.')
        for level in ('account', 'property'):
            expected_steps = [
                f'{level}_{step}'
                for step in (
                    'create_single',
                    'update_single',
                    'delete_single',
                    'create_for_clear',
                    'clear_single',
                    'create_batch',
                    'update_batch',
                    'delete_batch',
                    'create_for_batch_clear',
                    'clear_batch',
                )
            ]
            events = (
                self.data['cases']
                .get(f'{level}_access_lifecycle', {})
                .get('events', [])
            )
            completed_steps = [
                event['step'] for event in events if event.get('phase') == 'after'
            ]
            if (
                completed_steps != expected_steps
                or any(event.get('phase') == 'error' for event in events)
                or not events[-1].get('cleanup_verified')
                or not events[-1].get('original_bindings_unchanged')
            ):
                raise ValueError(
                    'Both access lifecycles must pass before retaining Viewer.'
                )


async def list_bindings(server, parent: str) -> list[dict[str, Any]]:
    """Read complete direct bindings with a bounded page count."""
    arguments = {
        'resource': 'accessBindings',
        'parent': parent,
        'limit': 100,
        'response_format': 'json_full',
        'api_version': 'v1alpha',
    }
    entries = []
    for _ in range(20):
        page = await call(server, 'ga_list', **arguments)
        entries.extend(page.get('accessBindings', []))
        arguments = page['_mcp']['next_arguments']
        if not arguments:
            return entries
    raise ValueError('Incomplete access inventory; no writes are permitted.')


async def check_originals(
    server, plan: dict[str, Any], *, recipient_absent: bool
) -> dict[str, list[dict[str, Any]]]:
    """Verify the property relationship and unchanged original bindings."""
    prop = await call(server, 'ga_get', name=plan['property'])
    assert prop['parent'] == plan['account']
    snapshots = {}
    test_user = (plan['test_user'] or '').casefold()
    for parent in (plan['account'], plan['property']):
        entries = await list_bindings(server, parent)
        existing = [entry for entry in entries if entry['user'].casefold() != test_user]
        assert canonical(existing) == canonical(plan['bindings'][parent])
        if recipient_absent:
            assert len(existing) == len(entries)
        snapshots[parent] = entries
    return snapshots


async def inventory(account: str, property_name: str, principal: str) -> dict[str, Any]:
    """Snapshot only the selected account/property and their direct bindings."""
    if not re.fullmatch(r'accounts/[0-9]+', account) or not re.fullmatch(
        r'properties/[0-9]+', property_name
    ):
        raise ValueError('Supply numeric account and property resource names.')
    with patch.dict(os.environ, {'GA_MCP_READ_ONLY': '1'}):
        server = create_server()
        account_result = await call(server, 'ga_get', name=account)
        prop = await call(server, 'ga_get', name=property_name)
        assert account_result['name'] == account
        assert prop['parent'] == account
        bindings = {
            parent: await list_bindings(server, parent)
            for parent in (account, property_name)
        }
    build_account_access_plan(
        account, property_name, principal, bindings, None, '000000000000'
    )
    return {
        'time': datetime.now(UTC).isoformat(),
        'account': account,
        'property': property_name,
        'principal': principal,
        'account_display_name': account_result.get('displayName'),
        'property_display_name': prop.get('displayName'),
        'bindings': bindings,
        'read_only': True,
    }


def main() -> None:
    """Read an access inventory or generate its fixed offline request preview."""
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--inventory', type=Path)
    source.add_argument('--account', help='Read-only inventory account ID or name.')
    parser.add_argument('--property', help='Read-only inventory property ID or name.')
    parser.add_argument('--principal', help='Operator email for a read-only inventory.')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--test-user', help='Explicitly selected temporary recipient.')
    parser.add_argument(
        '--retain-for-review',
        action='store_true',
        help='User-approved Viewer grants after both lifecycles pass.',
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Preserve the previous preview; use a new output path.')
    if args.account:
        if (
            not args.property
            or not args.principal
            or args.test_user
            or args.retain_for_review
        ):
            parser.error(
                'Inventory requires --property and --principal, without --test-user.'
            )
        account = (
            args.account
            if args.account.startswith('accounts/')
            else f'accounts/{args.account}'
        )
        property_name = (
            args.property
            if args.property.startswith('properties/')
            else f'properties/{args.property}'
        )
        result = asyncio.run(inventory(account, property_name, args.principal))
        with args.output.open('x') as output:
            json.dump(result, output, indent=2)
            output.write('\n')
        print(f'Read-only access inventory saved: {args.output}')
        return
    if args.property or args.principal:
        parser.error('Preview reads --inventory; do not override its targets.')
    recorded = json.loads(args.inventory.read_text())
    plan = build_account_access_plan(
        recorded['account'],
        recorded['property'],
        recorded['principal'],
        recorded['bindings'],
        args.test_user,
        uuid4().hex[:12],
        retain_for_review=args.retain_for_review,
    )
    plan['dry_run'] = asyncio.run(preview(plan))
    with args.output.open('x') as output:
        json.dump(plan, output, indent=2)
        output.write('\n')
    print(
        f'Offline access preview passed: {len(plan["dry_run"]["requests"])} requests; {args.output}'
    )


if __name__ == '__main__':
    main()
