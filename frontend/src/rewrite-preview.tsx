import { useEffect, useRef, useState } from "react";
import { EditorAdapter, type Snapshot } from "./editor";
import { errorMessage } from "./api";
import { safeSvg } from "./block-insert";
import type { RewriteTask } from "./rewrite";
import { CandidateText } from "./candidate-text";
import "./rewrite-preview.css";

export type CaptureRewritePreview = (task: RewriteTask) => Promise<Uint8Array>;
type Candidate = RewriteTask["candidates"][number];
type Pair = { before: string; after: string; page: number; pages: number };

// The source capture is fenced by the live editor. The isolated engine must
// independently match the exact paragraph evidence after HWPX round-trip.
export function assertRewritePreviewSource(original: Snapshot, loaded: Snapshot) {
  const target = original.selection.target, actual = loaded.selection.target;
  if (!target || !actual || target.kind !== "body_paragraph" ||
      ["kind", "section", "paragraph", "charOffset", "length"].some(key =>
        target[key as keyof typeof target] !== actual[key as keyof typeof actual]) ||
      ["text", "textSha256", "formatSha256", "adjacentContextSha256"].some(key =>
        original.evidence[key as keyof Snapshot["evidence"]] !== loaded.evidence[key as keyof Snapshot["evidence"]]))
    throw new Error("미리보기 문단이 요청 원문·서식과 다릅니다. 현재 문단에서 새 수정안을 요청하세요.");
}

