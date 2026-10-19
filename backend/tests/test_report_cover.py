"""Cover text is fenced; original cover geometry is used only by new plans."""
import base64
import copy
import io
import uuid
import zipfile
from xml.etree import ElementTree as E

import pytest

from app.document_standard import HH, HP, format_signature, source_parts
from app.hwpx import digest
from app.reference_standard import defaults
from app.reference_report import COVER_LAYOUT_POLICY, COVER_SPACER_STYLE, TOC_ANCHOR_STYLE, TITLE_LAYOUT_POLICY
from app.report_contract import report_blocks
from app.report_hwpx import body_text, paragraphs, scaffold
from app.report_rich import layout_rich_report
from app.report_toc import initial_config
from app.toc_contract import TocPreview
from app.toc_hwpx import analyze, compose_toc
from test_reference_report import DRAFT, parts
from test_reports import seed
from test_reports import report_env, ready
from test_collaboration import secured
from test_report_preview import render


def generated(preset, cover, legacy_title=False, **overrides):
    settings = {**defaults(preset), **overrides}
    blocks = report_blocks(DRAFT, rich=True, visual_settings=settings, cover=cover)
    if legacy_title:
        blocks[0].pop('title_layout', None)
    raw, plan = scaffold(seed(), blocks, visual_settings=settings)
    section, _ = paragraphs(raw)
    for item in plan:
        section[item['paragraph']].find(f'{{{HP}}}run/{{{HP}}}t').text = item['text']
        item['command_id'] = str(uuid.uuid4())
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, 'w') as target:
        for entry in source.infolist():
            target.writestr(entry, E.tostring(section) if entry.filename == 'Contents/section0.xml' else source.read(entry))
    assert format_signature(raw, plan) == format_signature(output.getvalue(), plan)
    return layout_rich_report(output.getvalue(), plan, visual_settings=settings), plan


def test_cover_is_explicit_fenced_text_and_old_plans_stay_unchanged():
    original = copy.deepcopy(DRAFT)
    settings = defaults('appendix-2')
    old = report_blocks(DRAFT, rich=True, visual_settings=settings)
    assert report_blocks(DRAFT, rich=True, visual_settings=settings, cover=None) == old
    plan = report_blocks(DRAFT, rich=True, visual_settings=settings, cover={'date': '2026. 10. 20.', 'department': '(주)예시 기획팀'})
    assert plan[0]['cover_layout'] == COVER_LAYOUT_POLICY
    assert [(item['kind'], item['style_kind'], item['text']) for item in plan[1:3]] == [
        ('body', 'cover_date', '2026. 10. 20.'), ('body', 'cover_department', '(주)예시 기획팀')]
    assert DRAFT == original
    assert report_blocks(DRAFT, rich=True, visual_settings=defaults('appendix-1'), cover={'date': '2026.'}) == report_blocks(DRAFT, rich=True, visual_settings=defaults('appendix-1'))


@pytest.mark.parametrize('preset,anchor_index,spacers', [('appendix-2',14,[111,113,*range(115,124)]), ('appendix-3',5,[131,133,135,137])])
def test_source_cover_spacing_title_box_and_toc_heading_fences(preset, anchor_index, spacers):
    raw, plan = generated(preset, {'date': '2026. 10. 20.', 'department': '업무지원팀'})
    header, section = parts(raw)
    chars = {c.get('id'): c for c in header.iter(f'{{{HH}}}charPr')}
    styles = {s.get('id'): s for s in header.iter(f'{{{HH}}}style')}
    _, source_header, source_section = source_parts()
    source_chars = {c.get('id'): c for c in source_header.iter(f'{{{HH}}}charPr')}
    actual_spacers = [p for p in section if styles[p.get('styleIDRef')].get('name') == COVER_SPACER_STYLE]
    assert [chars[p.find(f'{{{HP}}}run').get('charPrIDRef')].get('height') for p in actual_spacers] == [
        source_chars[source_section[i].find(f'{{{HP}}}run').get('charPrIDRef')].get('height') for i in spacers]
    assert section[1].find(f'.//{{{HP}}}tbl') is not None
    assert section[0].find(f'{{{HP}}}run/{{{HP}}}t') is None
    assert section[1].find(f'{{{HP}}}run/{{{HP}}}t') is None
    assert body_text(section[1]) == DRAFT['title']
    assert body_text(section[3]) == '2026. 10. 20.'
    department_index = 13 if preset == 'appendix-2' else 7
    assert body_text(section[department_index]) == '업무지원팀'
    assert section[department_index].get('pageBreak') == '0'
    assert section[anchor_index + 1 if preset == 'appendix-2' else 8].get('pageBreak') == '1'
    assert '2015' not in ''.join(body_text(p) for p in section)
    assert '○○' not in ''.join(body_text(p) for p in section)
    preview = {'id': str(uuid.uuid4()), 'plan': plan}
    config = initial_config(preview, raw, str(uuid.uuid4()))
    assert config['target']['paragraph'] == anchor_index
    assert all(body_text(section[item['paragraph']]) == plan_item['text'] for item,plan_item in zip(config['headings'], [p for p in plan if p['kind']=='heading']))
    value = TocPreview.model_validate(dict(request_id=str(uuid.uuid4()),base_version=preview['id'],source_base64=base64.b64encode(raw).decode(),source_sha256=digest(raw),**config,pass_index=1,pages={}))
    composed, _ = compose_toc(raw,value)
    assert analyze(composed,digest(composed))['toc']['status'] == 'current'


