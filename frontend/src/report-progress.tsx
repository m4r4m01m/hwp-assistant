import React, { useEffect, useRef, useState } from "react";
import { ReportContent, type ReportBlock } from "./report-content";

export type LiveReport = { stage: string; revision: number; activity: string; research_calls: number; elapsed_ms?: number;
  blocks: ReportBlock[]; timings: Record<string, number | null> };
export function ReportTiming({ timings }: { timings?: Record<string, number | null> }) {
  if (!timings) return null;
  const labels: Record<string, string> = { startup_ms: "연결 준비", first_text_ms: "첫 글 도착까지", research_ms: "자료 조회", generation_ms: "내용 생성 전체", client_apply_ms: "문서 입력", layout_ms: "서식 계산" };
  return <small className="report-timing">{Object.entries(labels).filter(([key]) => typeof timings[key] === "number")
    .map(([key, label]) => `${label} ${(timings[key]! / 1000).toFixed(1)}초`).join(" · ")}</small>;
}

export type ReportStage = "writing" | "applying" | "formatting" | "ready";
const steps = ["내용 작성", "문서에 입력", "서식·페이지 배치", "편집 가능"];
export function ReportProgress({ stage, count, total }: { stage: ReportStage; count?: number; total?: number }) {
  const current = ["writing", "applying", "formatting", "ready"].indexOf(stage);
  return <ol className="report-steps" aria-label="보고서 작성 진행 단계" data-stage={stage}>
    {steps.map((label, i) => <li key={label} className={i < current ? "done" : i === current ? "current" : ""}
      aria-current={i === current ? "step" : undefined}><b>{i < current ? "✓" : i + 1}</b>{label}
      {i === 1 && stage === "applying" && total ? <small>{count || 0}/{total} 문단</small> : null}</li>)}
  </ol>;
}
export function ReportWriting({ startedAt, progress, stopped }: { startedAt?: number; progress?: LiveReport; stopped?: boolean }) {
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
  return <div className="report-writing"><ReportProgress stage="writing" />
    <div className="report-stream-status" role="status">{!stopped && <span className="report-pulse" />}<b>{label}</b>
      <small>{Math.floor(seconds / 60)}분 {seconds % 60}초 경과{progress?.research_calls ? ` · 자료 조회 ${progress.research_calls}회 완료` : ""}</small>
      {!stopped && progress?.stage === "researching" && progress.activity && <p>{progress.activity}</p>}
      <ReportTiming timings={progress?.timings} />
    </div>
    <div ref={paper} className="report-live-scroll" aria-label="생성 중인 보고서" onScroll={e => {
      const el = e.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    }}><div className="report-live-paper">
      <ReportContent blocks={progress?.blocks || []} testId="stream-report-paragraph" />
      {!progress?.blocks.length && <p className="hint">첫 문장이 도착하면 이곳에서 바로 이어집니다. 자료 조사 중에는 본문이 아직 없을 수 있습니다.</p>}
      <p className="report-live-caption">{stopped ? "생성이 완료되지 않은 내용입니다. 한글 문서로 확정하거나 저장하지 않았습니다." : "실시간 생성 초안 · 내용이 바뀔 수 있으며, 최종 줄바꿈과 페이지 배치는 생성 완료 후 적용합니다."}</p>
    </div></div></div>;
}
