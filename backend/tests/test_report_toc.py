"""Report-owned native TOC endpoints; page evidence here is synthetic, not SDK proof."""
import base64
import copy
import io
import json
import uuid
import zipfile
from xml.etree import ElementTree as E
import pytest
from app.block_hwpx import HP,HH,serialize_block_xml
from app.db import Document,ReportTask,transaction
from app.hwpx import digest
from app.report_toc import POLICY
from app.toc_hwpx import analyze
from test_reports import report_env,ready,seed,synthetic_output
from test_collaboration import secured, as_user
from test_document_blocks import changed_zip


def prepare(c,do_render=True):
    template=c.get('/api/report-templates').json()['items'][0]
    task=dict(id=str(uuid.uuid4()),prompt='금융 보안 보고서 작성해주세요.',research=False,template=template)
    r=c.post('/api/report-tasks',json=task);assert r.status_code==200,r.text
    job=ready(c,task);url=f"/api/report-tasks/{task['id']}/preview"
    r=c.post(url,json=dict(id=str(uuid.uuid4()),draft_sha256=job['draft_sha256'],seed_base64=base64.b64encode(seed()).decode()))
    assert r.status_code==200,r.text
    p=r.json();assert p['toc_policy']==POLICY and p['toc_verified'] is False
    v=synthetic_output(p);v.pop('approval_id');v['preview_id']=p['id']
    if not do_render:return url,p,v
    r=c.post(url+'/render',json=v);assert r.status_code==200,r.text
    return url,r.json(),v


def start_value(p):return dict(request_id=str(uuid.uuid4()),preview_id=p['id'],layout_sha256=p['layout_sha256'],source_base64=p['layout_base64'],source_sha256=p['layout_sha256'])


def start(c,url,p,v=None):
    v=v or start_value(p);r=c.post(url+'/toc-prepare',json=v);assert r.status_code==200,r.text
    return v,r.json()


def stage_value(v,pages=None,index=1):return {k:v[k] for k in ('request_id','preview_id','layout_sha256')}|dict(pass_index=index,pages=pages or {})


def verify_value(v,p,index=1,pages=None):
    pages=pages or p['pages'];raw=base64.b64decode(p['output_base64'])
    evidence=dict(export_sha256=digest(raw),page_count=max(pages.values()),revision=3,epoch=1,
      headings=[{k:h[k] for k in ('id','section','paragraph','length','text_sha256')}|{'page':pages[h['id']]} for h in p['headings']])
    return {k:v[k] for k in ('request_id','preview_id','layout_sha256')}|dict(pass_index=index,engine_output_base64=p['output_base64'],layout_evidence=evidence)


def complete(c,url,p):
    v,stage=start(c,url,p);vb=verify_value(v,stage);r=c.post(url+'/toc-verify',json=vb);assert r.status_code==200,r.text
    return v,stage,vb,r.json()


def edit(raw,name,fn):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:root=E.fromstring(z.read(name));fn(root)
    return changed_zip(raw,{name:serialize_block_xml(root)})


def save_value(p):return dict(id=str(uuid.uuid4()),preview_id=p['id'],layout_sha256=p['layout_sha256'],output_base64=p['layout_base64'])


def test_new_preview_requires_toc_before_save_and_first_complete_is_pure(report_env):
    (c,app,_,folder),calls=report_env;url,p,_=prepare(c)
    assert c.post(url+'/save',json=save_value(p)).status_code==409
    v,stage,vb,result=complete(c,url,p);final=result['preview']
    assert result['verified'] and result['converged'] and result['can_adopt'] and final['toc_verified']
    assert final['layout_sha256']==result['output_sha256']==digest(base64.b64decode(final['layout_base64']))
    assert final['template']==p['template'] and final['toc']['pages']==result['measured_pages']
    assert len(final['toc']['headings'])==5 and stage['target']['paragraph']==1
    assert final['toc']['id']==str(uuid.uuid5(uuid.UUID(p['id']),'automatic-report-toc'))
    assert c.get('/api/documents').json()==[] and not list(folder.iterdir()) and len(calls)==1
    assert c.get(url).json()==final
    for endpoint,body,expected in [('toc-prepare',v,stage),('toc-verify',vb,result)]:
        r=c.post(url+'/'+endpoint,json=body);assert r.status_code==200 and r.json()==expected
    changed=copy.deepcopy(vb);changed['layout_evidence']['revision']+=1
    assert c.post(url+'/toc-verify',json=changed).status_code==409
    assert '_toc_completed' not in final and 'render_receipts' not in final


