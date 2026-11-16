#!/usr/bin/env python3
"""Validate maintained sources and isolated tests; --qml also exercises the UI."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qml', action='store_true', help='Run the isolated offscreen Quickshell widget check (Omarchy required)')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'sources.json').read_text())
    expected = {item['path'] for item in manifest['files']}
    actual = {str(path.relative_to(ROOT)) for path in ROOT.rglob('*') if path.is_file()
              and path.name != 'sources.json' and '__pycache__' not in path.parts and path.suffix != '.pyc'}
    if expected != actual:
        raise SystemExit('Source manifest file list differs: ' + str(expected ^ actual))
    for item in manifest['files']:
        path = ROOT / item['path']
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise SystemExit('Source hash mismatch: ' + item['path'])
        if path.stat().st_mode & 0o777 != int(item['mode'], 8):
            raise SystemExit('Source mode mismatch: ' + item['path'])
        if path.suffix == '.py' or path.parent == ROOT / 'scripts':
            ast.parse(path.read_text(), filename=str(path))
    subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'), '-p', 'test_*.py'], check=True)
    subprocess.run(['node', str(ROOT / 'tests/model.test.cjs')], check=True)
    if args.qml:
        subprocess.run([sys.executable, '-B', str(ROOT / 'tests/qml/run.py')], check=True)
    print('Verified', len(expected), 'maintained Deadlock sync sources; no live services changed.')


if __name__ == '__main__':
    main()
