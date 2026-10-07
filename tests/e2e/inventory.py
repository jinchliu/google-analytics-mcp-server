"""Read the sandbox target and exact administrator binding without exposing ADC."""

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.e2e.helpers import call

from google_analytics_mcp_server.server import create_server


async def inventory(property_name: str, principal: str) -> dict[str, Any]:
    """List configuration names and the explicitly supplied principal's binding."""
    with patch.dict(os.environ, {'GA_MCP_READ_ONLY': '1'}):
        server = create_server()
        prop = await call(server, 'ga_get', name=property_name)
        result = {
            'property': property_name,
            'display_name': prop['displayName'],
            'parent': prop['parent'],
            'adc_account': principal,
            'resources': {},
            'property_binding': [],
        }
        for resource in ('dataStreams', 'audiences', 'accessBindings'):
            arguments = {
                'resource': resource,
                'parent': property_name,
                'limit': 100,
                'response_format': 'json_full',
            }
            entries = []
            for _ in range(20):
                page = await call(server, 'ga_list', **arguments)
                entries.extend(page.get(resource, []))
                arguments = page['_mcp']['next_arguments']
                if not arguments:
                    break
            else:
                raise RuntimeError(
                    'Inventory page limit reached; do not assume completeness.'
                )
            result['resources'][resource] = {
                'count': len(entries),
                'has_more': False,
                'names': [entry['name'] for entry in entries]
                if resource != 'accessBindings'
                else [],
            }
            if resource == 'accessBindings':
                result['property_binding'] = [
                    entry
                    for entry in entries
                    if entry.get('user', '').casefold() == principal.casefold()
                ]
        if len(result['property_binding']) != 1:
            raise ValueError(
                'One existing direct property binding is required; no access was changed.'
            )
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--property', required=True)
    parser.add_argument('--principal', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    name = (
        args.property
        if args.property.startswith('properties/')
        else f'properties/{args.property}'
    )
    if not re.fullmatch(r'properties/[0-9]+', name):
        parser.error('Supply a numeric property ID or properties/PROPERTY_ID.')
    if args.output.exists():
        parser.error(
            'Output already exists; choose another path to retain earlier evidence.'
        )
    result = asyncio.run(inventory(name, args.principal))
    with args.output.open('x') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    print(f'Read-only inventory saved: {args.output}')


if __name__ == '__main__':
    main()