def test_three_pass_pages_bound_to_previous_actual_evidence(report_env):
    (c,_,_,_),_=report_env;url,p,_=prepare(c);v,s=start(c,url,p)
    pages={h['id']:2+i for i,h in enumerate(s['headings'])}
    bad=c.post(url+'/toc-preview',json=stage_value(v,pages,2));assert bad.status_code==409
    r=c.post(url+'/toc-verify',json=verify_value(v,s,pages=pages));assert r.status_code==200 and not r.json()['can_adopt']
    assert c.get(url).json()['toc_state']['next_pass']==2
    bad=c.post(url+'/toc-preview',json=stage_value(v,{k:n+1 for k,n in pages.items()},2));assert bad.status_code==409
    r=c.post(url+'/toc-preview',json=stage_value(v,pages,2));assert r.status_code==200,r.text
    s2=r.json();assert c.post(url+'/toc-preview',json=stage_value(v,pages,2)).json()==s2
    other={k:n+1 for k,n in pages.items()}
    r=c.post(url+'/toc-verify',json=verify_value(v,s2,2,other));assert r.status_code==200 and not r.json()['converged']
    r=c.post(url+'/toc-preview',json=stage_value(v,other,3));assert r.status_code==200,r.text
    s3=r.json();r=c.post(url+'/toc-verify',json=verify_value(v,s3,3,{k:n+1 for k,n in other.items()}))
    assert r.status_code==200 and r.json()['exhausted'] and not r.json()['can_adopt']
    assert c.post(url+'/toc-preview',json=stage_value(v,other,4)).status_code==422
    assert c.get(url).json()['layout_sha256']==p['layout_sha256'] and not c.get(url).json()['toc_verified']


@pytest.mark.parametrize('damage',['text','format','resource','hash'])
def test_initial_source_must_match_frozen_rich_layout(report_env,damage):
    (c,_,_,_),_=report_env;url,p,_=prepare(c);v=start_value(p);raw=base64.b64decode(p['layout_base64'])
    if damage=='text':raw=edit(raw,'Contents/section0.xml',lambda s:setattr(s[3].find(f'{{{HP}}}run/{{{HP}}}t'),'text','unapproved text'))
    elif damage=='format':raw=edit(raw,'Contents/header.xml',lambda h:next(h.iter(f'{{{HH}}}charPr')).set('height','9999'))
    elif damage=='resource':raw=changed_zip(raw,{'settings.xml':b'<changed/>'})
    v.update(source_base64=base64.b64encode(raw).decode(),source_sha256=digest(raw) if damage!='hash' else '0'*64)
    r=c.post(url+'/toc-prepare',json=v);assert r.status_code==422,r.text
    assert 'toc_state' not in c.get(url).json()


