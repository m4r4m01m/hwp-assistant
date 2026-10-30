import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {OrganizationManager} from '../../src/organization-manager';
function Fixture(){const[busy,setBusy]=useState(false),[denied,setDenied]=useState(false);return <><output aria-label="상위 메뉴 잠금">{String(busy)}</output>{denied?<p>관리자 권한 없음</p>:<OrganizationManager account={{user_id:'admin',is_admin:true,authentication_required:true}} onBusyChange={setBusy} onForbidden={()=>setDenied(true)}/>}</>;}
createRoot(document.getElementById('root')!).render(<Fixture/>);
