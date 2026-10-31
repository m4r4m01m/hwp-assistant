import {useCallback, useEffect, useRef, useState} from 'react';
import {api, errorMessage, type Document} from './api';
import {WorkspaceIcon} from './workspace-nav';

type Workcopy = {document_id:string; source_document_id:string; name:string; owner_id:string;
  current_version:string|null; created_at:number; can_edit:boolean; can_manage_participants?:boolean};
type Flow = {source_document:Document; branches:Workcopy[]; graph?:{truncated:boolean}};

/** Uses the history API's existing visibility rules; selecting never merges. */
export function DocumentWorkcopySwitcher({document:doc, disabled, onSelect}:{document:Document; disabled:boolean; onSelect:(doc:Document)=>void}) {
  const [flow,setFlow]=useState<Flow|null>(null),[error,setError]=useState('');
  const request=useRef<AbortController|null>(null);
  const refresh=useCallback(async()=>{
    if(request.current)return;
    const controller=new AbortController();request.current=controller;
    try{
      const value=await api<Flow>(`/documents/${doc.id}/branches`,{signal:controller.signal});
      if(!controller.signal.aborted){setFlow(value);setError('');}
    }catch(cause){if(!controller.signal.aborted){setFlow(null);setError(errorMessage(cause));}}
    finally{if(request.current===controller)request.current=null;}
  },[doc.id]);
  useEffect(()=>{void refresh();return()=>{request.current?.abort();request.current=null;};},[refresh]);
  const copies=(flow?.branches||[]).filter(item=>item.can_edit&&item.current_version);
  const listed=flow?.source_document.id===doc.id||copies.some(item=>item.document_id===doc.id);
  function select(id:string){
    if(disabled||id===doc.id||!flow)return;
    if(id===flow.source_document.id){onSelect(flow.source_document);return;}
    const copy=copies.find(item=>item.document_id===id);
    if(copy)onSelect({id:copy.document_id,name:copy.name,current_version:copy.current_version!,created_at:copy.created_at,role:'editor',branch_source_id:copy.source_document_id});
  }
  return <div className="document-workcopy-switcher" data-workcopy={!!doc.branch_source_id}>
    <WorkspaceIcon name="history"/>
    <select aria-label="작업본 선택" value={doc.id} disabled={disabled} onFocus={()=>void refresh()} onPointerDown={()=>void refresh()} onChange={event=>select(event.target.value)} title={error||`${doc.branch_source_id?'별도 작업본':'공동 원본'} · ${doc.name}${flow?.graph?.truncated?' · 최근 작업본을 표시합니다. 전체 경로는 문서 이력에서 확인하세요.':''}`}>
      {!listed&&<option value={doc.id}>{doc.branch_source_id?'현재 작업본':'공동 원본'}{doc.branch_source_id?` · ${doc.name}`:''}</option>}
      {flow&&<optgroup label="공동 문서"><option value={flow.source_document.id}>공동 원본{flow.source_document.role==='reader'?' · 읽기 전용':''}</option></optgroup>}
      {copies.length>0&&<optgroup label="별도 작업본">{copies.map(copy=><option key={copy.document_id} value={copy.document_id}>{copy.can_manage_participants?'내 작업본':`${copy.owner_id}의 작업본`} · {copy.name}</option>)}</optgroup>}
    </select>
    {error&&<button type="button" disabled={disabled} title={error} onClick={()=>void refresh()}>작업본 목록 다시 불러오기</button>}
  </div>;
}
