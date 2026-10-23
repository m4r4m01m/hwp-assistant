import { useEffect, useRef, useState } from "react";
import { api, ApiError, jsonRequest } from "./api";
import { EditorAdapter } from "./editor";
import { previewSvg } from "./preview-svg";
import { WorkspaceNav, WorkspaceIcon, type WorkspaceSection } from "./workspace-nav";
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
  type: "number" | "color" | "select" | "boolean" | "text" | "textarea";
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  options?: { value: string | number; label: string }[];
  help?: string;
  min_length?: number;
  max_length?: number;
  enabled_when?: { key: string; value: number | string | boolean };
  disabled_when?: { key: string; value: number | string | boolean };
};
type SettingsCatalog = { defaults: Settings; fields: Field[] };
type Preset = VisualSelection & { description: string };
type Catalog = SettingsCatalog & { items: VisualSelection[]; presets?: Preset[]; default_id?: string;
  legacy?: Record<string, SettingsCatalog>; max_pages?: number; reference_preview_url?: string;
  source?: { name: string; reference_sha256: string; profile_sha256: string; raw_endpoint_internal: boolean; preview_presentation?: string };
  rules?: { id: string; title: string; text: string; anchors: { part: string; path?: string; ordinal: number; top_level_index?: number; paragraph_id?: string | null }[] }[] };
type PreviewResponse = {
  settings_sha256: string;
  preview_sha256: string;
  preview_base64: string;
  base_standard: BaseStandard;
  max_pages?: number;
};
type RenderedPreview = PreviewResponse & { key: string; settings: Settings; pages: string[] };
type ReferenceResponse = { preview_base64: string; preview_sha256: string; reference_sha256: string; name: string; max_pages: number };
type ReferencePreview = Omit<ReferenceResponse, "preview_base64"> & { pages: string[] };
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
function readSettings(fields: Field[], values: Record<string, string>, legacyIntegerPercent = false): { settings: Settings; error: string } {
  const settings: Settings = {};
  for (const field of fields) {
    const input = values[field.key] ?? "";
    if (field.type === "text" || field.type === "textarea") {
      if ((field.min_length !== undefined && input.length < field.min_length) || (field.max_length !== undefined && input.length > field.max_length))
        return { settings, error: `${field.group} ${field.label}: ${field.min_length ?? 0}~${field.max_length ?? "허용"}자 범위로 입력하세요.` };
      settings[field.key] = input;
    } else if (field.type === "boolean") {
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
      if (legacyIntegerPercent && field.unit === "%" && !Number.isInteger(number)) return { settings, error: `${field.group} ${field.label}: 정수로 입력하세요.` };
      settings[field.key] = number;
    }
  }
  return { settings, error: "" };
}