def test_manual_edit_refresh_preserves_current_source_and_final_save_retry(report_env):
    (c,_,_,folder),_=report_env;url,p,_=prepare(c);_,_,_,done=complete(c,url,p);p=done['preview']
    raw=base64.b64decode(p['layout_base64'])
    raw=edit(raw,'Contents/section0.xml',lambda s:setattr(s[-1].find(f'{{{HP}}}run/{{{HP}}}t'),'text','manual follow-up content'))
    assert analyze(raw,digest(raw))['toc']['status']=='stale'
    v=start_value(p)|dict(source_base64=base64.b64encode(raw).decode(),source_sha256=digest(raw))
    v,s=start(c,url,p,v);assert c.post(url+'/save',json=save_value(p)).status_code==409
    vb=verify_value(v,s);r=c.post(url+'/toc-verify',json=vb);assert r.status_code==200,r.text
    refreshed=r.json()['preview'];out=base64.b64decode(refreshed['layout_base64'])
    assert analyze(out,digest(out))['toc']['status']=='current'
    with zipfile.ZipFile(io.BytesIO(out)) as z:assert b'manual follow-up content' in z.read('Contents/section0.xml')
    sv=save_value(refreshed);r=c.post(url+'/save',json=sv);assert r.status_code==200,r.text
    doc=r.json();assert c.post(url+'/save',json=sv).json()==doc
    assert len(list(folder.iterdir()))==1
    assert c.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content==out
    assert c.post(url+'/toc-verify',json=vb).status_code==409
    assert c.post(url+'/save',json=sv|dict(output_base64=p['layout_base64'])).status_code==409


@pytest.mark.parametrize('damage',['toc-text','deleted-heading','duplicate-heading','wrong-id'])
def test_refresh_damage_or_missing_identity_refused(report_env,damage):
    (c,_,_,_),_=report_env;url,p,_=prepare(c);_,_,_,done=complete(c,url,p);p=done['preview'];raw=base64.b64decode(p['layout_base64'])
    def fn(s):
        if damage=='toc-text':s[2].find(f'.//{{{HP}}}t').text='manual TOC'
        elif damage=='deleted-heading':s.remove(s[p['toc']['headings'][0]['paragraph']])
        elif damage=='duplicate-heading':s.append(copy.deepcopy(s[p['toc']['headings'][0]['paragraph']]))
        else:s[2].set('styleIDRef',s[0].get('styleIDRef'))
    raw=edit(raw,'Contents/section0.xml',fn);v=start_value(p)|dict(source_base64=base64.b64encode(raw).decode(),source_sha256=digest(raw))
    r=c.post(url+'/toc-prepare',json=v);assert r.status_code in (409,422),r.text
    assert c.get(url).json()['layout_sha256']==p['layout_sha256']


def test_cancel_pending_late_verify_and_new_request_fences(report_env):
    (c,_,_,_),_=report_env;url,p,_=prepare(c);v,s=start(c,url,p);cancel={k:v[k] for k in ('request_id','preview_id','layout_sha256')}
    r=c.post(url+'/toc-cancel',json=cancel);assert r.status_code==200 and r.json()['cancelled']
    assert c.post(url+'/toc-cancel',json=cancel).json()==r.json()
    assert c.post(url+'/toc-prepare',json=v).status_code==409
    v2,s2=start(c,url,p)
    assert c.post(url+'/toc-verify',json=verify_value(v,s)).status_code==409
    assert c.get(url).json()['toc_state']['request_id']==v2['request_id']
    assert c.post(url+'/toc-verify',json=verify_value(v2,s2)).status_code==200
    r=c.post(url+'/toc-cancel',json={k:v2[k] for k in ('request_id','preview_id','layout_sha256')});assert r.json()['completed'] and not r.json()['cancelled']


def test_owner_and_wrong_preview_base_checked_every_route(report_env):
    (c,_,_,_),_=report_env;url,p,_=prepare(c);v,s=start(c,url,p)
    for bad in (v|dict(preview_id=str(uuid.uuid4())),v|dict(layout_sha256='0'*64),v|dict(source_sha256='0'*64)):
        assert c.post(url+'/toc-prepare',json=bad).status_code in (409,422)
    routes=[('toc-prepare',v),('toc-preview',stage_value(v)),('toc-verify',verify_value(v,s)),('toc-cancel',{k:v[k] for k in ('request_id','preview_id','layout_sha256')})]
    as_user(c,'bob')
    for route,b in routes:assert c.post(url+'/'+route,json=b).status_code==404


