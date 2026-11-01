"""Publish reviewed source and verified offline volumes to a private GitHub release."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository):
        raise RuntimeError('Invalid repository name.')
    gh = ROOT / '.tools/github-cli/bin/gh.exe'
    if not gh.is_file():
        raise RuntimeError('Install GitHub CLI and log in first.')
    env = {**os.environ, 'GH_CONFIG_DIR': str(ROOT / '.tools/github-auth'),
           'GH_PROMPT_DISABLED': '1', 'GH_PAGER': 'cat', 'GIT_TERMINAL_PROMPT': '0'}
    git = shutil.which('git')
    if not git:
        raise RuntimeError('Git is required on the build/publish computer.')

    def run(command, *, check=True):
        result = subprocess.run([str(c) for c in command], cwd=ROOT, env=env,
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result

    login = json.loads(run([gh, 'api', 'user']).stdout)['login']
    if args.repository.split('/')[0].lower() != login.lower():
        raise RuntimeError('Authenticated account does not own the requested repository.')
    bundle = args.bundle.resolve()
    if not bundle.is_relative_to(ROOT / 'dist'):
        raise RuntimeError('Only verified project release artifacts may be published.')
    manifest = json.loads((bundle / 'offline-manifest.json').read_text(encoding='utf-8'))
    verification = json.loads((bundle / 'verification.json').read_text(encoding='utf-8'))
    if not verification.get('passed'):
        raise RuntimeError('Offline release verification has not passed.')
    version = manifest['version']
    tag = 'v' + version

    # The repository contains only application source, tests and build tooling.
    if not (ROOT / '.git').exists():
        run([git, 'init', '--initial-branch=main'])
    run([git, 'config', 'user.name', login])
    run([git, 'config', 'user.email', f'{json.loads(run([gh,"api","user"]).stdout)["id"]}+{login}@users.noreply.github.com'])
    helper = '!"' + gh.as_posix() + '" auth git-credential'
    run([git, 'config', 'credential.https://github.com.helper', helper])
    selected = ['.gitignore', 'README.md', 'THIRD_PARTY_NOTICES.md', 'VERSION', 'requirements.txt',
                'voiceclone', 'web', 'scripts', 'tests', 'distribution']
    selected += [p.name for p in ROOT.glob('*.py')]
    selected += [p.name for p in ROOT.glob('*.pyw')]
    selected += [p.name for p in ROOT.glob('*.cmd')]
    selected += ['config.json']
    run([git, 'add', '--', *selected])
    tracked = run([git, 'ls-files', '-z']).stdout.split('\0')
    forbidden = {'data', 'logs', '.tools', '.cache', '.venv', 'runtime', 'models', 'engines', 'dist'}
    for name in filter(None, tracked):
        if name.split('/')[0] in forbidden or (ROOT / name).stat().st_size > 10 * 1024**2:
            raise RuntimeError('Private or oversized file would enter Git: ' + name)
        raw = (ROOT / name).read_bytes()
        if re.search(rb'gh[opusr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}', raw):
            raise RuntimeError('A credential-like value was found in staged source: ' + name)
    diff = run([git, 'diff', '--cached', '--quiet'], check=False)
    if diff.returncode:
        run([git, 'commit', '-m', 'Package complete Windows offline voice studio v' + version])

    found = run([gh, 'repo', 'view', args.repository, '--json', 'isPrivate,url'], check=False)
    if found.returncode:
        run([gh, 'repo', 'create', args.repository, '--private', '--source', str(ROOT), '--remote', 'origin',
             '--description', 'Windows offline IndexTTS 2.5 voice studio with three takes, transcripts, cropping and stem separation'])
    elif not json.loads(found.stdout)['isPrivate']:
        raise RuntimeError('Refusing to publish private artifacts to a public repository.')
    expected_remote = 'https://github.com/' + args.repository + '.git'
    remote = run([git, 'remote', 'get-url', 'origin'], check=False)
    if remote.returncode:
        run([git, 'remote', 'add', 'origin', expected_remote])
    elif remote.stdout.strip() != expected_remote:
        raise RuntimeError('Origin points to a different repository; refusing to overwrite it.')
    run([git, 'push', '-u', 'origin', 'main'])
    if run([git, 'rev-parse', '--verify', tag], check=False).returncode:
        run([git, 'tag', '-a', tag, '-m', 'Verified Windows offline release ' + version])
    run([git, 'push', 'origin', tag])
    existing = run([gh, 'release', 'view', tag, '--repo', args.repository, '--json', 'isDraft'], check=False)
    if existing.returncode:
        run([gh, 'release', 'create', tag, '--repo', args.repository, '--verify-tag', '--draft',
             '--title', '回声 · Windows 离线版 ' + version,
             '--notes-file', ROOT / 'distribution/RELEASE_NOTES.md'])
    elif not json.loads(existing.stdout)['isDraft']:
        raise RuntimeError('The release is already published; this tool does not overwrite a published release.')

    assets = sorted(p for p in bundle.iterdir() if p.is_file())
    for path in assets:
        if path.stat().st_size >= 2**31:
            raise RuntimeError('A release asset exceeds the GitHub limit: ' + path.name)
    # Separate assets may upload independently. A failed upload is retried on
    # the next invocation, while completed, matching assets are left untouched.
    def release_info():
        # The by-tag REST endpoint only returns published releases. A draft
        # must be located in the authenticated owner's release list instead.
        items = json.loads(run([gh, 'api', f'repos/{args.repository}/releases?per_page=100']).stdout)
        return next(item for item in items if item['tag_name'] == tag)

    info = release_info()
    present = {a['name']: a for a in info['assets']}
    tasks = [p for p in assets if p.name not in present]
    for path in assets:
        if path.name in present and present[path.name]['size'] != path.stat().st_size:
            raise RuntimeError('Existing release asset differs; it was not overwritten: ' + path.name)
    print(json.dumps({'repository': args.repository, 'private': True, 'release': tag,
                      'assets_to_upload': len(tasks), 'total_GiB': round(sum(p.stat().st_size for p in tasks)/2**30,2)}), flush=True)

    def upload(path):
        run([gh, 'release', 'upload', tag, str(path), '--repo', args.repository])
        return path.name

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(upload, path) for path in tasks]
        for future in as_completed(futures):
            print('Uploaded: ' + future.result(), flush=True)
    info = release_info()
    present = {a['name']: a for a in info['assets']}
    local_digests = dict(line.split(' *', 1)[::-1] for line in (bundle / 'SHA256SUMS.txt').read_text().splitlines() if ' *' in line)
    for path in assets:
        asset = present.get(path.name)
        if not asset or asset['size'] != path.stat().st_size:
            raise RuntimeError('Release asset verification failed: ' + path.name)
        if asset.get('digest') and path.name in local_digests and asset['digest'] != 'sha256:' + local_digests[path.name]:
            raise RuntimeError('Remote release checksum mismatch: ' + path.name)
    run([gh, 'release', 'edit', tag, '--repo', args.repository, '--draft=false'])
    print('Published private release: https://github.com/' + args.repository + '/releases/tag/' + tag, flush=True)


if __name__ == '__main__':
    main()
