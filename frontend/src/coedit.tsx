import { useEffect, useRef, useState } from "react";
import { api, downloadBytes, downloadFile, errorMessage, fileUrl, jsonRequest, type Document } from "./api";
import { EditorAdapter, type ExportSnapshot } from "./editor";
import "./coedit.css";
import {DocumentWorkcopySwitcher} from "./document-workcopy-switcher";
import {CoeditLive} from "./coedit-live";
import {WorkspaceNav,WorkspaceIcon} from "./workspace-nav";

type Sync = { current_version: string; participants: {id: string; user_id: string}[] };
type Pending = {key: string; base: string; snapshot: ExportSnapshot};
export function Coedit({ document: doc, onClose, onTimeline, onAdvanced, onWorkcopy, onSwitchDocument }: {document: Document; onClose: () => void; onTimeline?:()=>void; onAdvanced?:(doc:Document)=>void; onWorkcopy?:(doc:Document)=>void; onSwitchDocument?:(doc:Document)=>void}) {
  const host = useRef<HTMLDivElement>(null), adapter = useRef<EditorAdapter | null>(null);
  const token = useRef(""), version = useRef(doc.current_version), pending = useRef<Pending | null>(null);
  const dirty = useRef(false), lastEdit = useRef(0), paused = useRef(false), alive = useRef(false), locked = useRef(false), ready = useRef(false);
  const liveEpoch = useRef<number|null>(null);
  const liveBaseline = useRef<{base:string;page:number;text:string}|null>(null);
  const joinId = useRef(crypto.randomUUID()), cycle = useRef<(force?: boolean) => Promise<void>>(async () => {});
  const [status, setStatus] = useState("공동편집 참여 중…"), [error, setError] = useState("");
  const [participants, setParticipants] = useState<Sync["participants"]>([]), [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [hasChanges,setHasChanges]=useState(false),[working,setWorking]=useState(false),[leaving,setLeaving]=useState(false);
  const destination=useRef<()=>void>(onClose);
  const [revisionRetry,setRevisionRetry]=useState(false);
  const revisionDestination=useRef<(()=>void)|undefined>(undefined);
  const reloadLatest=useRef<()=>Promise<void>>(async()=>{});
  const [discarding,setDiscarding]=useState(false);
  const discardDestination=useRef<(()=>void)|undefined>(undefined);
  const [remoteUpdate,setRemoteUpdate]=useState<'waiting'|'received'|null>(null);
  const [liveControls,setLiveControls]=useState<HTMLDivElement|null>(null);
  function freeze(value: boolean) { if (host.current) host.current.inert = value; }
  function recover() { const source = adapter.current?.exportHwpx(); if (source) downloadBytes(source.bytes, "공동편집-복구-" + doc.name); }
  function fail(cause: unknown) { paused.current = true; freeze(true); setError(errorMessage(cause)); setStatus("동기화 중단 · 내 편집 보존"); }
  useEffect(() => {
    alive.current = true; const controller = new AbortController(); let off = () => {}; let lastPoll = 0;
    let composing = false, loadingBuffer = false, expectedEpoch: number | null = null;
    const compositionStart = () => { composing = true; }, compositionEnd = () => { composing = false; lastEdit.current = Date.now(); };
    const headers = () => ({"X-Coedit-Session": token.current});
    async function load(next: string) {
      const current = adapter.current!;
      const bytes = await downloadFile(fileUrl(doc, next), {signal: controller.signal});
      if (!alive.current) return;
      let position: unknown;
      try { position = await current.sdk.hwpctrl.call("GetPos"); } catch { /* Optional cursor restoration. */ }
      loadingBuffer = true;
      try { await current.load(bytes, doc.name, {suppressDialogs: true, skipUnsavedGuard: true}); }
      finally { loadingBuffer = false; }
      if (Array.isArray(position) && position.length === 3) {
        try { await current.sdk.hwpctrl.call("SetPos", position); } catch { /* Document structure may have changed. */ }
      }
      expectedEpoch = current.exportHwpx().epoch; liveEpoch.current = expectedEpoch;
      version.current = next; dirty.current = false; setHasChanges(false);liveBaseline.current=null;setRemoteUpdate(null);
      try {const context=await current.sdk.getSelectionContext();const svg=await current.sdk.getPageSvg(context.page-1);const text=Array.from(new DOMParser().parseFromString(svg,"image/svg+xml").querySelectorAll("text")).map(node=>node.textContent||"").join("").replace(/\s/g,"");if(text.length<=20000)liveBaseline.current={base:next,page:context.page,text};}catch{/* Missing baseline falls back to explicitly labelled observation diff. */}
    }
    reloadLatest.current = async () => {
      const latest = await api<Document>(`/documents/${doc.id}`, {signal:controller.signal});
      await load(latest.current_version);
    };
    cycle.current = async (force = false) => {
      if(force){const deadline=Date.now()+11000;while(locked.current&&alive.current&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,40));}
      if (!alive.current || !ready.current || locked.current || paused.current) return;
      if (force && composing) {setStatus("입력 중인 글자를 완성한 후 반영하세요.");return;}
      if (!force && Date.now() - lastPoll < 2000) return;
      locked.current = true;
      try {
        if (adapter.current!.bridge.documentState().documentEpoch !== expectedEpoch)
          throw new Error("편집기에서 다른 문서가 열렸습니다. 원래 공동문서에 덮어쓰지 않습니다. 현재 내용을 다운로드하고 문서함에서 다시 여세요.");
        if (force && (dirty.current || pending.current)) {
          // Freeze only the short publish/rebase transaction. Never reload over
          // typing that occurred after the captured snapshot, including IME.
          freeze(true); setWorking(true); setStatus("변경사항 반영 중…");
          if (!pending.current) {
            const snapshot = adapter.current!.exportHwpx();
            if (snapshot.contentLoss.count) throw new Error("엔진의 내용 손실 경고가 있어 동기화를 중단했습니다. 내 편집을 다운로드하세요.");
            pending.current = {key: crypto.randomUUID(), base: version.current, snapshot};
          }
          const sent = pending.current;
          const result = await api<{version_id: string; current_version: string}>(`/documents/${doc.id}/coedit/changes?base_version=${encodeURIComponent(sent.base)}&request_id=${sent.key}`, {
            method: "POST", headers: {...headers(), "Content-Type": "application/hwp+zip"}, body: new Uint8Array(sent.snapshot.bytes), timeoutMs: 20000,
          });
          if (!alive.current) return;
          await load(result.current_version);
          if (!alive.current) return;
          pending.current = null;
        }
        const result = await api<Sync>(`/documents/${doc.id}/coedit`, {method: "PUT", headers: headers(), timeoutMs: 10000});
        if (!alive.current) return;
        setParticipants(result.participants); lastPoll = Date.now();
        // The GET-equivalent poll may complete after new typing. Defer loading;
        // the next publish merges this buffer against its exact old version.
        if (result.current_version !== version.current && !dirty.current && !composing) {
          if (adapter.current!.bridge.documentState().documentEpoch !== expectedEpoch) throw new Error("동기화 중 다른 문서가 열렸습니다. 현재 내용을 다운로드하세요.");
          freeze(true); await load(result.current_version);
          if(alive.current)setRemoteUpdate('received');
        } else if(result.current_version !== version.current) {
          setRemoteUpdate('waiting');
        }
        if (alive.current) setStatus(dirty.current || pending.current ? "미반영 변경 있음" : "미반영 변경 없음");
      } catch (cause) { if (alive.current) fail(cause); }
      finally { locked.current = false; if(alive.current)setWorking(false); if (alive.current && !paused.current) freeze(false); }
    };
    void (async () => {
      const joined = await api<{token: string; document: Document}>(`/documents/${doc.id}/coedit`, jsonRequest("POST", {request_id: joinId.current}));
      if (!alive.current) { void api(`/documents/${doc.id}/coedit`, {method: "DELETE", headers: {"X-Coedit-Session": joined.token}, keepalive: true}).catch(() => {}); return; }
      token.current = joined.token;
      const opened = await EditorAdapter.mount(host.current!, {signal: controller.signal});
      if (!alive.current) { opened.destroy(); return; } adapter.current = opened; freeze(true);
      try { await opened.sdk.plugins.load("hwpctrl"); } catch { /* Editing does not depend on optional cursor restore. */ }
      await load(joined.document.current_version);
      if (!alive.current) return;
      const frame = opened.sdk.element.contentWindow;
      frame?.addEventListener("compositionstart", compositionStart, true); frame?.addEventListener("compositionend", compositionEnd, true);
      const unsubscribe = opened.bridge.subscribe(() => {
        if (!alive.current || loadingBuffer) return;
        dirty.current = true; setHasChanges(true); lastEdit.current = Date.now(); setStatus("미반영 변경 있음");setRemoteUpdate(value=>value==='received'?null:value);
      });
      off = () => { unsubscribe(); frame?.removeEventListener("compositionstart", compositionStart, true); frame?.removeEventListener("compositionend", compositionEnd, true); };
      ready.current = true; setLoading(false); freeze(false); await cycle.current(true);
    })().catch(cause => { if (alive.current) { setLoading(false); fail(cause); } });
    const timer = setInterval(() => void cycle.current(), 400);
    const guard = (event: BeforeUnloadEvent) => { if (dirty.current || pending.current || locked.current) { event.preventDefault(); event.returnValue = ""; } };
    const release = () => { if (token.current) void api(`/documents/${doc.id}/coedit`, {method: "DELETE", headers: headers(), keepalive: true}).catch(() => {}); };
    const leave = (event: PageTransitionEvent) => { if (!event.persisted) release(); };
    window.addEventListener("beforeunload", guard); window.addEventListener("pagehide", leave);
    return () => { alive.current = false; ready.current = false; controller.abort(); clearInterval(timer); off(); release(); token.current = ""; adapter.current?.destroy(); adapter.current = null; window.removeEventListener("beforeunload", guard); window.removeEventListener("pagehide", leave); };
  }, [doc.id, attempt]);
  function close(next=onClose) {
    if (locked.current || working) return;
    if(dirty.current || pending.current){destination.current=next;setLeaving(true);return;}
    next();
  }
  async function applyAndLeave(){
    if(revisionRetry){await preserveRevision(destination.current);return;}
    paused.current=false;setError("");await cycle.current(true);
    if(!dirty.current&&!pending.current&&!paused.current){setLeaving(false);destination.current();}
  }
  async function preserveRevision(next?:()=>void){
    if(locked.current||doc.branch_source_id)return;
    if(!revisionRetry)revisionDestination.current=next;
    locked.current=true;setWorking(true);freeze(true);setError("");
    let completed: (()=>void)|undefined;
    try{
      if(!pending.current){const snapshot=adapter.current!.exportHwpx();if(snapshot.contentLoss.count||snapshot.epoch!==liveEpoch.current)throw new Error("편집 문서의 보존 상태를 확인할 수 없습니다. 작성 내용을 파일로 백업해 주세요.");pending.current={key:crypto.randomUUID(),base:version.current,snapshot};}
      const sent=pending.current;setRevisionRetry(true);
      const saved=await api<{document:Document}>(`/documents/${doc.id}/coedit/revision?base_version=${encodeURIComponent(sent.base)}&request_id=${sent.key}`,{method:'POST',headers:{'X-Coedit-Session':token.current,'Content-Type':'application/hwp+zip'},body:new Uint8Array(sent.snapshot.bytes),timeoutMs:20000});
      dirty.current=false;pending.current=null;setRevisionRetry(false);setHasChanges(false);setLeaving(false);
      completed=revisionDestination.current || (onWorkcopy ? () => onWorkcopy({...saved.document,branch_source_id:doc.id}) : onClose);
      revisionDestination.current=undefined;
    }catch(cause){fail(cause);}finally{locked.current=false;setWorking(false);if(!paused.current)freeze(false);}
    if(alive.current)completed?.();
  }
  async function discardAndReload(){
    if(locked.current||!ready.current)return;
    locked.current=true;setWorking(true);freeze(true);paused.current=true;setError("");
    let completed: (()=>void)|undefined;
    try{
      // Retain the frozen bytes until the replacement has actually loaded.
      if(!pending.current){const snapshot=adapter.current!.exportHwpx();pending.current={key:crypto.randomUUID(),base:version.current,snapshot};}
      await reloadLatest.current();
      if(!alive.current)return;
      pending.current=null;dirty.current=false;paused.current=false;
      setRevisionRetry(false);revisionDestination.current=undefined;setError("");setDiscarding(false);setLeaving(false);
      setHasChanges(false);setStatus("미반영 변경 없음");
      completed=discardDestination.current;discardDestination.current=undefined;
    }catch(cause){if(alive.current)fail(cause);}
    finally{locked.current=false;if(alive.current){setWorking(false);if(!paused.current)freeze(false);}}
    if(alive.current)completed?.();
  }
  async function advanced() {
    if(locked.current || !onAdvanced) return;
    if(dirty.current || pending.current){close(()=>void advanced());return;}
    if(paused.current) return;
    locked.current=true;freeze(true);
    try { await api(`/documents/${doc.id}/coedit`,{method:'DELETE',headers:{'X-Coedit-Session':token.current}});onAdvanced({...doc,current_version:version.current}); }
    catch(cause){fail(cause);} finally{locked.current=false;}
  }
  return <main className="workspace-shell coedit-workspace">
    <WorkspaceNav active="documents" label="문서 작업" onBack={()=>close()} backLabel="문서함으로" disabled={loading||working} navigationLabel="함께 작업" navigationIcon="share" navigation={<><p className="coedit-people-count" aria-label="공동편집 참여자">{new Set(participants.map(value=>value.user_id)).size}명 · {participants.length}개 세션 · {[...new Set(participants.map(value=>value.user_id))].join(', ')}</p><div ref={setLiveControls}/></>}>
      <button className="workspace-nav-route coedit-apply" aria-label="변경사항 반영" title="변경사항 반영" disabled={loading||working||!hasChanges||!!error} onClick={()=>void cycle.current(true)}><WorkspaceIcon name="save"/><span>변경사항 반영</span></button>
      {onTimeline&&<button className="workspace-nav-route" aria-label="문서 이력" title="문서 이력" disabled={loading||working} onClick={()=>close(onTimeline)}><WorkspaceIcon name="history"/><span>문서 이력</span></button>}
      {onAdvanced&&<button className="workspace-nav-route" aria-label="AI·서식 도구" disabled={loading||working||participants.length>1||!!error} title={participants.length>1?'다른 참여자가 나간 뒤 사용할 수 있습니다.':'AI·서식 도구'} onClick={()=>void advanced()}><WorkspaceIcon name="conversation"/><span>AI·서식 도구</span></button>}
      <button className="workspace-nav-route" aria-label="현재 내용 파일로 저장" title="현재 내용 파일로 저장" disabled={loading} onClick={recover}><WorkspaceIcon name="download"/><span>파일 다운로드</span></button>
      <details className="coedit-help"><summary aria-label="공동편집 안내" title="공동편집 안내"><WorkspaceIcon name="help"/><span>편집 안내</span></summary><p>‘변경사항 반영’을 눌러 저장합니다. 내 미반영 내용이 없을 때 다른 참여자의 반영본을 받아옵니다. 충돌하면 내 편집을 보존하고 별도 수정안으로 보관할 수 있습니다.</p>{doc.branch_source_id&&<p>이 수정안은 원본에 바로 반영되지 않습니다. 문서 이력에서 검토를 요청하세요.</p>}</details>
    </WorkspaceNav>
    <header className="coedit-document-header"><strong title={doc.name}>{doc.name}</strong>{onSwitchDocument&&<DocumentWorkcopySwitcher document={doc} disabled={loading||working||leaving||discarding} onSelect={target=>close(()=>onSwitchDocument(target))}/>}<span role="status">{status}</span></header>
    <div className="coedit-notices">
    {remoteUpdate&&<p className={`coedit-remote-update ${remoteUpdate}`} aria-live="polite">{remoteUpdate==='waiting'?'다른 참여자가 새 변경을 반영했습니다. 내 편집은 보존 중이며, ‘변경사항 반영’을 누르면 함께 병합합니다. 충돌하면 선택할 수 있도록 멈춥니다.':'다른 참여자의 변경을 이 문서에 반영했습니다.'}</p>}
    {error && <div className="coedit-error" role="alert"><div className="coedit-error-message"><strong>변경사항을 반영하지 못했습니다</strong><p>{error}</p><p>내 편집은 이 창에 남아 있습니다. 별도 작업본으로 이어서 작성하거나, 내 편집을 버리고 최신본을 열 수 있습니다.</p></div>
      {!doc.branch_source_id&&(hasChanges||pending.current)&&<button disabled={working} onClick={()=>void preserveRevision()}>{revisionRetry?"작업본 저장 다시 확인":"내 작업본으로 분리해 계속 작성"}</button>}
      {ready.current&&<button disabled={working} onClick={()=>{discardDestination.current=undefined;setDiscarding(true);}}>내 편집 버리고 최신본 열기</button>}
      {!revisionRetry&&<button disabled={working} onClick={() => { paused.current = false; setError(""); if(revisionRetry)void preserveRevision();else if (ready.current) void cycle.current(true); else { setLoading(true); setAttempt(value => value + 1); } }}>다시 시도</button>}
      <button disabled={working||loading} onClick={recover}>내 편집 파일로 백업</button>
    </div>}
    </div>
    {leaving&&<div className="coedit-leave-overlay"><section role="dialog" aria-modal="true" aria-label="미반영 변경 처리"><h2>아직 반영하지 않은 변경이 있습니다</h2>{error&&<p>{error}</p>}<p>공통 문서에 반영하거나 별도 수정안으로 보관할 수 있습니다.</p><button disabled={working} onClick={()=>void applyAndLeave()}>반영 후 이동</button>{!doc.branch_source_id&&<button disabled={working} onClick={()=>void preserveRevision(destination.current)}>내 작업본으로 보관 후 이동</button>}<button disabled={working} onClick={()=>setLeaving(false)}>계속 작성</button><button disabled={working} onClick={()=>{discardDestination.current=destination.current;setLeaving(false);setDiscarding(true);}}>변경을 버리고 이동</button></section></div>}
    {discarding&&<div className="coedit-leave-overlay"><section role="dialog" aria-modal="true" aria-label="내 편집 버리기 확인"><h2>내 편집을 버리고 최신본을 열까요?</h2><p>아직 반영하지 않은 내 변경만 버립니다. 다른 참여자가 반영한 내용과 기존 문서 이력은 유지됩니다.</p><p>내 내용을 남기려면 취소 후 ‘내 작업본으로 분리해 계속 작성’을 선택하세요.</p>{working?<p>최신 문서를 불러오는 중…</p>:error&&<p role="alert">{error}</p>}<button disabled={working} onClick={()=>void discardAndReload()}>내 편집 버리고 최신본 열기</button><button disabled={working} onClick={()=>setDiscarding(false)}>취소</button></section></div>}
    <section className="coedit-editor-area" aria-label="문서 편집 영역">
    <div className="coedit-canvas" ref={host} inert={loading} />
    <CoeditLive document={doc} adapter={adapter} token={token} version={version} epoch={liveEpoch} lastEdit={lastEdit} dirty={dirty} baseline={liveBaseline} enabled={!loading&&!error} controlsTarget={liveControls}/>
    </section>
  </main>;
}
