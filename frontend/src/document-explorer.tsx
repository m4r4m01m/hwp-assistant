import {createPortal} from 'react-dom';
import { useEffect, useRef, useState } from 'react';
import { api, jsonRequest, errorMessage, fileUrl, type Document } from './api';
import { WorkspaceIcon } from './workspace-nav';
import { BranchPreview } from './document-branches';
import type { TeamSpace } from './team-workspaces';
import {useOrganizationIdentity} from './account-identity';
import './document-explorer.css';
export type LibraryFolder = {id:string;name:string;parent_id:string|null;workspace_id?:string;revision?:string};
export const libraryViews: Record<string,string> = {'':'최근 문서',mine:'내 문서함','shared-with-me':'공유받은 문서',branches:'문서 수정안',confidential:'비밀 문서'};
export function folderPath(folders:LibraryFolder[], id:string):string {
  const names:string[]=[],seen=new Set<string>();let row=folders.find(f=>f.id===id);
  while(row&&!seen.has(row.id)){names.unshift(row.name);seen.add(row.id);row=folders.find(f=>f.id===row?.parent_id);}
  return names.join(' / ');
}
export function LibraryLocations({spaces,value,onSelect,onManage,onTrash}:{spaces:TeamSpace[];value:string;onSelect:(id:string)=>void;onManage:()=>void;onTrash:()=>void}) {
  const scopeRef=useRef<HTMLElement>(null);
  const closeMenus=()=>scopeRef.current?.querySelectorAll('details[open]').forEach(el=>el.removeAttribute('open'));
  const choose=(id:string)=>{onSelect(id);closeMenus();};
  useEffect(()=>{const outside=(event:PointerEvent)=>{if(event.target instanceof Node&&!scopeRef.current?.contains(event.target))closeMenus();};document.addEventListener('pointerdown',outside);return()=>document.removeEventListener('pointerdown',outside);},[]);

  const organization=useOrganizationIdentity(),affiliation=organization?.value,affiliationError=organization?.error;
  const ownIds=new Set([affiliation?.department?.id,affiliation?.team?.id].filter(Boolean));
  const personal=value==='mine'||value.startsWith('folder:');
  const shared=value==='shared-with-me'||spaces.some(s=>s.id===value&&!ownIds.has(s.id));
  const extra=['branches','confidential'].includes(value);
  return <nav ref={scopeRef} onKeyDown={e=>{if(e.key==='Escape')closeMenus();}} className="library-locations library-scope-bar" aria-label="문서함">
    <div className="library-primary-locations"><button className="library-primary-location" aria-label="최근 문서" aria-current={value===''?'page':undefined} onClick={()=>choose('')}><WorkspaceIcon name="history" size={21}/><span><small>전체 문서 모아보기</small><strong>최근 문서</strong></span></button><button className="library-primary-location" aria-label="내 문서함" aria-current={personal?'page':undefined} onClick={()=>choose('mine')}><WorkspaceIcon name="documents" size={21}/><span><small>나만의 작업 공간</small><strong>내 문서함</strong></span></button>
    {affiliationError?<button title={affiliationError} onClick={()=>organization?.reload()}>소속 다시 불러오기</button>:!affiliation?<p>소속 확인 중…</p>:<>
      {!affiliation.department&&<p>소속 미지정 · 관리자에게 소속 지정을 요청하세요.</p>}
      {([['team','우리 팀 문서함'],['department','우리 부서 문서함']] as const).map(([key,label])=>{const unit=affiliation[key];return unit&&<button className="library-primary-location" aria-label={`${label} ${unit.name}`} key={key} disabled={!unit.can_access} aria-current={unit.id===value?'page':undefined} title={unit.can_access?unit.name:'소속은 지정됐지만 문서함 접근 권한이 없습니다. 관리자에게 요청하세요.'} onClick={()=>choose(unit.id)}><WorkspaceIcon name="file" size={21}/><span><small>{label}</small><strong>{unit.name}</strong>{!unit.can_access&&<small>접근 권한 필요</small>}</span></button>;})}
      {affiliation.department&&!affiliation.team&&<p>소속 팀 미지정</p>}
    </>}
    <button className="library-primary-location" aria-label="공유받은 문서" aria-current={shared?'page':undefined} onClick={()=>choose('shared-with-me')}><WorkspaceIcon name="share" size={21}/><span><small>나에게 공유된 문서</small><strong>공유받은 문서</strong></span></button>
    <details className="library-location-group library-location-more" name="library-scope-option"><summary data-selected={extra||undefined}>{extra?libraryViews[value]:"더보기"}</summary><div className="library-option-panel">
    {Object.entries(libraryViews).filter(([key])=>['branches','confidential'].includes(key)).map(([key,label])=><button key={key} aria-current={key===value?'page':undefined} onClick={()=>choose(key)}><WorkspaceIcon name={key==='confidential'?'lock':key==='shared-with-me'?'share':key==='branches'?'history':'documents'} size={17}/>{label}</button>)}
    <button onClick={()=>{closeMenus();onTrash();}}><WorkspaceIcon name="trash" size={17}/>휴지통</button></div></details></div>
    <div className="library-scope-options">
    {shared&&<details className="library-location-group" name="library-scope-option"><summary>{spaces.find(s=>s.id===value)?.name||"다른 공유 문서함"}</summary><div className="library-option-panel"><div className="location-heading"><strong>문서함 관리</strong><button aria-label="공유 문서함 관리" title="소속·문서함 권한 관리" onClick={onManage}><WorkspaceIcon name="settings" size={16}/></button></div>
    {spaces.filter(s=>!ownIds.has(s.id)).map(s=><button key={s.id} aria-current={s.id===value?'page':undefined} onClick={()=>choose(s.id)}><WorkspaceIcon name="file" size={17}/><span>{s.name}</span></button>)}
    </div></details>}</div>
  </nav>;
}
export function LibraryFolders({folders,current,rootId,rootName,canManage,toolbarTarget,rowsTarget,columnCount,nameDescending,onSelect,onUpdated,onMove}:{folders:LibraryFolder[];current?:LibraryFolder;rootId:string;rootName:string;canManage:boolean;toolbarTarget:HTMLElement|null;rowsTarget:HTMLTableSectionElement|null;columnCount:number;nameDescending:boolean;onSelect:(id:string)=>void;onUpdated:()=>Promise<void>;onMove:(doc:string,folder:string|null)=>Promise<void>}){
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[editing,setEditing]=useState<{kind:'create'|'rename'|'delete';id:string;name:string;parent_id:string|null;revision?:string}|null>(null);
 const scope=rootId==='mine'?null:rootId,available=folders.filter(f=>(f.workspace_id||null)===scope);
 const path:LibraryFolder[]=[],seen=new Set<string>();let node=current;while(node&&!seen.has(node.id)){path.unshift(node);seen.add(node.id);node=available.find(f=>f.id===node?.parent_id);}
 const open=(kind:'create'|'rename'|'delete')=>{setError('');setEditing(kind==='create'?{kind,id:crypto.randomUUID(),name:'',parent_id:current?.id||null}:{kind,id:current!.id,name:current!.name,parent_id:current!.parent_id,revision:current!.revision});};
 async function submit(){if(!editing||busy)return;setBusy(true);setError('');const frozen=editing;try{const url=scope?`/workspaces/${scope}/folders/${frozen.id}`:`/folders/${frozen.id}`;await api(url,jsonRequest(frozen.kind==='delete'?'DELETE':'PUT',frozen.kind==='delete'?(scope?{expected_revision:frozen.revision}:{}):{name:frozen.name.trim(),parent_id:frozen.parent_id,...(scope?{expected_revision:frozen.revision||null}:{})}));await onUpdated();setEditing(null);if(frozen.kind==='delete')onSelect(current?.parent_id?'folder:'+current.parent_id:rootId);}catch(e){setError(errorMessage(e));}finally{setBusy(false);}}
 const drop=async(event:React.DragEvent,id:string|null)=>{const doc=event.dataTransfer.getData('application/x-hwp-document');if(doc&&canManage&&!busy){event.preventDefault();event.stopPropagation();setBusy(true);setError('');try{await onMove(doc,id);}catch(e){setError(errorMessage(e));}finally{setBusy(false);}}};
 const drag=(event:React.DragEvent)=>{if(canManage&&!busy&&event.dataTransfer.types.includes('application/x-hwp-document')){event.preventDefault();event.dataTransfer.dropEffect='move';}};
 return <section className="library-folder-browser" aria-label="폴더와 문서">{toolbarTarget&&createPortal(<div className="library-folder-actions"><button disabled={!canManage||busy} title={canManage?'현재 위치에 폴더 만들기':'문서함 편집 권한이 필요합니다.'} onClick={()=>open('create')}><WorkspaceIcon name="plus" size={16}/>새 폴더</button>{current&&<><button disabled={!canManage||busy} onClick={()=>open('rename')}>폴더 이름 변경</button><button disabled={!canManage||busy} onClick={()=>open('delete')}>폴더 삭제</button></>}</div>,toolbarTarget)}
 {current&&<nav aria-label="현재 폴더" className="library-folder-path"><button onClick={()=>onSelect(rootId)} onDragOver={drag} onDrop={e=>void drop(e,null)}>{rootName}</button>{path.map((f,i)=><span key={f.id}><span aria-hidden="true">/</span><button aria-current={i===path.length-1?'page':undefined} onClick={()=>onSelect('folder:'+f.id)} onDragOver={drag} onDrop={e=>void drop(e,f.id)}>{f.name}</button></span>)}</nav>}
 {rowsTarget&&createPortal(available.filter(f=>f.parent_id===(current?.id||null)).sort((a,b)=>(nameDescending?-1:1)*(a.name.localeCompare(b.name,'ko',{numeric:true})||a.id.localeCompare(b.id))).map(f=><tr key={f.id} className="library-folder-row" onDragOver={drag} onDrop={e=>void drop(e,f.id)}><td colSpan={columnCount}><button className="link" aria-label={f.name} disabled={busy} onClick={()=>onSelect('folder:'+f.id)}><WorkspaceIcon name="file" size={20}/><span>{f.name}</span><span className="library-folder-kind">폴더</span><span className="library-folder-open" aria-hidden="true">›</span></button></td></tr>),rowsTarget)}{error&&!editing&&<p role="alert">{error}</p>}
 {editing&&createPortal(<dialog className="library-folder-dialog" aria-label={editing.kind==='create'?'새 폴더':editing.kind==='rename'?'폴더 이름 변경':'폴더 삭제'} ref={el=>{if(el&&!el.open)el.showModal();}} onCancel={e=>{e.preventDefault();if(!busy)setEditing(null);}}><form onSubmit={e=>{e.preventDefault();void submit();}}><h2>{editing.kind==='create'?'새 폴더':editing.kind==='rename'?'폴더 이름 변경':'폴더 삭제'}</h2><p>{rootName}{current?' / '+folderPath(available,current.id):''}</p>{editing.kind==='delete'?<p>‘{editing.name}’ 폴더를 삭제할까요? 문서는 문서함 최상위로 이동하며 내용과 권한은 유지됩니다.</p>:<label>폴더 이름<input autoFocus required maxLength={100} value={editing.name} disabled={busy} onChange={e=>setEditing({...editing,name:e.target.value})}/></label>}{scope&&<p className="hint">이 폴더는 문서함 구성원에게 함께 표시됩니다.</p>}{error&&<p role="alert">{error}</p>}<div className="library-folder-dialog-actions"><button type="button" disabled={busy} onClick={()=>setEditing(null)}>취소</button><button className="primary" disabled={busy||!editing.name.trim()}>{busy?'저장 중…':editing.kind==='delete'?'삭제':editing.kind==='create'?'만들기':'저장'}</button></div></form></dialog>,document.body)}</section>;
}
export function LibraryDetails({doc,folders,canShare,onClose,onOpen,onHistory,onShare,onMove}:{doc:Document;folders:LibraryFolder[];canShare:boolean;onClose:()=>void;onOpen:()=>void;onHistory:()=>void;onShare:()=>void;onMove:(id:string,folder:string|null)=>Promise<void>}){
  const [preview,setPreview]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  return <aside className="library-details" aria-label="선택한 문서 정보"><header><strong>문서 정보</strong><button aria-label="문서 정보 닫기" onClick={onClose}><WorkspaceIcon name="close" size={18}/></button></header>
    <h2>{doc.name}</h2>{doc.confidential&&<p className="library-confidential-tag">비밀 · 직접 초대한 사용자만 접근</p>}<p>{doc.branch_source_id?'문서 수정안':doc.workspace_name|| (doc.personal_document?'개인 문서':'기존 문서')}</p>
    <dl><dt>내 권한</dt><dd>{{owner:'관리',editor:'편집',reader:'열람'}[doc.role]}</dd><dt>등록일</dt><dd>{new Date(doc.created_at*1000).toLocaleDateString('ko-KR')}</dd></dl>
    <div className="library-detail-actions"><button onClick={onOpen}>{doc.role==='reader'?'문서 보기':'문서 열기'}</button><button onClick={onHistory}>문서 이력</button>{canShare&&<button onClick={onShare}>공유 권한</button>}</div>
    <label>{doc.workspace_id?"문서함 폴더로 정리":"내 폴더로 정리"}<select aria-label="문서 폴더" disabled={busy||!!doc.workspace_id&&doc.role==='reader'} value={(doc.workspace_id?doc.workspace_folder_id:doc.folder_id)||''} onChange={async e=>{setBusy(true);setError('');try{await onMove(doc.id,e.target.value||null);}catch(e){setError(errorMessage(e));}finally{setBusy(false);}}}><option value="">폴더 지정 안 함</option>{folders.filter(f=>(f.workspace_id||null)===(doc.workspace_id||null)).map(f=><option key={f.id} value={f.id}>{folderPath(folders,f.id)}</option>)}</select></label>
    <p className="hint">{doc.workspace_id?"팀·부서 폴더 위치는 구성원에게 함께 표시됩니다. 문서 권한은 유지됩니다.":"폴더 정리는 나에게만 적용되며 공유 권한은 바뀌지 않습니다."}</p>{error&&<p role="alert">{error}</p>}
    <button onClick={()=>setPreview(true)}>문서 미리보기</button>
    {preview&&createPortal(<dialog className="library-preview-dialog" aria-labelledby="library-preview-title"
      ref={el=>{if(el&&!el.open)el.showModal();}} onCancel={e=>{e.preventDefault();setPreview(false);}}>
      <header><div><h2 id="library-preview-title">문서 미리보기</h2><p>{doc.name}</p></div><button autoFocus aria-label="문서 미리보기 닫기" onClick={()=>setPreview(false)}><WorkspaceIcon name="close" size={20}/></button></header>
      <BranchPreview key={doc.id+doc.current_version} title="문서 미리보기" url={fileUrl(doc,doc.current_version)}/>
    </dialog>,document.body)}
  </aside>;
}
