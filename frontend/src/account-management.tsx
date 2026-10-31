import {DocumentUsage} from "./document-usage";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, isAbortError, jsonRequest } from "./api";
import type { Account } from "./account-gate";
import { WorkspaceNav,WorkspaceIcon,type WorkspaceIconName } from "./workspace-nav";
import { OrganizationSettings, type Affiliation } from "./organization";
import "./account-management.css";
import {OrganizationManager} from "./organization-manager";

type ManagedAccount = {
  user_id: string; name: string; kind: string; is_admin: boolean; is_active: boolean;
  approval_status: "pending" | "approved" | "rejected"; status: string; revision: string;
  created_at: number; last_login_at: number | null; affiliation?: Affiliation;
};
type AccountList = { items: ManagedAccount[]; total: number };
type AccessPolicy = "allowlist" | "approved_accounts";
type Settings = { codex_account_access: AccessPolicy; mattermost?: { configured: boolean; pending: number; failed: number } };
type AuditEntry = {
  id: number; created_at: number; actor_id: string; action: string; target_type: string;
  target_id: string | null; document_id: string | null; version_id: string | null; document_name?: string | null;
};
type AuditPage = { items: AuditEntry[]; next_before: number | null };
type Tab = "accounts" | "organization" | "usage" | "settings" | "audit";
const statusLabels: Record<string, string> = { pending: "승인 대기", approved: "사용 중", rejected: "가입 거절", disabled: "비활성" };
const actionLabels: Record<string, string> = {
  "account.login": "로그인", "account.logout": "로그아웃", "account.signup": "가입 신청",
  "account.create": "계정 생성", "account.create-admin": "관리자 계정 생성", "account.promote-admin": "관리자 지정",
  "account.role": "계정 권한 변경", "organization.create": "조직 생성", "organization.rename": "조직 이름 변경", "organization.delete": "빈 조직 삭제",
  "account.affiliation": "소속 부서·팀 변경",
  "account.approve": "가입 승인", "account.reject": "가입 거절", "account.disable": "계정 비활성화",
  "account.enable": "계정 활성화", "account.password": "비밀번호 변경", "account.password_reset": "비밀번호 변경", "account.password_change": "본인 비밀번호 변경",
  "document.upload": "파일 추가", "document.create": "문서 생성", "document.delete": "파일 삭제",
  "document.modify": "문서 수정", "document.rename": "문서 이름 변경", "document.restore": "이전 버전 복원",
  "document.trash": "문서 휴지통 이동", "document.restore_from_trash": "휴지통 문서 복구",
  "document.download": "파일 다운로드", "document.copy": "문서 복제", "settings.codex_access": "AI 이용 권한 변경",
  "notifications.retry": "가입 알림 재전송",
  "visual_standard.delete": "문서 표준 삭제", "design_fragment.delete": "디자인 조각 삭제",
};
function date(value: number | null) {
  if (value === null) return "기록 없음";
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "short", timeStyle: "medium", timeZone: "Asia/Seoul" }).format(new Date(value * 1000));
}

