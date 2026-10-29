import {useEffect, useRef, useState} from 'react';
import {api, errorMessage, jsonRequest} from './api';

type Unit = {id:string;name:string;can_access:boolean};
export type Affiliation = {user_id:string;department:Unit|null;team:Unit|null;revision:string|null};
type CatalogUnit = {id:string;name:string;kind:'department'|'team';parent_id:string|null};

export function OrganizationSettings({onUpdated}:{onUpdated:()=>void}) {
  const [units,setUnits]=useState<CatalogUnit[]>([]),[user,setUser]=useState('');
  const [current,setCurrent]=useState<Affiliation|null>(null),[department,setDepartment]=useState(''),[team,setTeam]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const lock=useRef(false);
  useEffect(()=>{const c=new AbortController();api<CatalogUnit[]>('/organization/units',{signal:c.signal}).then(setUnits).catch(e=>{if(!c.signal.aborted)setError(errorMessage(e));});return()=>c.abort();},[]);
  async function run(fn:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError('');setNotice('');try{await fn();}catch(e){setError(errorMessage(e));}finally{lock.current=false;setBusy(false);}}
  function load(value:Affiliation){setCurrent(value);setDepartment(value.department?.id||'');setTeam(value.team?.id||'');}
  return <details className="organization-settings"><summary>계정 소속 부서·팀 지정</summary>
    <p>소속은 계정당 부서 하나·팀 하나입니다. 소속 변경은 문서함 접근 권한을 변경하지 않습니다. 문서함 구성원 권한은 별도로 관리하세요.</p>
    <form className="team-member-form" onSubmit={e=>{e.preventDefault();setCurrent(null);void run(async()=>load(await api<Affiliation>('/organization/accounts/'+encodeURIComponent(user.trim()))));}}>
      <label>소속을 지정할 계정 ID<input value={user} maxLength={80} disabled={busy} required onChange={e=>{setUser(e.target.value);setCurrent(null);setNotice('');}}/></label><button disabled={busy||!user.trim()}>소속 조회</button>
    </form>
    {current&&<form className="team-create-form" onSubmit={e=>{e.preventDefault();void run(async()=>{const value=await api<Affiliation>('/organization/accounts/'+encodeURIComponent(current.user_id),jsonRequest('PUT',{department_id:department||null,team_id:team||null,expected_revision:current.revision}));load(value);setNotice('소속을 저장했습니다. 문서 공유 권한은 그대로 유지됩니다.');onUpdated();});}}>
      <label>소속 부서<select aria-label="소속 부서" value={department} disabled={busy} onChange={e=>{setDepartment(e.target.value);setTeam('');}}><option value="">소속 없음</option>{units.filter(u=>u.kind==='department').map(u=><option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
      <label>소속 팀<select aria-label="소속 팀" value={team} disabled={busy||!department} onChange={e=>setTeam(e.target.value)}><option value="">팀 없음</option>{units.filter(u=>u.kind==='team'&&u.parent_id===department).map(u=><option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
      <button disabled={busy||(department===(current.department?.id||'')&&team===(current.team?.id||''))}>소속 저장</button>
      {((current.team&&!current.team.can_access)||(current.department&&!current.department.can_access))&&<p>소속 문서함의 접근 권한이 없습니다. 문서함 관리자에게 구성원 등록을 요청하세요.</p>}
    </form>}
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  </details>;
}
