import "./report-research.css";

export type ResearchPolicy = "require_completed" | "allow_unverified";
export type ResearchNoticeData = {
  version: "research-notice-v1";
  policy: ResearchPolicy;
  requested: boolean;
  status: "not_requested" | "completed" | "unverified";
  completed_calls: number;
  started_calls?: number;
  incomplete_calls?: number;
  source_verification: "unverified";
  warnings: { code: string; message: string }[];
};
type Source = { id: string; title: string; url: string; published: string };

export function ReportResearchNotice({ notice, compact = false }: { notice?: ResearchNoticeData; compact?: boolean }) {
  if (!notice) return null;
  const inherited = notice.warnings.some(warning => warning.code === "inherited_research_unverified");
  const title = notice.status === "not_requested" ? "공개자료 조회 안 함"
    : notice.status === "unverified" ? "자료 조회 미완료 · 최신 정보 미검증"
    : inherited ? "이전 초안의 자료 확인 미완료 · 재검토 필요"
    : "공개자료 조회 완료 · 출처별 확인 필요";
  const warnings = <ul>{notice.warnings.map((warning, index) => <li key={`${warning.code}:${index}`}>{warning.message}</li>)}</ul>;
  return <aside role="note" aria-label="자료 확인 상태" className={`report-research-notice${notice.status === "unverified" || inherited ? " report-research-attention" : ""}`}>
    <strong>{title}</strong>
    <p>{notice.requested ? `공개자료 조회 ${notice.completed_calls}회 완료${notice.incomplete_calls ? ` · ${notice.incomplete_calls}회 미완료` : ""}. ` : "이번 요청에서는 공개 웹 자료를 조회하지 않았습니다. "}내용·출처를 직접 확인한 뒤 문서를 확정하세요.</p>
    {notice.warnings.length > 0 && (compact ? <details><summary>자료 확인 사항 {notice.warnings.length}개</summary>{warnings}</details> : warnings)}
  </aside>;
}

export function ReportSources({ sources, unverified = false }: { sources: Source[]; unverified?: boolean }) {
  if (!sources.length) return <p className="hint">기재된 출처가 없습니다.</p>;
  return <div className="report-source-list">
    {unverified && <p className="hint">AI가 제시한 미검증 참고 출처입니다. 주소·발행일·내용을 직접 확인하세요. 인용 표시는 원문과의 일치나 사실 검증을 보증하지 않습니다.</p>}
    <ol>{sources.map(source => <li key={source.id}>
      <span>[{source.id}] </span>{unverified ? <><strong>{source.title}</strong><span className="report-source-badge">웹 확인 미완료</span><span className="report-source-url">{source.url}</span></>
        : <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>}
      {source.published && <span> · 기재 발행일 {source.published}{unverified ? " (미확인)" : ""}</span>}
    </li>)}</ol>
  </div>;
}