def test_status_rechecked_after_pure_compose_before_publish(report_env):
    # SQLite BEGIN IMMEDIATE serializes actual writers. Inject a changed repository
    # observation at the second lock/read, exercising the publication state fence.
    from app.report_toc import install_report_toc
    from app.report_preview import public_preview
    from app.reports import lock_report
    (c,app,_,_),_=report_env;url,p,_=prepare(c);reads=[]
    def observed(db,task,user):
        row=lock_report(db,task,user);reads.append(1)
        if len(reads)==2:row.status='failed'
        return row
    app.router.routes=[r for r in app.router.routes if '/preview/toc-' not in getattr(r,'path','')]
    install_report_toc(app,observed,public_preview)
    r=c.post(url+'/toc-prepare',json=start_value(p));assert r.status_code==409,r.text
    assert len(reads)==2 and 'toc_state' not in c.get(url).json()


def test_render_retry_returns_final_toc_without_reapplying_original_layout(report_env):
    (c,_,_,_),_=report_env;url,p,render=prepare(c);_,_,_,done=complete(c,url,p)
    assert c.post(url+'/render',json=render).json()==done['preview']
    from test_report_preview import prepare as legacy_prepare,render as legacy_render
    url2,_,legacy=legacy_prepare(c);assert 'toc_policy' not in legacy
    _,rendered=legacy_render(c,url2,legacy)
    assert c.post(url2+'/save',json=save_value(rendered)).status_code==200


def test_cancelled_request_stays_cancelled_after_other_cancel(report_env):
    (c,_,_,_),_=report_env;url,p,_=prepare(c);a,_=start(c,url,p)
    for v in (a,None):
        if v is None:v,_=start(c,url,p)
        assert c.post(url+'/toc-cancel',json={k:v[k] for k in ('request_id','preview_id','layout_sha256')}).json()['cancelled']
    assert c.post(url+'/toc-prepare',json=a).status_code==409
    assert 'toc_state' not in c.get(url).json()


@pytest.mark.parametrize('field',['id','reference_sha256','profile_sha256'])
@pytest.mark.parametrize('phase',['prepare','render','toc'])
def test_frozen_standard_guard_and_existing_response_reuse(report_env,monkeypatch,field,phase):
    import app.report_preview as module
    import app.report_toc as tocmod
    from app.report_templates import catalog
    (c,_,_,_),_=report_env
    if phase=='prepare':
        task=dict(id=str(uuid.uuid4()),prompt='금융 보안 보고서 작성해주세요.',research=False,template=catalog()['items'][0])
        assert c.post('/api/report-tasks',json=task).status_code==200
        job=ready(c,task);url=f"/api/report-tasks/{task['id']}/preview"
        request=dict(id=str(uuid.uuid4()),draft_sha256=job['draft_sha256'],seed_base64=base64.b64encode(seed()).decode())
        old=module.metadata
        monkeypatch.setattr(module,'metadata',lambda:old()|{field:'changed'})
        assert c.post(url,json=request).status_code==409
        assert c.get(url).status_code==404
    elif phase=='render':
        url,p,v=prepare(c,do_render=False);old=module.metadata
        monkeypatch.setattr(module,'metadata',lambda:old()|{field:'changed'})
        assert c.post(url+'/render',json=v).status_code==409
        assert 'layout_base64' not in c.get(url).json()
    else:
        url,p,_=prepare(c);old=tocmod.metadata
        monkeypatch.setattr(tocmod,'metadata',lambda:old()|{field:'changed'})
        assert c.post(url+'/toc-prepare',json=start_value(p)).status_code==409
        assert 'toc_state' not in c.get(url).json()


def test_completed_retries_do_not_reload_changed_standard(report_env,monkeypatch):
    (c,_,_,_),_=report_env;url,p,render=prepare(c);v,stage,vb,result=complete(c,url,p)
    import app.report_toc as toc;import app.report_preview as preview
    def unavailable():raise AssertionError('cached output must be reused')
    monkeypatch.setattr(toc,'metadata',unavailable);monkeypatch.setattr(preview,'metadata',unavailable)
    assert c.post(url+'/toc-prepare',json=v).json()==stage
    assert c.post(url+'/toc-verify',json=vb).json()==result
    assert c.post(url+'/render',json=render).json()==result['preview']


