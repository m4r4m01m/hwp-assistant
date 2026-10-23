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

export function ReportReview({ draft, formatName, includesSummary = true, feedback, onFeedback, onRevise, onFormat, busy, retry, revisionAvailable = true }: {
  draft: ReportDraft; formatName: string; includesSummary?: boolean; feedback: string;
  onFeedback: (value: string) => void; onRevise: () => void; onFormat: () => void;
  busy: boolean; retry: boolean; revisionAvailable?: boolean;
}) {
  return <div className="report-review" aria-label="초안 내용 검토">
    <header className="report-review-heading">
      <ReportProgress stage="review" />
      <div><h1>{draft.title}</h1>
        <p>내용을 확인하고 필요한 부분을 보완하세요. 문서 양식은 다음 단계에서 적용합니다.</p></div>
      <span className="report-format-chip">{formatName}</span>
    </header>
    <div className="report-review-body">
      <article className="report-review-content">
        <section className="report-review-summary"><h2>내용 요약</h2>{!includesSummary && <p className="hint">검토용 요약입니다. 선택한 양식에서는 별도 요약 절을 넣지 않습니다.</p>}<p><ReportText text={draft.summary} emphasis={draft.emphasis} /></p></section>
        {draft.sections.map((section, index) => <section className="report-review-section" key={index}>
          <h2><span>{index + 1}</span>{section.heading}</h2>
          <ReportContent blocks={[
            ...section.paragraphs.map(text => ({ text, kind: "body", emphasis: draft.emphasis })),
            ...(section.tables || []).map(table => ({ ...table, text: table.caption, kind: "table", emphasis: draft.emphasis })),
            ...(section.diagrams || []).map(diagram => ({ ...diagram, text: diagram.caption, kind: "diagram", emphasis: draft.emphasis })),
          ]} />
        </section>)}
        {draft.limitations.length > 0 && <section className="report-review-section">
          <h2>확인할 사항</h2>
          {draft.limitations.map((text, index) => <p key={index}>{text}</p>)}
        </section>}
        {draft.sources.length > 0 && <details className="report-review-sources">
          <summary>출처 {draft.sources.length}개 확인</summary>
          <ol>{draft.sources.map(source => <li key={source.id}>[{source.id}] <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>{source.published && ` · ${source.published}`}</li>)}</ol>
        </details>}
      </article>
      <aside className="report-feedback" aria-label="초안 보완 의견">
        <div className="report-review-next"><h2>내용 검토를 마쳤나요?</h2>
          <p>선택한 양식으로 배치한 뒤 문서에서 직접 편집할 수 있습니다.</p>
          <button className="primary" disabled={busy || retry || !!feedback.trim()} onClick={onFormat}>이 내용으로 문서 만들기</button>
          {feedback.trim() && !retry && <p className="hint">작성한 보완 의견을 먼저 반영하거나 지우고 진행하세요.</p>}
          <small>서버 저장은 마지막 단계에서 직접 확정합니다.</small>
        </div>
        <h2>보완할 내용</h2>
        <p>빠진 사실이나 수정할 부분을 적어 주세요. 현재 초안은 보관하고 보완본을 새로 작성합니다.</p>
        <label htmlFor="report-feedback">보완 의견</label>
        <textarea id="report-feedback" value={feedback} maxLength={6000} rows={7} disabled={busy || retry}
          placeholder="예: 추진 일정에 담당 부서를 추가하고, 대안별 장단점을 표로 비교해줘."
          onChange={event => onFeedback(event.target.value)} />
        <button disabled={busy || (!retry && (!revisionAvailable || !feedback.trim()))} onClick={onRevise}>{busy ? "보완 요청 확인 중…" : retry ? "같은 보완 요청 재확인" : "의견 반영해 다시 작성"}</button>
      </aside>
    </div>
  </div>;
}