export function StandardDesigner({ onClose, onRegistered, onReport, onNavigate }: {
  onClose: () => void;
  onRegistered?: (standard: VisualSelection) => void;
  onReport?: (standard: VisualSelection) => void;
  onNavigate?: (section: WorkspaceSection) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [settingsRevision, setSettingsRevision] = useState(3);
  const [view, setView] = useState<"settings" | "reference">("settings");
  const [reference, setReference] = useState<ReferencePreview | null>(null);
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
  const [settingsOpen, setSettingsOpen] = useState(() => window.matchMedia("(min-width: 721px)").matches);
  const [filter, setFilter] = useState("");
  const [settingsMode, setSettingsMode] = useState<"common" | "all">("common");
  const host = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const adapter = useRef<EditorAdapter | null>(null);
  const focusedSetting = useRef<HTMLElement | null>(null);
  const generation = useRef(0);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pageUrls = useRef<string[]>([]);
  const referencePageUrls = useRef<string[]>([]);
  const mounted = useRef(true);
  const definition = settingsRevision < 3 && catalog?.presets?.length ? catalog.legacy?.[String(settingsRevision)] : catalog;
  const checked = readSettings(definition?.fields || [], values, settingsRevision < 3);
  const currentKey = settingsKey(checked.settings);
  const valid = !!catalog && !!definition && !checked.error;
  const current = view === "settings" && valid && phase === "ready" && preview?.key === currentKey;
  const locked = registering || !!registration;

  useEffect(() => {
    const wide = window.matchMedia("(min-width: 721px)");
    const changed = () => setSettingsOpen(wide.matches);
    wide.addEventListener("change", changed);
    return () => wide.removeEventListener("change", changed);
  }, []);

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
      setSettingsRevision(result.presets?.length ? 3 : 2);
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
      referencePageUrls.current.forEach(url => URL.revokeObjectURL(url));
      referencePageUrls.current = [];
    };
  }, []);

  useEffect(() => {
    const requestGeneration = ++generation.current;
    if (!catalog || !seed || !valid || locked || view !== "settings") return;
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
            const limit = settingsRevision < 3 ? 6 : result.max_pages ?? catalog.max_pages;
            if (!Number.isInteger(limit) || !limit || !Number.isInteger(count) || count < 1 || count > limit)
              throw new Error(`예시 문서가 서버에서 정한 범위(1~${limit || "확인 필요"}쪽)를 벗어났습니다. 미리보기와 등록을 중단했습니다.`);
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
  }, [catalog, seed, currentKey, valid, locked, retry, inputRevision, settingsRevision, view]);

  useEffect(() => {
    if (view !== "reference" || !catalog?.reference_preview_url || !seed || locked) return;
    const requestGeneration = ++generation.current;
    let live = true;
    const controller = new AbortController();
    setPhase("loading"); setError("");
    const path = catalog.reference_preview_url.replace(/^\/api(?=\/)/, "");
    void api<ReferenceResponse>(path, { signal: controller.signal }).then(result => {
      const rendering = queue.current.catch(() => {}).then(async () => {
        if (!live || generation.current !== requestGeneration) return;
        const engine = adapter.current;
        if (!engine) throw new Error("기준서를 표시할 한글 엔진을 준비하지 못했습니다.");
        const bytes = decode(result.preview_base64);
        const expectedSource = catalog.source?.reference_sha256 || catalog.presets?.[0]?.base_standard.reference_sha256;
        if (await sha256(bytes) !== result.preview_sha256 || !expectedSource || result.reference_sha256 !== expectedSource)
          throw new Error("기준서의 원본 근거 또는 표시 파일 해시가 다릅니다.");
        if (!live || generation.current !== requestGeneration) return;
        setPhase("rendering");
        await engine.load(bytes.buffer, result.name + ".hwpx");
        if (!live || generation.current !== requestGeneration) return;
        restoreSettingFocus();
        const count = await engine.sdk.pageCount();
        if (!Number.isInteger(result.max_pages) || result.max_pages < 1 || !Number.isInteger(count) || count < 1 || count > result.max_pages)
          throw new Error("기준서 전체 쪽수를 확인할 수 없습니다. 일부 쪽만 표시하지 않았습니다.");
        const urls: string[] = [];
        try {
          for (let index = 0; index < count; index++) {
            if (!live || generation.current !== requestGeneration) return;
            const url = URL.createObjectURL(new Blob([previewSvg(await engine.sdk.getPageSvg(index))], { type: "image/svg+xml" }));
            urls.push(url);
            const image = new Image(); image.src = url; await image.decode();
          }
          if (!live || generation.current !== requestGeneration) return;
          const previous = referencePageUrls.current;
          referencePageUrls.current = urls;
          setReference({ name: result.name, preview_sha256: result.preview_sha256, reference_sha256: result.reference_sha256, max_pages: result.max_pages, pages: urls }); setPhase("ready");
          setTimeout(() => previous.forEach(url => URL.revokeObjectURL(url)), 0);
        } finally { if (referencePageUrls.current !== urls) urls.forEach(url => URL.revokeObjectURL(url)); }
      });
      queue.current = rendering;
      return rendering;
    }).catch(cause => {
      if (live && generation.current === requestGeneration && !controller.signal.aborted) { setError(String(cause)); setPhase("error"); }
    });
    return () => { live = false; controller.abort(); };
  }, [view, catalog, seed, locked, retry]);

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
    const revision = selected?.revision || (catalog.presets?.length ? 3 : 2);
    const sourceDefinition = revision < 3 && catalog.presets?.length ? catalog.legacy?.[String(revision)] : catalog;
    if (!sourceDefinition) { setError("이전 표준의 설정 계약을 불러오지 못했습니다. 목록을 다시 확인하세요."); return; }
    generation.current++;
    setView("settings");
    setSource(id);
    setSettingsRevision(revision);
    // Each old registration keeps its own field set; V3 source defaults never
    // enter V1/V2 settings or change their registration retry/hash contract.
    setValues(inputValues({ ...sourceDefinition.defaults, ...selected?.settings }));
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
  function canLeave() {
    if (locked) return false;
    const loaded = catalog?.items.find(item => item.id === source);
    if (currentKey !== settingsKey({ ...definition?.defaults, ...loaded?.settings })) {
      if (!saved && !window.confirm("아직 등록하지 않은 서식 설정이 있습니다. 문서 표준 편집을 닫을까요?")) return false;
    }
    return true;
  }
  function close() { if (canLeave()) onClose(); }

  function switchView(next: "settings" | "reference") {
    if (locked || next === view) return;
    generation.current++; setView(next); setPhase("waiting"); setError("");
  }

  const fields = (definition?.fields || []).filter(field => field.key !== "preset_id");
  const groupOrder = ["면 표시·주석", "쪽번호·주석", "문서 구성", "용지·여백", "본문 문서 제목", "제목 상자", "일반 본문", "항목 번호·기호",
    "□ 기호 항목", "ㅇ 기호 항목", "- 기호 항목", "대제목 · 첫째 항목", "중제목 · 둘째 항목", "소제목 · 셋째 항목",
    "표 구조", "표 내용", "표·그림 제목", "표·그림 단위", "표·그림 아래 주석", "본문 주석", "참고·출처",
    "표지 제목", "표지 일자", "표지 부서명", "목차 상자", "목차 제목", "목차 첫째 항목", "목차 둘째 항목", "작성 규정"];
  const groups = Array.from(new Set(fields.map(field => field.group))).sort((a, b) => {
    const aIndex = groupOrder.indexOf(a), bIndex = groupOrder.indexOf(b);
    return (aIndex < 0 ? groupOrder.length : aIndex) - (bIndex < 0 ? groupOrder.length : bIndex);
  });
  const commonKeys = new Set(["citation_style", "citation_superscript", "page_number_style", "page_number_position",
    "body_parenthetical_labels", "show_core_summary", "numbering_style", "margin_top_mm", "margin_bottom_mm", "margin_left_mm", "margin_right_mm",
    "title_font", "title_size_pt", "title_line_percent", "title_vertical_offset_pt", "body_font", "body_size_pt", "body_line_percent", "body_horizontal_align",
    "table_header_fill", "table_border_color", "table_border_scope", "table_width_percent", "table_horizontal_align", "table_vertical_align"]);
  const searching = !!filter.trim();
  const filteredFields = fields.filter(field => searching
    ? `${field.group} ${field.label} ${field.help || ""}`.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase())
    : settingsMode === "all" || commonKeys.has(field.key));
  const sourceSelection = catalog?.items.find(item => item.id === source);
  const olderSource = sourceSelection && sourceSelection.revision < 3 && !!catalog?.presets?.length;
  function fieldDisabled(field: Field) {
    return locked || view === "reference" || !!(field.enabled_when && checked.settings[field.enabled_when.key] !== field.enabled_when.value) ||
      !!(field.disabled_when && checked.settings[field.disabled_when.key] === field.disabled_when.value);
  }
  function fieldControl(field: Field) {
    const label = `${field.group} ${field.label}`, disabled = fieldDisabled(field);
    const description = field.help ? `standard-help-${field.key}` : undefined;
    if (field.type === "textarea") return <textarea aria-label={label} aria-describedby={description} minLength={field.min_length} maxLength={field.max_length} rows={4} value={values[field.key] || ""} disabled={disabled} onChange={event => change(field.key, event.target.value)} />;
    if (field.type === "text") return <input className="standard-text-value" type="text" aria-label={label} aria-describedby={description} minLength={field.min_length} maxLength={field.max_length} value={values[field.key] || ""} disabled={disabled} onChange={event => change(field.key, event.target.value)} />;
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
  const stateText = view === "settings" && checked.error ? "입력값 확인 필요" : !seed || !catalog ? "예시 준비 중…" : phase === "waiting" ? "변경 반영 대기…" : phase === "loading" ? view === "reference" ? "기준서 불러오는 중…" : "예시 서식 적용 중…" : phase === "rendering" ? "한글 엔진으로 배치 중…" : phase === "ready" && (current || view === "reference" && reference) ? `${view === "reference" ? "기준서 전체" : "반영 완료"} · ${view === "reference" ? reference?.pages.length : preview?.pages.length}쪽` : "미리보기 확인 필요";
  const displayError = view === "reference" ? error : checked.error || error;
  const selectedPreset = catalog?.presets?.find(preset => preset.settings.preset_id === values.preset_id);
  const displayedPages = view === "reference" ? reference?.pages : preview?.pages;
  return <main className="standard-designer" onFocusCapture={event => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || host.current?.contains(target)) return;
    focusedSetting.current = target.matches(".standard-settings input, .standard-settings select, .standard-settings textarea") ? target : null;
  }} onPointerDownCapture={event => {
    if (event.target instanceof HTMLElement && !event.target.matches(".standard-settings input, .standard-settings select, .standard-settings textarea")) focusedSetting.current = null;
  }}>
    <WorkspaceNav active="standards" compact className="standard-workspace-rail" label="표준 편집 도구" onBack={close} onNavigate={onNavigate ? section => { if (canLeave()) onNavigate(section); } : undefined} disabled={locked}>
      <button aria-label={`문서 표준 설정 ${settingsOpen ? "닫기" : "열기"}`} aria-expanded={settingsOpen} aria-controls="standard-settings-panel" onClick={() => setSettingsOpen(value => !value)}><WorkspaceIcon name="settings" /><span>설정</span></button>
    </WorkspaceNav>
    <div className={`standard-designer-grid${settingsOpen ? " settings-visible" : ""}`}>
      <aside id="standard-settings-panel" className="standard-settings" hidden={!settingsOpen} aria-label="문서 표준 설정">
        <header className="standard-designer-header"><h1>문서 표준 편집</h1><p>새 보고서의 여백·글자·표 서식을 정하고 저장합니다.</p></header>
        {view === "reference" ? <div className="standard-settings-intro"><strong>{catalog?.source?.name || "문서작성 기준서"}</strong>원본 기준서 전체를 읽기 전용으로 봅니다. 설정을 이어서 바꾸려면 ‘서식 미리보기’를 선택하세요.</div> : <>
        {catalog && <div className="standard-setting-fields standard-source"><label className="standard-field"><span>설정 불러오기</span><select aria-label="설정 불러오기" value={source} disabled={locked} onChange={event => selectSource(event.target.value)}><option value="">새 보고서 기본값</option>{catalog.items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>}
        {olderSource && <p className="standard-source-note" role="note">이전 등록 표준의 설정을 그대로 불러왔습니다. 기존 설정과 파일은 보존하고, 수정하면 같은 형식의 새 표준으로 등록합니다.</p>}
        {settingsRevision >= 3 && !!catalog?.presets?.length && <div className="standard-setting-fields standard-source standard-preset"><label className="standard-field"><span>기본 양식</span><select aria-label="기본 양식" value={values.preset_id || ""} disabled={locked} onChange={event => change("preset_id", event.target.value)}>{catalog.presets.map(preset => <option key={preset.id} value={String(preset.settings.preset_id)}>{preset.name}</option>)}</select></label><p className="standard-preset-description">{selectedPreset?.description}</p></div>}
        {catalog && <><div className="standard-settings-modes" role="tablist" aria-label="설정 범위">
          <button role="tab" aria-selected={settingsMode === "common"} onClick={() => setSettingsMode("common")}>자주 쓰는 설정</button>
          <button role="tab" aria-selected={settingsMode === "all"} onClick={() => setSettingsMode("all")}>상세 설정</button>
        </div><div className="standard-settings-search"><label htmlFor="standard-settings-filter">설정 찾기</label><div><input id="standard-settings-filter" type="search" value={filter} onChange={event => setFilter(event.target.value)} placeholder="주석, 여백, 정렬, 테두리…" />{filter && <button aria-label="설정 검색 지우기" onClick={() => setFilter("")}>×</button>}</div><p>{searching ? `전체 설정에서 ${filteredFields.length}개를 찾았습니다.` : settingsMode === "common" ? "주석·여백·글자·표를 빠르게 조절합니다. 들여쓰기 등 모든 항목은 상세 설정에서 볼 수 있습니다." : "기준서의 모든 서식과 작성 규정을 조절합니다."}</p></div></>}
        {groups.filter(group => filteredFields.some(field => field.group === group)).map(group => <details className="standard-setting-group" key={`${searching ? "filtered" : settingsMode}-${group}`} open={searching || groups.indexOf(group) === 0}>
          <summary>{group}</summary><div className="standard-setting-fields">{filteredFields.filter(field => field.group === group).map(field => <label className={`standard-field${field.type === "select" ? " standard-field-select" : ""}${field.type === "text" || field.type === "textarea" || field.key === "citation_style" ? " standard-field-text" : ""}${fieldDisabled(field) ? " standard-field-disabled" : ""}`} key={field.key}>
            <span>{field.label}</span>{fieldControl(field)}
            {field.help && <small className="standard-field-help" id={`standard-help-${field.key}`}>{field.help}</small>}
          </label>)}</div>
        </details>)}
        {catalog && !filteredFields.length && <p className="standard-settings-no-results" role="status">일치하는 설정이 없습니다.</p>}
        {catalog && <div className="standard-settings-footer"><button disabled={locked} onClick={() => selectSource("")}>새 표준 기본값 복원</button><p>현재 설정을 새 보고서 기본값으로 되돌립니다. 저장한 표준은 유지됩니다.</p></div>}
        {settingsRevision >= 3 && <details className="standard-source-rules"><summary>기본값과 미리보기 안내</summary><p>기준서에서 읽은 서식에 사용자 요청 기본값인 표 너비 100%, 기호 내어쓰기와 주석 자동 표식을 적용합니다.</p><p>기본 양식을 바꿔도 현재 수치와 문구는 유지합니다. 원본 기준서와 저장된 문서는 바뀌지 않습니다.</p>{catalog?.source?.preview_presentation && <p>{catalog.source.preview_presentation}</p>}<p>글꼴은 표시 환경에 따라 대체될 수 있습니다.</p></details>}
        </>}
        {!!catalog?.rules?.length && <details className="standard-source-rules"><summary>기준서 원문 규정</summary>{catalog.rules.map(rule => <section key={rule.id}><h2>{rule.title}</h2><p>{rule.text}</p></section>)}</details>}
      </aside>
      <section className="standard-preview" aria-label={view === "reference" ? "기준서 전체 실제 보기" : "문서 표준 실제 미리보기"}>
        <div className="standard-preview-toolbar">
          {catalog?.reference_preview_url ? <div className="standard-view-tabs" role="tablist" aria-label="기준서와 서식 보기"><button role="tab" aria-selected={view === "settings"} disabled={locked} onClick={() => switchView("settings")}>서식 미리보기</button><button role="tab" aria-selected={view === "reference"} disabled={locked} onClick={() => switchView("reference")}>기준서 전체</button></div> : <strong>서식 미리보기</strong>}
          <span className="standard-preview-state" role="status"><i className={`standard-preview-dot${current || view === "reference" && phase === "ready" ? "" : " pending"}`} aria-hidden="true" />{stateText}</span>
          {view === "settings" && <button className="primary" disabled={!current || locked || !!saved} onClick={() => setDialogOpen(true)}>{saved ? "등록 완료" : "새 표준 등록"}</button>}
        </div>
        {displayError && <div className="standard-designer-error" role="alert">{displayError}{(view === "reference" || !checked.error) && !locked && catalog && seed && <><br /><button onClick={() => setRetry(value => value + 1)}>미리보기 다시 확인</button></>}</div>}
        <div className="standard-preview-pages" aria-busy={phase !== "ready" || view === "settings" && !current}
          data-preview-sha256={view === "reference" ? reference?.preview_sha256 : preview?.preview_sha256}
          data-settings-sha256={view === "settings" ? preview?.settings_sha256 : undefined}>
          {saved && view === "settings" && <div className="standard-registration" role="status"><h2>‘{saved.name}’ 등록 완료</h2><p>새 보고서를 만들 때 이 표준을 선택할 수 있습니다.</p>{onReport && <button className="primary" onClick={() => onReport(saved)}>이 표준으로 보고서 작성</button>}</div>}
          {!displayedPages && <div className="standard-preview-empty">{stateText}</div>}
          {displayedPages?.map((url, index) => <figure key={url}><img src={url} alt={`${view === "reference" ? "기준서" : "문서 표준 예시"} ${index + 1}쪽`} /><figcaption>{index + 1} / {displayedPages.length}쪽</figcaption></figure>)}
        </div>
      </section>
    </div>
    <div className="standard-renderer" ref={host} aria-hidden="true" inert />
    <dialog className="standard-register-dialog" ref={dialog} onCancel={event => { if (locked) event.preventDefault(); else setDialogOpen(false); }} aria-labelledby="standard-register-title">
      <h2 id="standard-register-title">새 표준 등록</h2><p>현재 설정을 이름을 붙여 저장합니다. 보고서 작성의 ‘문서 양식’에서 선택할 수 있습니다.</p>
      <label htmlFor="standard-register-name">표준 이름</label><input id="standard-register-name" value={name} maxLength={120} disabled={locked} onChange={event => setName(event.target.value)} autoFocus placeholder="예: 부서 업무보고 표준" />
      {registration && !registering && <p role="status">입력과 요청 ID를 유지하고 있습니다. 같은 등록 결과를 재확인하세요.</p>}
      {registrationError && <p role="alert">{registrationError}</p>}
      <div className="standard-register-dialog-actions"><button disabled={locked} onClick={() => setDialogOpen(false)}>취소</button><button className="primary" disabled={registering || (!registration && (!current || !name.trim()))} onClick={() => void register()}>{registering ? "등록 확인 중…" : registration ? "같은 등록 결과 재확인" : "새 표준 등록"}</button></div>
    </dialog>
  </main>;
}
