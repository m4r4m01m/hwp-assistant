import { useMemo } from "react";
import { textDifference } from "./text-diff";

export function CandidateText({ original, replacement, ordinal, highlight }: {
  original: string; replacement: string; ordinal: number; highlight: boolean;
}) {
  const parts = useMemo(() => highlight ? textDifference(original, replacement) : [], [original, replacement, highlight]);
  if (!highlight) return <p className="text" data-testid={`candidate-${ordinal}`}>{replacement}</p>;
  return <>
    <p className="text" data-testid={`candidate-${ordinal}`}>{parts.filter(p => p.kind !== "removed").map((p, i) =>
      p.kind === "added" ? <mark className="text-added" key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>)}</p>
    {parts.some(p => p.kind !== "same") ? <details className="candidate-diff"><summary>원문에서 바뀐 부분 확인</summary>
      <p className="hint">밑줄은 후보에 추가된 표현, 취소선은 원문에서 빠진 표현입니다.</p>
      <p className="text" data-testid={`candidate-original-${ordinal}`}>{parts.filter(p => p.kind !== "added").map((p, i) =>
        p.kind === "removed" ? <del className="text-removed" key={i}>{p.text}</del> : <span key={i}>{p.text}</span>)}</p>
    </details> : <p className="hint">원문과 같은 문장입니다.</p>}
  </>;
}
