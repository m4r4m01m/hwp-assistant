import React, { useEffect, useRef, useState } from "react";
import { api, fileUrl, type Document, type Version } from "./api";
import {
  VersionComparison,
  authorLabel,
  causeLabel,
  dateLabel,
  useComparisonDialog,
} from "./version-comparison";

export function History({
  document: d,
  onClose,
  onRestore,
}: {
  document: Document;
  onClose: () => void;
  onRestore: (v: Version) => Promise<void>;
}) {
  const dialog = useRef<HTMLElement>(null);
  const [rows, setRows] = useState<Version[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [selection, setSelection] = useState<{ ids: string[]; error: string }>({
      ids: [],
      error: "",
    }),
    [pair, setPair] = useState<[Version, Version] | null>(null);
  const [changes, setChanges] = useState<{
    recorded: boolean;
    version: Version;
    entries: {
      path: string;
      before_sha256: string | null;
      after_sha256: string | null;
    }[];
  } | null>(null);
  const detailRequest = useRef(0);
  const toggleSelection = (id: string) =>
    setSelection((previous) => {
      if (previous.ids.includes(id))
        return { ids: previous.ids.filter((value) => value !== id), error: "" };
      if (previous.ids.length === 2)
        return {
          ...previous,
          error:
            "두 버전까지만 선택할 수 있습니다. 선택한 버전을 해제한 뒤 다시 선택하세요.",
        };
      return { ids: [...previous.ids, id], error: "" };
    });
  useEffect(() => {
    const abort = new AbortController();
    setSelection({ ids: [], error: "" });
    api<Version[]>(`/documents/${d.id}/versions`, { signal: abort.signal })
      .then((items) => {
        setRows(items);
      })
      .catch((e) => {
        if (!abort.signal.aborted) setError(e.message);
      });
    return () => {
      abort.abort();
      detailRequest.current++;
    };
  }, [d.id]);
  // The compare view owns its own focus trap; do not install two nested traps.
  if (pair)
    return (
      <VersionComparison
        document={d}
        left={pair[0]}
        right={pair[1]}
        onClose={() => setPair(null)}
      />
    );
  return (
    <HistoryDialog dialog={dialog} onClose={onClose}>
      <header className="history-header">
        <h2 id="history-title">{d.name} · 버전 기록</h2>
        <button onClick={onClose}>닫기</button>
      </header>
      <p>
        두 버전을 순서대로 선택하세요. 첫 번째는 기준, 두 번째는 비교
        대상입니다. 복원은 과거 이력을 유지한 채 새 버전을 만듭니다.
      </p>
      {error && <p role="alert">{error}</p>}
      <div className="history-compare-bar">
        <span>
          {rows.length < 2
            ? "비교하려면 저장 버전이 두 개 이상 필요합니다."
            : selection.ids.length === 0
              ? "기준으로 삼을 버전을 먼저 선택하세요. (0/2)"
              : selection.ids.length === 1
                ? "비교할 버전을 하나 더 선택하세요. (1/2)"
                : "선택한 순서대로 기준 문서와 비교 문서를 엽니다. (2/2)"}
        </span>
        <button
          className="primary"
          disabled={selection.ids.length !== 2 || busy}
          onClick={() => {
            const a = rows.find((v) => v.id === selection.ids[0]),
              b = rows.find((v) => v.id === selection.ids[1]);
            if (a && b) setPair([a, b]);
          }}
        >
          선택한 두 버전 비교
        </button>
      </div>
      {selection.error && (
        <p role="alert" className="bad">
          {selection.error}
        </p>
      )}
      <div className="history-table-wrap">
        <table className="history-table">
          <thead>
            <tr>
              <th>선택</th>
              <th>저장 버전</th>
              <th>저장한 사용자</th>
              <th>작업</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v, i) => (
              <tr key={v.id} data-version-id={v.id}>
                <td>
                  <label className="history-selection">
                    <input
                      type="checkbox"
                      aria-label={`버전 ${rows.length - i} 선택`}
                      aria-describedby={
                        selection.ids.includes(v.id)
                          ? `history-role-${v.id}`
                          : undefined
                      }
                      checked={selection.ids.includes(v.id)}
                      onChange={() => toggleSelection(v.id)}
                    />
                    {selection.ids.includes(v.id) && (
                      <span
                        id={`history-role-${v.id}`}
                        className="history-selection-role"
                      >
                        {selection.ids[0] === v.id ? "1 · 기준" : "2 · 비교"}
                      </span>
                    )}
                  </label>
                </td>
                <td>
                  <strong>
                    v{rows.length - i} · {causeLabel(v.cause)}{" "}
                    {v.id === d.current_version && (
                      <span className="history-current">현재</span>
                    )}
                  </strong>
                  <small>
                    {dateLabel(v.created_at)} · {v.size.toLocaleString()} bytes
                  </small>
                  <small title={v.id}>{v.id.slice(0, 8)}</small>
                </td>
                <td>
                  <span>{authorLabel(v.author)}</span>
                  {v.author?.identity_kind === "local" && (
                    <small>개인 식별 불가</small>
                  )}
                </td>
                <td className="history-actions">
                  <a className="button" href={fileUrl(d, v.id)}>
                    다운로드
                  </a>
                  <button
                    onClick={async () => {
                      const ticket = ++detailRequest.current;
                      setError("");
                      try {
                        const result = await api<NonNullable<typeof changes>>(
                          `/documents/${d.id}/versions/${v.id}/changes`,
                        );
                        if (ticket === detailRequest.current)
                          setChanges(result);
                      } catch (e) {
                        if (ticket === detailRequest.current)
                          setError(String(e));
                      }
                    }}
                  >
                    내부 구조 상세
                  </button>
                  <button
                    disabled={busy || d.role === "reader"}
                    onClick={async () => {
                      if (!confirm("이 버전으로 복원하여 새 버전을 만들까요?"))
                        return;
                      setBusy(true);
                      setError("");
                      try {
                        await onRestore(v);
                      } catch (e) {
                        setError(String(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    이 버전 복원
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {changes && (
        <div className="notice">
          <strong>
            내부 구조 상세 · 기준 버전:{" "}
            {changes.version.base_version || "최초 저장"}
          </strong>
          {!changes.recorded ? (
            <p>최초 저장 또는 변경분 기록 도입 전 버전입니다.</p>
          ) : !changes.entries.length ? (
            <p>HWPX 내부 항목의 내용 변경이 없습니다.</p>
          ) : (
            <ul>
              {changes.entries.map((e) => (
                <li key={e.path}>
                  {e.path} ·{" "}
                  {!e.before_sha256
                    ? "추가"
                    : !e.after_sha256
                      ? "삭제"
                      : "변경"}
                </li>
              ))}
            </ul>
          )}
          <p>
            HWPX 내부 항목의 해시 비교입니다. 실제 문서 변경은 위의 문서
            비교에서 확인하세요.
          </p>
        </div>
      )}
    </HistoryDialog>
  );
}

function HistoryDialog({
  dialog,
  onClose,
  children,
}: {
  dialog: React.RefObject<HTMLElement | null>;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useComparisonDialog(dialog, onClose);
  return (
    <div className="modal history-modal">
      <section
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-title"
      >
        {children}
      </section>
    </div>
  );
}
