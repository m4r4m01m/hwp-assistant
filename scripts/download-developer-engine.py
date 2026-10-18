"""Download the hash-pinned developer engine. Credentials stay in memory, API-host only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', default='.cache/developer/downloads')
args = parser.parse_args()
lock = json.loads((ROOT / 'scripts/developer-engine.lock.json').read_text(encoding='utf-8-sig'))
repository, tag, asset_name, expected = (lock[key] for key in ('repository', 'tag', 'asset', 'sha256'))
if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
    raise SystemExit('Invalid developer engine repository lock.')
if not re.fullmatch(r'[a-f0-9]{64}', expected) or '/' in asset_name or '\\' in asset_name:
    raise SystemExit('Invalid developer engine archive lock.')
folder = (ROOT / args.out).resolve()
if not folder.is_relative_to((ROOT / '.cache').resolve()) or folder == (ROOT / '.cache').resolve():
    raise SystemExit('Downloads must use a dedicated directory under .cache.')
folder.mkdir(parents=True, exist_ok=True)
destination = folder / asset_name

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def github_credential():
    # The repository is private. Use the developer's existing clone identity.
    # Never persist the response, pass a secret on a command line, or print it.
    environment = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'}
    try:
        result = subprocess.run(['git', 'credential', 'fill'], cwd=ROOT, env=environment,
                                input=f'protocol=https\nhost=github.com\npath={repository}.git\n\n',
                                text=True, capture_output=True, timeout=15)
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if result.returncode == 0 and fields.get('password'):
            return fields['password']
    except (OSError, subprocess.TimeoutExpired):
        pass
    if shutil.which('gh'):
        try:
            result = subprocess.run(['gh', 'auth', 'token', '--hostname', 'github.com'], cwd=ROOT,
                                    capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        allowed = {'api.github.com', 'release-assets.githubusercontent.com',
                   'objects.githubusercontent.com', 'github-production-release-asset-2e65be.s3.amazonaws.com'}
        if parsed.scheme != 'https' or parsed.hostname not in allowed or parsed.port not in (None, 443) or parsed.username or parsed.password:
            raise RuntimeError('Unexpected engine asset redirect; credential forwarding blocked.')
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if parsed.hostname != 'api.github.com':
            redirected.remove_header('Authorization')
        return redirected

if destination.exists():
    if digest(destination) != expected:
        raise SystemExit('Preserving an existing archive with a different hash. Move it aside and rerun.')
else:
    credential = github_credential()
    opener = urllib.request.build_opener(SafeRedirect())

    def request_api(url, accept='application/vnd.github+json'):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'api.github.com' or parsed.port not in (None, 443):
            raise RuntimeError('Engine API URL must use api.github.com over HTTPS.')
        headers = {'Accept': accept, 'User-Agent': 'hwp-assistant-developer-setup', 'X-GitHub-Api-Version': '2022-11-28'}
        if credential:
            headers['Authorization'] = 'Bearer ' + credential
        return opener.open(urllib.request.Request(url, headers=headers), timeout=30)

    try:
        with request_api(f'https://api.github.com/repos/{repository}/releases/tags/{urllib.parse.quote(tag, safe="")}') as response:
            release = json.load(response)
        matches = [asset for asset in release.get('assets', []) if asset.get('name') == asset_name]
        if len(matches) != 1:
            raise RuntimeError('The pinned engine asset is missing from the release.')
        asset = matches[0]
        if lock.get('asset_id') and asset['id'] != lock['asset_id']:
            raise RuntimeError('Engine asset identity differs from the checked-in lock.')
        if asset.get('size', 0) <= 0 or asset['size'] > 250 * 1024 * 1024:
            raise RuntimeError('Engine asset size is invalid.')
        with tempfile.NamedTemporaryFile(prefix='engine-', suffix='.download', dir=folder, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                with request_api(f'https://api.github.com/repos/{repository}/releases/assets/{asset["id"]}', 'application/octet-stream') as response:
                    transferred = 0
                    while block := response.read(1024 * 1024):
                        transferred += len(block)
                        if transferred > 250 * 1024 * 1024:
                            raise RuntimeError('Engine download exceeds size budget.')
                        temporary.write(block)
                temporary.flush()
                os.fsync(temporary.fileno())
            except BaseException:
                temporary.close()
                temporary_path.unlink(missing_ok=True)
                raise
        if digest(temporary_path) != expected:
            temporary_path.unlink()
            raise RuntimeError('Engine archive SHA-256 differs from the checked-in lock.')
        if destination.exists():
            raise RuntimeError('Engine archive appeared during download; preserving both files.')
        temporary_path.rename(destination)
    except urllib.error.HTTPError as error:
        raise SystemExit(f'GitHub engine download HTTP {error.code}. For this private repository, sign in with your own Git Credential Manager or gh auth login, then rerun. Offline kit/native/Docker alternatives: docs/developer-quickstart.md.') from None
    finally:
        credential = None

checksum = destination.with_name(destination.name + '.sha256')
if checksum.exists() and checksum.read_text().strip().lower() != expected:
    raise SystemExit('Existing engine checksum differs; it was preserved.')
if not checksum.exists():
    checksum.write_text(expected + '\n', encoding='ascii')
print(json.dumps({'archive': str(destination), 'sha256': expected, 'model_calls': 0}))
