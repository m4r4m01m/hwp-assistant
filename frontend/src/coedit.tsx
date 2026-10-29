import { useEffect, useRef, useState } from "react";
import { api, downloadBytes, downloadFile, errorMessage, fileUrl, jsonRequest, type Document } from "./api";
import { EditorAdapter, type ExportSnapshot } from "./editor";
import "./coedit.css";

type Sync = { current_version: string; participants: {id: string; user_id: string}[] };
type Pending = {key: string; base: string; snapshot: ExportSnapshot};
export function Coedit({ document: doc, onClose, onTimeline, onAdvanced }: {document: Document; onClose: () => void; onTimeline?:()=>void; onAdvanced?:(doc:Document)=>void}) {
  const host = useRef<HTMLDivElement>(null), adapter = useRef<EditorAdapter | null>(null);
  const token = useRef(""), version = useRef(doc.current_version), pending = useRef<Pending | null>(null);
  const dirty = useRef(false), lastEdit = useRef(0), paused = useRef(false), alive = useRef(false), locked = useRef(false), ready = useRef(false);
  const joinId = useRef(crypto.randomUUID()), cycle = useRef<(force?: boolean) => Promise<void>>(async () => {});
  const [status, setStatus] = useState("공동편집 참여 중…"), [error, setError] = useState("");
  const [participants, setParticipants] = useState<Sync["participants"]>([]), [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
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
      expectedEpoch = current.exportHwpx().epoch;
      version.current = next; dirty.current = false;
    }
    cycle.current = async (force = false) => {
      if (!alive.current || !ready.current || locked.current || paused.current) return;
      const typing = composing || (dirty.current && Date.now() - lastEdit.current < 800);
      if (!force && typing && Date.now() - lastPoll < 20000) return;
      if (force && composing) return;
      if (!force && !dirty.current && !pending.current && Date.now() - lastPoll < 2000) return;
      locked.current = true;
      try {
        if (adapter.current!.bridge.documentState().documentEpoch !== expectedEpoch)
          throw new Error("편집기에서 다른 문서가 열렸습니다. 원래 공동문서에 덮어쓰지 않습니다. 현재 내용을 다운로드하고 문서함에서 다시 여세요.");
        if ((dirty.current && (!typing || force)) || pending.current) {
          // Freeze only the short publish/rebase transaction. Never reload over
          // typing that occurred after the captured snapshot, including IME.
          freeze(true); setStatus("공동 변경 저장 중…");
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
        }
        if (alive.current) setStatus(dirty.current ? "내 변경 동기화 대기" : "공동 변경 저장 완료");
      } catch (cause) { if (alive.current) fail(cause); }
      finally { locked.current = false; if (alive.current && !paused.current) freeze(false); }
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
        dirty.current = true; lastEdit.current = Date.now(); setStatus("내 변경 동기화 대기");
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
  async function close(next=onClose) {
    if (locked.current) return;
    await cycle.current(true);
    if ((dirty.current || pending.current) && !confirm("내 변경이 아직 공동문서에 저장되지 않았습니다. 복구용 다운로드로 보관했나요? 저장하지 않고 나가시겠습니까?")) return;
    next();
  }
  async function advanced() {
    if(locked.current || !onAdvanced) return;
    await cycle.current(true);
    if(paused.current || dirty.current || pending.current) return;
    locked.current=true;freeze(true);
    try { await api(`/documents/${doc.id}/coedit`,{method:'DELETE',headers:{'X-Coedit-Session':token.current}});onAdvanced({...doc,current_version:version.current}); }
    catch(cause){fail(cause);} finally{locked.current=false;}
  }
  return <main className="coedit-workspace">
    <header><button disabled={loading} onClick={() => void close()}>저장 후 나가기</button><strong>{doc.name}</strong><span>{doc.branch_source_id?'작업본 편집':'문서 편집'}</span>{onTimeline&&<button disabled={loading} onClick={()=>void close(onTimeline)}>문서 이력·검토</button>}{onAdvanced&&<button disabled={loading||participants.length>1||!!error} title={participants.length>1?'같은 작업본의 다른 참여자가 나간 뒤 사용할 수 있습니다.':'작업본을 저장하고 단독 편집 도구를 엽니다.'} onClick={()=>void advanced()}>AI·서식 도구</button>}<button disabled={loading} onClick={recover}>내 편집 다운로드</button></header>
    {doc.branch_source_id&&<p className="coedit-help">수정은 이 작업본에 저장됩니다. 공통 문서에 반영하려면 ‘문서 이력·검토’에서 통합을 요청하세요. 이 작업본의 참여자는 자동으로 함께 편집합니다.</p>}
    <div className="coedit-status"><span role="status">{status}</span><span aria-label="공동편집 참여자">{new Set(participants.map(value => value.user_id)).size}명 · {participants.length}개 세션 · {[...new Set(participants.map(value => value.user_id))].join(", ")}</span></div>
    <details className="coedit-help"><summary>공동편집 안내</summary><p>입력을 잠시 멈추면 변경을 저장하고 약 2초마다 다른 참여자의 변경을 반영합니다. 겹치지 않는 본문 변경은 병합합니다. 같은 내용이나 복잡한 구조 변경이 충돌하면 내 편집을 보존한 채 멈춥니다. 동기화 중에는 입력을 잠시 멈추며, AI·표준 디자인 적용은 모두 나간 뒤 단독 편집에서 사용합니다.</p></details>
    {error && <div className="coedit-error" role="alert">{error}<button onClick={() => { paused.current = false; setError(""); if (ready.current) void cycle.current(true); else { setLoading(true); setAttempt(value => value + 1); } }}>{ready.current ? "같은 변경 다시 확인" : "다시 참여"}</button><button onClick={recover}>내 편집 다운로드</button><button onClick={() => {
      if (confirm("저장되지 않은 내 편집은 버리고 최신 공동문서로 다시 엽니다. 필요한 내용은 먼저 다운로드하세요.")) { dirty.current = false; pending.current = null; onClose(); }
    }}>미저장 편집을 버리고 나가기</button></div>}
    <div className="coedit-canvas" ref={host} inert={loading} />
  </main>;
}