export function AccountManagement({ account, onClose, initialTab="accounts" }: { account: Account; onClose: () => void; initialTab?:Tab }) {
  const [tab, setTab] = useState<Tab>(initialTab), [filter, setFilter] = useState("pending"), [offset, setOffset] = useState(0);
  const [accounts, setAccounts] = useState<AccountList>({ items: [], total: 0 });
  const [settings, setSettings] = useState<Settings | null>(null), [choice, setChoice] = useState<AccessPolicy>("allowlist");
  const [audit, setAudit] = useState<AuditPage>({ items: [], next_before: null });
  const [actor, setActor] = useState(""), [action, setAction] = useState(""), [auditQuery, setAuditQuery] = useState({ actor: "", action: "" });
  const [before, setBefore] = useState<number | null>(null), [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false), [forbidden, setForbidden] = useState(!account.is_admin);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [sessionEnded, setSessionEnded] = useState(false);
  const [passwordAccount, setPasswordAccount] = useState<ManagedAccount | null>(null), [password, setPassword] = useState(""), [confirmation, setConfirmation] = useState("");
  const [affiliationAccount, setAffiliationAccount] = useState<ManagedAccount | null>(null);
  const alive = useRef(true), lock = useRef(false), generation = useRef(0);
  useEffect(() => { alive.current = true; return () => { alive.current = false; generation.current++; }; }, []);
  function failure(cause: unknown) {
    if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) {
      setAffiliationAccount(null);
      setForbidden(true); setAccounts({ items: [], total: 0 }); setAudit({ items: [], next_before: null }); setSettings(null);
      setPasswordAccount(null); setPassword(""); setConfirmation("");
    }
    setError(errorMessage(cause));
  }
  useEffect(() => {
    if (!account.is_admin || forbidden || sessionEnded || (tab === "organization" || tab === "usage")) return;
    const controller = new AbortController(), current = ++generation.current;
    setLoading(true); setError("");
    const query = new URLSearchParams();
    let path: string;
    if (tab === "accounts") {
      query.set("limit", "100"); query.set("offset", String(offset)); if (filter) query.set("status", filter);
      path = `/admin/accounts?${query}`;
    } else if (tab === "settings") path = "/admin/settings";
    else {
      query.set("limit", "50"); if (auditQuery.actor) query.set("actor", auditQuery.actor);
      if (auditQuery.action) query.set("action", auditQuery.action); if (before !== null) query.set("before", String(before));
      path = `/admin/audit?${query}`;
    }
    void api<AccountList | Settings | AuditPage>(path, { signal: controller.signal }).then(value => {
      if (controller.signal.aborted || current !== generation.current) return;
      if (tab === "accounts") setAccounts(value as AccountList);
      else if (tab === "settings") { setSettings(value as Settings); setChoice((value as Settings).codex_account_access); }
      else setAudit(value as AuditPage);
    }).catch(cause => { if (!controller.signal.aborted && current === generation.current && !isAbortError(cause)) failure(cause); })
      .finally(() => { if (!controller.signal.aborted && current === generation.current) setLoading(false); });
    return () => { controller.abort(); generation.current++; };
  }, [account.is_admin, tab, filter, offset, auditQuery, before, refresh, forbidden, sessionEnded]);
  function changeTab(next: Tab) {
    if (lock.current) return;
    setAffiliationAccount(null);
    setTab(next); setError(""); setNotice(""); setPasswordAccount(null); setPassword(""); setConfirmation("");
  }
  async function mutate(path: string, body: unknown, message: string, ownPassword = false) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try {
      await api(path, { ...jsonRequest("POST", body), timeoutMs: 20000 });
      if (!alive.current) return;
      setNotice(message); setPasswordAccount(null); setPassword(""); setConfirmation("");
      if (ownPassword) setSessionEnded(true);
      else setRefresh(value => value + 1);
    } catch (cause) {
      if (!alive.current) return;
      failure(cause);
      setPassword(""); setConfirmation("");
      if (!(cause instanceof ApiError) || cause.status >= 500) setNotice("처리 결과가 확실하지 않습니다. 같은 작업을 반복하기 전에 새로고침하여 현재 상태와 감사 기록을 확인하세요.");
      else if (cause.status === 409) setRefresh(value => value + 1);
    } finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  function manage(row: ManagedAccount, operation: "approve" | "reject" | "enable" | "disable") {
    if (lock.current) return;
    const words = { approve: "가입 승인", reject: "가입 거절", enable: "활성화", disable: "비활성화" };
    if (!window.confirm(`${row.user_id} 계정을 ${words[operation]}하시겠습니까?${operation === "disable" ? " 기존 로그인은 종료되며 문서와 이력은 보존됩니다." : ""}`)) return;
    setAffiliationAccount(null);
    void mutate(`/admin/accounts/${encodeURIComponent(row.user_id)}/${operation}`, { expected_revision: row.revision }, `${row.user_id} 계정을 ${words[operation]}했습니다.`);
  }
  async function savePolicy(event: React.FormEvent) {
    event.preventDefault();
    if (lock.current || !settings || choice === settings.codex_account_access) return;
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const value = await api<Settings>("/admin/settings", { ...jsonRequest("PATCH", { codex_account_access: choice }), timeoutMs: 20000 });
      if (alive.current) { setSettings({ ...settings, ...value }); setChoice(value.codex_account_access); setNotice("AI 이용 권한 설정을 저장했습니다."); }
    } catch (cause) { if (alive.current) { failure(cause); setNotice("저장 결과가 불확실하면 새로고침하여 현재 설정을 먼저 확인하세요."); } }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  return <div className="workspace-shell account-admin-shell">
    <WorkspaceNav active="documents" onBack={onClose} backLabel="문서함으로" disabled={busy} navigationLabel="계정 필터" navigation={tab==="accounts"?(<div className="account-admin-toolbar"><label>계정 상태<select aria-label="계정 상태" value={filter} disabled={busy} onChange={event => { setAffiliationAccount(null); setFilter(event.target.value); setOffset(0); if(event.currentTarget.closest(".workspace-nav")?.getAttribute("data-mode")==="icons")event.currentTarget.closest("details")?.removeAttribute("open"); }}><option value="pending">승인 대기</option><option value="approved">사용 중</option><option value="disabled">비활성</option><option value="rejected">가입 거절</option><option value="">전체</option></select></label><button disabled={busy || loading} onClick={() => setRefresh(value => value + 1)}>새로고침</button></div>):undefined}>{!forbidden&&!sessionEnded&&(<div className="account-admin-tabs account-admin-side-tabs" role="tablist" aria-label="관리 항목" aria-orientation="vertical">
          {([['accounts', '계정 및 가입 승인'], ['organization', '조직도·권한'], ['usage', '문서 사용 현황'], ['settings', 'AI 이용 권한'], ['audit', '감사 로그']] as const).map(([id, label]) => <button id={`admin-tab-${id}`} className="workspace-nav-route" title={label} aria-label={label} type="button" key={id} role="tab" aria-selected={tab === id} aria-controls={`admin-panel-${id}`} disabled={busy} onClick={() => changeTab(id)}><WorkspaceIcon name={(({accounts:"settings",organization:"share",usage:"table",settings:"conversation",audit:"history"}) as Record<Tab,WorkspaceIconName>)[id]}/><span>{label}</span></button>)}
        </div>)}</WorkspaceNav>
    <main className="drive drive-workspace account-admin" aria-label="계정 관리">
      <header className="account-admin-heading"><div><span className="account-eyebrow">관리자</span><h1>{tab==="usage"?"문서 사용 현황":tab==="organization"?"조직도·권한":tab==="settings"?"AI 이용 권한":tab==="audit"?"감사 로그":"계정 관리"}</h1><p>{tab==="usage"?"부서·팀 문서함의 문서 수와 저장 용량을 확인합니다.":"가입 승인과 소속 부서·팀, 계정 상태, AI 이용 권한, 작업 기록을 관리합니다."}</p></div></header>
      {forbidden ? <section className="account-admin-message" role="alert"><h2>관리자 권한이 필요합니다</h2><p>{error || "관리자 계정으로 다시 로그인해 주세요."}</p><button onClick={() => location.reload()}>로그인 상태 다시 확인</button></section>
      : sessionEnded ? <section className="account-admin-message" role="status"><h2>다시 로그인해 주세요</h2><p>현재 계정의 비밀번호를 변경하여 기존 로그인을 종료했습니다.</p><button className="primary" onClick={() => location.reload()}>로그인 화면으로</button></section>
      : <>

        {notice && <p className="account-admin-notice" role="status">{notice}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <section role="tabpanel" id={`admin-panel-${tab}`} aria-labelledby={`admin-tab-${tab}`} className="account-admin-panel">
          {tab === "usage" && <DocumentUsage onForbidden={failure}/>}
          {tab === "organization" && <OrganizationManager account={account} onBusyChange={value=>{lock.current=value;setBusy(value);}} onForbidden={failure}/>}
          {tab === "accounts" && <>
            <p className="account-admin-help">총 {accounts.total}개 계정</p>
            <p className="account-admin-help">비활성화해도 기존 문서와 수정 이력은 보존됩니다. guest는 여러 체험자가 함께 사용하는 계정이므로 감사 로그도 guest로 기록됩니다.</p>
            {affiliationAccount && <div className="account-admin-affiliation">
              <header><h2>{affiliationAccount.user_id} 소속 부서·팀</h2><button type="button" disabled={busy} onClick={() => setAffiliationAccount(null)}>소속 배치 닫기</button></header>
              <OrganizationSettings key={affiliationAccount.user_id} userId={affiliationAccount.user_id}
                onBusyChange={value => { lock.current = value; setBusy(value); }}
                onForbidden={failure} onUpdated={() => setRefresh(value => value + 1)} />
            </div>}
            {passwordAccount && <form className="account-admin-password" aria-label={`${passwordAccount.user_id} 비밀번호 변경`} onSubmit={event => {
              event.preventDefault(); if (password !== confirmation) { setError("비밀번호 확인이 일치하지 않습니다."); return; }
              void mutate(`/admin/accounts/${encodeURIComponent(passwordAccount.user_id)}/password`, { password, expected_revision: passwordAccount.revision }, `${passwordAccount.user_id} 계정의 비밀번호를 변경했습니다. 기존 로그인은 종료됩니다.`, passwordAccount.user_id === account.user_id);
            }}><h2>{passwordAccount.user_id} 비밀번호 변경</h2><p>8~128자의 새 비밀번호를 지정하세요. 해당 계정의 기존 로그인은 종료됩니다.</p><label>새 비밀번호<input type="password" autoComplete="new-password" name="new-password" minLength={8} maxLength={128} required value={password} disabled={busy} onChange={event => setPassword(event.target.value)} /></label><label>새 비밀번호 확인<input type="password" autoComplete="new-password" minLength={8} maxLength={128} required value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)} /></label><div className="account-admin-actions"><button className="primary" disabled={busy}>비밀번호 변경 저장</button><button type="button" disabled={busy} onClick={() => { setPasswordAccount(null); setPassword(""); setConfirmation(""); }}>취소</button></div></form>}
            {loading ? <p role="status">계정 목록을 불러오는 중…</p> : accounts.items.length === 0 ? <p className="account-admin-empty">해당 상태의 계정이 없습니다.</p> : <ul className="account-admin-list">{accounts.items.map(row => <li key={row.user_id} className="account-admin-card"><div className="account-admin-identity"><strong>{row.user_id}</strong><span>{row.name}</span><span className={`account-admin-badge ${row.status}`}>{statusLabels[row.status] || row.status}</span>{row.is_admin && <span className="account-admin-badge">관리자</span>}</div><dl><div><dt>가입 요청</dt><dd>{date(row.created_at)}</dd></div><div><dt>최근 로그인</dt><dd>{date(row.last_login_at)}</dd></div><div><dt>소속 부서</dt><dd>{row.affiliation?.department?.name || "미지정"}</dd></div><div><dt>소속 팀</dt><dd>{row.affiliation?.team?.name || "미지정"}</dd></div></dl><div className="account-admin-actions">{row.approval_status === "pending" ? <><button className="primary" disabled={busy} onClick={() => manage(row, "approve")}>{row.user_id} 승인</button><button disabled={busy} onClick={() => manage(row, "reject")}>{row.user_id} 거절</button></> : row.approval_status === "approved" && <>{row.is_active ? <button disabled={busy || row.user_id === account.user_id} onClick={() => manage(row, "disable")}>{row.user_id} 비활성화</button> : <button disabled={busy} onClick={() => manage(row, "enable")}>{row.user_id} 활성화</button>}<button disabled={busy} onClick={() => { setAffiliationAccount(null); setPasswordAccount(row); setPassword(""); setConfirmation(""); setError(""); }}>{row.user_id} 비밀번호 변경</button>{row.is_active && <button disabled={busy} aria-expanded={affiliationAccount?.user_id === row.user_id} onClick={() => {
                if (lock.current) return;
                setAffiliationAccount(row); setPasswordAccount(null); setPassword(""); setConfirmation(""); setError(""); setNotice("");
              }}>{row.user_id} 소속 배치</button>}</>}</div></li>)}</ul>}
            <div className="account-admin-actions"><button disabled={busy || loading || offset === 0} onClick={() => { setAffiliationAccount(null); setOffset(value => Math.max(0, value - 100)); }}>이전 계정</button><button disabled={busy || loading || offset + 100 >= accounts.total} onClick={() => { setAffiliationAccount(null); setOffset(value => value + 100); }}>다음 계정</button></div>
          </>}
          {tab === "settings" && <>
            <h2>서버 AI 이용 권한</h2><p className="account-admin-help">가입 승인과 AI 이용 권한을 함께 관리합니다. 계정을 비활성화하면 이 설정과 관계없이 접속할 수 없습니다.</p>
            {loading ? <p role="status">설정을 불러오는 중…</p> : settings && <><form className="account-admin-settings" onSubmit={event => void savePolicy(event)}><label className="account-admin-option"><input type="radio" name="codex-access" value="approved_accounts" checked={choice === "approved_accounts"} disabled={busy} onChange={() => setChoice("approved_accounts")} /><span><strong>guest와 승인된 모든 계정 허용</strong><small>챌린지 체험용. 활성 상태의 guest·admin·승인 회원이 서버의 AI를 이용합니다.</small></span></label><label className="account-admin-option"><input type="radio" name="codex-access" value="allowlist" checked={choice === "allowlist"} disabled={busy} onChange={() => setChoice("allowlist")} /><span><strong>운영자가 지정한 계정만 허용</strong><small>서버에 설정된 기존 AI 허용 목록을 적용합니다.</small></span></label><p className="account-admin-help">같은 서버의 AI 로그인과 공통 사용 한도를 사용합니다. 이 설정은 문서 접근 권한을 바꾸지 않습니다.</p><button type="submit" className="primary" disabled={busy || choice === settings.codex_account_access}>AI 권한 설정 저장</button></form>
            {settings.mattermost && <section className="account-admin-notifications" aria-label="가입 요청 알림"><h2>Mattermost 가입 알림</h2><p>{settings.mattermost.configured ? "서버의 가입 요청 알림이 설정되어 있습니다." : "서버 알림 설정이 필요합니다. 운영자가 Mattermost 연결 주소를 서버에 등록해야 합니다."}</p><p>전송 대기 {settings.mattermost.pending}건 · 전송 실패 {settings.mattermost.failed}건</p><p className="account-admin-help">알림 전송 상태와 관계없이 가입 요청은 계정 및 가입 승인 화면에서 확인할 수 있습니다.</p>{settings.mattermost.failed > 0 && <button disabled={busy || !settings.mattermost.configured} onClick={() => void mutate("/admin/notifications/retry", {}, "실패한 가입 알림의 재전송을 접수했습니다.")}>실패한 알림 다시 보내기</button>}</section>}
            </>}
          </>}
          {tab === "audit" && <>
            <h2>계정별 작업 기록</h2><p className="account-admin-help">한국 시간 기준입니다. 파일 작업과 계정 변경을 기록하며 문서 본문과 비밀번호는 표시하지 않습니다.</p>
            <form className="account-admin-toolbar" onSubmit={event => { event.preventDefault(); setBefore(null); setAuditQuery({ actor: actor.trim(), action: action.trim() }); }}><label>계정 아이디<input maxLength={80} value={actor} disabled={busy} onChange={event => setActor(event.target.value)} placeholder="전체 계정" /></label><label>작업 종류<select aria-label="작업 종류" value={action} disabled={busy} onChange={event => setAction(event.target.value)}><option value="">전체 작업</option>{Object.entries(actionLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><button disabled={busy || loading} type="submit">기록 조회</button></form>
            {loading ? <p role="status">감사 로그를 불러오는 중…</p> : audit.items.length === 0 ? <p className="account-admin-empty">조회한 작업 기록이 없습니다.</p> : <ol className="account-audit-list">{audit.items.map(row => <li key={row.id}><div className="account-audit-heading"><time>{date(row.created_at)}</time><strong>{row.actor_id}</strong><span>{actionLabels[row.action] || "기타 작업"}</span></div><p>{row.document_name || row.target_id || row.document_id || row.target_type}</p>{row.document_id && <small>문서 ID: {row.document_id}</small>}{row.version_id && <small>버전 ID: {row.version_id}</small>}<small>기록 #{row.id}</small></li>)}</ol>}
            <div className="account-admin-actions"><button disabled={busy || loading || before === null} onClick={() => setBefore(null)}>최신 기록</button><button disabled={busy || loading || audit.next_before === null} onClick={() => setBefore(audit.next_before)}>이전 기록 더 보기</button></div>
          </>}
        </section>
      </>}
    </main>
  </div>;
}
