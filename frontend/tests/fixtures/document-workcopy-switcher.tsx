import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {DocumentWorkcopySwitcher} from '../../src/document-workcopy-switcher';
function Fixture(){
 const [selected,setSelected]=useState(''),[count,setCount]=useState(0);
 return <><DocumentWorkcopySwitcher document={{id:'current',branch_source_id:'source',name:'현재 수정안',current_version:'c1',role:'owner',created_at:1}} disabled={false} onSelect={doc=>{setSelected(doc.id);setCount(n=>n+1);}}/><output aria-label="전환 대상">{selected}</output><output aria-label="전환 횟수">{count}</output></>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
