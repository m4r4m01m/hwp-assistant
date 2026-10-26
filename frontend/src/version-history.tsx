import React, { useEffect, useRef, useState } from "react";
import { api, errorMessage, type Document, type Version } from "./api";
import { SavedDownload } from "./saved-download";
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
  onRestore: (v: Version, requestId: string) => Promise<Version>;
}) {
  const dialog = useRef<HTMLElement>(null);
  const [rows, setRows] = useState<Version[]>([]),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [attempt, setAttempt] = useState(0),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [detailId, setDetailId] = useState<string | null>(null),
    [detailLoading, setDetailLoading] = useState(false),
    [detailError, setDetailError] = useState("");
  const restoreRequest = useRef<{ version: string; id: string } | null>(null);
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
    setLoading(true);
    setError("");
    setRows([]);
    setChanges(null);
    setDetailId(null);
    setSelection({ ids: [], error: "" });
    api<Version[]>(`/documents/${d.id}/versions`, { signal: abort.signal })
      .then((items) => {
        if (!abort.signal.aborted) setRows(items);
      })
      .catch((e) => {
        if (!abort.signal.aborted) setError(errorMessage(e));
      })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => {
      abort.abort();
      detailRequest.current++;
    };
  }, [d.id, attempt]);
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
    <HistoryDialog dialog={dialog} onClose={() => { if (!busy) onClose(); }}>
      <header className="history-header">
        <h2 id="history-title">{d.name} · 버전 기록</h2>
        <button disabled={busy} onClick={onClose}>닫기</button>
      </header>
      <p>
        두 버전을 순서대로 선택하세요. 첫 번째는 기준, 두 번째는 비교
        대상입니다. 복원은 과거 이력을 유지한 채 새 버전을 만듭니다.
      </p>
      {loading && <p role="status">버전 기록을 불러오는 중…</p>}
      {notice && <p role="status" className="notice">{notice}</p>}
      {error && <div role="alert"><p>{error}</p><button disabled={loading} onClick={() => setAttempt(value => value + 1)}>버전 기록 다시 불러오기</button></div>}
      <div className="history-compare-bar">
        <span>
          {loading ? "저장 버전을 확인하고 있습니다." : rows.length < 2
            ? "비교하려면 저장 버전이 두 개 이상 필요합니다."
            : selection.ids.length === 0
              ? "기준으로 삼을 버전을 먼저 선택하세요. (0/2)"
              : selection.ids.length === 1
                ? "비교할 버전을 하나 더 선택하세요. (1/2)"
                : "선택한 순서대로 기준 문서와 비교 문서를 엽니다. (2/2)"}
        </span>
        <button
          className="primary"
          disabled={loading || selection.ids.length !== 2 || busy}
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
              <React.Fragment key={v.id}>
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
                  {v.cause === "restore" && <small className="history-restore-source">
                    {v.restore_source ? `이 문서의 v${rows.length - rows.findIndex(source => source.id === v.restore_source!.version_id)}에서 복원 · ${dateLabel(v.restore_source.created_at)}` : "복원 원본 정보가 기록되지 않은 이전 버전"}
                  </small>}
                </td>
                <td>
                  <span>{authorLabel(v.author)}</span>
                  {v.author?.identity_kind === "local" && (
                    <small>개인 식별 불가</small>
                  )}
                </td>
                <td className="history-actions">
                  <SavedDownload document={d} versionId={v.id} icon={false} />
                  <button
                    aria-expanded={detailId === v.id}
                    aria-controls={`history-detail-${v.id}`}
                    onClick={async () => {
                      const ticket = ++detailRequest.current;
                      if (detailId === v.id) { setDetailId(null); setDetailLoading(false); return; }
                      setDetailId(v.id);
                      setChanges(null);
                      setDetailLoading(true);
                      setDetailError("");
                      try {
                        const result = await api<NonNullable<typeof changes>>(
                          `/documents/${d.id}/versions/${v.id}/changes`,
                        );
                        if (ticket === detailRequest.current)
                          setChanges(result);
                      } catch (e) {
                        if (ticket === detailRequest.current)
                          setDetailError(errorMessage(e));
                      } finally {
                        if (ticket === detailRequest.current) setDetailLoading(false);
                      }
                    }}
                  >
                    내부 구조 상세
                  </button>
                  <button
                    disabled={busy || d.role === "reader"}
                    onClick={async () => {
                      if (!confirm(`${d.name}의 v${rows.length - i} (${dateLabel(v.created_at)}) 내용을 새 버전으로 복원할까요? 현재 버전도 기록에 남습니다.`))
                        return;
                      setBusy(true);
                      setError("");
                      try {
                        if (restoreRequest.current?.version !== v.id)
                          restoreRequest.current = { version: v.id, id: crypto.randomUUID() };
                        await onRestore(v, restoreRequest.current.id);
                        restoreRequest.current = null;
                        setNotice(`${d.name}의 v${rows.length - i}에서 복원한 새 버전을 저장했습니다.`);
                        setAttempt(value => value + 1);
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
              {detailId === v.id && <tr><td colSpan={4}>
                <div id={`history-detail-${v.id}`} className="history-structure-detail" aria-live="polite">
                  <strong>v{rows.length - i} 내부 구조 상세</strong>
                  {detailLoading && <p role="status">변경한 파일을 확인하는 중…</p>}
                  {detailError && <p role="alert">{detailError}</p>}
                  {changes?.version.id === v.id && <>
                    <p>{changes.version.base_version ? `직전 v${rows.length - rows.findIndex(source => source.id === changes.version.base_version)}과 비교` : "최초 저장"}</p>
                    {!changes.recorded ? <p>이 버전에는 내부 변경 기록이 없습니다. 최초 저장 또는 기록 도입 전 버전입니다.</p>
                      : !changes.entries.length ? <p>HWPX 내부 항목의 내용 변경이 없습니다.</p>
                      : <ul>{changes.entries.map(entry => <li key={entry.path}><code>{entry.path}</code> · {!entry.before_sha256 ? "추가" : !entry.after_sha256 ? "삭제" : "변경"}</li>)}</ul>}
                    <p className="hint">파일 구성의 변경 기록입니다. 글·표·서식의 차이는 ‘선택한 두 버전 비교’에서 확인하세요.</p>
                  </>}
                </div>
              </td></tr>}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
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
