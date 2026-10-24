import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, jsonRequest } from "./api";
import { EditorAdapter } from "./editor";
import { captureReport, fenceReport } from "./report-toc";
import type { LayoutHeading } from "./editor";
import { embedStandardPreviewFonts, standardPreviewFontNames } from "./standard-preview-fonts";
import { alignComparisonPages, compareSampleRegions, comparisonColors, measureSampleRegions, pageSettingRegions, settingImpact, type SampleRegion, type SamplePageGeometry } from "./standard-preview-regions";
import { WorkspaceNav, WorkspaceIcon, type WorkspaceSection } from "./workspace-nav";
import "./standard-designer.css";
import { DesignLibrary } from "./design-library";

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
  optional?: boolean;
  default?: number | string | boolean;
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
  parenthetical_scopes?: { value: string; label: string }[];
  legacy?: Record<string, SettingsCatalog>; max_pages?: number; reference_preview_url?: string;
  source?: { name: string; reference_sha256: string; profile_sha256: string; raw_endpoint_internal: boolean; preview_presentation?: string };
  rules?: { id: string; title: string; text: string; anchors: { part: string; path?: string; ordinal: number; top_level_index?: number; paragraph_id?: string | null }[] }[] };
type PreviewResponse = {
  settings_sha256: string;
  preview_sha256: string;
  preview_base64: string;
  base_standard: BaseStandard;
  max_pages?: number;
  context: string;
  phase: "layout" | "toc" | "ready";
  sample_id: string;
  sample_sha256: string;
  sample_regions: SampleRegion[];
  needs_toc: boolean;
  expires_at: number;
  page_count?: number;
  verification?: { converged: boolean; headings: LayoutHeading[]; measured_pages: Record<string, number> };
  toc_stage?: { headings: LayoutHeading[]; pass_index: number };
};
type PageSize = { width: number; height: number };
type RenderedPreview = PreviewResponse & { key: string; settings: Settings; pages: string[]; pageSizes: PageSize[]; geometry: SamplePageGeometry[]; geometryError?: string };
type ReferenceResponse = { preview_base64: string; preview_sha256: string; reference_sha256: string; name: string; max_pages: number };
type ReferencePreview = Omit<ReferenceResponse, "preview_base64"> & { pages: string[]; pageSizes: PageSize[] };
type Comparison = { pages: string[]; geometry: SamplePageGeometry[]; pageSizes: PageSize[]; settings: Settings };
type Registration = {
  replaces?: string;
  id: string;
  name: string;
  settings: Settings;
  settings_sha256: string;
  preview_sha256: string;
  context: string;
  preview_base64: string;
  base_standard: BaseStandard;
};
type PendingChange = { type: "source"; id: string } | { type: "leave"; section?: WorkspaceSection };