def test_rich_table_and_diagram_collapsed_coordinates_produce_exact_headings(report_env,monkeypatch):
    import app.reports as reports
    from report_rich_fixture import RICH_DRAFT
    monkeypatch.setattr(reports,'generate_report',lambda *args:(copy.deepcopy(RICH_DRAFT),{}))
    (c,_,_,_),_=report_env;url,p,_=prepare(c);v,stage,vb,result=complete(c,url,p)
    final=base64.b64decode(result['preview']['layout_base64'])
    with zipfile.ZipFile(io.BytesIO(final)) as z:s=E.fromstring(z.read('Contents/section0.xml'))
    assert len(list(s.iter(f'{{{HP}}}tbl')))==3
    assert [h['text'] for h in stage['headings']]==[i['text'] for i in p['plan'] if i['kind']=='heading']
    assert [h['id'] for h in stage['headings']]==[str(uuid.uuid5(uuid.UUID(p['id']),'heading:'+i['command_id'])) for i in p['plan'] if i['kind']=='heading']


@pytest.mark.parametrize('field',['id','reference_sha256','profile_sha256'])
@pytest.mark.parametrize('phase',['prepare','render','toc-stage','toc-verify'])
def test_standard_change_during_calculation_refuses_publication(report_env,monkeypatch,field,phase):
    import app.report_preview as previewmod
    import app.report_toc as tocmod
    from app.report_templates import catalog
    (c,_,_,_),_=report_env;changed=[]
    module=previewmod if phase in ('prepare','render') else tocmod
    old_metadata=module.metadata
    monkeypatch.setattr(module,'metadata',lambda:old_metadata()|({field:'changed-during-calculation'} if changed else {}))
    if phase=='prepare':
        task=dict(id=str(uuid.uuid4()),prompt='금융 보안 보고서 작성해주세요.',research=False,template=catalog()['items'][0])
        assert c.post('/api/report-tasks',json=task).status_code==200
        job=ready(c,task);url=f"/api/report-tasks/{task['id']}/preview"
        original=previewmod.scaffold
        def compute(*args):
            result=original(*args);changed.append(1);return result
        monkeypatch.setattr(previewmod,'scaffold',compute)
        v=dict(id=str(uuid.uuid4()),draft_sha256=job['draft_sha256'],seed_base64=base64.b64encode(seed()).decode())
        assert c.post(url,json=v).status_code==409
        assert c.get(url).status_code==404
    elif phase=='render':
        url,p,v=prepare(c,do_render=False);original=previewmod.layout_rich_report
        def compute(*args):
            result=original(*args);changed.append(1);return result
        monkeypatch.setattr(previewmod,'layout_rich_report',compute)
        assert c.post(url+'/render',json=v).status_code==409
        assert 'layout_base64' not in c.get(url).json()
    elif phase=='toc-stage':
        url,p,_=prepare(c);original=tocmod.compose_toc
        def compute(*args):
            result=original(*args);changed.append(1);return result
        monkeypatch.setattr(tocmod,'compose_toc',compute)
        assert c.post(url+'/toc-prepare',json=start_value(p)).status_code==409
        assert 'toc_state' not in c.get(url).json()
    else:
        url,p,_=prepare(c);v,s=start(c,url,p);original=tocmod.verify_toc
        def compute(*args):
            result=original(*args);changed.append(1);return result
        monkeypatch.setattr(tocmod,'verify_toc',compute)
        assert c.post(url+'/toc-verify',json=verify_value(v,s)).status_code==409
        assert c.get(url).json()['toc_verified'] is False
        assert c.get(url).json()['layout_sha256']==p['layout_sha256']
    assert len(changed)==1
