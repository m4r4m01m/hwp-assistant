import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ReportContent, type ReportBlock } from "./report-content";

export type LiveReport = { stage: string; revision: number; activity: string; research_calls: number; elapsed_ms?: number;
  blocks: ReportBlock[]; timings: Record<string, number | null> };
export type ReportFailure = { code: string; stage?: string; research_calls?: number; validation_paths?: string[] };
export function ReportFailureNotice({ message, failure, onRequest }: { message: string; failure?: ReportFailure; onRequest: () => void }) {
  const guidance: Record<string, string> = {
    research_unavailable: "서버의 Codex 웹 검색 연결을 확인한 뒤 새 요청을 시작하세요. 직접 제공한 자료만 사용하려면 작성 요청에서 공개자료 조회를 끌 수 있습니다.",
    timeout: "요청 범위를 줄이거나 서버 연결 상태를 확인한 뒤 새 요청을 시작하세요.",
    login_required: "운영자가 서버 Codex의 ChatGPT 로그인 상태를 확인해야 합니다.",
    invalid_report: "요청의 범위와 표·흐름도 내용을 확인한 뒤 새 요청을 시작하세요.",
  };
  const stages: Record<string, string> = { starting: "연결 준비", waiting: "응답 대기", researching: "공개자료 조회", writing: "본문 작성", validating: "내용 검증" };
  return <div className="report-workspace-error error" role="alert">
    <p>{message}</p>
    {failure && <>
      <p>{guidance[failure.code] || "서버 연결과 요청 내용을 확인한 뒤 새 요청을 시작하세요."}</p>
      <details><summary>실패 단계 확인</summary>
        <p>{stages[failure.stage || ""] || "보고서 생성"}{Number.isSafeInteger(failure.research_calls) && (failure.research_calls || 0) >= 0 ? ` · 공개자료 조회 ${failure.research_calls}회 완료` : ""}</p>
      </details>
    </>}
    <button type="button" onClick={onRequest}>작성 요청 확인</button>
  </div>;
}
export function ReportTiming({ timings }: { timings?: Record<string, number | null> }) {
  if (!timings) return null;
  const labels: Record<string, string> = { startup_ms: "연결 준비", first_text_ms: "첫 글 도착까지", research_ms: "자료 조회", generation_ms: "내용 생성 전체", client_apply_ms: "문서 입력", layout_ms: "서식 계산" };
  const entries = Object.entries(labels).filter(([key]) => typeof timings[key] === "number");
  if (!entries.length) return null;
  return <details className="report-timing-details"><summary>단계별 소요 시간</summary><small className="report-timing">{entries
    .map(([key, label]) => `${label} ${(timings[key]! / 1000).toFixed(1)}초`).join(" · ")}</small></details>;
}

export type ReportStage = "writing" | "review" | "applying" | "formatting" | "ready";
const steps = ["초안 작성", "내용 검토·보완", "서식·페이지 배치", "편집·저장"];
export function ReportProgress({ stage, count, total }: { stage: ReportStage; count?: number; total?: number }) {
  const current = stage === "applying" ? 2 : ["writing", "review", "formatting", "ready"].indexOf(stage);
  return <ol className="report-steps" aria-label="보고서 작성 진행 단계" data-stage={stage}>
    {steps.map((label, i) => <li key={label} className={i < current ? "done" : i === current ? "current" : ""}
      aria-current={i === current ? "step" : undefined}><b>{i < current ? "✓" : i + 1}</b>{label}
      {i === 2 && stage === "applying" && total ? <small>문서 입력 {count || 0}/{total} 문단</small> : null}</li>)}
  </ol>;
}
export function ReportWriting({ startedAt, progress, stopped, controlsHost }: { startedAt?: number; progress?: LiveReport; stopped?: boolean; controlsHost?: HTMLElement | null }) {
  const [seconds, setSeconds] = useState(0);
  const paper = useRef<HTMLDivElement>(null), follow = useRef(true);
  useEffect(() => { if (follow.current && paper.current) paper.current.scrollTop = paper.current.scrollHeight; }, [progress?.revision]);
  useEffect(() => {
    const start = startedAt ? startedAt * 1000 : Date.now();
    const tick = () => setSeconds(Math.max(0, Math.floor((Date.now() - start) / 1000)));
    if (stopped) { setSeconds(Math.floor((progress?.elapsed_ms || 0) / 1000)); return; }
    tick(); const timer = setInterval(tick, 1000); return () => clearInterval(timer);
  }, [startedAt, stopped]);
  const label = stopped ? "작성 중인 내용 보존" : progress?.stage === "researching" ? "공개자료 확인 중…" : progress?.stage === "writing" ? "본문 작성 중…" : progress?.stage === "validating" ? "내용 검증 중…" : "Codex 응답 준비 중…";
  const controls = <><ReportProgress stage="writing" />
    <div className="report-stream-status" role="status">{!stopped && <span className="report-pulse" />}<b>{label}</b>
      <small>{Math.floor(seconds / 60)}분 {seconds % 60}초 경과{progress?.research_calls ? ` · 자료 조회 ${progress.research_calls}회 완료` : ""}</small>
      {!stopped && progress?.stage === "researching" && progress.activity && <p>{progress.activity}</p>}
      <ReportTiming timings={progress?.timings} />
    </div></>;
  return <div className="report-writing">{controlsHost ? createPortal(controls, controlsHost) : controls}
    <div ref={paper} className="report-live-scroll" aria-label="생성 중인 보고서" onScroll={e => {
      const el = e.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    }}><div className="report-draft-content">
      <ReportContent blocks={progress?.blocks || []} testId="stream-report-paragraph" />
      {!progress?.blocks.length && <p className="hint">{stopped ? "완료된 초안이 없습니다. 실패 안내와 작성 요청을 확인하세요." : "첫 문장이 도착하면 이곳에서 바로 이어집니다. 자료 조사 중에는 본문이 아직 없을 수 있습니다."}</p>}
      <p className="report-live-caption">{stopped ? "생성이 완료되지 않은 내용입니다. 한글 문서로 확정하거나 저장하지 않았습니다." : "실시간 생성 초안 · 내용이 바뀔 수 있으며, 최종 줄바꿈과 페이지 배치는 생성 완료 후 적용합니다."}</p>
    </div></div></div>;
}