const inputValues = (settings: Settings) => Object.fromEntries(Object.entries(settings).map(([key, value]) => [key, String(value)]));
const settingsKey = (settings: Settings) => JSON.stringify(Object.fromEntries(Object.entries(settings).sort(([a], [b]) => a.localeCompare(b))));
const parentheticalRoles = ["square", "circle", "dash", "dot", "body", "summary"];
const favoritesStorageKey = "hwp.standard.favorite-groups.v2";
const bodyRoles = ["square", "circle", "dash", "dot"];
const headingRoles = ["heading", "subheading", "minor_heading", "numbered_4", "numbered_5", "numbered_6"];
const roleGroups: Record<string, string> = { square: "문단 내용 · □ 항목", circle: "문단 내용 · ㅇ 항목", dash: "문단 내용 · - 항목", dot: "문단 내용 · ․ 항목", heading: "문단 구분 · 1단계", subheading: "문단 구분 · 2단계", minor_heading: "문단 구분 · 3단계", numbered_4: "문단 구분 · 4단계", numbered_5: "문단 구분 · 5단계", numbered_6: "문단 구분 · 6단계" };
function readFavorites(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(favoritesStorageKey) || localStorage.getItem("hwp.standard.favorites.v1") || "[]");
    return Array.isArray(value) ? [...new Set(value.filter((key): key is string => typeof key === "string" && key.length < 100).map(key => key.replace("문단 제목", "문단 구분").replace("본문 ·", "문단 내용 ·")))].slice(0, 400) : [];
  } catch { return []; }
}
function sourceSettings(defaults: Settings = {}, selected?: VisualSelection): Settings {
  const settings = { ...defaults, ...selected?.settings };
  // A saved standard without this policy keeps its original preview/hash contract.
  if (selected && !Object.prototype.hasOwnProperty.call(selected.settings, "preview_layout_policy")) delete settings.preview_layout_policy;
  if (selected && !Object.prototype.hasOwnProperty.call(selected.settings, "body_parenthetical_scope")) delete settings.body_parenthetical_scope;
  return settings;
}
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
    if (field.optional && values[field.key] === undefined) continue;
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
      if (!input.trim() || !Number.isFinite(number))
        return { settings, error: `${field.group} ${field.label}: ${field.min ?? "최솟값"}~${field.max ?? "최댓값"}${field.unit || ""} 범위의 수치를 입력하세요.` };
      if (legacyIntegerPercent && field.unit === "%" && !Number.isInteger(number)) return { settings, error: `${field.group} ${field.label}: 정수로 입력하세요.` };
      const bounded = Math.min(field.max ?? Infinity, Math.max(field.min ?? -Infinity, number));
      settings[field.key] = bounded;
    }
  }
  // Layout policy is a frozen contract field, not a user-editable style control.
  if (values.preview_layout_policy !== undefined) {
    if (values.preview_layout_policy !== "NATIVE_V1") return { settings, error: "미리보기 배치 기준을 확인할 수 없습니다. 표준을 다시 불러오세요." };
    settings.preview_layout_policy = values.preview_layout_policy;
  }
  // An absent scope retains the legacy whole-document toggle and its hash.
  if (values.body_parenthetical_scope !== undefined) {
    const roles = values.body_parenthetical_scope ? values.body_parenthetical_scope.split(",") : [];
    if (roles.some(role => !parentheticalRoles.includes(role)) || new Set(roles).size !== roles.length)
      return { settings, error: "괄호 요약어의 표시 위치를 확인할 수 없습니다. 표준을 다시 불러오세요." };
    settings.body_parenthetical_scope = values.body_parenthetical_scope;
  }
  for (const [key, roles] of [["enabled_body_roles", bodyRoles], ["enabled_heading_roles", headingRoles]] as const) {
    if (values[key] === undefined) continue;
    const selected = values[key].split(",");
    if (!selected.length || !selected.some(role => roles.includes(role)) || roles.filter(role => selected.includes(role)).join(",") !== values[key]) return { settings, error: "본문과 문단 구분은 각각 하나 이상 사용하세요." };
    settings[key] = values[key];
  }
  if (values.heading_structure_policy !== undefined) {
    if (values.heading_structure_policy !== "CONTENT_V1") return { settings, error: "문단 구성 기준을 확인할 수 없습니다." };
    settings.heading_structure_policy = values.heading_structure_policy;
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
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [compare, setCompare] = useState(true);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const comparisonRef = useRef<Comparison | null>(null);
  const [saveMode, setSaveMode] = useState("new");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [inputNotice, setInputNotice] = useState("");
  const [compactLayout, setCompactLayout] = useState(() => window.innerWidth < 1180);
  const [zoom, setZoom] = useState<"page" | "fit" | "actual">("page");
  const [pageIndex, setPageIndex] = useState(0);
  const [paneSize, setPaneSize] = useState<PageSize>({ width: 0, height: 0 });
  const [sourceCandidate, setSourceCandidate] = useState("");
  const [pendingChange, setPendingChange] = useState<PendingChange | null>(null);
  const [filter, setFilter] = useState("");
  const [openGroups, setOpenGroups] = useState<string[]>(["문서 구성"]);
  const [openCategories, setOpenCategories] = useState<string[]>(["기본 구성·용지", "즐겨찾기"]);
  const [favorites, setFavorites] = useState<string[]>(readFavorites);
  const favoriteFocus = useRef<string | null>(null);
  useEffect(() => {
    try { localStorage.setItem(favoritesStorageKey, JSON.stringify(favorites)); } catch { /* Session-only favorites when storage is unavailable. */ }
    if (favoriteFocus.current) {
      const button = document.querySelector<HTMLButtonElement>(`button[data-favorite-key="${CSS.escape(favoriteFocus.current)}"]`);
      button?.focus();
      favoriteFocus.current = null;
    }
  }, [favorites]);
  const [hoveredField, setHoveredField] = useState("");
  const [focusedField, setFocusedField] = useState("");
  const [helpPosition, setHelpPosition] = useState<{ left: number; top: number } | null>(null);
  const [customFonts, setCustomFonts] = useState<Set<string>>(() => new Set());
  const [renderDetail, setRenderDetail] = useState("");
  const [expired, setExpired] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const layout = useRef<HTMLDivElement>(null);
  const pagePane = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const changeDialog = useRef<HTMLDialogElement>(null);
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
  const locked = registering || !!registration || deleting;
  const sourceSelection = catalog?.items.find(item => item.id === source);
  const baselineValues = inputValues(sourceSettings(definition?.defaults, sourceSelection));
  const baseline = readSettings(definition?.fields || [], baselineValues, settingsRevision < 3);
  // Compare the persisted meaning of valid numbers/colors, but keep incomplete
  // or invalid input dirty so navigating away cannot silently discard it.
  const changedSettings = !!catalog && (checked.error || baseline.error
    ? settingsKey(values) !== settingsKey(baselineValues)
    : currentKey !== settingsKey(baseline.settings));

  useEffect(() => {
    if (!preview) { setExpired(false); return; }
    const remaining = preview.expires_at * 1000 - Date.now();
    setExpired(!Number.isFinite(remaining) || remaining <= 0);
    if (!Number.isFinite(remaining) || remaining <= 0) return;
    const timer = setTimeout(() => setExpired(true), remaining);
    return () => clearTimeout(timer);
  }, [preview]);

  useEffect(() => {
    const element = layout.current;
    if (!element) return;
    setCompactLayout(element.clientWidth < 960);
    const observer = new ResizeObserver(entries => {
      const compact = entries[0].contentRect.width < 960;
      setCompactLayout(compact);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    const pane = pagePane.current;
    if (!pane) return;
    const observer = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      setPaneSize(previous => previous.width === width && previous.height === height ? previous : { width, height });
    });
    observer.observe(pane);
    return () => observer.disconnect();
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
      comparisonRef.current?.pages.forEach(url => URL.revokeObjectURL(url));
      pageUrls.current.forEach(url => URL.revokeObjectURL(url));
      pageUrls.current = [];
      referencePageUrls.current.forEach(url => URL.revokeObjectURL(url));
      referencePageUrls.current = [];
    };
  }, []);

  useEffect(() => {
    const requestGeneration = ++generation.current;
    if (!catalog || !seed || !valid || locked || view !== "settings") return;
    if (saved && preview?.key === currentKey && saved.preview_sha256 === preview.preview_sha256 && saved.settings_sha256 === preview.settings_sha256) { setPhase("ready"); return; }
    let live = true;
    const controller = new AbortController();
    setPhase("waiting");
    setError("");
    const settings = checked.settings;
    const timer = setTimeout(() => {
      setPhase("loading");
      void api<PreviewResponse>("/visual-standards/sample-preview", { ...jsonRequest("POST", { id: crypto.randomUUID(), settings, seed_base64: seed }), signal: controller.signal })
        .then(result => {
          const rendering = queue.current.catch(() => {}).then(async () => {
            if (!live || requestGeneration !== generation.current) return;
            const engine = adapter.current;
            if (!engine) throw new Error("미리보기 엔진을 다시 열어 주세요.");
            setPhase("rendering");
            const binding = { settings: result.settings_sha256, sample: result.sample_sha256, base: settingsKey(result.base_standard) };
            let count = 0;
            for (let pass = 0; ; pass++) {
              if (!live || requestGeneration !== generation.current) return;
              if (!result.context || result.settings_sha256 !== binding.settings || result.sample_sha256 !== binding.sample || settingsKey(result.base_standard) !== binding.base)
                throw new Error("확인 중인 예시와 서식 기준이 달라졌습니다. 등록하지 않았습니다.");
              const bytes = decode(result.preview_base64);
              if (await sha256(bytes) !== result.preview_sha256) throw new Error("미리보기 파일의 해시가 서버 결과와 다릅니다. 등록하지 않았습니다.");
              if (!live || requestGeneration !== generation.current) return;
              setRenderDetail(result.phase === "toc" ? `목차 쪽수 확인 중… ${result.toc_stage?.pass_index || 1}/3회` : "같은 업무 예시에 서식·페이지 배치 중…");
              await engine.load(bytes.buffer, "문서 표준 업무 예시.hwpx");
              if (!live || requestGeneration !== generation.current) return;
              restoreSettingFocus();
              count = await engine.sdk.pageCount();
              const limit = result.max_pages;
              if (!Number.isInteger(limit) || !limit || !Number.isInteger(count) || count < 1 || count > limit)
                throw new Error(`예시 문서가 서버에서 정한 범위(1~${limit || "확인 필요"}쪽)를 벗어났습니다. 미리보기와 등록을 중단했습니다.`);
              if (result.phase === "ready") {
                const final = captureReport(engine), outputSha = await sha256(final.source.bytes);
                if (!live || requestGeneration !== generation.current) return;
                if (outputSha !== result.preview_sha256 || count !== result.page_count)
                  throw new Error("확인한 예시를 다시 열었을 때 파일 또는 실제 쪽수가 달라졌습니다. 등록하지 않았습니다.");
                if (result.needs_toc) {
                  const verified = result.verification;
                  if (!verified?.converged || !verified.headings?.length) throw new Error("최종 목차의 검증 근거가 없습니다.");
                  const measured = engine.bridge.layoutEvidence(verified.headings);
                  if (measured.epoch !== final.source.epoch || measured.revision !== final.source.revision || measured.export_sha256 !== outputSha || measured.page_count !== count ||
                      settingsKey(Object.fromEntries(measured.headings.map(item => [item.id, item.page]))) !== settingsKey(verified.measured_pages))
                    throw new Error("다시 연 예시의 실제 제목 쪽수가 확인한 목차와 다릅니다. 등록하지 않았습니다.");
                }
                fenceReport(final);
                break;
              }
              if (pass >= 4 || !["layout", "toc"].includes(result.phase)) throw new Error("예시의 실제 쪽수 검증을 마치지 못했습니다. 등록하지 않았습니다.");
              const captured = captureReport(engine);
              const evidence = result.phase === "toc" && result.toc_stage ? engine.bridge.layoutEvidence(result.toc_stage.headings) : undefined;
              if (result.phase === "toc" && (!evidence || evidence.epoch !== captured.source.epoch || evidence.revision !== captured.source.revision || evidence.export_sha256 !== await sha256(captured.source.bytes)))
                throw new Error("목차 쪽수와 확인한 문서가 일치하지 않습니다.");
              fenceReport(captured);
              if (!live || requestGeneration !== generation.current) return;
              const next = await api<PreviewResponse>("/visual-standards/sample-preview/verify", { ...jsonRequest("POST", {
                context: result.context, engine_output_base64: encode(captured.source.bytes), page_count: count,
                ...(evidence ? { layout_evidence: evidence } : {}),
              }), signal: controller.signal });
              if (!live || requestGeneration !== generation.current) return;
              fenceReport(captured);
              result = next;
            }
            const urls: string[] = [];
            const svgs: string[] = [];
            const pageSizes: PageSize[] = [];
            try {
              for (let index = 0; index < count; index++) {
                if (!live || requestGeneration !== generation.current) return;
                const svg = await embedStandardPreviewFonts(await engine.sdk.getPageSvg(index));
                svgs.push(svg);
                const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
                urls.push(url);
                const image = new Image();
                image.src = url;
                await image.decode();
                pageSizes.push({ width: image.naturalWidth, height: image.naturalHeight });
              }
              if (!live || requestGeneration !== generation.current) return;
              let geometry: SamplePageGeometry[] = [], geometryError = "";
              try { geometry = await measureSampleRegions(svgs, result.sample_regions || [], controller.signal); }
              catch (cause) { if (!controller.signal.aborted) geometryError = String(cause); }
              if (!live || requestGeneration !== generation.current) return;
              const previous = pageUrls.current;
              pageUrls.current = urls;
              restoreSettingFocus();
              setPreview({ ...result, key: currentKey, settings, pages: urls, pageSizes, geometry, geometryError });
              if (!comparisonRef.current) {
                const before = { pages: svgs.map(svg => URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }))), geometry, pageSizes, settings };
                comparisonRef.current = before; setComparison(before);
              }
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
  }, [catalog, seed, currentKey, valid, locked, retry, inputRevision, settingsRevision, view, saved]);

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
        const pageSizes: PageSize[] = [];
        try {
          for (let index = 0; index < count; index++) {
            if (!live || generation.current !== requestGeneration) return;
            const url = URL.createObjectURL(new Blob([await embedStandardPreviewFonts(await engine.sdk.getPageSvg(index))], { type: "image/svg+xml" }));
            urls.push(url);
            const image = new Image(); image.src = url; await image.decode();
            pageSizes.push({ width: image.naturalWidth, height: image.naturalHeight });
          }
          if (!live || generation.current !== requestGeneration) return;
          const previous = referencePageUrls.current;
          referencePageUrls.current = urls;
          setReference({ name: result.name, preview_sha256: result.preview_sha256, reference_sha256: result.reference_sha256, max_pages: result.max_pages, pages: urls, pageSizes }); setPhase("ready");
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

  useEffect(() => {
    const element = changeDialog.current;
    if (!element) return;
    if (pendingChange && !element.open) element.showModal();
    else if (!pendingChange && element.open) element.close();
  }, [pendingChange]);

  function change(key: string, value: string) {
    if (locked) return;
    generation.current++;
    setValues(previous => {
      const next = { ...previous, [key]: value };
      if (key === "enabled_heading_roles") next.heading_structure_policy = "CONTENT_V1";
      if (key === "body_parenthetical_labels" && value === "true" && previous.body_parenthetical_scope === undefined && settingsRevision >= 3)
        next.body_parenthetical_scope = (previous.enabled_body_roles || bodyRoles.join(",")).split(",").includes("square") ? "square" : "";
      return next;
    });
    setInputRevision(previous => previous + 1);
    setSaved(null);
    setPhase("waiting");
    setError("");
    setRegistrationError("");
  }
  function selectSource(id: string) {
    if (!catalog || locked) return;
    const selected = catalog.items.find(item => item.id === id);
    if (id && !selected) return;
    const revision = selected?.revision || (catalog.presets?.length ? 3 : 2);
    const sourceDefinition = revision < 3 && catalog.presets?.length ? catalog.legacy?.[String(revision)] : catalog;
    if (!sourceDefinition) { setError("이전 표준의 설정 계약을 불러오지 못했습니다. 목록을 다시 확인하세요."); return; }
    generation.current++;
    setView("settings");
    comparisonRef.current?.pages.forEach(url => URL.revokeObjectURL(url));
    comparisonRef.current = null; setComparison(null);
    setSource(id);
    setSourceCandidate(id);
    setHoveredField("");
    setFocusedField("");
    setCustomFonts(new Set());
    setSettingsRevision(revision);
    // Each old registration keeps its own field set; V3 source defaults never
    // enter V1/V2 settings or change their registration retry/hash contract.
    setValues(inputValues(sourceSettings(sourceDefinition.defaults, selected)));
    setInputRevision(previous => previous + 1);
    setName(selected ? selected.name : "");
    setSaveMode(selected ? "replace" : "new");
    setSaved(null);
    setPhase("waiting");
    setError("");
    setRegistrationError("");
  }
  async function register() {
    if (registering || (!registration && (!current || !preview || !name.trim()))) return;
    // An unknown previous POST keeps its exact ID/context even after expiry:
    // the server checks its durable registration receipt before the TTL.
    if (!registration && (!Number.isFinite(preview!.expires_at) || preview!.expires_at * 1000 <= Date.now())) {
      setRegistrationError("예시 확인 시간이 지났습니다. 설정과 이름은 유지하고 현재 값으로 다시 미리보기 합니다.");
      setRetry(value => value + 1);
      return;
    }
    const body = registration || {
      id: crypto.randomUUID(), name: name.trim(), settings: preview!.settings,
      ...(saveMode === "replace" && sourceSelection ? { replaces: sourceSelection.id } : {}),
      settings_sha256: preview!.settings_sha256, preview_sha256: preview!.preview_sha256,
      context: preview!.context, preview_base64: preview!.preview_base64, base_standard: preview!.base_standard,
    };
    setRegistration(body);
    setRegistering(true);
    setRegistrationError("");
    try {
      const result = await api<VisualSelection>("/visual-standards/sample-register", jsonRequest("POST", { id: body.id, name: body.name, context: body.context, preview_base64: body.preview_base64, ...(body.replaces ? { replaces: body.replaces } : {}) }));
      if (!mounted.current) return;
      if (result.id !== body.id || result.name !== body.name || result.settings_sha256 !== body.settings_sha256 ||
          result.preview_sha256 !== body.preview_sha256 || settingsKey(result.settings) !== settingsKey(body.settings) ||
          settingsKey(result.base_standard) !== settingsKey(body.base_standard))
        throw new Error("등록 응답이 요청한 표준과 달라 결과 재확인이 필요합니다.");
      setRegistration(null);
      setSaved(result);
      setSource(result.id);
      setSourceCandidate(result.id);
      setName(result.name); setSaveMode("replace");
      setCatalog(previous => previous ? { ...previous, items: [result, ...previous.items.filter(item => item.id !== result.id && item.id !== body.replaces)] } : previous);
      setDialogOpen(false);
      onRegistered?.(result);
    } catch (cause) {
      if (!mounted.current) return;
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        setRegistration(null);
        if (cause.status === 409) {
          setRegistrationError("등록 기준을 다시 확인해야 합니다. 설정과 이름을 유지하고 최신 예시를 다시 준비합니다.");
          setRetry(value => value + 1);
        }
        else setRegistrationError(String(cause));
      } else setRegistrationError("등록 결과를 확인하지 못했습니다. 입력과 요청을 보존했습니다. ‘같은 등록 결과 재확인’을 눌러 주세요.");
    } finally { if (mounted.current) setRegistering(false); }
  }
  function applyChange(action: PendingChange) {
    if (locked) return;
    setPendingChange(null);
    if (action.type === "source") selectSource(action.id);
    else if (action.section) onNavigate?.(action.section);
    else onClose();
  }
  function requestChange(action: PendingChange) {
    if (locked) return;
    if (changedSettings) setPendingChange(action);
    else applyChange(action);
  }
  function close() { requestChange({ type: "leave" }); }

  function switchView(next: "settings" | "reference") {
    if (locked || next === view) return;
    setHoveredField(""); setFocusedField("");
    setPageIndex(0);
    generation.current++; setView(next); setPhase("waiting"); setError("");
  }

  const fields = (definition?.fields || []).filter(field => field.key !== "preset_id" && (settingsRevision < 3 || field.group !== "일반 본문" && field.key !== "numbering_style"));
  const groupOrder = ["문서 구성", "용지·여백", "줄바꿈·줄 채움", "문서 제목", "제목 상자", "일반 본문", "항목 번호·기호",
    "문단 내용 · □ 항목", "문단 내용 · ㅇ 항목", "문단 내용 · - 항목", "문단 내용 · ․ 항목", "문단 구분 · 1단계", "문단 구분 · 2단계", "문단 구분 · 3단계", "문단 구분 · 4단계", "문단 구분 · 5단계", "문단 구분 · 6단계",
    "표 구조", "표 내용", "표·그림 제목", "표·그림 단위", "면 표시·주석", "쪽번호·주석", "표·그림 아래 주석", "본문 주석", "참고·출처",
    "표지 제목", "표지 일자", "표지 부서명", "목차 상자", "목차 제목", "목차 · 1단계 제목", "목차 · 2단계 제목", "작성 규정"];
  const groups = Array.from(new Set(fields.map(field => field.group))).sort((a, b) => {
    const aIndex = groupOrder.indexOf(a), bIndex = groupOrder.indexOf(b);
    return (aIndex < 0 ? groupOrder.length : aIndex) - (bIndex < 0 ? groupOrder.length : bIndex);
  });
  const searching = !!filter.trim();
  const filteredFields = fields.filter(field => !searching || `${field.group} ${field.label} ${field.help || ""}`.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()));
  const isFavorite = (group: string) => favorites.includes(group) || fields.some(field => field.group === group && favorites.includes(field.key));
  const displayedGroups = groups.filter(group => filteredFields.some(field => field.group === group));
  const orderedGroups = [...displayedGroups.filter(isFavorite), ...displayedGroups.filter(group => !isFavorite(group))];
  function categoryForGroup(group: string) {
    if (isFavorite(group)) return "즐겨찾기";
    if (group.startsWith("문단 구분")) return "문단 구분";
    if (group.startsWith("문단 내용") || group === "일반 본문") return "문단 내용";
    if (group.startsWith("목차")) return "목차";
    if (group.startsWith("표지")) return "표지";
    if (group.includes("주석") || group === "참고·출처") return "주석·참고";
    if (group.startsWith("표")) return "표·그림";
    if (["문서 구성", "용지·여백", "줄바꿈·줄 채움", "문서 제목", "제목 상자"].includes(group)) return "기본 구성·용지";
    return "기타 규정";
  }
  const categories = ["즐겨찾기", "기본 구성·용지", "문단 구분", "문단 내용", "표·그림", "주석·참고", "표지", "목차", "기타 규정"].filter(category => orderedGroups.some(group => categoryForGroup(group) === category));
  function unavailable(group: string) {
    if (settingsRevision < 3) return "";
    const appendix1 = values.cover_mode === "OFF" || (values.cover_mode !== "ON" && values.preset_id === "appendix-1");
    const noToc = values.toc_mode === "NONE" || ((!values.toc_mode || values.toc_mode === "ORIGINAL") && values.preset_id === "appendix-1");
    if (noToc && group.startsWith("목차")) return "현재 구성에 목차가 없습니다. 문서 구성 → 목차에서 추가할 수 있습니다.";
    if (appendix1 && group.startsWith("표지")) return "현재 구성에 표지가 없습니다. 문서 구성 → 표지에서 추가할 수 있습니다.";
    if (!appendix1 && ["문서 제목", "제목 상자"].includes(group)) return "표지가 있는 구성에서는 문서 제목을 본문 첫머리에 반복하지 않습니다. 표지 설정을 사용하세요.";
    const optional: Record<string, [string, string]> = {

      "붙임·별첨 제목": ["attachment", "현재 예시에는 별도 붙임 문서가 없습니다."],
      "표·그림 단위": ["unit", "현재 예시의 표에는 금액·수량 등의 단위 표시가 없습니다."],
    };
    const entry = optional[group];
    return current && entry && !preview?.sample_regions.some(region => region.role === entry[0]) ? entry[1] : "";
  }
  const categoryUnavailable = (category: string) => orderedGroups.filter(group => categoryForGroup(group) === category).every(group => !!unavailable(group));
  function roleForGroup(group: string) { return Object.keys(roleGroups).find(role => roleGroups[role] === group); }
  function roleEnabled(role: string) {
    const key = bodyRoles.includes(role) ? "enabled_body_roles" : "enabled_heading_roles";
    return values[key] === undefined || values[key].split(",").includes(role);
  }
  function roleToggle(group: string) {
    const role = roleForGroup(group);
    if (!role || settingsRevision < 3) return null;
    const roles = bodyRoles.includes(role) ? bodyRoles : headingRoles;
    const key = bodyRoles.includes(role) ? "enabled_body_roles" : "enabled_heading_roles";
    const selected = new Set(values[key] === undefined ? roles : values[key].split(","));
    const last = roles === headingRoles ? role === "heading" : selected.has(role) && selected.size === 1;
    return <label className="standard-role-toggle" title={last ? "최소 한 항목은 사용해야 합니다." : bodyRoles.includes(role) ? "끄면 내용은 유지하고 가까운 사용 중 기호로 표시합니다." : role === "heading" ? "보고서의 큰 논점은 유지하고 사용 중인 문단 구분 서식으로 표시합니다." : "끄면 하위 제목의 의미를 문단 내용에 합칩니다. 사실·수치·출처는 유지합니다."}>
      <input type="checkbox" aria-label={`${group} 사용`} checked={selected.has(role)} disabled={locked || last} onChange={event => {
        if (roles === headingRoles) {
          const depth = roles.indexOf(role);
          roles.forEach((item, index) => { if (event.target.checked && index <= depth) selected.add(item); else if (!event.target.checked && index >= depth) selected.delete(item); });
          if (!selected.size) selected.add("heading");
        } else if (event.target.checked) selected.add(role); else selected.delete(role);
        change(key, roles.filter(value => selected.has(value)).join(","));
      }} /><span>사용</span></label>;
  }
  const olderSource = sourceSelection && sourceSelection.revision < 3 && !!catalog?.presets?.length;
  function fieldDisabled(field: Field) {
    return locked || view === "reference" || !!unavailable(field.group) || !!(roleForGroup(field.group) && !roleEnabled(roleForGroup(field.group)!)) || !!(field.enabled_when && checked.settings[field.enabled_when.key] !== field.enabled_when.value) ||
      !!(field.disabled_when && checked.settings[field.disabled_when.key] === field.disabled_when.value);
  }
  function fieldControl(field: Field) {
    const label = `${field.group} ${field.label}`, disabled = fieldDisabled(field);
    const displayValue = values[field.key] ?? String(field.default ?? "");
    const description = field.help ? `standard-help-${field.key}` : undefined;
    if (field.type === "text" && field.key.endsWith("_font")) {
      const currentFont = values[field.key] || "";
      const referenceFonts = Array.from(new Set([currentFont, ...Object.entries(definition?.defaults || {}).filter(([key]) => key.endsWith("_font")).map(([, value]) => String(value))])).filter(value => value && !standardPreviewFontNames.includes(value));
      const custom = customFonts.has(field.key);
      return <div className="standard-font-control"><select aria-label={label} aria-describedby="standard-font-help" disabled={disabled} value={custom ? "__custom__" : currentFont} onChange={event => {
        if (event.target.value === "__custom__") setCustomFonts(previous => new Set(previous).add(field.key));
        else { setCustomFonts(previous => { const next = new Set(previous); next.delete(field.key); return next; }); change(field.key, event.target.value); }
      }}><optgroup label="웹 미리보기 제공 글꼴">{standardPreviewFontNames.map(font => <option key={font} value={font}>{font}</option>)}</optgroup><optgroup label="현재 값·기준서 글꼴">{referenceFonts.map(font => <option key={font} value={font}>{font}</option>)}</optgroup><option value="__custom__">직접 입력…</option></select>{custom && <input className="standard-text-value" type="text" aria-label={`${label} 직접 입력`} value={currentFont} minLength={field.min_length} maxLength={field.max_length} disabled={disabled} onChange={event => change(field.key, event.target.value)} />}</div>;
    }
    if (field.type === "textarea") return <textarea aria-label={label} aria-describedby={description} minLength={field.min_length} maxLength={field.max_length} rows={4} value={displayValue} disabled={disabled} onChange={event => change(field.key, event.target.value)} />;
    if (field.type === "text") return <input className="standard-text-value" type="text" aria-label={label} aria-describedby={description} minLength={field.min_length} maxLength={field.max_length} value={displayValue} disabled={disabled} onChange={event => change(field.key, event.target.value)} />;
    if (field.type === "boolean") return <span className="standard-toggle">
      <input type="checkbox" role="switch" aria-label={label} aria-describedby={description} checked={values[field.key] === "true"} disabled={disabled} onChange={event => change(field.key, String(event.target.checked))} />
      <span aria-hidden="true">{values[field.key] === "true" ? "켜짐" : "꺼짐"}</span>
    </span>;
    if (field.type === "select") return <select aria-label={label} aria-describedby={description} value={displayValue} disabled={disabled} onChange={event => change(field.key, event.target.value)}>
      {field.options?.filter(option => field.key !== "citation_style" || option.value !== "NOTE_SHORT" || displayValue === "NOTE_SHORT").map(option => <option key={option.value} value={option.value}>{option.value === "ORIGINAL" && field.key === "cover_mode" ? `별첨 기본값 · ${values.preset_id === "appendix-1" ? "표지 없음" : "표지 사용"}` : option.value === "ORIGINAL" && field.key === "toc_mode" ? `별첨 기본값 · ${values.preset_id === "appendix-1" ? "목차 없음" : values.preset_id === "appendix-2" ? "별도 쪽" : values.cover_mode === "OFF" ? "별도 쪽 · 표지 없음" : "표지 안 목차"}` : option.value === "NOTE_SHORT" ? "이전 표준 · 본문·설명 모두 축약" : option.label}</option>)}
    </select>;
    if (field.type === "color") return <span className="standard-color">
      <input type="color" aria-label={`${label} 선택`} aria-describedby={description} value={/^#[0-9a-f]{6}$/i.test(values[field.key] || "") ? values[field.key] : "#000000"} disabled={disabled} onChange={event => change(field.key, event.target.value)} />
      <input type="text" aria-label={label} aria-describedby={description} spellCheck={false} maxLength={7} value={displayValue} disabled={disabled} onChange={event => change(field.key, event.target.value)} />
    </span>;
    return <span className="standard-number"><span className="standard-value"><input type="number" aria-label={label} aria-describedby={description} min={field.min} max={field.max} step={field.step} value={displayValue} disabled={disabled} onChange={event => change(field.key, event.target.value)} onBlur={event => {
      const parsed = Number(event.target.value);
      const fallback = Number(field.default ?? field.min ?? 0);
      const finite = event.target.value.trim() && Number.isFinite(parsed) ? parsed : fallback;
      const bounded = Math.min(field.max ?? Infinity, Math.max(field.min ?? -Infinity, finite));
      const step = field.step || 1, origin = field.min ?? 0;
      const snapped = Math.min(field.max ?? Infinity, Math.max(field.min ?? -Infinity, Number((origin + Math.round((bounded - origin) / step) * step).toFixed(4))));
      if (String(snapped) !== event.target.value) { change(field.key, String(snapped)); setInputNotice(`${field.label}: ${snapped}${field.unit || ""}로 보정했습니다.`); }
    }} /><span>{field.unit}</span></span><small>{field.min} ~ {field.max}{field.unit}</small></span>;
  }
  const stateText = view === "settings" && checked.error ? "입력값 확인 필요" : !seed || !catalog ? "예시 준비 중…" : phase === "waiting" ? "변경 반영 대기…" : phase === "loading" ? view === "reference" ? "기준서 불러오는 중…" : "예시 서식 적용 중…" : phase === "rendering" ? view === "settings" ? renderDetail || "한글 엔진으로 배치 중…" : "한글 엔진으로 배치 중…" : phase === "ready" && (current || view === "reference" && reference) ? `${view === "reference" ? "기준서 전체" : "반영 완료"} · ${view === "reference" ? reference?.pages.length : preview?.pages.length}쪽` : "미리보기 확인 필요";
  const displayError = view === "reference" ? error : checked.error || error;
  const selectedPreset = catalog?.presets?.find(preset => preset.settings.preset_id === values.preset_id);
  const displayedPages = view === "reference" ? reference?.pages : preview?.pages;
  const pairedPages = useMemo(() => comparison && preview ? alignComparisonPages(comparison.geometry, preview.geometry) : [], [comparison, preview]);
  const comparing = compare && view === "settings" && !!comparison && !!pairedPages.length;
  const totalPages = comparing ? pairedPages.length : displayedPages?.length || 0;
  const shownPage = Math.max(0, Math.min(pageIndex, totalPages - 1));
  const beforePage = comparing ? pairedPages[shownPage]?.before ?? -1 : shownPage;
  const afterPage = comparing ? pairedPages[shownPage]?.after ?? -1 : shownPage;
  const pageLabel = (index: number) => comparing ? `전 ${pairedPages[index].before === null ? '없음' : pairedPages[index].before! + 1} · 후 ${pairedPages[index].after === null ? '없음' : pairedPages[index].after! + 1}쪽` : `${index + 1} / ${totalPages}쪽`;
  const shownSize = view === "reference" ? reference?.pageSizes[shownPage] : preview?.geometry[afterPage] || preview?.pageSizes[afterPage] || (compare ? comparison?.pageSizes[beforePage] : undefined);
  const pageWidth = shownSize ? zoom === "actual" ? shownSize.width : zoom === "fit" ? Math.max(1, paneSize.width - 2) :
    Math.max(1, Math.min(shownSize.width, (comparing ? (paneSize.width - 24) / 2 : paneSize.width) - 2, (paneSize.height - (comparing ? 40 : 2)) * shownSize.width / shownSize.height)) : undefined;
  const scopes = settingsRevision >= 2 ? catalog?.parenthetical_scopes?.filter(scope => parentheticalRoles.includes(scope.value) && (settingsRevision < 3 || scope.value !== "body") && (!bodyRoles.includes(scope.value) || roleEnabled(scope.value)) && (scope.value !== "summary" || values.show_core_summary === "true")) || [] : [];
  const selectedScopes = new Set(values.body_parenthetical_scope === undefined ? settingsRevision >= 3 && values.body_parenthetical_labels !== "true" ? ["square"] : parentheticalRoles : values.body_parenthetical_scope.split(",").filter(Boolean));
  // Pointer and keyboard focus both expose the same contextual explanation.
  const activeField = hoveredField || focusedField;
  const activeCategory = activeField.startsWith("category:") ? activeField.slice(9) : "";
  const activeGroup = activeField.startsWith("group:") ? activeField.slice(6) : activeCategory;
  const impactFields = fields.filter(field => activeCategory ? categoryForGroup(field.group) === activeCategory && !unavailable(field.group) : field.group === activeGroup);
  const groupImpacts = impactFields.map(field => settingImpact(field.key, values.preset_id || ""));
  const impact = activeGroup ? { title: activeGroup, roles: [...new Set(groupImpacts.flatMap(value => value.roles))],
    description: unavailable(activeGroup) || (activeCategory && categoryUnavailable(activeCategory) ? "현재 구성에는 없습니다. 설정값은 보존하며 해당 구성을 선택하면 다시 사용할 수 있습니다." : roleForGroup(activeGroup) && !roleEnabled(roleForGroup(activeGroup)!) ? "사용하지 않는 항목입니다. 문단 구분을 끄면 하위 제목의 의미를 문단 내용에 합치고, 문단 내용 기호를 끄면 사용 중 기호로 표시합니다." : groupImpacts[0]?.description || "이 묶음의 설정을 예시에 적용합니다.") } : activeField ? settingImpact(activeField, values.preset_id || "") : null;
  const impactRects = (preview?.geometry || []).map(page => {
    const textRects = page.rects.filter(rect => impact?.roles.includes(rect.role));
    if (!impact?.roles.includes("page")) return textRects;
    const keys = activeGroup ? impactFields.filter(field => settingImpact(field.key, values.preset_id || "").roles.includes("page")).map(field => field.key) : [activeField];
    const pageRects = keys.flatMap(key => pageSettingRegions(key, page, preview!.settings));
    return [...new Map([...pageRects, ...textRects].map(rect => [JSON.stringify(rect), rect])).values()];
  });
  const changedKeys = comparison ? Array.from(new Set([...Object.keys(comparison.settings), ...Object.keys(preview?.settings || {})])).filter(key => comparison.settings[key] !== preview?.settings[key]) : [];
  const comparisonMarks = useMemo(() => compareSampleRegions(comparison?.geometry || [], preview?.geometry || [], changedKeys, String(preview?.settings.preset_id || "")), [comparison, preview]);
  const changeColor = (key: string) => changedKeys.includes(key) ? comparisonColors[changedKeys.indexOf(key) % comparisonColors.length] : undefined;
  const changedPages = (key: string) => pairedPages.flatMap((pair, i) => [...(comparisonMarks.before[pair.before ?? -1] || []), ...(comparisonMarks.after[pair.after ?? -1] || [])].some(mark => mark.setting === key) ? [i] : []);
  const impactPages = impactRects.flatMap((rects, index) => rects.length ? [index + 1] : []);
  function showHelp(key: string, element: HTMLElement, focus = false) {
    const box = element.getBoundingClientRect();
    setHelpPosition({ left: Math.max(8, Math.min(box.right + 12, window.innerWidth - 300)), top: Math.max(8, Math.min(box.top, window.innerHeight - 180)) });
    if (focus) setFocusedField(key); else setHoveredField(key);
  }
  useEffect(() => { setPageIndex(previous => Math.max(0, Math.min(previous, Math.max(0, totalPages - 1)))); }, [totalPages]);
  // Wheel distance can cross several pages; enlarged pages scroll to their edge first.
  const wheelState = useRef({ delta: 0, time: 0 });
  useEffect(() => {
    const pane = pagePane.current;
    if (!pane) return;
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY) || !totalPages || phase !== "ready") return;
      const down = event.deltaY > 0;
      if (zoom !== "page" && (down ? pane.scrollTop + pane.clientHeight < pane.scrollHeight - 2 : pane.scrollTop > 2)) return;
      event.preventDefault();
      const now = performance.now(), state = wheelState.current;
      if (now - state.time > 180 || Math.sign(state.delta) !== Math.sign(event.deltaY)) state.delta = 0;
      state.time = now;
      state.delta += event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? pane.clientHeight : 1);
      const steps = Math.trunc(state.delta / 100);
      if (!steps) return;
      state.delta -= steps * 100;
      setPageIndex(previous => Math.max(0, Math.min(totalPages - 1, previous + steps)));
    };
    pane.addEventListener("wheel", wheel, { passive: false });
    return () => pane.removeEventListener("wheel", wheel);
  }, [totalPages, phase, zoom]);
  useLayoutEffect(() => { pagePane.current?.scrollTo({ top: 0, left: 0 }); }, [shownPage, view, zoom, displayedPages]);

  function scopeControl(field: Field) {
    if (field.key !== "body_parenthetical_labels" || !scopes.length) return null;
    return <details className="standard-scope-fold"><summary>괄호 요약어 표시 위치</summary><fieldset aria-label="괄호 요약어 표시 위치" className="standard-parenthetical-scope" disabled={fieldDisabled(field) || values.body_parenthetical_labels !== "true"}>
      <p>(현황), (조치)처럼 문장 앞에 붙는 짧은 요약어입니다.</p>
      {scopes.map(scope => {
        const key = `body_parenthetical_scope.${scope.value}`;
        return <label key={scope.value} data-setting-key={key} data-changed={!!changeColor("body_parenthetical_scope")} style={{ borderLeftColor: changeColor("body_parenthetical_scope") }} onMouseEnter={event => showHelp(key, event.currentTarget)} onMouseLeave={() => setHoveredField("")} onFocus={event => showHelp(key, event.currentTarget, true)} onBlur={() => setFocusedField("")}>
          <input type="checkbox" value={scope.value} aria-label={`괄호 요약어 ${scope.label}`} checked={selectedScopes.has(scope.value)} onChange={event => {
            const selected = new Set(selectedScopes);
            if (event.target.checked) selected.add(scope.value); else selected.delete(scope.value);
            change("body_parenthetical_scope", parentheticalRoles.filter(role => selected.has(role)).join(","));
          }} /><span>{scope.label}</span>
        </label>;
      })}
      <small>필요한 내용에만 요약어를 허용합니다. 모든 항목에 강제로 붙이지 않습니다. 제목·표·주석에는 적용하지 않습니다.{values.numbering_style === "NUMBERED" && " 번호형도 문단 역할에 따라 선택합니다."}</small>
    </fieldset></details>;
  }
  function settingRow(field: Field) {
    const locations = changedPages(field.key);
    return <Fragment key={field.key}><div className="standard-setting-row">
      <label className={`standard-field${field.type === "select" ? " standard-field-select" : ""}${field.type === "text" || field.type === "textarea" || field.key === "citation_style" ? " standard-field-text" : ""}${fieldDisabled(field) ? " standard-field-disabled" : ""}${activeField === field.key ? " standard-field-active" : ""}`} data-setting-key={field.key} data-changed={!!changeColor(field.key)} style={{ borderLeftColor: changeColor(field.key) }} onMouseEnter={event => showHelp(field.key, event.currentTarget)} onMouseLeave={() => setHoveredField("")} onFocus={event => showHelp(field.key, event.currentTarget, true)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocusedField(""); }}>
        <span>{field.label}</span>{fieldControl(field)}
        {field.help && <small className="standard-field-help" id={`standard-help-${field.key}`}>{field.help}</small>}
      </label>
    </div>{compare && changeColor(field.key) && <div className="standard-change-locations" style={{ color: changeColor(field.key) }}>{locations.length ? <><span>변경 위치</span>{locations.map(index => <button key={index} onClick={() => setPageIndex(index)}>{pageLabel(index)}</button>)}</> : <small>설정 변경됨 · 별도로 강조할 위치 없음</small>}</div>}{scopeControl(field)}</Fragment>;
  }
  return <main className="standard-designer" onFocusCapture={event => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || host.current?.contains(target)) return;
    focusedSetting.current = target.matches(".standard-settings input, .standard-settings select, .standard-settings textarea") ? target : null;
  }} onPointerDownCapture={event => {
    if (event.target instanceof HTMLElement && !event.target.matches(".standard-settings input, .standard-settings select, .standard-settings textarea")) focusedSetting.current = null;
  }}>
    <WorkspaceNav active="standards" compact className="standard-workspace-rail" label="표준 편집 도구" onBack={close} onNavigate={onNavigate ? section => requestChange({ type: "leave", section }) : undefined} disabled={locked}>
      <button aria-label={`문서 표준 설정 ${settingsOpen ? "닫기" : "열기"}`} aria-expanded={settingsOpen} aria-controls="standard-settings-panel" onClick={() => setSettingsOpen(value => !value)}><WorkspaceIcon name="settings" /><span>설정</span></button>
    </WorkspaceNav>
    <div ref={layout} className={`standard-designer-grid${settingsOpen ? " settings-visible" : ""}${compactLayout ? " compact-layout" : ""}`}>
      <aside id="standard-settings-panel" className="standard-settings" hidden={!settingsOpen} aria-label="문서 표준 설정">
        <header className="standard-designer-header"><div className="standard-heading-row"><h1>문서 표준 편집{changedSettings ? " *" : ""}</h1><button onClick={() => setSettingsOpen(false)}>설정 접기</button></div><p>같은 예시로 서식을 비교하세요.</p>
          <div className="standard-preview-toolbar">
            {catalog?.reference_preview_url ? <div className="standard-view-tabs" role="tablist" aria-label="기준서와 서식 보기"><button role="tab" aria-selected={view === "settings"} disabled={locked} onClick={() => switchView("settings")}>서식 미리보기</button><button role="tab" aria-selected={view === "reference"} disabled={locked} onClick={() => switchView("reference")}>기준서 전체</button></div> : <strong>서식 미리보기</strong>}
            {view === "settings" && <button className="primary" disabled={!current || locked || !!saved} onClick={() => { setSaveMode(sourceSelection ? "replace" : "new"); if (sourceSelection) setName(sourceSelection.name); expired ? setRetry(value => value + 1) : setDialogOpen(true); }}>{saved ? "저장 완료" : expired ? "예시 다시 확인" : sourceSelection ? "변경사항 저장" : "새 표준 등록"}</button>}
          </div>
          <div className="standard-preview-description"><strong>{view === "reference" ? catalog?.source?.name || "문서작성 기준서" : sourceSelection ? `${sourceSelection.name}${changedSettings ? " · 수정 중" : ""}` : selectedPreset?.name || "서식 예시"}</strong><span className="standard-preview-state" role="status"><i className={`standard-preview-dot${current || view === "reference" && phase === "ready" ? "" : " pending"}`} aria-hidden="true" />{stateText}</span></div>
        </header>
        <div className="standard-settings-content">
        {displayError && <div className="standard-designer-error" role="alert">{displayError}{(view === "reference" || !checked.error) && !locked && catalog && seed && <><br /><button onClick={() => setRetry(value => value + 1)}>미리보기 다시 확인</button></>}</div>}
        {current && expired && !registration && !saved && <p className="standard-designer-error" role="status">확인 시간이 지났습니다. ‘예시 다시 확인’을 눌러 주세요. 설정과 이름은 유지됩니다.</p>}
        {saved && view === "settings" && <div className="standard-registration" role="status"><h2>‘{saved.name}’ 등록 완료</h2>{onReport && <button className="primary" onClick={() => onReport(saved)}>이 표준으로 보고서 작성</button>}</div>}
        {view === "reference" ? <div className="standard-settings-intro"><strong>{catalog?.source?.name || "문서작성 기준서"}</strong>원본 기준서 전체를 읽기 전용으로 봅니다. 설정을 이어서 바꾸려면 ‘서식 미리보기’를 선택하세요.</div> : <>
        {settingsRevision >= 3 && !!catalog?.presets?.length && <div className="standard-setting-fields standard-source standard-preset"><label className="standard-field"><span>기본 양식 · 별첨 구성</span><select aria-label="기본 양식" value={values.preset_id || ""} disabled={locked} onChange={event => change("preset_id", event.target.value)}>{catalog.presets.map(preset => <option key={preset.id} value={String(preset.settings.preset_id)}>{preset.name}</option>)}</select></label><p className="standard-preset-description">{selectedPreset?.description}</p><p className="standard-preset-description">별첨 1·2·3은 고정된 시작 양식입니다. 조절한 결과는 사용자 표준으로 저장합니다. 표지·목차 등은 아래 문서 구성에서 변경하세요.</p></div>}
        {!!catalog?.items.length && <details className="standard-saved-sources" open><summary>사용자 표준 관리</summary><div className="standard-setting-fields standard-source"><p className="standard-preset-description">표준을 선택해 수정합니다. 저장할 때 덮어쓰기 또는 새 표준 추가를 선택할 수 있습니다. *는 저장하지 않은 변경입니다.</p><label className="standard-field"><span>저장한 표준</span><select aria-label="저장한 표준" value={sourceCandidate} disabled={locked} onChange={event => setSourceCandidate(event.target.value)}><option value="">불러올 표준 선택</option>{catalog.items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><button disabled={locked || !sourceCandidate} onClick={() => requestChange({ type: "source", id: sourceCandidate })}>선택한 표준 편집</button>{sourceSelection && <button disabled={locked} onClick={() => setDeleteOpen(true)}>현재 표준 삭제</button>}</div></details>}
        {sourceSelection && <p className="standard-source-note" role="note">‘{sourceSelection.name}’에서 시작{changedSettings ? " · 설정 수정 중" : " · 저장된 설정"}</p>}
        {olderSource && <p className="standard-source-note" role="note">이전 등록 표준의 설정을 그대로 불러왔습니다. 기존 설정과 파일은 보존하고, 수정하면 같은 형식의 새 표준으로 등록합니다.</p>}
        {settingsRevision >= 3 && values.numbering_style === "NUMBERED" && <p className="standard-source-note">이 표준은 이전의 본문 번호 방식을 사용합니다. <button disabled={locked} onClick={() => change("numbering_style", "BULLETS")}>본문 기호로 전환</button></p>}
        {inputNotice && <p className="standard-source-note" role="status">{inputNotice}</p>}
        {settingsRevision >= 3 && <details className="standard-saved-sources"><summary>디자인 보관함 · 표와 제목</summary><DesignLibrary settings={valid ? checked.settings : undefined} disabled={locked || !current} onApply={patch => { generation.current++; setValues(old => ({ ...old, ...inputValues(patch) })); setInputRevision(x => x + 1); setSaved(null); setPhase("waiting"); }} /></details>}
        {catalog && <div className="standard-settings-search"><label htmlFor="standard-settings-filter">설정 찾기</label><div><input id="standard-settings-filter" type="search" value={filter} onChange={event => setFilter(event.target.value)} placeholder="주석, 여백, 정렬, 테두리…" />{filter && <button aria-label="설정 검색 지우기" onClick={() => setFilter("")}>×</button>}</div><p>{searching ? `전체 설정에서 ${filteredFields.length}개를 찾았습니다.` : "문단 구분은 제목의 단계, 문단 내용은 □·ㅇ·-로 작성하는 설명입니다."}</p></div>}
        {catalog && <p className="standard-favorites-note">묶음 왼쪽 ☆를 누르면 위로 모입니다. 사용 체크는 문서에 쓸 항목을 정합니다.</p>}
        <details className="standard-font-note"><summary>글꼴 안내</summary><p id="standard-font-help">웹 제공 글꼴과 현재 표준의 글꼴을 선택합니다. 기기에서 지원하지 않는 글꼴은 대체될 수 있습니다.</p></details>
        {categories.map(category => <details className="standard-setting-category" key={category} data-setting-category={category} open={searching || openCategories.includes(category)} onToggle={event => { const open = event.currentTarget.open; setOpenCategories(previous => previous.includes(category) === open ? previous : open ? [...previous, category] : previous.filter(item => item !== category)); }}>
          <summary onMouseEnter={event => showHelp(`category:${category}`, event.currentTarget)} onMouseLeave={() => setHoveredField("")} onFocus={event => showHelp(`category:${category}`, event.currentTarget, true)} onBlur={() => setFocusedField("")}><span>{category}</span>{categoryUnavailable(category) && <small>없음</small>}</summary>
          {category === "문단 구분" && <p className="standard-category-help">내용을 나누는 제목 단계입니다. 필요한 단계만 사용하세요. 깊은 단계를 끄면 그 의미를 문단 내용에 합치며 사실·수치·출처는 유지합니다. 큰 논점을 나누는 최상위 구분은 유지합니다.</p>}
          {category === "문단 내용" && <p className="standard-category-help">각 문단에 담을 논점과 설명입니다. □ 주요 내용 → ㅇ 보충 설명 → - 세부 내용 → ․ 추가 설명 순으로 구성합니다.</p>}
        {orderedGroups.filter(group => categoryForGroup(group) === category).map(group => <section className={`standard-setting-section${isFavorite(group) ? " is-favorite" : ""}${unavailable(group) ? " is-unavailable" : ""}`} key={`${searching ? "filtered" : "all"}-${group}`} data-setting-group={group}>
          <div className="standard-group-heading" onMouseEnter={event => showHelp(`group:${group}`, event.currentTarget)} onMouseLeave={() => setHoveredField("")} onFocus={event => showHelp(`group:${group}`, event.currentTarget, true)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocusedField(""); }}>
            <button className="standard-favorite" data-favorite-key={group} aria-label={`${group} 즐겨찾기 ${isFavorite(group) ? "해제" : "추가"}`} aria-pressed={isFavorite(group)} onClick={() => {
              favoriteFocus.current = group;
              setOpenCategories(previous => previous.includes("즐겨찾기") ? previous : [...previous, "즐겨찾기"]);
              setFavorites(previous => { const removed = previous.filter(key => key !== group && !fields.some(field => field.group === group && field.key === key)); return isFavorite(group) ? removed : [...removed, group]; });
            }}>{isFavorite(group) ? "★" : "☆"}</button>
            <button className="standard-group-toggle" aria-expanded={searching || openGroups.includes(group)} onClick={() => setOpenGroups(previous => previous.includes(group) ? previous.filter(value => value !== group) : [...previous, group])}>{group}<span aria-hidden="true">{searching || openGroups.includes(group) ? "−" : "+"}</span></button>
            {roleToggle(group)}
            {unavailable(group) && <span className="standard-absence" title={unavailable(group)}>없음</span>}
          </div>
          {(searching || openGroups.includes(group)) && <div className="standard-setting-fields">{unavailable(group) ? <p className="standard-category-help">{unavailable(group)} 기존 설정값은 보존합니다.</p> : <>{roleForGroup(group) && <p className="standard-category-help">{settingImpact(fields.find(field => field.group === group && !field.key.endsWith("_marker"))?.key || "", values.preset_id || "").description}</p>}{group === "줄바꿈·줄 채움" && <p className="standard-category-help">자간 최종값: {bodyRoles.map(role => `${roleGroups[role].replace("문단 내용 · ", "")} ${Math.min(50, Math.max(-20, Number(values[role + "_spacing_percent"] || 0) + Number(checked.settings.body_tracking_percent || 0)))}%`).join(" / ")}. 마지막 절의 긴 문장은 강제 개행 없이 자동 줄바꿈됩니다.</p>}{filteredFields.filter(field => field.group === group).sort((a, b) => Number(b.key.endsWith("_marker")) - Number(a.key.endsWith("_marker"))).map(field => settingRow(field))}</>}</div>}
        </section>)}</details>)}
        {catalog && !filteredFields.length && <p className="standard-settings-no-results" role="status">일치하는 설정이 없습니다.</p>}
        {catalog && <div className="standard-settings-footer"><button disabled={locked} onClick={() => requestChange({ type: "source", id: "" })}>새 표준 기본값 복원</button><p>별첨 1과 새 보고서 기본 설정으로 돌아갑니다. 현재 조절한 값은 초기화하고, 저장한 표준은 유지합니다.</p></div>}
        {settingsRevision >= 3 && <details className="standard-source-rules"><summary>기본값과 미리보기 안내</summary><p>기준서에서 읽은 서식에 사용자 요청 기본값인 표 너비 100%, 기호 내어쓰기와 주석 자동 표식을 적용합니다.</p><p>오른쪽은 같은 합성 업무 자료에 별첨 구성과 현재 설정을 적용한 예시입니다. AI를 호출하거나 실제 업무 사실을 새로 작성하지 않습니다.</p><p>기본 양식을 바꿔도 조절한 수치는 유지합니다. 구성·표식·요약 표시를 바꾸면 같은 자료의 배치와 표시가 달라집니다. 원본 기준서와 저장된 문서는 바뀌지 않습니다.</p><p>기준서 원문은 ‘기준서 전체’에서 확인할 수 있습니다. 글꼴은 표시 환경에 따라 대체될 수 있습니다.</p></details>}
        </>}
        {!!catalog?.rules?.length && <details className="standard-source-rules"><summary>기준서 원문 규정</summary>{catalog.rules.map(rule => <section key={rule.id}><h2>{rule.title}</h2><p>{rule.text}</p></section>)}</details>}
        </div>

      </aside>
      <section className="standard-preview" aria-label={view === "reference" ? "기준서 전체 실제 보기" : "문서 표준 실제 미리보기"}>
        <div className="standard-preview-controls">
          {view === "settings" && <button aria-pressed={compare} disabled={!comparison} onClick={() => { setCompare(x => !x); setZoom("page"); }}>변경 전·후 비교</button>}
          {!settingsOpen && <button className="standard-open-settings" onClick={() => setSettingsOpen(true)} aria-controls="standard-settings-panel" aria-expanded={false}><WorkspaceIcon name="settings" size={16} />설정 열기</button>}
          <div className="standard-preview-zoom" role="group" aria-label="미리보기 확대"><button aria-pressed={zoom === "page"} onClick={() => setZoom("page")}>한 쪽 보기</button><button aria-pressed={zoom === "fit"} onClick={() => setZoom("fit")}>폭 맞춤</button><button aria-pressed={zoom === "actual"} onClick={() => setZoom("actual")}>100%</button></div>
          {compare && <small className="standard-compare-note">변경한 글자·줄에 밑줄 · 왼쪽 설정과 같은 색 · {changedKeys.length}개 설정 변경</small>}<nav className="standard-page-nav" aria-label="미리보기 쪽 이동"><button aria-label="이전 쪽" disabled={!totalPages || shownPage === 0} onClick={() => setPageIndex(shownPage - 1)}>‹</button><select aria-label="미리보기 쪽" disabled={!totalPages} value={totalPages ? shownPage + 1 : ""} onChange={event => setPageIndex(Number(event.target.value) - 1)}>{totalPages ? Array.from({ length: totalPages }, (_, index) => <option key={index} value={index + 1}>{pageLabel(index)}</option>) : <option value="">준비 중</option>}</select><button aria-label="다음 쪽" disabled={!totalPages || shownPage + 1 >= totalPages} onClick={() => setPageIndex(shownPage + 1)}>›</button></nav>
        </div>
        <div ref={pagePane} className={`standard-preview-pages${compare && comparison && view === "settings" ? " is-comparing" : ""}`} aria-busy={phase !== "ready" || view === "settings" && !current}
          data-zoom={zoom}
          data-page-index={shownPage}
          data-page-count={totalPages}
          data-sample-id={view === "settings" ? preview?.sample_id : undefined}
          data-sample-sha256={view === "settings" ? preview?.sample_sha256 : undefined}
          data-preview-sha256={view === "reference" ? reference?.preview_sha256 : preview?.preview_sha256}
          data-settings-sha256={view === "settings" ? preview?.settings_sha256 : undefined}>
          {!displayedPages && <div className="standard-preview-empty">{stateText}</div>}
          {compare && comparison && view === "settings" && <div className="standard-before"><strong>변경 전 · {beforePage >= 0 ? `${beforePage + 1}쪽` : "대응 쪽 없음"}</strong>{comparison.pages[beforePage] ? <figure style={{ width: pageWidth }}><div className="standard-page-surface"><img src={comparison.pages[beforePage]} alt={`변경 전 ${beforePage + 1}쪽`} />{comparison.geometry[beforePage] && <svg className="standard-region-highlight comparison-highlight" aria-hidden="true" viewBox={`0 0 ${comparison.geometry[beforePage].width} ${comparison.geometry[beforePage].height}`}>{(comparisonMarks.before[beforePage] || []).map((rect, i) => <rect key={i} data-setting-key={rect.setting} data-target-role={rect.role} x={rect.x} y={rect.y + rect.height} width={rect.width} height={1.5} style={{ fill: rect.color }} />)}</svg>}</div></figure> : <p>추가된 쪽입니다. 다음 비교에서 같은 본문을 이어서 볼 수 있습니다.</p>}</div>}
          {!!displayedPages?.[afterPage] && <figure key={displayedPages[afterPage]} data-page-index={shownPage} style={{ width: pageWidth }}>{compare && view === "settings" && <strong>변경 후 · {afterPage >= 0 ? `${afterPage + 1}쪽` : "대응 쪽 없음"}</strong>}<div className="standard-page-surface" style={{ aspectRatio: shownSize ? `${shownSize.width} / ${shownSize.height}` : undefined }}><img src={displayedPages[afterPage]} alt={`${view === "reference" ? "기준서" : "문서 표준 예시"} ${afterPage + 1}쪽`} />{compare && current && preview?.geometry[afterPage] && <svg className="standard-region-highlight comparison-highlight" aria-hidden="true" viewBox={`0 0 ${preview.geometry[afterPage].width} ${preview.geometry[afterPage].height}`}>{(comparisonMarks.after[afterPage] || []).map((rect, i) => <rect key={i} data-setting-key={rect.setting} data-target-role={rect.role} x={rect.x} y={rect.y + rect.height} width={rect.width} height={1.5} style={{ fill: rect.color }} />)}</svg>}{!compare && current && preview?.geometry[afterPage] && impactRects[afterPage]?.length > 0 && <svg className="standard-region-highlight" aria-hidden="true" viewBox={`0 0 ${preview.geometry[afterPage].width} ${preview.geometry[afterPage].height}`} preserveAspectRatio="none">{impactRects[afterPage].map((rect, rectIndex) => <rect key={rectIndex} data-setting-key={activeField} data-target-role={rect.role} data-page-index={shownPage} x={rect.x} y={rect.y} width={rect.width} height={rect.height} />)}</svg>}</div></figure>}
          {comparing && afterPage < 0 && <div className="standard-before"><strong>변경 후 · 대응 쪽 없음</strong><p>현재 구성에서 빠진 쪽입니다. 본문은 같은 내용끼리 연결합니다.</p></div>}
        </div>
      </section>
    </div>
    {view === "settings" && impact && helpPosition && <aside id="standard-setting-tooltip" role="tooltip" className="standard-setting-tooltip" style={helpPosition} data-setting-key={activeField}><strong>{impact.title}</strong><p>{fields.find(field => field.key === activeField)?.help || impact.description}</p><small>{current && impactPages.length ? `예시 ${impactPages.join(", ")}쪽에 적용됩니다.` : "현재 예시에 없거나 아직 반영 중인 항목입니다."}</small></aside>}
    <div className="standard-renderer" ref={host} aria-hidden="true" inert />
    <dialog className="standard-register-dialog" ref={dialog} onCancel={event => { if (locked) event.preventDefault(); else setDialogOpen(false); }} aria-labelledby="standard-register-title">
      <h2 id="standard-register-title">표준 저장</h2><p>기본 별첨은 고정되어 있습니다. 사용자 표준을 덮어써도 이전 버전과 기존 문서는 보존합니다.</p>{sourceSelection && <fieldset disabled={locked}><legend>저장 방법</legend><label><input type="radio" name="standard-save-mode" checked={saveMode === "replace"} onChange={() => { setSaveMode("replace"); setName(sourceSelection.name); }} />현재 표준 덮어쓰기</label><label><input type="radio" name="standard-save-mode" checked={saveMode === "new"} onChange={() => { setSaveMode("new"); setName(sourceSelection.name + " 사본"); }} />새 표준으로 추가</label></fieldset>}
      <label htmlFor="standard-register-name">표준 이름</label><input id="standard-register-name" value={name} maxLength={120} disabled={locked} onChange={event => setName(event.target.value)} autoFocus placeholder="예: 부서 업무보고 표준" />
      {registration && !registering && <p role="status">입력과 요청 ID를 유지하고 있습니다. 같은 등록 결과를 재확인하세요.</p>}
      {registrationError && <p role="alert">{registrationError}</p>}
      <div className="standard-register-dialog-actions"><button disabled={locked} onClick={() => setDialogOpen(false)}>취소</button><button className="primary" disabled={registering || (!registration && (!current || !name.trim()))} onClick={() => void register()}>{registering ? "등록 확인 중…" : registration ? "같은 등록 결과 재확인" : expired ? "예시 다시 확인" : saveMode === "replace" && sourceSelection ? "현재 표준 덮어쓰기" : "새 표준 등록"}</button></div>
    </dialog>
    {deleteOpen && sourceSelection && <div className="standard-delete-backdrop"><section className="standard-delete-dialog" role="alertdialog" aria-label="사용자 표준 삭제"><h2>‘{sourceSelection.name}’ 삭제</h2><p>목록에서 제거합니다. 이 표준으로 만든 기존 문서와 이전 버전은 보존합니다.{changedSettings ? " 저장하지 않은 변경사항은 사라집니다." : ""}</p><button disabled={deleting} onClick={() => setDeleteOpen(false)}>취소</button><button disabled={deleting} onClick={async () => { setDeleting(true); try { await api(`/visual-standards/${sourceSelection.id}`, { method: "DELETE" }); const next = await api<Catalog>("/visual-standards"); setCatalog(next); setDeleteOpen(false); setSource(""); setSourceCandidate(""); setValues(inputValues(next.defaults)); setSaved(null); setInputRevision(x => x + 1); generation.current++; setPhase("waiting"); } catch (cause) { setError(String(cause)); } finally { setDeleting(false); } }}>삭제 확인</button></section></div>}
    <dialog className="standard-register-dialog" ref={changeDialog} role="alertdialog" aria-labelledby="standard-change-title" aria-describedby="standard-change-description" onCancel={() => setPendingChange(null)}>
      <h2 id="standard-change-title">{pendingChange?.type === "leave" ? "서식 설정을 닫을까요?" : "서식 설정 바꾸기"}</h2><p id="standard-change-description">현재 조절한 설정을 아직 등록하지 않았습니다. {pendingChange?.type === "leave" ? "화면을 나가면 이 설정은 사라집니다." : "계속하면 불러올 설정으로 바뀝니다."} 저장된 표준은 유지됩니다.</p>
      <div className="standard-register-dialog-actions"><button autoFocus onClick={() => setPendingChange(null)}>계속 편집</button><button disabled={locked} onClick={() => pendingChange && applyChange(pendingChange)}>{pendingChange?.type === "leave" ? "등록하지 않고 나가기" : "설정 바꾸기"}</button></div>
    </dialog>
  </main>;
}
