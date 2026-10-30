import {useEffect, useRef, useState} from 'react';
import {api, errorMessage, jsonRequest, type Document} from './api';
export type EditTarget = {document:Document;mode:'direct'|'view'|'workcopy'};
export function DocumentEditEntry({document:doc,onReady,onClose}:{document:Document;onReady:(target:EditTarget)=>void;onClose:()=>void}) {
  const request=useRef(crypto.randomUUID()),[attempt,setAttempt]=useState(0),[error,setError]=useState('');
  const ready=useRef(onReady);ready.current=onReady;
  useEffect(()=>{let active=true;setError('');api<EditTarget>(`/documents/${doc.id}/edit-target`,jsonRequest('POST',{request_id:request.current}))
    .then(value=>{if(active)ready.current(value);}).catch(e=>{if(active)setError(errorMessage(e));});return()=>{active=false;};},[doc.id,attempt]);
  return <main className="drive"><h1>{doc.name}</h1>{error?<><p role="alert">{error}</p><button onClick={()=>setAttempt(n=>n+1)}>같은 열기 요청 다시 확인</button></>:<p role="status">편집할 작업본을 준비하고 있습니다…</p>}<button onClick={onClose}>문서함으로</button></main>;
}
