#!/usr/bin/env python3
"""Package explicit release inputs; never walk account/runtime directories."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--windows-installer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.version or any(c not in '0123456789.-abcdefghijklmnopqrstuvwxyz' for c in args.version):
        parser.error('Version must be a simple release identifier')
    args.output.mkdir(parents=True, exist_ok=True)
    windows = args.output / f'Deadlock-Sync-{args.version}-Setup.exe'
    linux = args.output / f'deadlock-sync-{args.version}-linux-omarchy-x64.tar.gz'
    common = ['LICENSE', 'THIRD-PARTY-NOTICES.md', 'GUIDE.md', 'UPSTREAM.md', 'VALIDATION.md']
    if (args.windows_installer.is_symlink() or not args.windows_installer.is_file()
            or args.windows_installer.name != windows.name):
        raise SystemExit('Expected the matching versioned Windows setup EXE')
    with args.windows_installer.open('rb') as handle:
        if handle.read(2) != b'MZ':
            raise SystemExit('Windows installer is not a PE executable')
    if args.windows_installer.resolve() != windows.resolve():
        shutil.copyfile(args.windows_installer, windows)
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
        with path.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        sums.append(f'{digest}  {path.name}\n')
        print(path.name, path.stat().st_size, digest)
    (args.output / 'SHA256SUMS.txt').write_text(''.join(sums))


if __name__ == '__main__':
    main()
