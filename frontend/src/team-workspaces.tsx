import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, jsonRequest, type Document } from "./api";
import type { Account } from "./account-gate";
import { useComparisonDialog } from "./version-comparison";
import "./team-workspaces.css";

export type TeamSpace = { id: string; name: string; kind: "department" | "team"; parent_id: string | null; role: "manager" | "editor" | "reader" | null };
type Member = { user_id: string; role: "manager" | "editor" | "reader" };
const roleName = { manager: "공간 관리자", editor: "편집자", reader: "열람자" };

export function TeamWorkspaces({ account, document, onClose, onUpdated }: {
  account: Account; document?: Document; onClose: () => void; onUpdated: () => void;
}) {
  const [spaces, setSpaces] = useState<TeamSpace[]>([]), [selected, setSelected] = useState("");
  const [members, setMembers] = useState<Member[]>([]), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const [membersLoading, setMembersLoading] = useState(false);
  const [name, setName] = useState(""), [kind, setKind] = useState<"department" | "team">("department"), [parent, setParent] = useState("");
  const [user, setUser] = useState(""), [role, setRole] = useState<Member["role"]>("reader");
  const pendingCreate = useRef<{ id: string; name: string; kind: string; parent_id: string | null } | null>(null);
  const pendingAssign = useRef<{ workspace_id: string; expected_version: string } | null>(null);
  const gate = useRef(false);
  const dialog = useRef<HTMLElement | null>(null);
  const close = () => {
    if (gate.current) return;
    if ((pendingCreate.current || pendingAssign.current) && !confirm("서버 응답을 확인하지 못한 요청이 있습니다. 닫기 전에 같은 요청으로 재확인하는 것을 권장합니다. 그래도 닫을까요?")) return;
    onClose();
  };
  useComparisonDialog(dialog, close);
  const space = spaces.find(s => s.id === selected);
  const refresh = async () => { const rows = await api<TeamSpace[]>("/workspaces"); setSpaces(rows); setSelected(current => rows.some(row => row.id === current) ? current : rows[0]?.id || ""); return rows; };
  useEffect(() => { let active = true; setLoading(true); setError(""); setNotice(""); api<TeamSpace[]>("/workspaces").then(rows => { if (active) { setSpaces(rows); setSelected(document?.workspace_id || rows[0]?.id || ""); } })
    .catch(e => { if (active) setError(errorMessage(e)); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [document?.id]);
  useEffect(() => { setMembers([]); if (!selected || document) { setMembersLoading(false); return; } const controller = new AbortController(); setMembersLoading(true);
    api<Member[]>(`/workspaces/${selected}/members`, { signal: controller.signal }).then(rows => { if (!controller.signal.aborted) setMembers(rows); }).catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); })
      .finally(() => { if (!controller.signal.aborted) setMembersLoading(false); });
    return () => controller.abort(); }, [selected, document?.id]);
  async function act(work: () => Promise<void>) {
    if (gate.current) return; gate.current = true; setBusy(true); setError(""); setNotice("");
    try { await work(); onUpdated(); } catch (e) { setError(errorMessage(e)); } finally { gate.current = false; setBusy(false); }
  }
  async function memberChange(id: string, next?: Member["role"]) {
    if (next && members.some(member => member.user_id === id && member.role === next)) return;
    const target = selected;
    await act(async () => { await api(`/workspaces/${target}/members/${encodeURIComponent(id)}`, next ? jsonRequest("PUT", { role: next }) : { method: "DELETE" });
      // Self-removal/reduced permissions may invalidate the old selected space.
      setMembers([]); const rows = await refresh();
      if (rows.some(row => row.id === target)) setMembers(await api<Member[]>(`/workspaces/${target}/members`));
      setUser(""); setNotice("구성원 권한을 반영했습니다."); });
  }
  return <div className="team-space-backdrop"><section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="team-space-title" className="team-space-panel">
    <header><div><small>조직 공간 · 실험 기능</small><h2 id="team-space-title">{document ? "문서 공간 지정" : "팀·부서 공간"}</h2></div><button disabled={busy} onClick={close} aria-label="공간 관리 닫기">닫기</button></header>
    <p>공간 구성원은 문서함을 함께 사용합니다. 다른 팀 사람은 개별 공유받은 문서만 볼 수 있습니다. 상위 부서에 속해도 하위 팀 권한은 자동으로 생기지 않습니다.</p>
    {error && <div><p role="alert" className="error">{error}</p>{!pendingCreate.current && !pendingAssign.current && <button disabled={busy} onClick={() => void act(async () => {
      const rows = await refresh(); if (!document && rows.some(row => row.id === selected)) setMembers(await api<Member[]>(`/workspaces/${selected}/members`));
    })}>다시 불러오기</button>}</div>}{notice && <p role="status">{notice}</p>}
    {loading ? <p role="status">공간을 불러오는 중…</p> : <>
      <label>공간 선택<select value={selected} disabled={busy || !!pendingAssign.current} onChange={e => { setSelected(e.target.value); setError(""); setNotice(""); }}><option value="">공간을 선택하세요</option>{spaces.map(s => <option key={s.id} value={s.id}>{s.kind === "department" ? "부서" : "팀"} · {s.name}{s.role ? ` · ${roleName[s.role]}` : ""}</option>)}</select></label>
      {!spaces.length && <p className="hint">참여 중인 공간이 없습니다.{account.is_admin && !document ? " 아래에서 공간을 만들 수 있습니다." : " 공간 관리자에게 참여를 요청하세요."}</p>}
      {document ? <div className="team-space-assignment"><h3>{document.name}</h3><p>이 문서를 선택한 공간의 구성원에게 공개합니다. 이동하면 이전의 개별 공유 권한은 해제됩니다. 다른 팀 사람은 이동 후 공유 권한에서 다시 초대하세요. 문서 이력과 원본은 보존됩니다.</p>
        <button className="primary" disabled={busy || !space || space.role !== "manager" || document.workspace_id === selected} onClick={() => {
          if (!pendingAssign.current && !confirm(`“${document.name}”의 접근 범위를 “${space?.name}” 공간으로 변경할까요?`)) return;
          void act(async () => { const frozen = pendingAssign.current ||= { workspace_id: selected, expected_version: document.current_version };
            try { await api(`/documents/${document.id}/workspace`, jsonRequest("PUT", frozen)); pendingAssign.current = null; onClose(); }
            catch (e) { if (e instanceof ApiError && e.status >= 400 && e.status < 500) pendingAssign.current = null; throw e; }
          });
        }}>{pendingAssign.current ? "같은 배치 요청 재확인" : "이 공간에 문서 배치"}</button><p className="hint">문서의 원 소유자이면서 대상 공간 관리자여야 합니다. 열려 있는 편집은 먼저 종료하세요.</p></div> : <>
        {space && <div><h3>구성원과 권한</h3>{membersLoading && <p role="status">구성원을 불러오는 중…</p>}<ul className="team-members">{members.map(m => <li key={m.user_id}><strong>{m.user_id}</strong><span>{roleName[m.role]}</span>{space.role === "manager" && <>
          <select aria-label={`${m.user_id} 권한`} value={m.role} disabled={busy} onChange={e => void memberChange(m.user_id, e.target.value as Member["role"])}>{Object.entries(roleName).map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select>
          <button disabled={busy} onClick={() => { if (confirm(`${m.user_id}의 공간 접근 권한을 회수할까요?`)) void memberChange(m.user_id); }}>제외</button></>}</li>)}</ul>
          {space.role === "manager" && <form className="team-member-form" onSubmit={e => { e.preventDefault(); if (user.trim()) void memberChange(user.trim(), role); }}><label>계정 ID<input value={user} onChange={e => setUser(e.target.value)} maxLength={80} required disabled={busy}/></label><label>권한<select value={role} onChange={e => setRole(e.target.value as Member["role"])} disabled={busy}>{Object.entries(roleName).map(([v,label]) => <option key={v} value={v}>{label}</option>)}</select></label><button disabled={busy}>구성원 추가</button></form>}
        </div>}
        {account.is_admin && <details><summary>새 부서·팀 만들기</summary><form className="team-create-form" onSubmit={e => { e.preventDefault(); void act(async () => {
          const frozen = pendingCreate.current ||= { id: crypto.randomUUID(), name: name.trim(), kind, parent_id: kind === "team" && parent ? parent : null };
          try { const created = await api<TeamSpace>("/workspaces", jsonRequest("POST", frozen)); pendingCreate.current = null; await refresh(); setSelected(created.id); setName(""); setNotice("공간을 만들었습니다. 구성원을 추가하세요."); }
          catch (e) { if (e instanceof ApiError && e.status >= 400 && e.status < 500) pendingCreate.current = null; throw e; }
        }); }}><label>종류<select value={kind} disabled={busy || !!pendingCreate.current} onChange={e => setKind(e.target.value as typeof kind)}><option value="department">부서</option><option value="team">팀</option></select></label>
          <label>공간 이름<input required maxLength={80} value={name} disabled={busy || !!pendingCreate.current} onChange={e => setName(e.target.value)}/></label>
          {kind === "team" && <label>상위 부서<select value={parent} disabled={busy || !!pendingCreate.current} onChange={e => setParent(e.target.value)}><option value="">없음</option>{spaces.filter(s => s.kind === "department" && s.role === "manager").map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>}
          <button disabled={busy || !name.trim()}>{pendingCreate.current ? "같은 생성 요청 재확인" : "공간 만들기"}</button></form></details>}
      </>}
    </>}
  </section></div>;
}
