#!/usr/bin/env python3
"""Build a single offline Windows setup EXE with NSIS (host-side only)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows-publish', type=Path, required=True)
    parser.add_argument('--dotnet-notices', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ingest', type=Path)
    parser.add_argument('--ingest-license', type=Path)
    parser.add_argument('--makensis', default='makensis')
    parser.add_argument('--nsis-dir', type=Path)
    args = parser.parse_args()
    version = ET.parse(ROOT / 'windows/DeadlockSync/DeadlockSync.csproj').findtext('.//Version')
    if not version or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise SystemExit('Expected a three-part application version')
    pin = json.loads((ROOT / 'upstream.json').read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    destination = (args.output / f'Deadlock-Sync-{version}-Setup.exe').resolve()
    with tempfile.TemporaryDirectory(prefix='deadlock-setup-') as stage:
        stage = Path(stage)
        payload = stage / 'payload'
        payload.mkdir()
        app = args.windows_publish / 'DeadlockSync.exe'
        if app.is_symlink() or not app.is_file():
            raise SystemExit('Missing self-contained Windows build')
        # The release build is deliberately one self-contained file; no build/source trees.
        shutil.copyfile(app, payload / 'DeadlockSync.exe')
        engine = payload / 'ingest/deadlock-api-ingest.exe'
        engine.parent.mkdir()
        if args.ingest:
            shutil.copyfile(args.ingest, engine)
        else:
            with urllib.request.urlopen(pin['asset']['url'], timeout=90) as response, engine.open('wb') as output:
                shutil.copyfileobj(response, output)
        if digest(engine) != pin['asset']['sha256'] or engine.stat().st_size != pin['asset']['size']:
            raise SystemExit('Ingest engine failed pinned hash/size verification')
        license_file = payload / 'INGEST-LICENSE.txt'
        if args.ingest_license:
            shutil.copyfile(args.ingest_license, license_file)
        else:
            with urllib.request.urlopen(pin['license']['url'], timeout=30) as response:
                license_file.write_bytes(response.read(100_000))
        if digest(license_file) != pin['license']['sha256']:
            raise SystemExit('Upstream license failed pinned hash verification')
        for name in ('LICENSE', 'THIRD-PARTY-NOTICES.md', 'upstream.json'):
            shutil.copyfile(ROOT / name, payload / name)
        shutil.copyfile(ROOT / 'windows/installer/NSIS-NOTICES.txt', payload / 'NSIS-NOTICES.txt')
        for source, target in (('LICENSE.txt', 'DOTNET-LICENSE.txt'),
                               ('ThirdPartyNotices.txt', 'DOTNET-THIRD-PARTY-NOTICES.txt')):
            shutil.copyfile(args.dotnet_notices / source, payload / target)
        files = sorted(path for path in payload.rglob('*') if path.is_file())
        deletes = stage / 'delete-files.nsh'
        deletes.write_text(''.join('Delete "$INSTDIR\\app\\' + str(path.relative_to(payload)).replace('/', '\\') + '"\n'
                                   for path in files))
        environment = os.environ.copy()
        if args.nsis_dir:
            environment['NSISDIR'] = str(args.nsis_dir.resolve())
        subprocess.run([args.makensis, '-V2', '-NOCD', '-NOCONFIG',
                        f'-DVERSION={version}', f'-DPAYLOAD={payload}',
                        f'-DPAYLOAD_KIB={(sum(p.stat().st_size for p in files) + 1023) // 1024}',
                        f'-DDELETE_FILES={deletes}', f'-DOUTPUT={destination}',
                        str(ROOT / 'windows/installer/DeadlockSync.nsi')],
                       env=environment, check=True)
    print(json.dumps({'file': str(destination), 'size': destination.stat().st_size,
                      'sha256': digest(destination), 'app_sha256': digest(app)}))


if __name__ == '__main__':
    main()
