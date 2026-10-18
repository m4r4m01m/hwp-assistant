import { useEffect, useRef, useState } from "react";
import { api, ApiError, jsonRequest } from "./api";
import { EditorAdapter } from "./editor";
import { previewSvg } from "./preview-svg";
import "./standard-designer.css";

type Settings = Record<string, number | string | boolean>;
type BaseStandard = { id: string; reference_sha256: string; profile_sha256: string };
export type VisualSelection = {
  id: string;
  revision: number;
  name: string;
  settings: Settings;
  settings_sha256: string;
  base_standard: BaseStandard;
  preview_sha256: string;
};
type Field = {
  key: string;
  label: string;
  group: string;
  type: "number" | "color" | "select" | "boolean";
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  options?: { value: string | number; label: string }[];
  help?: string;
  enabled_when?: { key: string; value: number | string | boolean };
  disabled_when?: { key: string; value: number | string | boolean };
};
type Catalog = { defaults: Settings; fields: Field[]; items: VisualSelection[] };
type PreviewResponse = {
  settings_sha256: string;
  preview_sha256: string;
  preview_base64: string;
  base_standard: BaseStandard;
};
type RenderedPreview = PreviewResponse & { key: string; settings: Settings; pages: string[] };
type Registration = {
  id: string;
  name: string;
  settings: Settings;
  settings_sha256: string;
  preview_sha256: string;
  seed_base64: string;
  base_standard: BaseStandard;
};

