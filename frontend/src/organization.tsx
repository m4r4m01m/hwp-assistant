import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, isAbortError, jsonRequest } from "./api";

type Unit = { id: string; name: string; can_access: boolean };
export type Affiliation = { user_id: string; department: Unit | null; team: Unit | null; revision: string | null };
type CatalogUnit = { id: string; name: string; kind: "division" | "department" | "team"; parent_id: string | null };
type Props = {
  onUpdated: () => void;
  userId?: string;
  onBusyChange?: (busy: boolean) => void;
  onForbidden?: (cause: unknown) => void;
};

export function OrganizationSettings({ onUpdated, userId, onBusyChange, onForbidden }: Props) {
  const [units, setUnits] = useState<CatalogUnit[]>([]), [user, setUser] = useState("");
  const [requestedUser, setRequestedUser] = useState(""), [reload, setReload] = useState(0);
  const [current, setCurrent] = useState<Affiliation | null>(null);
  const [department, setDepartment] = useState(""), [team, setTeam] = useState("");
  const [loading, setLoading] = useState(false), [saving, setSaving] = useState(false);
  const [blocked, setBlocked] = useState(false), [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const alive = useRef(true), lock = useRef(false), generation = useRef(0);
  const callbacks = useRef({ onUpdated, onBusyChange, onForbidden });
  callbacks.current = { onUpdated, onBusyChange, onForbidden };
  const target = userId ?? requestedUser;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      generation.current++;
      if (lock.current) callbacks.current.onBusyChange?.(false);
    };
  }, []);
  function load(value: Affiliation) {
    setCurrent(value);
    setDepartment(value.department?.id || "");
    setTeam(value.team?.id || "");
  }
  function failed(cause: unknown) {
    setError(errorMessage(cause));
    if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) {
      setCurrent(null); setUnits([]); setForbidden(true);
      callbacks.current.onForbidden?.(cause);
    }
  }
  useEffect(() => {
    const controller = new AbortController(), sequence = ++generation.current;
    if (forbidden) { setLoading(false); return () => controller.abort(); }
    setCurrent(null); setUnits([]); setError(""); setNotice(""); setBlocked(false);
    if (!target) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    void Promise.all([
      api<CatalogUnit[]>("/organization/units", { signal: controller.signal }),
      api<Affiliation>("/organization/accounts/" + encodeURIComponent(target), { signal: controller.signal }),
    ]).then(([catalog, value]) => {
      if (!alive.current || controller.signal.aborted || sequence !== generation.current) return;
      setUnits(catalog); load(value);
    }).catch(cause => {
      if (alive.current && !controller.signal.aborted && sequence === generation.current && !isAbortError(cause)) failed(cause);
    }).finally(() => {
      if (alive.current && !controller.signal.aborted && sequence === generation.current) setLoading(false);
    });
    return () => { controller.abort(); generation.current++; };
  }, [target, reload, forbidden]);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (lock.current || blocked || !current || current.user_id !== target) return;
    const sequence = generation.current;
    lock.current = true; setSaving(true); callbacks.current.onBusyChange?.(true);
    setError(""); setNotice("");
    try {
      const value = await api<Affiliation>("/organization/accounts/" + encodeURIComponent(current.user_id), {
        ...jsonRequest("PUT", { department_id: department || null, team_id: team || null, expected_revision: current.revision }),
        timeoutMs: 20000,
      });
      if (!alive.current || sequence !== generation.current) return;
      load(value); window.dispatchEvent(new Event("hwp-organization-updated")); setNotice("소속을 저장했습니다. 문서 공유 권한은 그대로 유지됩니다.");
      callbacks.current.onUpdated();
    } catch (cause) {
      if (!alive.current || sequence !== generation.current) return;
      failed(cause);
      if (!(cause instanceof ApiError) || cause.status === 409 || cause.status >= 500) {
        setBlocked(true);
        setNotice("다시 조회하여 현재 소속을 확인한 뒤 변경해 주세요. 저장 요청은 자동으로 반복하지 않습니다.");
      }
    } finally {
      lock.current = false;
      if (alive.current) { setSaving(false); callbacks.current.onBusyChange?.(false); }
    }
  }
  const content = <>
    <p>소속은 계정당 부서 하나·팀 하나입니다. 소속 변경은 문서함 접근 권한을 변경하지 않습니다. 문서함 구성원 권한은 별도로 관리하세요.</p>
    {!userId && <form className="team-member-form" onSubmit={event => {
      event.preventDefault();
      if (lock.current || forbidden) return;
      setRequestedUser(user.trim()); setReload(value => value + 1);
    }}>
      <label>소속을 지정할 계정 ID<input value={user} maxLength={80} disabled={saving || forbidden} required onChange={event => {
        setUser(event.target.value); setRequestedUser(""); setCurrent(null); setNotice("");
      }} /></label>
      <button disabled={saving || forbidden || !user.trim()}>소속 조회</button>
    </form>}
    {loading && <p role="status">소속 정보를 불러오는 중…</p>}
    {current && current.user_id === target && <form className="team-create-form" aria-label={current.user_id + " 소속 변경"} onSubmit={event => void save(event)}>
      <label>소속 부서<select aria-label="소속 부서" value={department} disabled={saving || blocked} onChange={event => { setDepartment(event.target.value); setTeam(""); }}>
        <option value="">소속 없음</option>{units.filter(unit => unit.kind === "department").map(unit => <option key={unit.id} value={unit.id}>{unit.name}</option>)}
      </select></label>
      <label>소속 팀<select aria-label="소속 팀" value={team} disabled={saving || blocked || !department} onChange={event => setTeam(event.target.value)}>
        <option value="">팀 없음</option>{units.filter(unit => unit.kind === "team" && unit.parent_id === department).map(unit => <option key={unit.id} value={unit.id}>{unit.name}</option>)}
      </select></label>
      <button disabled={saving || blocked || (department === (current.department?.id || "") && team === (current.team?.id || ""))}>소속 저장</button>
      {units.length === 0 && <p>등록된 부서·팀이 없습니다. 문서함의 공유 문서함 관리에서 부서와 팀을 먼저 만들어 주세요.</p>}
      {((current.team && !current.team.can_access) || (current.department && !current.department.can_access)) && <p>소속 문서함의 접근 권한이 없습니다. 문서함 관리자에게 구성원 등록을 요청하세요.</p>}
    </form>}
    {target && !forbidden && <button type="button" disabled={saving || loading} onClick={() => setReload(value => value + 1)}>소속 다시 조회</button>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </>;
  return userId
    ? <section className="organization-settings" aria-label={userId + " 소속 배치"}>{content}</section>
    : <details className="organization-settings"><summary>계정 소속 부서·팀 지정</summary>{content}</details>;
}