@pytest.mark.parametrize('preset', ['appendix-2', 'appendix-3'])
def test_empty_cover_leaves_source_positions_without_inventing_date_or_department(preset):
    raw, plan = generated(preset, {})
    header, section = parts(raw)
    config = initial_config({'id':str(uuid.uuid4()), 'plan':plan},raw,str(uuid.uuid4()))
    assert len(config['headings']) == 4
    assert all(p.get('style_kind') not in ('cover_date','cover_department') for p in plan)
    assert [body_text(p) for p in section if body_text(p) and not p.findall(f'.//{{{HP}}}tbl')] == [p['text'] for p in plan[1:] if not p.get('visual_id')]


def test_cover_settings_and_user_department_parentheses_do_not_gain_emphasis():
    raw, plan = generated('appendix-3', {'date':'2026년 10월', 'department':'(주) 예시팀'}, cover_date_size_pt=20, cover_department_size_pt=22, cover_rule_color='#112233',cover_frame_height_mm=25)
    header, section = parts(raw)
    chars = {c.get('id'): c for c in header.iter(f'{{{HH}}}charPr')}
    assert chars[section[3].find(f'{{{HP}}}run').get('charPrIDRef')].get('height') == '2000'
    assert chars[section[7].find(f'{{{HP}}}run').get('charPrIDRef')].get('height') == '2200'
    assert len(section[7].findall(f'{{{HP}}}run')) == 1
    table = section[1].find(f'.//{{{HP}}}tbl')
    assert table.find(f'{{{HP}}}sz').get('height') == str(round(25*7200/25.4))
    fill_id = table.find(f'{{{HP}}}tr/{{{HP}}}tc').get('borderFillIDRef')
    fill = next(f for f in header.iter(f'{{{HH}}}borderFill') if f.get('id')==fill_id)
    assert fill.find(f'{{{HH}}}topBorder').get('color') == '#112233'
    assert fill.find(f'{{{HH}}}leftBorder').get('type') == 'NONE'


def test_new_appendix1_reserves_15mm_title_table_and_source_gap_while_legacy_plan_keeps_paragraph():
    raw, plan = generated('appendix-1',None)
    assert plan[0]['title_layout']==TITLE_LAYOUT_POLICY
    header, section = parts(raw)
    title = section[1].find(f'.//{{{HP}}}tbl')
    assert title is not None
    assert title.find(f'{{{HP}}}sz').get('height')==str(round(15*7200/25.4))
    assert body_text(section[1])==DRAFT['title']
    assert body_text(section[2])==''
    assert body_text(section[3])=='1. 수집 현황'
    assert section[1].find(f'{{{HP}}}run/{{{HP}}}t') is None
    old, old_plan = generated('appendix-1',None,legacy_title=True)
    assert 'title_layout' not in old_plan[0]
    _, old_section=parts(old)
    assert old_section[1].find(f'.//{{{HP}}}tbl') is None
    assert body_text(old_section[2])=='1. 수집 현황'


def test_cover_request_retries_and_preview_keep_the_same_fenced_user_fields(report_env):
    (client, _, _, folder), _ = report_env
    catalog = client.get('/api/visual-standards').json()
    preset = next(p for p in catalog['presets'] if p['settings']['preset_id']=='appendix-3')
    selection = {key:preset[key] for key in ('id','revision','name','settings','settings_sha256','base_standard','preview_sha256')}
    template = client.get('/api/report-templates').json()['items'][0]
    body = {'id':str(uuid.uuid4()),'prompt':'합성 자료를 토대로 업무 보고서를 작성해 주세요.','research':False,
            'visual_standard':selection,'template':template,'cover':{'date':'2026. 10. 20.','department':'운영지원팀'}}
    response = client.post('/api/report-tasks',json=body)
    assert response.status_code == 200,response.text
    job = ready(client,body)
    assert client.post('/api/report-tasks',json=body).status_code == 200
    assert client.post('/api/report-tasks',json={**body,'cover':{'date':'2027.','department':'변경팀'}}).status_code == 409
    url = f"/api/report-tasks/{body['id']}/preview"
    request = {'id':str(uuid.uuid4()),'draft_sha256':job['draft_sha256'],'seed_base64':base64.b64encode(seed()).decode()}
    response = client.post(url,json=request)
    assert response.status_code == 200,response.text
    preview = response.json()
    assert [p['text'] for p in preview['plan'][1:3]] == ['2026. 10. 20.','운영지원팀']
    _, rendered = render(client,url,preview)
    assert client.post(url,json=request).json() == rendered
    raw = base64.b64decode(rendered['layout_base64'])
    _, section = parts(raw)
    assert body_text(section[3])=='2026. 10. 20.' and body_text(section[7])=='운영지원팀'
    assert client.get('/api/documents').json()==[] and not list(folder.iterdir())
