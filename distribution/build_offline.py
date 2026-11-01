"""Build independent sub-2-GiB ZIP volumes from the installed, working project.

No personal audio, credentials, user caches, or absolute virtualenv paths are copied.
Large files are streamed as ordered chunks and restored by Install-Offline.ps1.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_NAMES = {'.git', '__pycache__', '.pytest_cache', '.mypy_cache', '.msc', '.mv',
                  '._____temp', '.DS_Store', '_editable_impl_indextts.pth'}
EXCLUDED_SUFFIXES = {'.pyc', '.pyo', '.nbc', '.nbi', '.part', '.egg-link'}


@dataclass
class Entry:
    name: str
    source: Path | None = None
    data: bytes | None = None

    @property
    def size(self):
        return len(self.data) if self.data is not None else self.source.stat().st_size


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def allowed(path):
    return not any(part in EXCLUDED_NAMES for part in path.parts) and path.suffix not in EXCLUDED_SUFFIXES


def tree(source, prefix, *, skip=()):
    for folder, directories, files in os.walk(source):
        directories[:] = sorted(d for d in directories if d not in EXCLUDED_NAMES and d not in skip)
        for name in sorted(files):
            path = Path(folder) / name
            relative = path.relative_to(source)
            if allowed(relative):
                yield Entry((Path(prefix) / relative).as_posix(), source=path)


def collect_entries(root, stage):
    entries = []
    for name in ['app.py', 'config.json', 'desktop_launcher.pyw', 'README.md',
                 'requirements.txt', 'VERSION', 'THIRD_PARTY_NOTICES.md']:
        entries.append(Entry(name, source=root / name))
    for directory in ['voiceclone', 'web', 'scripts', 'distribution']:
        entries.extend(tree(root / directory, directory))
    entries.append(Entry('README-OFFLINE.md', source=root / 'distribution/README-OFFLINE.md'))
    entries.append(Entry('音色工作室.exe', source=stage / '音色工作室.exe'))
    entries.append(Entry('_prerequisites/VC_redist.x64.exe', source=root / '.tools/redist/VC_redist.x64.exe'))
    entries.extend(tree(root / '.tools/ffmpeg', '.tools/ffmpeg'))
    entries.extend(tree(root / 'models', 'models'))
    repo = root / 'engines/index'
    for directory in ['indextts', 'third_party', 'checkpoints']:
        for entry in tree(repo / directory, 'engines/index/' + directory):
            # An unused upstream alternative to the actual model.safetensors.
            if entry.name.endswith('/w2v-bert-2.0/conformer_shaw.pt'):
                continue
            entries.append(entry)
    for name in ['LICENSE', 'LICENSE_ZH.txt', 'README.md', 'pyproject.toml', 'local-install.json']:
        if (repo / name).is_file():
            entries.append(Entry('engines/index/' + name, source=repo / name))
    base = Path(sys.base_prefix)
    for role, site in [('app', root / '.venv/Lib/site-packages'),
                       ('engine', repo / '.venv/Lib/site-packages')]:
        prefix = 'runtime/' + role
        for entry in tree(base, prefix):
            relative = Path(entry.name).relative_to(prefix)
            if relative.parts[:2] == ('Lib', 'site-packages') or relative.parts[0] == 'Scripts':
                continue
            entries.append(entry)
        for entry in tree(site, prefix + '/Lib/site-packages'):
            if entry.name.endswith('/direct_url.json'):
                # Editable installer bookkeeping is not used at runtime and can
                # otherwise disclose the original developer's absolute path.
                continue
            entries.append(entry)
        paths = ['python311.zip', '.', 'Lib', 'DLLs', r'Lib\site-packages', r'..\..']
        if role == 'engine':
            paths.append(r'..\..\engines\index')
        entries.append(Entry(prefix + '/python311._pth', data=('\n'.join(paths) + '\nimport site\n').encode()))
    version = (root / 'VERSION').read_text(encoding='utf-8').strip()
    metadata = {'version': version, 'offline': True, 'platform': 'Windows-x86_64',
                'python': sys.version.split()[0], 'engine': 'IndexTTS-2.5',
                'features': ['three-sequential-takes', 'local-transcription', 'waveform-crop',
                             'noise-reduction', 'music-vocal-separation', 'background-export',
                             'resident-gpu-model', 'desktop-launcher']}
    entries.append(Entry('offline-package.json', data=json.dumps(metadata, ensure_ascii=False, indent=2).encode()))
    # Windows PowerShell 5.1 requires a BOM to read non-ASCII script source.
    for entry in entries:
        if entry.name.endswith('.ps1'):
            entry.data = entry.source.read_text(encoding='utf-8-sig').encode('utf-8-sig')
            entry.source = None
    seen = set()
    for entry in entries:
        if entry.name in seen:
            raise RuntimeError('Duplicate payload path: ' + entry.name)
        seen.add(entry.name)
        if entry.name.split('/')[0] in {'data', 'logs', '.cache', '.git'} or 'github-auth' in entry.name:
            raise RuntimeError('Disallowed private data path: ' + entry.name)
        if entry.source is not None and not entry.source.is_file():
            raise FileNotFoundError(entry.source)
    return sorted(entries, key=lambda item: item.name)


def compile_launchers(root, stage):
    csc = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    for name, source in [('Install.exe', 'SetupLauncher.cs'), ('音色工作室.exe', 'StudioLauncher.cs')]:
        subprocess.run([str(csc), '/nologo', '/target:winexe', '/platform:x64', '/codepage:65001',
                        '/reference:System.Windows.Forms.dll', '/out:' + str(stage / name),
                        str(root / 'distribution' / source)], check=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def refresh_candidate(entries, output, root, stage):
    """Refresh application/bootstrap files in an unpublished candidate, not weights."""
    manifest_path = output / 'offline-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    inventory_path = output / 'payload-inventory.json'
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    by_name = {entry.name: entry for entry in entries}
    inventory_by_name = {entry['path']: entry for entry in inventory}
    total_delta = 0
    for volume in manifest['volumes']:
        path = output / volume['name']
        changes = {}
        with zipfile.ZipFile(path) as old:
            for info in old.infolist():
                if not info.filename.startswith('payload/'):
                    continue
                name = info.filename[8:]
                relevant = (name in ['app.py','desktop_launcher.pyw','README.md','THIRD_PARTY_NOTICES.md','音色工作室.exe']
                            or name.startswith(('voiceclone/','web/','scripts/','distribution/'))
                            or name.endswith('/python311._pth'))
                if relevant and name in by_name and info.file_size < 5 * 1024**2:
                    entry = by_name[name]
                    replacement = entry.data if entry.data is not None else entry.source.read_bytes()
                    if old.read(info) != replacement:
                        changes[info.filename] = replacement
                        total_delta += len(replacement) - info.file_size
                        inventory_by_name[name]['bytes'] = len(replacement)
            if not changes:
                continue
            temporary = path.with_suffix('.refresh.tmp')
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as new:
                for info in old.infolist():
                    fresh = zipfile.ZipInfo(info.filename,info.date_time)
                    fresh.compress_type = zipfile.ZIP_DEFLATED
                    fresh._compresslevel = 3
                    fresh.external_attr = info.external_attr
                    with new.open(fresh,'w',force_zip64=True) as target:
                        if info.filename in changes:
                            target.write(changes[info.filename])
                        else:
                            with old.open(info) as source:
                                shutil.copyfileobj(source,target,length=8*1024*1024)
        temporary.replace(path)
        volume.update(bytes=path.stat().st_size,sha256=digest_file(path))
        if volume['bytes'] >= 2**31:
            raise RuntimeError('Refreshed candidate volume exceeds GitHub limit.')
        print(json.dumps({'refreshed':path.name,'files':len(changes)}),flush=True)
    manifest['installed_bytes'] += total_delta
    manifest['package_id'] = str(uuid.uuid4())
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    inventory_path.write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    shutil.copy2(stage / 'Install.exe',output / 'Install.exe')
    (output / 'Install-Offline.ps1').write_text((root/'distribution/Install-Offline.ps1').read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
    (output / 'verification.json').unlink(missing_ok=True)
    sums=[digest_file(p)+' *'+p.name for p in sorted(output.iterdir()) if p.is_file() and p.name!='SHA256SUMS.txt']
    (output/'SHA256SUMS.txt').write_text('\n'.join(sums)+'\n',encoding='utf-8')
    print('Candidate refreshed; fresh verification is required before publishing.',flush=True)


def write_volumes(entries, output, version, volume_bytes, chunk_bytes):
    chunks, volumes, inventory = [], [], []
    archive = None
    budget = 0
    processed = 0
    started = time.monotonic()
    total = sum(entry.size for entry in entries)

    def close_archive():
        nonlocal archive
        if archive is None:
            return
        path = Path(archive.filename)
        archive.close()
        volumes.append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest_file(path)})
        if path.stat().st_size >= 2**31:
            raise RuntimeError('Volume exceeds the GitHub per-asset limit: ' + path.name)
        print(json.dumps({'volume': path.name, 'MiB': round(path.stat().st_size / 2**20, 1),
                          'progress': round(processed / max(1, total) * 100, 1),
                          'elapsed_seconds': round(time.monotonic() - started)}), flush=True)
        archive = None

    try:
        for file_index, entry in enumerate(entries):
            size = entry.size
            inventory.append({'path': entry.name, 'bytes': size})
            pieces = max(1, (size + chunk_bytes - 1) // chunk_bytes)
            if entry.data is not None:
                import io
                source = io.BytesIO(entry.data)
            else:
                source = entry.source.open('rb')
            with source:
                for piece in range(pieces):
                    length = min(chunk_bytes, size - piece * chunk_bytes)
                    if archive is None or budget + length > volume_bytes:
                        close_archive()
                        name = f'HuishengVoiceStudio-{version}.part{len(volumes)+1:02d}.zip'
                        archive = zipfile.ZipFile(output / name, 'w', compression=zipfile.ZIP_DEFLATED,
                                                  compresslevel=3, allowZip64=True)
                        budget = 0
                    if pieces == 1:
                        name = 'payload/' + entry.name
                    else:
                        name = f'__chunks__/{file_index:06d}/{piece:04d}'
                        chunks.append({'entry': name, 'path': entry.name,
                                       'offset': piece * chunk_bytes, 'bytes': length})
                    remaining = length
                    with archive.open(name, 'w', force_zip64=True) as target:
                        while remaining:
                            data = source.read(min(8 * 1024 * 1024, remaining))
                            if not data:
                                raise RuntimeError('Source file changed during packaging: ' + entry.name)
                            target.write(data)
                            remaining -= len(data)
                    budget += length
                    processed += length
        close_archive()
    finally:
        if archive is not None:
            archive.close()
    return volumes, chunks, inventory, total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--stage-test', type=Path)
    parser.add_argument('--refresh-candidate', type=Path)
    args = parser.parse_args()
    root = ROOT
    version = (root / 'VERSION').read_text(encoding='utf-8').strip()
    stage = root / '.cache/offline-build' / version
    stage.mkdir(parents=True, exist_ok=True)
    compile_launchers(root, stage)
    entries = collect_entries(root, stage)
    total = sum(entry.size for entry in entries)
    print(json.dumps({'files': len(entries), 'installed_GiB': round(total / 2**30, 2), 'version': version}), flush=True)
    if args.refresh_candidate:
        output = args.refresh_candidate.resolve()
        if not output.is_relative_to(root / 'dist'):
            raise RuntimeError('Candidate refresh must stay inside project dist.')
        refresh_candidate(entries,output,root,stage)
        return
    if args.stage_test:
        target = args.stage_test.resolve()
        if not target.is_relative_to(root / '.cache'):
            raise RuntimeError('Test staging must stay inside the project cache.')
        target.mkdir(parents=True, exist_ok=False)
        for index, entry in enumerate(entries):
            path = target / entry.name
            path.parent.mkdir(parents=True, exist_ok=True)
            if entry.data is not None:
                path.write_bytes(entry.data)
            else:
                try:
                    os.link(entry.source, path)
                except OSError:
                    shutil.copy2(entry.source, path)
            if index and index % 10000 == 0:
                print('Staged files:', index, flush=True)
        print('Relocated test payload:', target, flush=True)
        return
    if args.inventory_only:
        (stage / 'inventory.json').write_text(json.dumps([{'path': e.name, 'bytes': e.size} for e in entries], ensure_ascii=False, indent=2), encoding='utf-8')
        return
    output = (args.output or root / 'dist' / ('offline-' + version)).resolve()
    if not output.is_relative_to(root):
        raise RuntimeError('Build output must stay inside the project workspace.')
    output.mkdir(parents=True, exist_ok=False)
    if shutil.disk_usage(output).free < total * .9:
        raise RuntimeError('Not enough free space for offline build output.')
    volumes, chunks, inventory, installed = write_volumes(entries, output, version, int(1.7 * 2**30), 1024**3)
    manifest = {'format': 1, 'version': version, 'package_id': str(uuid.uuid4()),
                'installed_bytes': installed, 'file_count': len(entries),
                'volumes': volumes, 'chunks': chunks}
    (output / 'offline-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'payload-inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(stage / 'Install.exe', output / 'Install.exe')
    (output / 'Install-Offline.ps1').write_text((root / 'distribution/Install-Offline.ps1').read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
    for name, source in [('README-OFFLINE.md', root / 'distribution/README-OFFLINE.md'),
                         ('THIRD_PARTY_NOTICES.md', root / 'THIRD_PARTY_NOTICES.md'),
                         ('IndexTTS-LICENSE.txt', root / 'engines/index/LICENSE'),
                         ('IndexTTS-LICENSE-ZH.txt', root / 'engines/index/LICENSE_ZH.txt')]:
        shutil.copy2(source, output / name)
    sums = [digest_file(p) + ' *' + p.name for p in sorted(output.iterdir()) if p.is_file()]
    (output / 'SHA256SUMS.txt').write_text('\n'.join(sums) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'volumes': len(volumes),
                      'compressed_GiB': round(sum(v['bytes'] for v in volumes) / 2**30, 2)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
