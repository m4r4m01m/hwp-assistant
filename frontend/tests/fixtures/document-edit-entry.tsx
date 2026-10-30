import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {DocumentEditEntry,type EditTarget} from '../../src/document-edit-entry';
function Fixture(){const [result,setResult]=useState<EditTarget|null>(null),[closed,setClosed]=useState(false);return result?<output aria-label="편집 대상">{result.mode}:{result.document.id}</output>:closed?<p>문서함</p>:<DocumentEditEntry document={{id:'source',name:'공통 보고서',current_version:'v1',created_at:1,role:'editor'}} onReady={setResult} onClose={()=>setClosed(true)}/>;}
createRoot(document.getElementById('root')!).render(<Fixture/>);