export function RewritePreview({ task, candidate, capture, approve, onClose, onCandidateChange }: {
  task: RewriteTask; candidate: Candidate; capture: CaptureRewritePreview;
  approve?: (applicationId: string) => Promise<void>; onClose: () => void;
  onCandidateChange?: (candidate: Candidate) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null), host = useRef<HTMLDivElement>(null);
  const engine = useRef<EditorAdapter | null>(null), source = useRef<Uint8Array | null>(null);
  const alive = useRef(false), working = useRef(false), urls = useRef<string[]>([]);
  const applicationId = useRef(task.application?.id || crypto.randomUUID());
  const [pair, setPair] = useState<Pair | null>(null), [busy, setBusy] = useState(true), [saving, setSaving] = useState(false);
  const [retrySave, setRetrySave] = useState(false);
  const [error, setError] = useState("");
  function image(svg: string) {
    const url = URL.createObjectURL(new Blob([safeSvg(svg)], { type: "image/svg+xml" }));
    urls.current.push(url); return url;
  }
  async function render(requested?: number) {
    if (working.current || !source.current || !engine.current) return;
    working.current = true; setBusy(true); setError("");
    const preview = engine.current;
    try {
      await preview.load(new Uint8Array(source.current).buffer, "수정 비교.hwpx", { suppressDialogs: true, skipUnsavedGuard: true });
      if (!alive.current) return;
      const target = task.snapshot.selection.target;
      if (!target) throw new Error("비교할 본문 문단이 없습니다.");
      const focused = await preview.sdk.focusTarget(target);
      if (!alive.current) return;
      const snapshot = preview.selection();
      assertRewritePreviewSource(task.snapshot, snapshot);
      const beforePages = await preview.sdk.pageCount();
      const page = requested ?? Math.max(0, focused.page - 1);
      const beforeSvg = page < beforePages ? await preview.sdk.getPageSvg(page) : "";
      if (!alive.current) return;
      const before = beforeSvg ? image(beforeSvg) : "";
      await preview.apply(snapshot, candidate.replacement);
      if (!alive.current) return;
      const exported = preview.exportHwpx();
      if (exported.contentLoss.count) throw new Error("수정안 미리보기에서 내용 손실이 감지됐습니다. 원본 문서는 그대로입니다.");
      const afterPages = await preview.sdk.pageCount();
      const afterSvg = page < afterPages ? await preview.sdk.getPageSvg(page) : "";
      if (!alive.current) return;
      const after = afterSvg ? image(afterSvg) : "";
      const retained = new Set([before, after]);
      urls.current.filter(url => !retained.has(url)).forEach(url => URL.revokeObjectURL(url));
      urls.current = urls.current.filter(url => retained.has(url));
      setPair({ before, after, page, pages: Math.max(beforePages, afterPages) });
    } catch (cause) {
      if (alive.current) { setPair(null); setError(errorMessage(cause)); }
    } finally { working.current = false; if (alive.current) setBusy(false); }
  }
  useEffect(() => {
    const controller = new AbortController(), element = dialog.current!;
    const previous = document.activeElement as HTMLElement | null;
    alive.current = true; element.showModal();
    void (async () => {
      try {
        const bytes = await capture(task);
        if (!alive.current) return;
        source.current = bytes;
        const preview = await EditorAdapter.mount(host.current!, { signal: controller.signal });
        if (!alive.current) { preview.destroy(); return; }
        engine.current = preview;
        await render();
      } catch (cause) { if (alive.current) { setError(errorMessage(cause)); setBusy(false); } }
    })();
    return () => {
      alive.current = false; controller.abort(); engine.current?.destroy(); engine.current = null;
      urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current = [];
      element.close(); if (previous?.isConnected) previous.focus();
    };
  }, []);
  return <dialog ref={dialog} className="rewrite-preview-dialog" aria-label="문장 수정 전후 비교" onCancel={event => {
    event.preventDefault(); if (!saving) onClose();
  }}>
    <header><div><h2>문장 수정 전후 비교</h2><p>후보 {candidate.ordinal}을 실제 문서에 배치한 미리보기입니다.</p></div>
      <button disabled={saving} autoFocus onClick={onClose}>비교 닫기</button></header>
    <div className="rewrite-preview-actions">
      {onCandidateChange && <label>수정안 <select aria-label="비교할 수정안" value={candidate.id} disabled={busy || saving || retrySave}
        onChange={event => { const next = task.candidates.find(item => item.id === event.target.value); if (next) onCandidateChange(next); }}>
        {task.candidates.map(item => <option key={item.id} value={item.id}>후보 {item.ordinal}</option>)}
      </select></label>}
      <button disabled={busy || !pair || pair.page === 0} onClick={() => void render(pair!.page - 1)}>이전 쪽</button>
      <span>{pair ? `${pair.page + 1} / ${pair.pages}쪽` : "문서 배치 확인 중"}</span>
      <button disabled={busy || !pair || pair.page + 1 >= pair.pages} onClick={() => void render(pair!.page + 1)}>다음 쪽</button>
      {approve && <button className="primary" disabled={busy || saving || !pair} onClick={async () => {
        setSaving(true); setError("");
        try { await approve(applicationId.current); if (alive.current) onClose(); }
        catch (cause) { if (alive.current) { setRetrySave(true); setError(errorMessage(cause)); } }
        finally { if (alive.current) setSaving(false); }
      }}>{saving ? "적용·저장 확인 중…" : retrySave ? "같은 수정안 저장 재확인" : "이 수정안 적용·저장"}</button>}
    </div>
    {busy && <p role="status">원본을 보존하면서 별도 문서에 수정안을 배치하고 있습니다…</p>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="rewrite-preview-pages" aria-busy={busy}>{pair && <>
      <figure><figcaption>AS-IS · 수정 전</figcaption>{pair.before ? <img src={pair.before} alt="수정 전 실제 문서" /> : <p>해당 쪽 없음</p>}</figure>
      <figure><figcaption>TO-BE · 수정 후</figcaption>{pair.after ? <img src={pair.after} alt="수정 후 실제 문서" /> : <p>해당 쪽 없음</p>}</figure>
    </>}</div>
    <details className="rewrite-preview-text"><summary>바뀐 문장 확인</summary>
      <CandidateText original={task.snapshot.evidence.text} replacement={candidate.replacement} ordinal={candidate.ordinal} highlight />
    </details>
    <div ref={host} className="rewrite-preview-engine" aria-hidden="true" inert />
  </dialog>;
}
