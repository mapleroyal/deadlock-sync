#!/usr/bin/env python3
"""Package explicit release inputs; never walk account/runtime directories."""
import argparse
import hashlib
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--windows-publish', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dotnet-notices', type=Path, required=True)
    args = parser.parse_args()
    if not args.version or any(c not in '0123456789.-abcdefghijklmnopqrstuvwxyz' for c in args.version):
        parser.error('Version must be a simple release identifier')
    args.output.mkdir(parents=True, exist_ok=True)
    windows = args.output / f'deadlock-sync-{args.version}-windows-x64.zip'
    linux = args.output / f'deadlock-sync-{args.version}-linux-omarchy-x64.tar.gz'
    common = ['LICENSE', 'THIRD-PARTY-NOTICES.md', 'GUIDE.md', 'UPSTREAM.md', 'VALIDATION.md']
    entries = [(ROOT / name, name) for name in common]
    entries += [(ROOT / 'windows' / 'README.md', 'README.md'), (ROOT / 'upstream.json', 'upstream.json')]
    for name in ('Install.ps1', 'Uninstall.ps1'):
        entries.append((ROOT / 'windows/scripts' / name, name))
    for name in ('LICENSE.txt', 'ThirdPartyNotices.txt'):
        target = 'DOTNET-LICENSE.txt' if name == 'LICENSE.txt' else 'DOTNET-THIRD-PARTY-NOTICES.txt'
        entries.append((args.dotnet_notices / name, target))
    if not (args.windows_publish / 'DeadlockSync.exe').is_file():
        raise SystemExit('Missing published Windows app')
    for path in sorted(args.windows_publish.iterdir()):
        if path.is_symlink() or path.is_dir():
            raise SystemExit('Unexpected publish input: ' + str(path))
        if path.suffix.lower() != '.pdb':
            entries.append((path, 'app/' + path.name))
    with zipfile.ZipFile(windows, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, name in entries:
            if path.is_symlink() or not path.is_file():
                raise SystemExit('Invalid release input: ' + str(path))
            archive.write(path, name)
    with tarfile.open(linux, 'w:gz') as archive:
        for name in common:
            archive.add(ROOT / name, arcname=name, recursive=False)
        archive.add(ROOT / 'upstream.json', arcname='upstream.json', recursive=False)
        archive.add(ROOT / 'linux/README.md', arcname='README.md', recursive=False)
        for path in sorted((ROOT / 'linux/omarchy').rglob('*')):
            if path.is_symlink():
                raise SystemExit('Unexpected Linux symlink: ' + str(path))
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                archive.add(path, arcname=str(path.relative_to(ROOT / 'linux')), recursive=False)
    sums = []
    for path in (windows, linux):
        digest = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
        sums.append(f'{digest}  {path.name}\n')
        print(path.name, path.stat().st_size, digest)
    (args.output / 'SHA256SUMS.txt').write_text(''.join(sums))


if __name__ == '__main__':
    main()
