"""Copy rendered login blocks through a stub, never invoke a real gcloud login."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


def login_blocks():
    text = Path('README.md').read_text()
    for match in re.finditer(
        r'^([ ]*)```bash\n(.*?)^\1```', text, re.MULTILINE | re.DOTALL
    ):
        indent = len(match[1])
        block = '\n'.join(
            line[indent:] if line.startswith(' ' * indent) else line
            for line in match[2].splitlines()
        )
        if re.search(r'^gcloud auth application-default login\s', block, re.MULTILINE):
            yield block


@pytest.mark.parametrize(
    'block',
    list(login_blocks()),
    ids=['ga-only', 'ga-and-gtm'],
)
def test_rendered_login_scopes_are_one_shell_argument(block, tmp_path):
    output = tmp_path / 'arguments.json'
    stub = tmp_path / 'gcloud'
    stub.write_text(
        f'#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\nPath(os.environ["CAPTURE_ARGS"]).write_text(json.dumps(sys.argv[1:]))\n'
    )
    stub.chmod(0o700)
    env = {**os.environ, 'PATH': str(tmp_path), 'CAPTURE_ARGS': str(output)}
    subprocess.run(
        ['/bin/sh', '-eu', '-c', block],
        env=env,
        check=True,
        capture_output=True,
        timeout=5,
    )
    args = json.loads(output.read_text())
    scopes = [arg for arg in args if arg.startswith('--scopes=')]
    assert len(scopes) == 1
    assert len(args) == 5
    assert all(
        scope.startswith('https://www.googleapis.com/auth/')
        for scope in scopes[0].removeprefix('--scopes=').split(',')
    )
    assert not any(arg.startswith('https://') for arg in args)
