"""Review upstream discovery drift without silently changing default routes."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1] / 'src/google_analytics_mcp_server/discovery'


def index(document: dict[str, Any]) -> dict[str, Any]:
    """Flatten method specifications for a readable drift report."""
    result: dict[str, Any] = {}

    def visit(node: dict[str, Any]) -> None:
        for method in node.get('methods', {}).values():
            result[method['id']] = method
        for child in node.get('resources', {}).values():
            visit(child)

    visit(document)
    return result


def changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """Report additions, removals and recursive value differences."""
    return {
        'added': sorted(after.keys() - before.keys()),
        'removed': sorted(before.keys() - after.keys()),
        'changed': sorted(
            k for k in before.keys() & after.keys() if before[k] != after[k]
        ),
    }


def changed_paths(before: Any, after: Any, prefix: str = '') -> list[str]:
    """Locate nested parameter, field, enum and description changes."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            path = f'{prefix}/{key}'
            if key not in before or key not in after:
                result.append(path)
            else:
                result.extend(changed_paths(before[key], after[key], path))
        return result
    return [prefix or '/']


def route_proposals(
    documents: list[tuple[Path, str, bytes, dict[str, Any]]],
) -> dict[str, Any]:
    """Suggest owner/default reviews without changing the route manifest."""
    existing = json.loads((ROOT / 'routes.json').read_text())
    current: dict[str, set[str]] = {}
    for _, _, _, doc in documents:
        api = 'admin' if doc['name'] == 'analyticsadmin' else 'data'
        for identity in index(doc):
            key = f'{api}:{identity.removeprefix(doc["name"] + ".")}'
            current.setdefault(key, set()).add(doc['version'])
    return {
        'new_methods_requiring_owners': {
            key: {
                'suggested_version': 'v1beta' if 'v1beta' in current[key] else 'v1alpha'
            }
            for key in sorted(current.keys() - existing.keys())
        },
        'removed_methods': sorted(existing.keys() - current.keys()),
        'unavailable_default_versions': [
            key
            for key in sorted(current.keys() & existing.keys())
            if existing[key]['default_version'] not in current[key]
        ],
        'route_manifest_changed': False,
    }


def main() -> int:
    """Fetch all snapshots first; writing is an explicit developer action."""
    from datetime import UTC, datetime

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='exit 1 if upstream differs')
    mode.add_argument(
        '--write',
        action='store_true',
        help='replace snapshots; route decisions remain manual',
    )
    args = parser.parse_args()
    fetched: list[tuple[Path, str, bytes, dict[str, Any]]] = []
    different = False
    for service in ('analyticsadmin', 'analyticsdata'):
        for version in ('v1beta', 'v1alpha'):
            path = ROOT / f'{service}.{version}.json'
            url = f'https://{service}.googleapis.com/$discovery/rest?version={version}'
            with urlopen(url, timeout=30) as response:
                payload = response.read()
            current = json.loads(payload)
            if current.get('name') != service or current.get('version') != version:
                raise ValueError(f'Unexpected discovery identity from {url}')
            previous = json.loads(path.read_bytes())
            changed = previous != current
            different |= changed
            print(
                json.dumps(
                    {
                        'document': path.name,
                        'before_revision': previous.get('revision'),
                        'after_revision': current.get('revision'),
                        'methods': changes(index(previous), index(current)),
                        'schemas': changes(
                            previous.get('schemas', {}), current.get('schemas', {})
                        ),
                        'changed_paths': changed_paths(previous, current),
                        'changed': changed,
                    },
                    indent=2,
                )
            )
            fetched.append((path, url, payload, current))
    print(json.dumps({'route_review': route_proposals(fetched)}, indent=2))
    if args.write:
        records = [
            '# Discovery snapshots',
            '',
            f'Fetched {datetime.now(UTC).isoformat()}. Review routes.json separately; no default versions were changed.',
            '',
            '| Document | Revision | SHA-256 | Source |',
            '|---|---|---|---|',
        ]
        for path, url, payload, doc in fetched:
            temporary = path.with_suffix('.json.new')
            temporary.write_bytes(payload)
            temporary.replace(path)
            records.append(
                f'| {path.name} | {doc["revision"]} | {hashlib.sha256(payload).hexdigest()} | {url} |'
            )
        (ROOT / 'SOURCES.md').write_text('\n'.join(records) + '\n')
        print(
            'Snapshots updated. Review method owners, default routes, schemas and contracts before release.'
        )
    return 1 if args.check and different else 0


if __name__ == '__main__':
    sys.exit(main())