const inputValues = (settings: Settings) => Object.fromEntries(Object.entries(settings).map(([key, value]) => [key, String(value)]));
const settingsKey = (settings: Settings) => JSON.stringify(Object.fromEntries(Object.entries(settings).sort(([a], [b]) => a.localeCompare(b))));
function encode(bytes: Uint8Array) {
  const parts: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 8192)
    parts.push(String.fromCharCode(...bytes.subarray(offset, offset + 8192)));
  return btoa(parts.join(""));
}
function decode(value: string) { return Uint8Array.from(atob(value), char => char.charCodeAt(0)); }
async function sha256(bytes: Uint8Array) {
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes))))
    .map(value => value.toString(16).padStart(2, "0")).join("");
}
function readSettings(fields: Field[], values: Record<string, string>): { settings: Settings; error: string } {
  const settings: Settings = {};
  for (const field of fields) {
    const input = values[field.key] ?? "";
    if (field.type === "boolean") {
      if (input !== "true" && input !== "false") return { settings, error: `${field.group} ${field.label}: 켜짐 또는 꺼짐을 선택하세요.` };
      settings[field.key] = input === "true";
    } else if (field.type === "select") {
      const option = field.options?.find(option => String(option.value) === input);
      if (!option) return { settings, error: `${field.group} ${field.label}: 목록의 항목을 선택하세요.` };
      settings[field.key] = option.value;
    } else if (field.type === "color") {
      if (!/^#[0-9a-f]{6}$/i.test(input)) return { settings, error: `${field.label}: #과 6자리 색상값을 입력하세요.` };
      settings[field.key] = input.toUpperCase();
    } else {
      const number = Number(input);
      if (!input.trim() || !Number.isFinite(number) || (field.min !== undefined && number < field.min) || (field.max !== undefined && number > field.max))
        return { settings, error: `${field.group} ${field.label}: ${field.min ?? "최솟값"}~${field.max ?? "최댓값"}${field.unit || ""} 범위의 수치를 입력하세요.` };
      if (field.unit === "%" && !Number.isInteger(number)) return { settings, error: `${field.group} ${field.label}: 정수로 입력하세요.` };
      settings[field.key] = number;
    }
  }
  return { settings, error: "" };
}

export function StandardDesigner({ onClose, onRegistered, onReport }: {
  onClose: () => void;
  onRegistered?: (standard: VisualSelection) => void;
  onReport?: (standard: VisualSelection) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [source, setSource] = useState("");
  const [seed, setSeed] = useState("");
  const [phase, setPhase] = useState<"preparing" | "waiting" | "loading" | "rendering" | "ready" | "error">("preparing");
  const [preview, setPreview] = useState<RenderedPreview | null>(null);
  const [error, setError] = useState("");
  const [registrationError, setRegistrationError] = useState("");
  const [retry, setRetry] = useState(0);
  const [inputRevision, setInputRevision] = useState(0);
  const [name, setName] = useState("");
  const [registration, setRegistration] = useState<Registration | null>(null);
  const [registering, setRegistering] = useState(false);
  const [saved, setSaved] = useState<VisualSelection | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const host = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const adapter = useRef<EditorAdapter | null>(null);
  const focusedSetting = useRef<HTMLElement | null>(null);
  const generation = useRef(0);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pageUrls = useRef<string[]>([]);
  const mounted = useRef(true);
  const checked = readSettings(catalog?.fields || [], values);
  const currentKey = settingsKey(checked.settings);
  const valid = !!catalog && !checked.error;
  const current = valid && phase === "ready" && preview?.key === currentKey;
  const locked = registering || !!registration;

  function restoreSettingFocus() {
    const control = focusedSetting.current, active = document.activeElement;
    if (control?.isConnected && (active === document.body || (active instanceof HTMLElement && host.current?.contains(active))))
      control.focus({ preventScroll: true });
  }

  useEffect(() => {
    mounted.current = true;
    let live = true;
    const controller = new AbortController();
    void api<Catalog>("/visual-standards", { signal: controller.signal }).then(result => {
      if (!live) return;
      setCatalog(result);
      setValues(inputValues(result.defaults));
    }).catch(cause => { if (live && !controller.signal.aborted) { setError(String(cause)); setPhase("error"); } });
    void (async () => {
      const engine = await EditorAdapter.mount(host.current!);
      if (!live) { engine.destroy(); return; }
      adapter.current = engine;
      await engine.bridge.newDocument();
      if (!live) return;
      const exported = engine.exportHwpx();
      if (exported.contentLoss.count) throw new Error("예시 문서의 빈 바탕 파일을 만들지 못했습니다.");
      setSeed(encode(exported.bytes));
    })().catch(cause => { if (live) { setError(String(cause)); setPhase("error"); } });
    return () => {
      live = false;
      mounted.current = false;
      generation.current++;
      controller.abort();
      adapter.current?.destroy();
      adapter.current = null;
      pageUrls.current.forEach(url => URL.revokeObjectURL(url));
      pageUrls.current = [];
    };
  }, []);

  useEffect(() => {
    const requestGeneration = ++generation.current;
    if (!catalog || !seed || !valid || locked) return;
    let live = true;
    const controller = new AbortController();
    setPhase("waiting");
    setError("");
    const settings = checked.settings;
    const timer = setTimeout(() => {
      setPhase("loading");
      void api<PreviewResponse>("/visual-standards/preview", { ...jsonRequest("POST", { settings, seed_base64: seed }), signal: controller.signal })
        .then(result => {
          const rendering = queue.current.catch(() => {}).then(async () => {
            if (!live || requestGeneration !== generation.current) return;
            const engine = adapter.current;
            if (!engine) throw new Error("미리보기 엔진을 다시 열어 주세요.");
            const bytes = decode(result.preview_base64);
            if (await sha256(bytes) !== result.preview_sha256) throw new Error("미리보기 파일의 해시가 서버 결과와 다릅니다. 등록하지 않았습니다.");
            if (!live || requestGeneration !== generation.current) return;
            setPhase("rendering");
            await engine.load(bytes.buffer, "문서 표준 예시.hwpx");
            if (!live || requestGeneration !== generation.current) return;
            restoreSettingFocus();
            const count = await engine.sdk.pageCount();
            if (!Number.isInteger(count) || count < 1 || count > 6) throw new Error("예시 문서가 예상 범위(1~6쪽)를 벗어났습니다. 미리보기와 등록을 중단했습니다.");
            const urls: string[] = [];
            try {
              for (let index = 0; index < count; index++) {
                if (!live || requestGeneration !== generation.current) return;
                const svg = previewSvg(await engine.sdk.getPageSvg(index));
                const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
                urls.push(url);
                const image = new Image();
                image.src = url;
                await image.decode();
              }
              if (!live || requestGeneration !== generation.current) return;
              const previous = pageUrls.current;
              pageUrls.current = urls;
              restoreSettingFocus();
              setPreview({ ...result, key: currentKey, settings, pages: urls });
              setPhase("ready");
              setTimeout(() => previous.forEach(url => URL.revokeObjectURL(url)), 0);
            } finally {
              if (pageUrls.current !== urls) urls.forEach(url => URL.revokeObjectURL(url));
            }
          });
          queue.current = rendering;
          return rendering;
        }).catch(cause => {
          if (live && requestGeneration === generation.current && !controller.signal.aborted) { setError(String(cause)); setPhase("error"); }
        });
    }, 400);
    return () => { live = false; clearTimeout(timer); controller.abort(); };
  }, [catalog, seed, currentKey, valid, locked, retry, inputRevision]);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (dialogOpen && !element.open) element.showModal();
    else if (!dialogOpen && element.open) element.close();
  }, [dialogOpen]);

  function change(key: string, value: string) {
    if (locked) return;
    generation.current++;
    setValues(previous => ({ ...previous, [key]: value }));
    setInputRevision(previous => previous + 1);
    setSaved(null);
    setPhase("waiting");
    setError("");
    setRegistrationError("");
  }
  function selectSource(id: string) {
    if (!catalog || locked) return;
    const selected = catalog.items.find(item => item.id === id);
    generation.current++;
    setSource(id);
    // Editing an older registration creates a new copy. Its frozen snapshot and
    // hash remain untouched; only the copy receives defaults for new options.
    setValues(inputValues({ ...catalog.defaults, ...selected?.settings }));
    setInputRevision(previous => previous + 1);
    setName(selected ? `${selected.name} 사본` : "");
    setSaved(null);
    setPhase("waiting");
    setError("");
    setRegistrationError("");
  }
  async function register() {
    if (registering || (!registration && (!current || !preview || !name.trim()))) return;
    const body = registration || {
      id: crypto.randomUUID(), name: name.trim(), settings: preview!.settings,
      settings_sha256: preview!.settings_sha256, preview_sha256: preview!.preview_sha256,
      seed_base64: seed, base_standard: preview!.base_standard,
    };
    setRegistration(body);
    setRegistering(true);
    setRegistrationError("");
    try {
      const result = await api<VisualSelection>("/visual-standards", jsonRequest("POST", body));
      if (!mounted.current) return;
      if (result.id !== body.id || result.name !== body.name || result.settings_sha256 !== body.settings_sha256 ||
          result.preview_sha256 !== body.preview_sha256 || settingsKey(result.settings) !== settingsKey(body.settings) ||
          settingsKey(result.base_standard) !== settingsKey(body.base_standard))
        throw new Error("등록 응답이 요청한 표준과 달라 결과 재확인이 필요합니다.");
      setRegistration(null);
      setSaved(result);
      setSource(result.id);
      setName(result.name);
      setCatalog(previous => previous ? { ...previous, items: [result, ...previous.items.filter(item => item.id !== result.id)] } : previous);
      setDialogOpen(false);
      onRegistered?.(result);
    } catch (cause) {
      if (!mounted.current) return;
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        setRegistration(null);
        if (cause.status === 409) setRegistrationError("등록 기준이나 같은 요청의 내용이 달라졌습니다. 최신 미리보기를 확인한 뒤 다시 등록하세요.");
        else setRegistrationError(String(cause));
      } else setRegistrationError("등록 결과를 확인하지 못했습니다. 입력과 요청을 보존했습니다. ‘같은 등록 결과 재확인’을 눌러 주세요.");
    } finally { if (mounted.current) setRegistering(false); }
  }
  function close() {
    if (locked) return;
    if (source || currentKey !== settingsKey(catalog?.defaults || {})) {
      if (!saved && !window.confirm("아직 등록하지 않은 서식 설정이 있습니다. 문서 표준 편집을 닫을까요?")) return;
    }
    onClose();
  }

  const groups = Array.from(new Set(catalog?.fields.map(field => field.group) || []));
  const filteredFields = (catalog?.fields || []).filter(field => !filter.trim() || `${field.group} ${field.label} ${field.help || ""}`.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()));
  const sourceSelection = catalog?.items.find(item => item.id === source);
  const olderSource = sourceSelection && Object.keys(catalog!.defaults).some(key => !(key in sourceSelection.settings));
  function fieldDisabled(field: Field) {
    return locked || !!(field.enabled_when && checked.settings[field.enabled_when.key] !== field.enabled_when.value) ||
      !!(field.disabled_when && checked.settings[field.disabled_when.key] === field.disabled_when.value);
  }
  function fieldControl(field: Field) {
    const label = `${field.group} ${field.label}`, disabled = fieldDisabled(field);
    const description = field.help ? `standard-help-${field.key}` : undefined;
    if (field.type === "boolean") return <span className="standard-toggle">
      <input type="checkbox" role="switch" aria-label={label} aria-describedby={description} checked={values[field.key] === "true"} disabled={disabled} onChange={event => change(field.key, String(event.target.checked))} />
      <span aria-hidden="true">{values[field.key] === "true" ? "켜짐" : "꺼짐"}</span>
    </span>;
    if (field.type === "select") return <select aria-label={label} aria-describedby={description} value={values[field.key] || ""} disabled={disabled} onChange={event => change(field.key, event.target.value)}>
      {field.options?.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>;
    if (field.type === "color") return <span className="standard-color">
      <input type="color" aria-label={`${label} 선택`} aria-describedby={description} value={/^#[0-9a-f]{6}$/i.test(values[field.key] || "") ? values[field.key] : "#000000"} disabled={disabled} onChange={event => change(field.key, event.target.value)} />
      <input type="text" aria-label={label} aria-describedby={description} spellCheck={false} maxLength={7} value={values[field.key] || ""} disabled={disabled} onChange={event => change(field.key, event.target.value)} />
    </span>;
    return <span className="standard-value"><input type="number" aria-label={label} aria-describedby={description} min={field.min} max={field.max} step={field.step} value={values[field.key] || ""} disabled={disabled} onChange={event => change(field.key, event.target.value)} /><span>{field.unit}</span></span>;
  }
  const stateText = checked.error ? "입력값 확인 필요" : !seed || !catalog ? "예시 준비 중…" : phase === "waiting" ? "변경 반영 대기…" : phase === "loading" ? "예시 서식 적용 중…" : phase === "rendering" ? "한글 엔진으로 배치 중…" : phase === "ready" && current ? `반영 완료 · ${preview?.pages.length}쪽` : "미리보기 확인 필요";
  return <main className="standard-designer" onFocusCapture={event => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || host.current?.contains(target)) return;
    focusedSetting.current = target.matches(".standard-settings input, .standard-settings select") ? target : null;
  }} onPointerDownCapture={event => {
    if (event.target instanceof HTMLElement && !event.target.matches(".standard-settings input, .standard-settings select")) focusedSetting.current = null;
  }}>
    <nav className="standard-mobile-rail" aria-label="표준 편집 도구">
      <button aria-label="← 문서 목록" title="문서 목록" disabled={locked} onClick={close}>←<small>목록</small></button>
      <button aria-label={`문서 표준 설정 ${settingsOpen ? "닫기" : "열기"}`} aria-expanded={settingsOpen} onClick={() => setSettingsOpen(value => !value)}>☷<small>설정</small></button>
      <button aria-label={saved ? "등록 완료" : "표준 템플릿으로 등록"} title="표준 템플릿으로 등록" className="primary" disabled={!current || locked || !!saved} onClick={() => setDialogOpen(true)}>＋<small>{saved ? "완료" : "등록"}</small></button>
    </nav>
    <div className="standard-designer-grid">
      <aside className={`standard-settings${settingsOpen ? " settings-open" : ""}`} aria-label="문서 표준 설정">
    <header className="standard-designer-header">
      <div><small>새 보고서에 사용할 서식</small><h1>문서 표준 편집</h1><p>왼쪽 설정을 바꾸면 오른쪽 예시에 반영됩니다.</p></div>
      <div className="standard-designer-actions">
        <button disabled={locked} onClick={close}>← 문서 목록</button>
        <button className="primary" disabled={!current || locked || !!saved} onClick={() => setDialogOpen(true)}>{saved ? "등록 완료" : "표준 템플릿으로 등록"}</button>
      </div>
    </header>
        <div className="standard-preview-toolbar"><strong>예시 문서</strong><span className="standard-preview-state" role="status"><i className={`standard-preview-dot${current ? "" : " pending"}`} aria-hidden="true" />{stateText}</span></div>
        {(error || checked.error) && <div className="standard-designer-error" role="alert">{checked.error || error}{!checked.error && !locked && catalog && seed && <><br /><button onClick={() => setRetry(value => value + 1)}>미리보기 다시 확인</button></>}</div>}
        <div className="standard-settings-intro"><strong>서식 설정</strong>기본 표준과 기존 문서는 보존됩니다. 수정한 설정은 새 표준으로 등록합니다.</div>
        {catalog && <div className="standard-setting-fields standard-source"><label className="standard-field"><span>설정 불러오기</span><select aria-label="설정 불러오기" value={source} disabled={locked} onChange={event => selectSource(event.target.value)}><option value="">기본 문서작성 표준</option>{catalog.items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>}
        {olderSource && <p className="standard-source-note" role="note">이전 표준의 설정을 불러왔습니다. 새 옵션은 기본값으로 보완하며, 등록하면 별도의 새 표준으로 저장합니다.</p>}
        {catalog && <div className="standard-settings-search"><label htmlFor="standard-settings-filter">설정 찾기</label><div><input id="standard-settings-filter" type="search" value={filter} onChange={event => setFilter(event.target.value)} placeholder="정렬, 테두리, 쪽번호…" />{filter && <button aria-label="설정 검색 지우기" onClick={() => setFilter("")}>×</button>}</div></div>}
        {groups.filter(group => filteredFields.some(field => field.group === group)).map(group => <details className="standard-setting-group" key={`${filter ? "filtered" : "all"}-${group}`} open={!!filter || groups.indexOf(group) < 3}>
          <summary>{group}</summary><div className="standard-setting-fields">{filteredFields.filter(field => field.group === group).map(field => <label className={`standard-field${field.type === "select" ? " standard-field-select" : ""}${fieldDisabled(field) ? " standard-field-disabled" : ""}`} key={field.key}>
            <span>{field.label}</span>{fieldControl(field)}
            {field.help && <small className="standard-field-help" id={`standard-help-${field.key}`}>{field.help}</small>}
          </label>)}</div>
        </details>)}
        {catalog && !filteredFields.length && <p className="standard-settings-no-results" role="status">일치하는 설정이 없습니다.</p>}
        {catalog && <div className="standard-settings-footer"><button disabled={locked} onClick={() => selectSource("")}>기본값으로 되돌리기</button><p>제목의 파란 상하선과 □ → ㅇ → - 계층은 유지합니다. 글꼴은 예시를 표시하는 환경에 따라 대체될 수 있습니다.</p></div>}
      </aside>
      <section className="standard-preview" aria-label="문서 표준 실제 미리보기">
        <div className="standard-preview-pages" aria-busy={!current}>
          {saved && <div className="standard-registration" role="status"><h2>‘{saved.name}’ 등록 완료</h2><p>새 보고서를 만들 때 이 표준을 선택할 수 있습니다.</p>{onReport && <button className="primary" onClick={() => onReport(saved)}>이 표준으로 보고서 작성</button>}</div>}
          {!preview && <div className="standard-preview-empty">{stateText}</div>}
          {preview?.pages.map((url, index) => <figure key={url}><img src={url} alt={`문서 표준 예시 ${index + 1}쪽`} /><figcaption>{index + 1} / {preview.pages.length}쪽 · 실제 한글 엔진 미리보기</figcaption></figure>)}
        </div>
      </section>
    </div>
    <div className="standard-renderer" ref={host} aria-hidden="true" inert />
    <dialog className="standard-register-dialog" ref={dialog} onCancel={event => { if (locked) event.preventDefault(); else setDialogOpen(false); }} aria-labelledby="standard-register-title">
      <h2 id="standard-register-title">표준 템플릿 등록</h2><p>현재 미리보기의 서식 설정을 새 표준으로 보관합니다. 이후 수정은 다시 새 표준으로 등록합니다.</p>
      <label htmlFor="standard-register-name">표준 이름</label><input id="standard-register-name" value={name} maxLength={120} disabled={locked} onChange={event => setName(event.target.value)} autoFocus placeholder="예: 부서 업무보고 표준" />
      {registration && !registering && <p role="status">입력과 요청 ID를 유지하고 있습니다. 같은 등록 결과를 재확인하세요.</p>}
      {registrationError && <p role="alert">{registrationError}</p>}
      <div className="standard-register-dialog-actions"><button disabled={locked} onClick={() => setDialogOpen(false)}>취소</button><button className="primary" disabled={registering || (!registration && (!current || !name.trim()))} onClick={() => void register()}>{registering ? "등록 확인 중…" : registration ? "같은 등록 결과 재확인" : "새 표준 등록"}</button></div>
    </dialog>
  </main>;
}
