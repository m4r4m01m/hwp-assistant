import {useEffect, useRef, useState} from 'react';
import {api, errorMessage, jsonRequest, type Document} from './api';

export function DocumentConfidential({document:doc,onClose}:{document:Document;onClose:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null),gate=useRef(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const request=useRef({enabled:!doc.confidential,expected_revision:doc.confidential_revision||null,expected_version:doc.current_version});
  useEffect(()=>{dialog.current?.showModal();},[]);
  async function save(){if(gate.current)return;gate.current=true;setBusy(true);setError('');try{
    await api(`/documents/${doc.id}/confidential`,jsonRequest('PUT',request.current));onClose();
  }catch(cause){setError(errorMessage(cause));}finally{gate.current=false;setBusy(false);}}
  return <dialog ref={dialog} className="document-confidential-dialog" aria-labelledby="confidential-title" onCancel={event=>{event.preventDefault();if(!gate.current)onClose();}}>
    <h2 id="confidential-title">{doc.confidential?'비밀 문서 해제':'비밀 문서로 지정'}</h2><strong>{doc.name}</strong>
    {doc.confidential?<p>비밀 표시와 부·팀 문서함 이동 제한을 해제합니다. 개인 문서와 현재 공유 권한은 그대로 유지됩니다.</p>:<><p>소유자와 직접 초대한 사용자만 접근할 수 있습니다. 같은 부·팀 소속이나 시스템 관리자라는 이유로 열리지 않습니다.</p><p>기존 개별 공유 권한은 유지됩니다. 부·팀 문서함 이동, 일반 사본 복제와 양식 등록은 차단합니다.</p></>}
    <p className="hint">앱의 접근 범위를 제한하는 기능입니다. 열람 권한이 있는 사용자의 파일 다운로드는 허용됩니다.</p>
    {error&&<p role="alert">{error} 목록에서 현재 상태를 다시 확인할 수도 있습니다.</p>}
    <div className="document-confidential-actions"><button disabled={busy} onClick={onClose}>취소</button><button className="primary" disabled={busy} onClick={()=>void save()}>{busy?'설정 중…':error?'같은 설정 다시 확인':doc.confidential?'비밀 문서 해제':'비밀 문서로 지정'}</button></div>
  </dialog>;
}
