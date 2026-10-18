import base64
import copy
import io
import json
import zipfile
from xml.etree import ElementTree as E
import pytest
from app.report_contract import ReportDraft, report_blocks
from app.report_partial import preview_blocks
from app.report_hwpx import HP, HH, paragraphs
from app.report_rich import BLUE, emphasis_ranges, layout_rich_report
from app.report_layout import layout_report
from app.report_hwpx import scaffold
from report_rich_fixture import RICH_DRAFT
from test_reports import report_env, seed, synthetic_output
from test_collaboration import secured
from test_report_preview import prepare, render


def test_generation_schema_requires_explicit_rich_arrays_but_old_drafts_still_load():
    from test_reports import DRAFT
    schema = ReportDraft.generation_schema()
    assert 'emphasis' in schema['required']
    assert set(schema['$defs']['ReportSection']['required']) == {'heading', 'paragraphs', 'tables', 'diagrams'}
    assert ReportDraft.model_validate(DRAFT).emphasis == []


def parsed(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return E.fromstring(z.read('Contents/header.xml')), E.fromstring(z.read('Contents/section0.xml'))


def test_rich_preview_preserves_verified_cells_and_retries_without_new_document(report_env, monkeypatch):
    from app import reports
    monkeypatch.setattr(reports, 'generate_report', lambda *args: (copy.deepcopy(RICH_DRAFT), {}))
    (client, _, _, folder), _ = report_env
    url, request, frozen = prepare(client)
    body, rendered = render(client, url, frozen)
    raw = base64.b64decode(rendered['layout_base64']); header, section = parsed(raw)
    assert len(list(section.iter(f'{{{HP}}}tbl'))) == 2
    cells = [''.join(t.text or '' for t in cell.iter(f'{{{HP}}}t')) for cell in section.iter(f'{{{HP}}}tc')]
    assert cells == ['우선순위', '담당', '조치', '기한', '상', '보안팀', '접근 권한 점검', '30일 내',
                     '중', '업무팀', '복구 훈련 및 결과 확인', '60일 내', '이상 징후 접수', '→', '영향 범위 확인', '→', '보호 조치 실행', '→', '복구 및 검증']
    chars = {e.get('id'): e for e in header.iter(f'{{{HH}}}charPr')}
    styles = {}
    for run in section.iter(f'{{{HP}}}run'):
        t = run.find(f'{{{HP}}}t')
        if t is not None and t.text:
            c = chars[run.get('charPrIDRef')]
            styles[t.text] = (c.find(f'{{{HH}}}bold') is not None, c.get('textColor'))
    assert styles['(요약)'] == (True, '#000000')
    assert styles['30일 내'] == (False, BLUE)
    assert styles['접근 권한 점검'] == (True, BLUE)
    assert client.post(url, json=request).json() == rendered
    assert client.post(url + '/render', json=body).json() == rendered
    assert not list(folder.iterdir()) and client.get('/api/documents').json() == []
    # Cell text must also have its own preimage and receipt; no layout bypass.
    bad = copy.deepcopy(body); bad['receipts'][8]['afterTextSha256'] = '0' * 64
    assert client.post(url + '/render', json=bad).status_code == 409


@pytest.mark.parametrize('damage', ['shape', 'cell_size', 'empty', 'citation', 'emphasis', 'duplicate', 'too_many'])
def test_rich_contract_rejects_unbound_or_unbounded_content(damage):
    value = copy.deepcopy(RICH_DRAFT)
    if damage == 'shape': value['sections'][0]['tables'][0]['rows'][0].pop()
    if damage == 'cell_size': value['sections'][0]['tables'][0]['rows'][0][0] = '가' * 53
    if damage == 'empty': value['sections'][2]['paragraphs'] = []
    if damage == 'citation': value['sections'][1]['diagrams'][0]['steps'][0] += ' [S9]'
    if damage == 'emphasis': value['emphasis'][0]['text'] = '본문에 없는 주장'
    if damage == 'duplicate': value['emphasis'].append(value['emphasis'][0])
    if damage == 'too_many':
        for s in value['sections'][1:]: s['tables'] = copy.deepcopy(value['sections'][0]['tables'])
    with pytest.raises(ValueError): ReportDraft.model_validate(value)


def test_rich_prefixes_are_safe_and_include_visuals_before_final_json():
    raw = json.dumps(RICH_DRAFT, ensure_ascii=False)
    for i in range(len(raw)):
        blocks = preview_blocks(raw[:i]); json.dumps(blocks, ensure_ascii=False).encode('utf-8')
    blocks = preview_blocks(raw[:raw.index('"sources"')])
    assert next(b for b in blocks if b['kind'] == 'table')['rows'][0][2] == '접근 권한 점검'
    assert len(next(b for b in blocks if b['kind'] == 'diagram')['steps']) == 4
    assert preview_blocks('{"emphasis":{},"title":"제목"}')[0]['text'] == '제목'


def test_emphasis_overlap_and_soft_breaks_preserve_characters():
    text = '□ (요약) 핵심 서비스 보호와 접근 권한 점검을 수행함'
    flags = emphasis_ranges(text, [{'text': '(요약) 핵심', 'style': 'blue'}, {'text': '핵심 서비스', 'style': 'bold'}])
    assert flags[text.index('요')] == 3 and flags[text.index('핵')] == 3
    assert flags[text.index('서')] == 1 and flags[-1] == 0


def test_concise_items_do_not_split_selected_emphasis_at_a_word_boundary():
    from app.report_outline import split_bullet
    phrase = '담당자와 완료 기한을 함께 확인'
    for padding in range(40, 90):
        text = '□ ' + '가' * padding + ' ' + phrase + ' 후 보호 조치를 시행함. ' * 4
        parts = split_bullet(text, [phrase])
        assert any(phrase in p for p in parts)
        assert ''.join(p[2:] for p in parts) == text[2:]


def test_legacy_layout_remains_plain_and_new_layout_keeps_full_logical_order():
    value = copy.deepcopy(RICH_DRAFT)
    blocks = report_blocks(value, concise=True, rich=True)
    data, plan = scaffold(seed(), blocks)
    for i, p in enumerate(plan): p['command_id'] = str(i)
    applied = base64.b64decode(synthetic_output({'id':'test', 'scaffold_base64':base64.b64encode(data).decode(), 'plan':plan})['output_base64'])
    legacy = layout_report(applied, plan)
    assert not list(parsed(legacy)[1].iter(f'{{{HP}}}tbl'))
    assert paragraphs(legacy)[1] == [''] + [p['text'] for p in plan]
    rich = layout_rich_report(applied, plan)
    _, section = parsed(rich)
    texts = [''.join(t.text or '' for r in p.findall(f'{{{HP}}}run') for t in r.findall(f'{{{HP}}}t')) for p in section.iter(f'{{{HP}}}p')]
    assert [t for t in texts if t] == [p['text'] for p in plan]
