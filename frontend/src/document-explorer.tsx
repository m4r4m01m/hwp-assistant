import { useState } from 'react';
import { api, jsonRequest, errorMessage, fileUrl, type Document } from './api';
import { WorkspaceIcon } from './workspace-nav';
import { BranchPreview } from './document-branches';
import type { TeamSpace } from './team-workspaces';
import './document-explorer.css';
export type LibraryFolder = {id:string;name:string;parent_id:string|null};
export const libraryViews: Record<string,string> = {'':'최근 문서',mine:'내 문서함','shared-with-me':'공유받은 문서',branches:'개인 작업본',unassigned:'기존 문서함'};
export function folderPath(folders:LibraryFolder[], id:string):string {
  const names:string[]=[],seen=new Set<string>();let row=folders.find(f=>f.id===id);
  while(row&&!seen.has(row.id)){names.unshift(row.name);seen.add(row.id);row=folders.find(f=>f.id===row?.parent_id);}
  return names.join(' / ');
}
export function LibraryLocations({folders,spaces,value,onSelect,onUpdated,onMove,onManage,onTrash}:{folders:LibraryFolder[];spaces:TeamSpace[];value:string;onSelect:(id:string)=>void;onUpdated:()=>void;onMove:(doc:string,folder:string|null)=>Promise<void>;onManage:()=>void;onTrash:()=>void}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function run(fn:()=>Promise<unknown>){if(busy)return;setBusy(true);setError('');try{await fn();onUpdated();}catch(e){setError(errorMessage(e));}finally{setBusy(false);}}
  async function create(){const name=prompt('새 폴더 이름');if(!name)return;await run(()=>api('/folders/'+crypto.randomUUID(),jsonRequest('PUT',{name,parent_id:value.startsWith('folder:')?value.slice(7):null})));}
  function tree(parent:string|null,depth=0):React.ReactNode {return folders.filter(f=>f.parent_id===parent).map(f=><div key={f.id}><button style={{paddingLeft:12+depth*12}} aria-current={value==='folder:'+f.id?'page':undefined} onClick={()=>onSelect('folder:'+f.id)} onDragOver={e=>{if(e.dataTransfer.types.includes('application/x-hwp-document'))e.preventDefault();}} onDrop={e=>{const id=e.dataTransfer.getData('application/x-hwp-document');if(id){e.preventDefault();void run(()=>onMove(id,f.id));}}}><WorkspaceIcon name="file" size={17}/><span>{f.name}</span></button>{depth<8&&tree(f.id,depth+1)}</div>);}
  return <nav className="library-locations" aria-label="문서함 위치">
    {Object.entries(libraryViews).filter(([key])=>key!=='unassigned').map(([key,label])=><button key={key} aria-current={key===value?'page':undefined} onClick={()=>onSelect(key)}><WorkspaceIcon name={key==='shared-with-me'?'share':key==='branches'?'history':'documents'} size={17}/>{label}</button>)}
    <div className="location-heading"><strong>내 폴더</strong><button aria-label="새 폴더" title="현재 폴더 아래에 만들기" disabled={busy} onClick={()=>void create()}><WorkspaceIcon name="plus" size={16}/></button></div>
    {!folders.length&&<p>폴더를 만들어 문서를 정리하세요.</p>}{tree(null)}
    <div className="location-heading"><strong>공유 문서함</strong><button aria-label="공유 문서함 관리" onClick={onManage}><WorkspaceIcon name="settings" size={16}/></button></div>
    {spaces.map(s=><button key={s.id} aria-current={s.id===value?'page':undefined} onClick={()=>onSelect(s.id)}><WorkspaceIcon name="file" size={17}/><span>{s.name}</span></button>)}
    <button aria-current={value==='unassigned'?'page':undefined} onClick={()=>onSelect('unassigned')}><WorkspaceIcon name="documents" size={17}/>기존 문서함</button>
    <button onClick={onTrash}><WorkspaceIcon name="trash" size={17}/>휴지통</button>
    {error&&<p role="alert">{error}</p>}
  </nav>;
}
export function LibraryDetails({doc,folders,canShare,onClose,onOpen,onHistory,onShare,onMove}:{doc:Document;folders:LibraryFolder[];canShare:boolean;onClose:()=>void;onOpen:()=>void;onHistory:()=>void;onShare:()=>void;onMove:(id:string,folder:string|null)=>Promise<void>}){
  const [preview,setPreview]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  return <aside className="library-details" aria-label="선택한 문서 정보"><header><strong>문서 정보</strong><button aria-label="문서 정보 닫기" onClick={onClose}><WorkspaceIcon name="close" size={18}/></button></header>
    <h2>{doc.name}</h2><p>{doc.branch_source_id?'개인 작업본':doc.workspace_name|| (doc.personal_document?'개인 문서':'기존 문서함')}</p>
    <dl><dt>내 권한</dt><dd>{{owner:'관리',editor:'편집',reader:'열람'}[doc.role]}</dd><dt>등록일</dt><dd>{new Date(doc.created_at*1000).toLocaleDateString('ko-KR')}</dd></dl>
    <div className="library-detail-actions"><button onClick={onOpen}>{doc.role==='reader'?'문서 보기':'문서 열기'}</button><button onClick={onHistory}>문서 이력</button>{canShare&&<button onClick={onShare}>공유 권한</button>}</div>
    <label>내 폴더로 정리<select aria-label="문서 폴더" disabled={busy} value={doc.folder_id||''} onChange={async e=>{setBusy(true);setError('');try{await onMove(doc.id,e.target.value||null);}catch(e){setError(errorMessage(e));}finally{setBusy(false);}}}><option value="">폴더 지정 안 함</option>{folders.map(f=><option key={f.id} value={f.id}>{folderPath(folders,f.id)}</option>)}</select></label>
    <p className="hint">폴더 정리는 나에게만 적용되며 공유 권한은 바뀌지 않습니다.</p>{error&&<p role="alert">{error}</p>}
    {preview?<BranchPreview key={doc.id+doc.current_version} title="문서 미리보기" url={fileUrl(doc,doc.current_version)}/>:<button onClick={()=>setPreview(true)}>문서 미리보기</button>}
  </aside>;
}
