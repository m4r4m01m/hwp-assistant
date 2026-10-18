"""Prepare pinned engine build sources, preserving all unrelated work and documents."""
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import argparse
import json

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / '.cache/rhwp'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', default='.cache/rhwp-core-fix')
args = parser.parse_args()
SOURCE = (ROOT / args.source).resolve()
if not SOURCE.is_relative_to((ROOT / '.cache').resolve()) or SOURCE == (ROOT / '.cache').resolve():
    raise SystemExit('Build source must be a dedicated directory inside .cache')
LOCK = json.loads((ROOT / 'scripts/engine-lock.json').read_text())
PIN = LOCK['commit']
PATCH = ROOT / f"scripts/patches/{LOCK['patch']}.patch"
SELECTION_PATCH = ROOT / f"scripts/patches/{LOCK['selection_patch']}.patch"
PATCH_FILES = {
    PATCH: ['src/serializer/hwpx/header.rs', 'src/serializer/hwpx/shape.rs', 'src/serializer/hwpx/char_shapes.rs'],
    SELECTION_PATCH: ['src/document_core/queries/cursor_nav.rs'],
}
PATCHED_FILES = [name for names in PATCH_FILES.values() for name in names]
paths = ['Cargo.toml', 'Cargo.lock', 'build.rs', 'rust-toolchain.toml', 'rustfmt.toml',
         'src', 'crates', 'examples', 'saved/blank2010.hwp', 'tools/rhwp-subsecond',
         'bindings/Native', 'tools/batch-convert', 'tools/llm_verifier/verdict_protocol',
         'tools/llm_verifier/claim_bind', 'tools/llm_verifier/criteria_decomp',
         'scripts/wasm-pack-locked.sh', 'README.md', 'LICENSE']

def git(*args):
    return subprocess.check_output(['git', '-C', str(REPO), *args])

subprocess.run(['node', '--input-type=module', '-e',
                "import { ensureSource } from './scripts/engine-build-lib.mjs'; ensureSource();"],
               cwd=ROOT, check=True)
if git('rev-parse', 'HEAD').decode().strip() != PIN:
    raise SystemExit('Vendor checkout differs from the pinned commit. No files changed.')
archive = git('archive', '--format=tar', PIN, *paths)
SOURCE.mkdir(parents=True, exist_ok=True)
pending = []
patch_hash = hashlib.sha256(PATCH.read_bytes()).hexdigest()
if patch_hash != LOCK['patch_sha256']:
    raise SystemExit('Patch does not match engine-lock.json')
selection_hash = hashlib.sha256(SELECTION_PATCH.read_bytes()).hexdigest()
if selection_hash != LOCK['selection_patch_sha256']:
    raise SystemExit('Selection patch does not match engine-lock.json')
combined_hash = hashlib.sha256((patch_hash + selection_hash).encode()).hexdigest()
expected_root = ROOT / '.cache/rhwp-patch-check' / combined_hash
if not (expected_root / 'verified').exists():
    for name in PATCHED_FILES:
        target = expected_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            output.write(git('show', f'{PIN}:{name}'))
    for patch_file in PATCH_FILES:
        subprocess.run(['git', 'apply', '--no-index',
                        '--directory=' + expected_root.relative_to(ROOT).as_posix(), str(patch_file)],
                       cwd=ROOT, check=True)
    (expected_root / 'verified').write_text(combined_hash)
expected = {name: (expected_root / name).read_bytes().replace(b'\r\n', b'\n') for name in PATCHED_FILES}
for name, content in expected.items():
    if hashlib.sha256(content).hexdigest() != LOCK['source_sha256'][Path(name).name]:
        raise SystemExit(f'Unexpected patched source: {name}')
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    for member in tar.getmembers():
        if member.isdir():
            continue
        if not member.isfile():
            raise SystemExit(f'Unexpected source entry: {member.name}')
        target = (SOURCE / member.name).resolve()
        if not target.is_relative_to(SOURCE.resolve()):
            raise SystemExit('Unsafe archive path')
        data = tar.extractfile(member).read()
        if target.exists() and target.read_bytes() != data:
            if member.name not in PATCHED_FILES:
                raise SystemExit(f'Preserving modified build source: {member.name}')
            if target.read_bytes().replace(b'\r\n', b'\n') != expected[member.name]:
                raise SystemExit(f'Preserving unknown source changes: {member.name}')
            # Only the exact reverse of our checked-in patch is allowed.
            owning_patch = next(patch for patch, names in PATCH_FILES.items() if member.name in names)
            check = subprocess.run(['git', 'apply', '--no-index', '--reverse', '--check',
                                    '--directory=' + SOURCE.relative_to(ROOT).as_posix(), str(owning_patch)], cwd=ROOT,
                                   capture_output=True)
            if check.returncode:
                raise SystemExit('Preserving unknown source changes; patch verification failed')
            continue
        if not target.exists():
            pending.append((target, data))
for target, data in pending:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
command = ['git', 'apply', '--no-index', '--directory=' + SOURCE.relative_to(ROOT).as_posix()]
for patch_file in PATCH_FILES:
    reverse = subprocess.run(command + ['--reverse', '--check', str(patch_file)], cwd=ROOT,
                             capture_output=True)
    if reverse.returncode:
        subprocess.run(command + ['--check', str(patch_file)], cwd=ROOT, check=True)
        subprocess.run(command + [str(patch_file)], cwd=ROOT, check=True)
for name in PATCHED_FILES:
    actual = (SOURCE / name).read_bytes()
    if actual.replace(b'\r\n', b'\n') != expected[name]:
        raise SystemExit(f'Patch was not applied: {name}')
    if actual != expected[name]:
        (SOURCE / name).write_bytes(expected[name])
print(f'Pinned source + empty-fill/selection patches verified: {SOURCE}')
print(f'Patch SHA256: {patch_hash}')
print(f'Selection patch SHA256: {selection_hash}')
