import { useEffect, useRef, useState } from "react";
import { api, ApiError, jsonRequest } from "./api";
import type { Snapshot } from "./editor";

export type ConfirmedFact = {
  id: string; text: string;
  source: "user_statement" | "document_version" | "user_confirmed_ai_excerpt";
  source_note: string; version_id: string | null;
};
export type WritingContext = {
  revision: number; base_version: string; stale: boolean; purpose: string; facts: string;
  selection: Snapshot | null; writing_criteria: string; confirmed_facts: ConfirmedFact[];
  confirmation: { id: string; by: string; at: number } | null;
};
export const sourceLabel = (source: ConfirmedFact["source"]) => ({
  user_statement: "사용자 입력", document_version: "문서 저장 버전", user_confirmed_ai_excerpt: "AI 문장을 사용자가 확인",
})[source];
const draftOf = (value: WritingContext) => ({ purpose: value.purpose, facts: value.facts,
  writing_criteria: value.writing_criteria || "", confirmed_facts: value.confirmed_facts || [] });

export function WritingContextEditor({ thread, context, baseVersion, writable, capture, onSaved, onDirty }: {
  thread: string; context: WritingContext; baseVersion: string; writable: boolean;
  capture?: () => Snapshot; onSaved: () => Promise<void>; onDirty: (dirty: boolean) => void;
}) {
  const [draft, setDraft] = useState(() => draftOf(context));
  const [revision, setRevision] = useState(context.revision);
  const [dirty, setDirty] = useState(false), [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const edits = useRef(0);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const pending = useRef<{ body: object; edits: number } | null>(null);
  const [needsRecheck, setNeedsRecheck] = useState(false);
  const stale = context.stale || context.base_version !== baseVersion;
  const conflict = context.revision !== revision;
  useEffect(() => {
    if (!dirty) { setDraft(draftOf(context)); setRevision(context.revision); setConfirmed(false); }
  }, [context, dirty]);
  useEffect(() => { onDirty(dirty || needsRecheck); }, [dirty, needsRecheck, onDirty]);
  const change = (next: typeof draft) => { edits.current++; setDraft(next); setDirty(true); setConfirmed(false); setNotice(""); };
  function factChange(id: string, updates: Partial<ConfirmedFact>) {
    change({ ...draft, confirmed_facts: draft.confirmed_facts.map(f => f.id === id ? { ...f, ...updates } : f) });
  }
  async function save(shareSelection: boolean) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      if (!pending.current) {
        const selection = shareSelection && capture ? capture() : context.selection;
        pending.current = { edits: edits.current, body: {
          request_id: crypto.randomUUID(), expected_revision: revision, base_version: baseVersion, ...draft,
          selection, confirm_for_rewrite: confirmed,
        }};
      }
      const frozen = pending.current;
      setNeedsRecheck(true); onDirty(true);
      const saved = await api<{revision: number}>(`/threads/${thread}/context`, jsonRequest("PUT", frozen.body));
      if (!active.current) return;
      await onSaved();
      if (!active.current) return;
      pending.current = null; setNeedsRecheck(false); setRevision(saved.revision);
      if (edits.current === frozen.edits) { setDirty(false); onDirty(false); }
      const wasConfirmed = (frozen.body as {confirm_for_rewrite: boolean}).confirm_for_rewrite;
      setNotice(edits.current !== frozen.edits ? "이전 저장을 확인했습니다. 그 뒤에 입력한 변경은 아직 저장하지 않았습니다." : wasConfirmed ? "확인한 작성 정보를 저장했습니다. 후보 요청에서 사용 여부를 선택하세요." : "작성 정보를 저장했습니다. 후보에 사용하려면 내용을 확인한 뒤 확인 체크와 함께 저장하세요.");
      setConfirmed(false);
    } catch (e) {
      if (!active.current) return;
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) { pending.current = null; setNeedsRecheck(false); }
      else setNeedsRecheck(!!pending.current);
      setError(`${String(e)} 입력은 화면에 보존했습니다. ${pending.current ? "같은 저장 내용으로 결과를 재확인하세요. 추가 입력은 별도로 유지됩니다." : "대화 새로고침으로 서버 상태를 확인할 수 있습니다."}`);
    }
    finally { if (active.current) setBusy(false); }
  }
  return <section className="writing-context" aria-label="후보 작성 정보">
    <h2>추가 작성 정보</h2>
    <p className="hint">문장 수정에 함께 사용할 목적·확인 사실·작성 기준을 입력하세요.</p>
    {stale && <p className="notice">문서의 저장본이 바뀌었습니다. 작성 정보를 확인하고 다시 저장하세요.</p>}
    {context.confirmation && <p className="hint" data-testid="context-confirmation">내용 확인 완료 · {new Date(context.confirmation.at * 1000).toLocaleString()}</p>}
    {conflict && dirty && <p role="alert">다른 변경이 저장되어 있습니다. 현재 입력은 유지했습니다. 내용을 비교한 뒤 다시 작성하거나 서버 내용을 불러오세요.</p>}
    {conflict && dirty && <details open><summary>서버에 저장된 최신 작성 정보 비교</summary>
      <ContextSnapshot value={{ ...context, id: thread }} />
      {writable && <button disabled={busy || needsRecheck} onClick={() => { setRevision(context.revision); setConfirmed(false); setError("");
        setNotice("최신 내용을 확인한 것으로 표시했습니다. 현재 입력을 검토한 뒤 저장하세요. 내용은 자동 병합하지 않았습니다."); }}>최신 내용을 확인하고 내 입력 계속 편집</button>}
    </details>}
    <label>작성 목적<textarea aria-label="작성 목적" value={draft.purpose} disabled={!writable || busy}
      onChange={e => change({ ...draft, purpose: e.target.value })} maxLength={4000} placeholder="누구에게, 무엇을 전달하기 위한 문서인가요?" /></label>
    <h3>사용자가 확인한 사실</h3>
    <p className="hint">실제로 사용할 문장만 넣으세요. AI에서 가져온 문장은 출처를 표시하며, 확인 체크는 외부 사실 검증을 뜻하지 않습니다.</p>
    {draft.confirmed_facts.map((fact, index) => <fieldset key={fact.id} disabled={!writable || busy}>
      <legend>확인 사실 {index + 1}</legend>
      <textarea aria-label={`확인 사실 ${index + 1}`} value={fact.text} maxLength={2000} onChange={e => factChange(fact.id, { text: e.target.value })} />
      <label>출처<select aria-label={`사실 ${index + 1} 출처`} value={fact.source} onChange={e => {
        const source = e.target.value as ConfirmedFact["source"];
        factChange(fact.id, { source, version_id: source === "document_version" ? baseVersion : null });
      }}>{(["user_statement", "document_version", "user_confirmed_ai_excerpt"] as const).map(source => <option key={source} value={source}>{sourceLabel(source)}</option>)}</select></label>
      <label>출처 설명<input aria-label={`사실 ${index + 1} 출처 설명`} value={fact.source_note} maxLength={500} onChange={e => factChange(fact.id, { source_note: e.target.value })} placeholder="예: 회의에서 확인, AI 초안 중 확인한 문장" /></label>
      {fact.version_id && <small>출처 버전: {fact.version_id}</small>}
      <button onClick={() => change({ ...draft, confirmed_facts: draft.confirmed_facts.filter(f => f.id !== fact.id) })}>사실 {index + 1} 삭제</button>
    </fieldset>)}
    {writable && <button disabled={busy || draft.confirmed_facts.length >= 20} onClick={() => change({ ...draft,
      confirmed_facts: [...draft.confirmed_facts, { id: crypto.randomUUID(), text: "", source: "user_statement", source_note: "", version_id: null }] })}>확인 사실 추가</button>}
    <label>작성 기준<textarea aria-label="작성 기준" value={draft.writing_criteria} disabled={!writable || busy}
      onChange={e => change({ ...draft, writing_criteria: e.target.value })} maxLength={4000} placeholder="독자, 말투, 분량, 유지할 표현 등을 적으세요." /></label>
    {!!(draft.facts || context.facts) && <details><summary>이전 작성 메모</summary>
      <label>확정 사실·작성 기준<textarea aria-label="확정 사실·작성 기준" value={draft.facts} disabled={!writable || busy}
        onChange={e => change({ ...draft, facts: e.target.value })} maxLength={12000} /></label>
      <p className="hint">기존 메모는 보존합니다. 후보에 사용할 내용은 위의 확인 사실 또는 작성 기준으로 직접 옮기세요.</p>
    </details>}
    {writable && <>
      <label className="check-label"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />목적·사실·기준을 확인했으며 후보 생성에 사용할 수 있습니다</label>
      <button disabled={busy || conflict && !needsRecheck} onClick={() => void save(false)}>{needsRecheck ? "작성 정보 저장 결과 재확인" : "작성 정보 저장"}</button>
      {capture && <button disabled={busy || conflict || needsRecheck} onClick={() => void save(true)}>현재 문단을 작성 정보에 저장</button>}
      {dirty && <p className="hint">작성 정보에 아직 저장하지 않은 변경이 있습니다.</p>}
      {dirty && <button disabled={busy || needsRecheck} onClick={() => {
        if (confirm("저장하지 않은 작성 정보를 버리고 마지막으로 불러온 서버 내용으로 되돌릴까요?")) {
          setDirty(false); onDirty(false); setDraft(draftOf(context)); setRevision(context.revision); setError("");
        }
      }}>작성 정보 편집 취소</button>}
    </>}
    {capture && <p className="hint">문단 저장은 이 앱에 기록하는 작업입니다. AI 요청은 수정 후보 만들기에서 진행합니다.</p>}
    {context.selection && <details><summary>작성 정보에 저장한 문단</summary><p className="text">{context.selection.evidence.text}</p></details>}
    {context.revision > 0 && <details><summary>작성 정보 저장 기록</summary><p>작성 정보 버전 {context.revision}{context.confirmation ? ` · 확인자 ${context.confirmation.by}` : ""}</p></details>}
    {notice && <p className="hint" data-testid="context-saved">{notice}</p>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}

export type FrozenWritingContext = Pick<WritingContext, "purpose" | "writing_criteria" | "confirmed_facts" | "confirmation"> & {
  id: string; revision: number; base_version: string;
};
export function ContextSnapshot({ value }: { value: FrozenWritingContext }) {
  return <div className="context-snapshot" data-testid="frozen-writing-context">
    <p>작성 정보 리비전 {value.revision} · 확인자 {value.confirmation?.by}</p>
    {value.purpose && <p className="text">작성 목적: {value.purpose}</p>}
    {value.confirmed_facts.map(fact => <div key={fact.id}><p className="text">{fact.text}</p>
      <small>{sourceLabel(fact.source)}{fact.source_note ? ` · ${fact.source_note}` : ""}{fact.version_id ? ` · 버전 ${fact.version_id}` : ""}</small></div>)}
    {value.writing_criteria && <p className="text">작성 기준: {value.writing_criteria}</p>}
  </div>;
}
