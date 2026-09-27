"""Read-only DB/blob checks for the release wiring and synthetic table UI trial."""
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / '.cache/engine-release-2026-09-27'
TABLE = ROOT / '.cache/hancom-table-2026-09-27'
read = lambda path: json.loads(path.read_text(encoding='utf-8'))
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
tag = lambda e: e.tag.rsplit('}', 1)[-1]

def inspect(path):
    with ZipFile(path) as z:
        assert z.testzip() is None
        header = ET.fromstring(z.read('Contents/header.xml'))
        section = ET.fromstring(z.read('Contents/section0.xml'))
        para_defs = {e.get('id'): e for e in header.iter() if tag(e) == 'paraPr'}
        char_defs = {e.get('id'): e for e in header.iter() if tag(e) == 'charPr'}
        border_defs = {e.get('id'): {tag(c): c.attrib for c in e if tag(c).endswith('Border')}
                       for e in header.iter() if tag(e) == 'borderFill'}
        paragraphs = []
        for p in section:
            if tag(p) != 'p':
                continue
            text = ''.join(e.text or '' for run in p if tag(run) == 'run' for e in run if tag(e) == 't')
            if text:
                para = para_defs[p.get('paraPrIDRef')]
                align = next(e.get('horizontal') for e in para if tag(e) == 'align')
                runs = [r for r in p if tag(r) == 'run' and any(tag(e) == 't' and e.text for e in r)]
                bold = all(any(tag(c) == 'bold' for c in char_defs[r.get('charPrIDRef')]) for r in runs)
                paragraphs.append({'text': text, 'align': align, 'bold': bold})
        tables = []
        for t in section.iter():
            if tag(t) != 'tbl':
                continue
            cells = []
            for c in t.iter():
                if tag(c) == 'tc':
                    cells.append({'text': ''.join(e.text or '' for e in c.iter() if tag(e) == 't'),
                                  'geometry': {tag(e): e.attrib for e in c if tag(e) in ['cellAddr', 'cellSpan', 'cellSz', 'cellMargin']},
                                  'borders': border_defs[c.get('borderFillIDRef')]})
            tables.append({'rows': t.get('rowCnt'), 'cols': t.get('colCnt'), 'cells': cells})
        return {'sha256': sha(path), 'bytes': path.stat().st_size, 'paragraphs': paragraphs, 'tables': tables}

created, imported = read(TABLE / 'create-proof.json'), read(TABLE / 'import-proof.json')
plain = read(OUT / 'browser/create-proof.json')
for proof in [created, imported, plain]:
    assert 'failure' not in proof
    assert set(proof['stages'].values()) == {'passed'}
for proof in [created, imported]:
    assert proof['page_errors'] == proof['csp_violations'] == proof['external_requests'] == []
    assert proof['wasm_responses'] and all(x['sha256'] == proof['engine']['coreBuild']['wasm_sha256'] for x in proof['wasm_responses'])
product, hancom = inspect(TABLE / 'create-download.hwpx'), inspect(TABLE / 'hancom-saved.hwpx')
assert product['paragraphs'] == hancom['paragraphs']
assert product['paragraphs'][0] == {'text': '표 서식 보존 시험', 'align': 'CENTER', 'bold': True}
assert len(product['tables']) == len(hancom['tables']) == 1
expected = json.loads(json.dumps(product['tables']))
expected[0]['cells'][3]['text'] += ' 한컴 확인'
assert expected == hancom['tables']
assert created['saved']['sha256'] == product['sha256']
assert imported['saved']['sha256'] == imported['input']['sha256'] == hancom['sha256']
ui = read(TABLE / 'hancom-ui-observations.json')
assert {x['phase'] for x in ui} >= {'opened_product', 'edited_table_cell', 'saved_hancom', 'reopened_hancom'}

db = sqlite3.connect('file:' + (ROOT / '.cache/codex-flow/test.db').as_posix() + '?mode=ro', uri=True)
db.row_factory = sqlite3.Row
def versions(doc):
    result = []
    for row in db.execute('SELECT id,base_version,sha256,storage_key,cause FROM versions WHERE document_id=? ORDER BY created_at', (doc,)):
        data = dict(row)
        assert sha(ROOT / '.cache/codex-flow/files' / data.pop('storage_key')) == data['sha256']
        data['blob_matches_db_hash'] = True
        result.append(data)
    return result

documents = []
for proof, count, digest in [(created, 1, product['sha256']), (imported, 1, hancom['sha256']), (plain, 2, plain['sha256'])]:
    doc = proof['document']['id']; rows = versions(doc)
    assert len(rows) == count and rows[-1]['sha256'] == digest
    assert rows[-1]['id'] == proof['document']['current_version']
    documents.append({'document_id': doc, 'versions': rows})
prior = read(ROOT / 'docs/evidence/hancom-roundtrip-result-2026-09-27.json')
assert versions(prior['source_document_id']) == prior['source_versions_preserved']
assert versions(prior['web_import']['document_id']) == prior['web_import']['versions']
history = read(ROOT / '.cache/codex-flow-before/blob-hashes.json')
assert all(sha(ROOT / 'data/files' / name) == digest for name, digest in history)
assert sha(OUT / 'wasm-pack-native-pkg/rhwp_bg.wasm') == created['engine']['coreBuild']['wasm_sha256']
source_guard = read(OUT / 'source-preservation.json')
assert source_guard['source_preserved']
result = {
    'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
    'scope': 'Build wiring + Windows no-opt packaging + actual Hancom 2018 simple table UI roundtrip; NO AI',
    'build': {'metadata': created['engine'], 'windows_wasm_pack': '0.15.0 --release --no-opt',
              'same_wasm_sha256': sha(OUT / 'wasm-pack-native-pkg/rhwp_bg.wasm'),
              'docker_execution': 'not run: Docker engine and WSL Linux unavailable',
              'optimized_deployment': 'not verified', 'default_engine_replaced': False,
              'source_preservation': source_guard},
    'table_product': product, 'table_hancom': hancom, 'documents': documents,
    'stages': {'built_frontend_save_reopen': 'passed with diagnostic core and nginx-equivalent headers (Vite preview)',
               'hancom_open_cell_edit_save_reopen': 'passed', 'hancom_output_web_import_reopen': 'passed',
               'heading_and_non_target_text': 'passed: text/bold/alignment preserved',
               'table_structure_and_border_values': 'passed: 2x2, geometry and four border definitions preserved',
               'hancom_editor_visual_border': 'unresolved: outer border invisible at 125%; present in native-generated PNG',
               'zoom_recheck': 'not completed: failed to activate captured window',
               'browser_csp_page_errors_external_requests': 'zero in both table trials'},
    'ui_phases': [{'phase': p['phase'], 'at': p['at']} for p in ui],
    'original_actual_codex_document_preserved': prior['source_document_id'],
    'historical_blobs_preserved': len(history), 'ai_calls': 0,
    'hancom_preview_sha256': sha(TABLE / 'hancom-generated-preview.png'),
    'evidence_directories': [OUT.relative_to(ROOT).as_posix(), TABLE.relative_to(ROOT).as_posix()],
    'unverified': ['Docker/Linux/Binaryen optimized build', 'nginx container runtime',
                   'PostgreSQL/Compose', 'general or repeated Hancom compatibility',
                   'other user actual Codex continuation', 'independent context restore'],
}
destination = ROOT / 'docs/evidence/engine-release-and-table-2026-09-27.json'
with destination.open('x', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
print(json.dumps({'stages': result['stages'], 'historical_blobs_preserved': len(history),
                  'documents': [d['document_id'] for d in documents]}, ensure_ascii=False))
