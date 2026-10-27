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
  const [progress, setProgress] = useState("원문 확인 중"), [pair, setPair] = useState<{ before: string; after: string; page: number; afterPage: number; pages: number } | null>(null);
  const [selected, setSelected] = useState(0), [lines, setLines] = useState<{ before: number; after: number }[]>([]);
  const context = turn.rewrite_context!, changes = turn.document_rewrites || [];
  const tables = turn.table_layout ? context.table_targets || [] : [];
  const locations = [...tables.map(table => ({ paragraph: table.paragraph, label: `표 · ${table.caption || table.paragraph + 1}`, textIndex: -1 })),
    ...changes.map((change, index) => ({ paragraph: change.paragraph, label: `본문 ${change.paragraph + 1}`, textIndex: index }))];
  const originals = changes.map(change => context.paragraphs.find(p => p.paragraph === change.paragraph)!);
  const beforeLength = originals.reduce((sum, p) => sum + p.length, 0), afterLength = changes.reduce((sum, p) => sum + Array.from(p.replacement).length, 0);
  const reduction = beforeLength ? Math.round((1 - afterLength / beforeLength) * 100) : 0;
  async function render(page: number, afterPage = page) {
    const source = capture.current, preview = engine.current;
    if (!source || !preview || !actions.isOwner(source)) throw new Error("비교 중 문서가 바뀌었습니다. 현재 문서로 다시 요청하세요.");
    const counts = await Promise.all([source.adapter.sdk.pageCount(), preview.sdk.pageCount()]);
    const svgs = await Promise.all([page >= 0 && page < counts[0] ? source.adapter.sdk.getPageSvg(page) : "", afterPage >= 0 && afterPage < counts[1] ? preview.sdk.getPageSvg(afterPage) : ""]);
    if (!alive.current) return;
    if (!actions.isOwner(source)) throw new Error("미리보기 준비 중 원문이 바뀌었습니다.");
    const images = svgs.map(svg => svg ? URL.createObjectURL(new Blob([safeSvg(svg)], { type: "image/svg+xml" })) : "");
    urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current = images.filter(Boolean);
    setPair({ before: images[0], after: images[1], page, afterPage, pages: Math.max(...counts) });
  }
  async function showChange(index: number) {
    const change = locations[index];
    setSelected(index);
    await render(capture.current!.adapter.bridge.paragraphPage(0, change.paragraph) - 1, engine.current!.bridge.paragraphPage(0, change.paragraph) - 1);
  }
  useEffect(() => {
    const controller = new AbortController(), element = dialog.current!;
    alive.current = true; element.showModal();
    void (async () => {
      try {
        const source = await actions.capture(turn);
        if (!alive.current) { actions.release(source); return; }
        capture.current = source;
        if (!context || (!changes.length && !tables.length) || !actions.isOwner(source) || await blockHash(source.source.bytes) !== context.source_sha256)
          throw new Error("AI가 읽은 원문과 현재 문서가 다릅니다. 현재 문서로 다시 요청하세요.");
        setProgress("별도 문서 준비 중");
        const preview = await EditorAdapter.mount(actions.getStagingHost(), { signal: controller.signal });
        if (!alive.current) { preview.destroy(); return; }
        engine.current = preview;
        let previewBytes = new Uint8Array(source.source.bytes);
        if (turn.table_layout) {
          setProgress("표 위치·제목 간격 반영 중");
          const result = await api<{ source_sha256: string; output_base64: string; output_sha256: string }>(`/assistant-turns/${turn.id}/layout-preview`, {
            ...jsonRequest("POST", { base_version: source.document.current_version, source_base64: base64(source.source.bytes) }),
            headers: { "Content-Type": "application/json", "X-Edit-Session": source.token }, signal: controller.signal,
          });
          previewBytes = Uint8Array.from(atob(result.output_base64), char => char.charCodeAt(0));
          if (result.source_sha256 !== context.source_sha256 || await blockHash(previewBytes) !== result.output_sha256 || !actions.isOwner(source))
            throw new Error("표 배치 미리보기와 원문이 다릅니다.");
        }
        await preview.load(previewBytes.buffer, "문서 전체 수정 비교.hwpx", { suppressDialogs: true, skipUnsavedGuard: true });
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
        setProgress("내용과 요청한 변경 범위 확인 중");
        const verified = await api<{ verified: boolean; source_sha256: string; output_sha256: string; turn_id: string }>(`/assistant-turns/${turn.id}/rewrite-check`, {
          ...jsonRequest("POST", { base_version: source.document.current_version, source_base64: base64(source.source.bytes), engine_output_base64: base64(output.bytes) }),
          headers: { "Content-Type": "application/json", "X-Edit-Session": source.token }, signal: controller.signal,
        });
        if (!alive.current) return;
        if (!verified.verified || verified.turn_id !== turn.id || verified.source_sha256 !== context.source_sha256 || verified.output_sha256 !== outputSha256 || !actions.isOwner(source))
          throw new Error("검증한 수정안과 현재 문서가 일치하지 않습니다.");
        prepared.current = { capture: source, adapter: preview, output, outputSha256, requestId: turn.id };
        const beforeLayout = source.adapter.bodyLayout(), afterLayout = preview.bodyLayout();
        setLines(changes.map(change => ({
          before: beforeLayout.paragraphs.find(p => p.section === 0 && p.paragraph === change.paragraph)!.lines.length,
          after: afterLayout.paragraphs.find(p => p.section === 0 && p.paragraph === change.paragraph)!.lines.length,
        })));
        await showChange(0);
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
    <header><div><h2>문서 전체 수정 전후 비교</h2><p>수정 가능 {context.paragraphs.length}개 중 {changes.length}개 변경 · {beforeLength} → {afterLength}자{reduction >= 0 ? ` · ${reduction}% 축약` : ` · ${-reduction}% 증가`}{tables.length ? ` · 표 ${tables.length}개 배치 변경` : ""}</p></div><button disabled={applying} autoFocus onClick={onClose}>비교 닫기</button></header>
    <div className="rewrite-preview-actions">
      <label>변경 위치 <select aria-label="수정한 문단" value={selected} disabled={busy || applying || !pair} onChange={event => { setBusy(true); void showChange(Number(event.target.value)).catch(cause => setError(errorMessage(cause))).finally(() => setBusy(false)); }}>
        {locations.map((location, index) => <option key={location.paragraph} value={index}>{location.label}{location.textIndex >= 0 && lines[location.textIndex] ? ` · ${lines[location.textIndex].before} → ${lines[location.textIndex].after}줄` : " · 배치"}</option>)}
      </select></label>
      <button disabled={busy || !pair || pair.page <= 0 || pair.afterPage <= 0} onClick={() => { setBusy(true); void render(pair!.page - 1, pair!.afterPage - 1).catch(cause => setError(errorMessage(cause))).finally(() => setBusy(false)); }}>이전 쪽</button>
      <span>{pair ? `${pair.page + 1} / ${pair.pages}쪽` : progress}</span>
      <button disabled={busy || !pair || pair.page + 1 >= pair.pages} onClick={() => { setBusy(true); void render(pair!.page + 1, pair!.afterPage + 1).catch(cause => setError(errorMessage(cause))).finally(() => setBusy(false)); }}>다음 쪽</button>
      <button className="primary" disabled={busy || applying || !prepared.current || !pair} onClick={async () => {
        if (!prepared.current) return;
        setApplying(true); setError("");
        try { await actions.adopt(prepared.current, turn); adopted.current = true; onClose(); }
        catch (cause) { if (alive.current) setError(errorMessage(cause)); }
        finally { if (alive.current) setApplying(false); }
      }}>{applying ? "문서에 반영 중…" : "문서에 적용 · 저장 전"}</button>
    </div>
    {busy && <p role="status">{progress}</p>}{error && <p className="error" role="alert">{error}</p>}
    <p className="hint">원문은 승인 전까지 유지됩니다. 적용 후 서버 저장으로 변경사항을 보관하세요. {turn.table_layout ? `${turn.table_layout.parent_half ? "표 왼쪽: 상위 기호와 본문 시작 사이 50% 지점. " : ""}${turn.table_layout.caption_gap_mm !== null ? `표 제목 아래 ${turn.table_layout.caption_gap_mm} mm. ` : ""}표 내용·제목·주석은 유지합니다.` : Object.entries(context.preserved).filter(([, count]) => count > 0).map(([name, count]) => `${name} ${count}개 유지`).join(" · ")}</p>
    <div className="rewrite-preview-pages">{pair && <>
      <figure><figcaption>AS-IS · 수정 전 {pair.page + 1}쪽</figcaption>{pair.before ? <img src={pair.before} alt="수정 전 실제 문서" /> : <p>해당 쪽 없음</p>}</figure>
      <figure><figcaption>TO-BE · 수정 후 {pair.afterPage + 1}쪽</figcaption>{pair.after ? <img src={pair.after} alt="수정 후 실제 문서" /> : <p>해당 쪽 없음</p>}</figure>
    </>}</div>
    {locations[selected]?.textIndex >= 0 && <details className="rewrite-preview-text" open><summary>선택한 문단의 문장 비교{lines[locations[selected].textIndex] ? ` · 실제 ${lines[locations[selected].textIndex].before} → ${lines[locations[selected].textIndex].after}줄` : ""}</summary>
      <div className="rewrite-text-pair"><div><strong>수정 전</strong><p className="text">{originals[locations[selected].textIndex]?.text}</p></div><div><strong>수정 후</strong><CandidateText original={originals[locations[selected].textIndex]?.text || ""} replacement={changes[locations[selected].textIndex]?.replacement || ""} ordinal={selected + 1} highlight /></div></div>
    </details>}
  </dialog>;
}
