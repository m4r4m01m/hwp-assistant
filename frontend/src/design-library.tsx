import { useEffect, useRef, useState } from "react";
import { api, ApiError, jsonRequest } from "./api";
type Settings = Record<string, string | number | boolean>;
type Design = { id: string; name: string; kind: "table" | "title"; settings: Settings };
export function DesignLibrary({ settings, onApply, onChanged, disabled = false }: { settings?: Settings; onApply?: (settings: Settings) => void; onChanged?: (id: string) => void; disabled?: boolean }) {
  const [items, setItems] = useState<Design[]>([]), [name, setName] = useState(""), [kind, setKind] = useState<"table" | "title">("table");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const pending = useRef<{ id: string; name: string; kind: string; settings: Settings } | null>(null);
  const [removing, setRemoving] = useState<Design | null>(null);
  const reload = async () => setItems((await api<{ items: Design[] }>("/design-fragments")).items);
  useEffect(() => { void reload().catch(cause => setError(String(cause))); }, []);
  async function save() {
    if (!settings || !name.trim()) return;
    const body = pending.current || { id: crypto.randomUUID(), name: name.trim(), kind, settings };
    pending.current = body; setBusy(true); setError("");
    try { const item = await api<Design>("/design-fragments", jsonRequest("POST", body)); setItems(old => [item, ...old.filter(x => x.id !== item.id)]); pending.current = null; setName(""); setNotice("디자인을 보관함에 저장했습니다."); onChanged?.(item.id); }
    catch (cause) { if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) pending.current = null; setError(String(cause)); } finally { setBusy(false); }
  }
  return <section className="design-library" aria-label="디자인 보관함">
    <p>표의 선·색·여백이나 제목의 글자·디자인을 저장합니다. 문서 편집의 ‘서식·디자인’과 표준 편집에서 다시 사용할 수 있습니다.</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {settings && <div className="design-save"><label>저장할 디자인<select aria-label="저장할 디자인" value={kind} disabled={busy || !!pending.current || disabled} onChange={e => setKind(e.target.value as "table" | "title")}><option value="table">표</option><option value="title">문서·표지 제목</option></select></label><input aria-label="디자인 이름" placeholder="예: 파란 가로선 표" value={name} maxLength={120} disabled={busy || !!pending.current || disabled} onChange={e => setName(e.target.value)} /><button disabled={disabled || busy || !name.trim()} onClick={() => void save()}>{pending.current ? "저장 결과 재확인" : "디자인 저장"}</button>{pending.current && !busy && <p>응답을 확인하지 못했습니다. 같은 입력으로 재확인합니다.</p>}</div>}
    {!items.length && <p>저장한 디자인이 없습니다. 문서 표준 편집에서 모양을 조절한 뒤 ‘디자인 보관함’에 저장하세요.</p>}
    <div className="design-items">{items.map(item => <article key={item.id}><div className={`design-swatch ${item.kind}`} aria-hidden="true" style={{ borderColor: String(item.settings[item.kind === "table" ? "table_border_color" : "title_rule_color"] || "#437fc1"), background: item.kind === "table" ? String(item.settings.table_header_fill) : "white", color: item.kind === "title" ? String(item.settings.title_color || "#222") : "#222" }}>{item.kind === "table" ? "항목　│　내용" : "문서 제목"}</div><strong>{item.name}</strong><small>{item.kind === "table" ? "표 디자인" : "제목 디자인"} · 모양 안내</small>{onApply && <button disabled={busy || disabled} onClick={() => { onApply(item.settings); setNotice(`‘${item.name}’을 현재 표준에 적용했습니다. 오른쪽 실제 예시를 확인하세요.`); }}>현재 표준에 적용</button>}<button disabled={busy || disabled} onClick={() => setRemoving(item)}>삭제</button></article>)}</div>
    {removing && <div role="alertdialog" aria-label="디자인 삭제"><p>‘{removing.name}’을 보관함에서 삭제할까요? 이미 적용한 표준은 유지됩니다.</p><button onClick={() => setRemoving(null)}>취소</button><button disabled={busy} onClick={async () => { setBusy(true); setError(""); try { await api(`/design-fragments/${removing.id}`, { method: "DELETE" }); setItems(old => old.filter(item => item.id !== removing.id)); setRemoving(null); onChanged?.(removing.id); } catch (cause) { setError(String(cause)); } finally { setBusy(false); } }}>삭제 확인</button></div>}
  </section>;
}
