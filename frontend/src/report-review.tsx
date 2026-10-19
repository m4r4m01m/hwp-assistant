import { ReportContent, ReportText, type Emphasis } from "./report-content";
import { ReportProgress } from "./report-progress";
import "./report-review.css";

export type ReportDraft = {
  title: string; summary: string; emphasis?: Emphasis[];
  sections: { heading: string; paragraphs: string[];
    tables?: { caption: string; columns: string[]; rows: string[][] }[];
    diagrams?: { caption: string; steps: string[] }[] }[];
  sources: { id: string; title: string; url: string; published: string }[]; limitations: string[];
};

export function ReportReview({ draft, formatName, feedback, onFeedback, onRevise, onFormat, busy, retry }: {
  draft: ReportDraft; formatName: string; feedback: string;
  onFeedback: (value: string) => void; onRevise: () => void; onFormat: () => void;
  busy: boolean; retry: boolean;
}) {
  return <div className="report-review" aria-label="초안 내용 검토">
    <header className="report-review-heading">
      <ReportProgress stage="review" />
      <div><small>2. 내용 검토·보완</small><h1>{draft.title}</h1>
        <p>내용을 먼저 확인하세요. 보완한 뒤 선택한 양식으로 문서를 만들 수 있습니다.</p></div>
      <span className="report-format-chip">{formatName}</span>
    </header>
    <div className="report-review-body">
      <article className="report-review-content">
        <section className="report-review-summary"><h2>내용 요약</h2><p><ReportText text={draft.summary} emphasis={draft.emphasis} /></p></section>
        {draft.sections.map((section, index) => <section className="report-review-section" key={index}>
          <h2><span>{index + 1}</span>{section.heading}</h2>
          <ReportContent blocks={[
            ...section.paragraphs.map(text => ({ text, kind: "body", emphasis: draft.emphasis })),
            ...(section.tables || []).map(table => ({ ...table, text: table.caption, kind: "table", emphasis: draft.emphasis })),
            ...(section.diagrams || []).map(diagram => ({ ...diagram, text: diagram.caption, kind: "diagram", emphasis: draft.emphasis })),
          ]} />
        </section>)}
        {(draft.sources.length > 0 || draft.limitations.length > 0) && <section className="report-review-section">
          <h2>출처와 확인할 사항</h2>
          <ol>{draft.sources.map(source => <li key={source.id}>[{source.id}] <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>{source.published && ` · ${source.published}`}</li>)}</ol>
          {draft.limitations.map((text, index) => <p key={index}>{text}</p>)}
        </section>}
      </article>
      <aside className="report-feedback" aria-label="초안 보완 의견">
        <h2>보완할 내용이 있나요?</h2>
        <p>빠진 내용, 수정할 사실, 강조할 결론을 알려 주세요. 현재 초안은 보관하고 보완본을 새로 작성합니다.</p>
        <label htmlFor="report-feedback">보완 의견</label>
        <textarea id="report-feedback" value={feedback} maxLength={6000} rows={7} disabled={busy || retry}
          placeholder="예: 추진 일정에 담당 부서를 추가하고, 대안별 장단점을 표로 비교해줘."
          onChange={event => onFeedback(event.target.value)} />
        <button disabled={busy || (!retry && !feedback.trim())} onClick={onRevise}>{busy ? "보완 요청 확인 중…" : retry ? "같은 보완 요청 재확인" : "의견 반영해 다시 작성"}</button>
        <div className="report-review-next"><p>내용이 준비되면 실제 한글 서식과 쪽수를 적용합니다.</p>
          {feedback.trim() && !retry && <p className="hint">작성한 의견을 먼저 반영하거나 지우고 진행하세요.</p>}
          <button className="primary" disabled={busy || retry || !!feedback.trim()} onClick={onFormat}>이 내용으로 문서 만들기</button>
          <small>문서는 마지막 단계에서 직접 편집하고 서버에 저장합니다.</small>
        </div>
      </aside>
    </div>
  </div>;
}
