"""Read-only evidence check for the synthetic Hancom empty-fill regression."""
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

root = Path(__file__).resolve().parent.parent
out = root / '.cache/hancom-empty-fill-v2-2026-09-27'
read_json = lambda p: json.loads(p.read_text(encoding='utf-8'))
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
created = read_json(out / 'create-proof.json')
imported = read_json(out / 'import-proof.json')
ui = read_json(out / 'hancom-ui-observations.json')
assert 'failure' not in created and 'failure' not in imported
assert all(s == 'passed' for p in [created, imported] for s in p['stages'].values())

def inspect(path):
    with ZipFile(path) as z:
        assert z.testzip() is None
        header = ET.fromstring(z.read('Contents/header.xml'))
        brushes = [e.attrib for e in header.iter() if e.tag.endswith('}winBrush')]
        shades = sorted({e.get('shadeColor') for e in header.iter() if e.tag.endswith('}charPr')})
        paragraphs = []
        for name in sorted(n for n in z.namelist() if n.startswith('Contents/section') and n.endswith('.xml')):
            for paragraph in ET.fromstring(z.read(name)):
                if paragraph.tag.endswith('}p'):
                    paragraphs.append(''.join(t.text or '' for t in paragraph.iter() if t.tag.endswith('}t')))
        return {'sha256': sha(path), 'size': path.stat().st_size, 'brushes': brushes,
                'shades': shades, 'paragraphs': paragraphs}

product = inspect(out / 'create-download.hwpx')
hancom = inspect(out / 'hancom-saved.hwpx')
assert hancom['paragraphs'] == product['paragraphs'] + ['한컴 수정 확인: 흰 배경에서 수정하고 저장했습니다.']
assert product['sha256'] == created['sha256']
assert hancom['sha256'] == imported['sha256'] == imported['input_sha256']
assert all(b['faceColor'] == '#FFFFFFFF' for info in [product, hancom] for b in info['brushes'])
assert product['shades'] == hancom['shades'] == ['#FFFFFFFF']
assert {u['phase'] for u in ui} >= {'opened_product', 'edited_hancom', 'saved_hancom', 'reopened_hancom'}
assert all(u['white_background_observed'] for u in ui)
assert '한컴 수정 확인:' in next(u['accessibility'] for u in ui if u['phase'] == 'reopened_hancom')

db = sqlite3.connect('file:' + (root / '.cache/codex-flow/test.db').as_posix() + '?mode=ro', uri=True)
db.row_factory = sqlite3.Row
def versions(document_id):
    rows = [dict(r) for r in db.execute('SELECT id,base_version,sha256,storage_key,cause FROM versions WHERE document_id=? ORDER BY created_at', (document_id,))]
    for row in rows:
        row['blob_matches_db_hash'] = sha(root / '.cache/codex-flow/files' / row.pop('storage_key')) == row['sha256']
        assert row['blob_matches_db_hash']
    return rows

product_versions = versions(created['document']['id'])
import_versions = versions(imported['document']['id'])
assert len(product_versions) == 2 and len(import_versions) == 1
assert product_versions[0]['id'] == created['initial_version']
assert product_versions[-1]['id'] == created['document']['current_version']
assert product_versions[-1]['sha256'] == product['sha256']
assert import_versions[0]['sha256'] == hancom['sha256']
original = read_json(root / 'docs/evidence/hancom-roundtrip-result-2026-09-27.json')
assert versions(original['source_document_id']) == original['source_versions_preserved']
assert versions(original['web_import']['document_id']) == original['web_import']['versions']
historical = read_json(root / '.cache/codex-flow-before/blob-hashes.json')
assert all(sha(root / 'data/files' / name) == digest for name, digest in historical)
with ZipFile(out / 'hancom-saved.hwpx') as z:
    preview = out / 'hancom-generated-preview.png'
    with preview.open('xb') as output:
        output.write(z.read('Preview/PrvImage.png'))
result = {
    'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
    'scope': 'One synthetic plain-paragraph document, actual Hancom 2018 10.0.0.5060 UI roundtrip; no AI',
    'engine': created['engine'], 'product_output': product, 'actual_hancom_output': hancom,
    'document_id': created['document']['id'], 'product_versions': product_versions,
    'import_document_id': imported['document']['id'], 'import_versions': import_versions,
    'stages': {'product_save_and_web_reopen': 'passed', 'hancom_open_edit_save_reopen': 'passed',
               'hancom_black_background_regression': 'passed for this synthetic fixture',
               'hancom_output_web_import_reopen': 'passed', 'original_and_history_preservation': 'passed'},
    'ui_phases': [{'phase': u['phase'], 'at': u['at']} for u in ui],
    'prior_actual_codex_document_preserved': original['source_document_id'],
    'historical_blobs_preserved': len(historical), 'ai_calls': 0,
    'unverified': ['general Hancom compatibility', 'tables/pictures/complex formatting in Hancom',
                   'repeated three-cycle roundtrip', 'Docker/wasm-opt deployment build',
                   'other user actual Codex continuation', 'independent context restore'],
    'evidence_directory': out.relative_to(root).as_posix(),
}
target = root / 'docs/evidence/hancom-empty-fill-fix-2026-09-27.json'
with target.open('x', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
    f.write('\n')
print(json.dumps({'stages': result['stages'], 'historical_blobs_preserved': len(historical),
                  'document_id': result['document_id'], 'import_document_id': result['import_document_id']}, ensure_ascii=False))
