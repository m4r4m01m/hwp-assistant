import { useEffect, useRef, useState } from "react";
import { api, errorMessage, jsonRequest } from "./api";
import { EditorAdapter } from "./editor";
import { blockHash, safeSvg } from "./block-insert";
import type { TocCapture, PreparedToc } from "./toc";
import type { AssistantTurn } from "./document-assistant";
import { CandidateText } from "./candidate-text";
import "./rewrite-preview.css";

export type DocumentRewriteActions = {
  capture: (turn: AssistantTurn) => TocCapture | Promise<TocCapture>;
  isOwner: (capture: TocCapture) => boolean;
  release: (capture: TocCapture) => void;
  adopt: (prepared: PreparedToc, turn: AssistantTurn) => Promise<void>;
  getStagingHost: () => HTMLDivElement;
};
export type ParagraphRewrite = { section: 0; paragraph: number; before_sha256: string; replacement: string };
function base64(bytes: Uint8Array) {
  const chunks: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 8192) chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 8192)));
  return btoa(chunks.join(""));
}

export function DocumentRewritePreview({ turn, actions, onClose }: {
  turn: AssistantTurn; actions: DocumentRewriteActions; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null), capture = useRef<TocCapture | null>(null), engine = useRef<EditorAdapter | null>(null);
  const prepared = useRef<PreparedToc | null>(null), alive = useRef(false), adopted = useRef(false), urls = useRef<string[]>([]);
  const [busy, setBusy] = useState(true), [applying, setApplying] = useState(false), [error, setError] = useState("");
  const [progress, setProgress] = useState("원문 확인 중"), [pair, setPair] = useState<{ before: string; after: string; page: number; pages: number } | null>(null);
  const context = turn.rewrite_context!, changes = turn.document_rewrites || [];
  async function render(page: number) {
    const source = capture.current, preview = engine.current;
    if (!source || !preview || !actions.isOwner(source)) throw new Error("비교 중 문서가 바뀌었습니다. 현재 문서로 다시 요청하세요.");
    const counts = await Promise.all([source.adapter.sdk.pageCount(), preview.sdk.pageCount()]);
    const svgs = await Promise.all([page < counts[0] ? source.adapter.sdk.getPageSvg(page) : "", page < counts[1] ? preview.sdk.getPageSvg(page) : ""]);
    if (!alive.current) return;
    if (!actions.isOwner(source)) throw new Error("미리보기 준비 중 원문이 바뀌었습니다.");
    const images = svgs.map(svg => svg ? URL.createObjectURL(new Blob([safeSvg(svg)], { type: "image/svg+xml" })) : "");
    urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current = images.filter(Boolean);
    setPair({ before: images[0], after: images[1], page, pages: Math.max(...counts) });
  }
  useEffect(() => {
    const controller = new AbortController(), element = dialog.current!;
    alive.current = true; element.showModal();
    void (async () => {
      try {
        const source = await actions.capture(turn);
        if (!alive.current) { actions.release(source); return; }
        capture.current = source;
        if (!context || !changes.length || !actions.isOwner(source) || await blockHash(source.source.bytes) !== context.source_sha256)
          throw new Error("AI가 읽은 원문과 현재 문서가 다릅니다. 현재 문서로 다시 요청하세요.");
        setProgress("별도 문서 준비 중");
        const preview = await EditorAdapter.mount(actions.getStagingHost(), { signal: controller.signal });
        if (!alive.current) { preview.destroy(); return; }
        engine.current = preview;
        await preview.load(new Uint8Array(source.source.bytes).buffer, "문서 전체 수정 비교.hwpx", { suppressDialogs: true, skipUnsavedGuard: true });
        for (const [index, change] of changes.entries()) {
          if (!alive.current) return;
          const original = context.paragraphs.find(p => p.section === change.section && p.paragraph === change.paragraph);
          if (!original || change.before_sha256 !== original.text_sha256) throw new Error("수정안의 본문 위치가 다릅니다.");
          setProgress(`본문 문단 반영 ${index + 1} / ${changes.length}`);
          await preview.sdk.focusTarget({ kind: "body_paragraph", section: original.section, paragraph: original.paragraph, charOffset: 0, length: original.length });
          if (!alive.current) return;
          const snapshot = preview.selection();
          if (snapshot.evidence.text !== original.text || snapshot.evidence.textSha256 !== original.text_sha256)
            throw new Error("비교할 문단과 요청 원문이 다릅니다.");
          await preview.apply(snapshot, change.replacement);
        }
        if (!alive.current) return;
        const output = preview.exportHwpx(), outputSha256 = await blockHash(output.bytes);
        if (output.contentLoss.count || !actions.isOwner(source)) throw new Error("문서 보존 상태를 확인할 수 없습니다. 원본은 그대로입니다.");
        setProgress("제목·표·주석·서식 보존 확인 중");
        const verified = await api<{ verified: boolean; source_sha256: string; output_sha256: string; turn_id: string }>(`/assistant-turns/${turn.id}/rewrite-check`, {
          ...jsonRequest("POST", { base_version: source.document.current_version, source_base64: base64(source.source.bytes), engine_output_base64: base64(output.bytes) }),
          headers: { "Content-Type": "application/json", "X-Edit-Session": source.token }, signal: controller.signal,
        });
        if (!alive.current) return;
        if (!verified.verified || verified.turn_id !== turn.id || verified.source_sha256 !== context.source_sha256 || verified.output_sha256 !== outputSha256 || !actions.isOwner(source))
          throw new Error("검증한 수정안과 현재 문서가 일치하지 않습니다.");
        prepared.current = { capture: source, adapter: preview, output, outputSha256, requestId: turn.id };
        await render(0);
      } catch (cause) { if (alive.current) setError(errorMessage(cause)); }
      finally { if (alive.current) setBusy(false); }
    })();
    return () => {
      alive.current = false; controller.abort(); element.close();
      if (!adopted.current) engine.current?.destroy();
      if (capture.current) actions.release(capture.current);
      urls.current.forEach(url => URL.revokeObjectURL(url));
    };
  }, []);
  return <dialog ref={dialog} className="rewrite-preview-dialog" aria-label="문서 전체 수정 전후 비교" onCancel={event => { event.preventDefault(); if (!applying) onClose(); }}>
    <header><div><h2>문서 전체 수정 전후 비교</h2><p>{changes.length}개 본문 문단 수정 · 제목·표·주석은 보존합니다.</p></div><button disabled={applying} autoFocus onClick={onClose}>비교 닫기</button></header>
    <div className="rewrite-preview-actions">
      <button disabled={busy || !pair || pair.page === 0} onClick={() => { setBusy(true); void render(pair!.page - 1).catch(cause => setError(errorMessage(cause))).finally(() => setBusy(false)); }}>이전 쪽</button>
      <span>{pair ? `${pair.page + 1} / ${pair.pages}쪽` : progress}</span>
      <button disabled={busy || !pair || pair.page + 1 >= pair.pages} onClick={() => { setBusy(true); void render(pair!.page + 1).catch(cause => setError(errorMessage(cause))).finally(() => setBusy(false)); }}>다음 쪽</button>
      <button className="primary" disabled={busy || applying || !prepared.current || !pair} onClick={async () => {
        if (!prepared.current) return;
        setApplying(true); setError("");
        try { await actions.adopt(prepared.current, turn); adopted.current = true; onClose(); }
        catch (cause) { if (alive.current) setError(errorMessage(cause)); }
        finally { if (alive.current) setApplying(false); }
      }}>{applying ? "문서에 반영 중…" : "문서에 적용 · 저장 전"}</button>
    </div>
    {busy && <p role="status">{progress}</p>}{error && <p className="error" role="alert">{error}</p>}
    <p className="hint">원문은 승인 전까지 유지됩니다. 적용 후 서버 저장으로 변경사항을 보관하세요. {Object.entries(context.preserved).filter(([, count]) => count > 0).map(([name, count]) => `${name} ${count}개 유지`).join(" · ")}</p>
    <div className="rewrite-preview-pages">{pair && <>
      <figure><figcaption>AS-IS · 수정 전</figcaption>{pair.before ? <img src={pair.before} alt="수정 전 실제 문서" /> : <p>해당 쪽 없음</p>}</figure>
      <figure><figcaption>TO-BE · 수정 후</figcaption>{pair.after ? <img src={pair.after} alt="수정 후 실제 문서" /> : <p>해당 쪽 없음</p>}</figure>
    </>}</div>
    <details className="rewrite-preview-text"><summary>수정한 {changes.length}개 문단 확인</summary>{changes.map((change, index) => <CandidateText key={`${change.section}:${change.paragraph}`} original={context.paragraphs.find(p => p.paragraph === change.paragraph)?.text || ""} replacement={change.replacement} ordinal={index + 1} highlight />)}</details>
  </dialog>;
}
