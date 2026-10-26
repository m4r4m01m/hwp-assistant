import { useState, useSyncExternalStore, type ReactNode } from "react";
import { useAiConnection } from "./ai-connection";
import "./workspace-nav.css";

export type WorkspaceSection = "documents" | "reports" | "templates" | "standards";
export type WorkspaceIconName = WorkspaceSection | "back" | "plus" | "upload" | "search" | "refresh" | "download" | "history" | "copy" | "rename" | "share" | "settings" | "close" | "save" | "document" | "file" | "block" | "toc" | "conversation" | "review" | "help" | "undo" | "more" | "trash" | "page" | "font" | "heading" | "paragraph" | "table" | "note" | "layout" | "star" | "sidebar-expanded" | "sidebar-icons" | "sidebar-hidden";

const paths: Record<WorkspaceIconName, ReactNode> = {
  documents: <><path d="M5 3h9l5 5v13H5z"/><path d="M14 3v5h5M8 12h8M8 16h6"/></>,
  reports: <><path d="M5 3h14v18H5zM8 16v-3m4 3V9m4 7v-5M8 6h8"/></>,
  templates: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
  standards: <><path d="M4 7h16M4 17h16M8 4v6m8 4v6"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></>,
  back: <path d="m10 5-7 7 7 7M3 12h18"/>,
  plus: <path d="M12 4v16M4 12h16"/>,
  upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/></>,
  search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></>,
  refresh: <><path d="M20 10a8 8 0 1 0-1 8M20 3v7h-7"/></>,
  download: <path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>,
  history: <><path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v6l4 2"/></>,
  copy: <><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/></>,
  rename: <><path d="m4 16 12-12 4 4L8 20l-5 1zM13 7l4 4"/></>,
  share: <><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m9 10 6-4M9 14l6 4"/></>,
  settings: <><path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="7" cy="18" r="2"/></>,
  close: <path d="m6 6 12 12M18 6 6 18"/>,
  save: <><path d="M4 3h13l4 4v14H3V3zM7 3v6h9V3M7 21v-8h10v8"/></>,
  document: <><path d="M5 3h9l5 5v13H5z"/><path d="M14 3v5h5M8 12h8M8 16h6"/></>,
  file: <path d="M3 6h7l2 2h9v12H3zM3 6V4h7l2 2h7v2"/>,
  block: <path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM17.5 14v7M14 17.5h7"/>,
  toc: <path d="M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01"/>,
  conversation: <path d="M4 4h16v12H9l-5 4zM8 8h8M8 12h5"/>,
  review: <path d="M4 3h12v8M4 3v18h8M8 7h4M8 11h3M15 15l2 2 4-5"/>,
  help: <path d="M9.1 8a3 3 0 0 1 5.8 1c0 2-3 2-3 4M12 17h.01M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20"/>,
  undo: <path d="M9 4 4 9l5 5M4 9h10a6 6 0 0 1 0 12h-3"/>,
  trash: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></>,
  page: <><rect x="5" y="2" width="14" height="20" rx="1"/><path d="M8 6h8v12H8z" strokeDasharray="2 2"/></>,
  font: <path d="m3 20 6-16 6 16M5 15h8M16 11h6m-3 0v9"/>,
  heading: <path d="M4 4v16M14 4v16M4 12h10M18 12h2v8m-2 0h4"/>,
  paragraph: <path d="M12 4v16M18 4v16M21 4H8a5 5 0 0 0 0 10h4"/>,
  table: <><rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 9h18M3 14h18M9 4v16M15 4v16"/></>,
  note: <><path d="M5 3h14v18H5zM8 16h8M8 19h5M8 11h3"/><path d="M15 6v6m-3-4.5 6 3m-6 0 6-3"/></>,
  layout: <><rect x="3" y="3" width="18" height="18" rx="1"/><path d="M3 8h18M9 8v13M12 12h6M12 16h6"/></>,
  star: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z"/>,
  more: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
  "sidebar-expanded": <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M10 4v16M6 8h1M6 12h1M6 16h1M13 8h5M13 12h5"/></>,
  "sidebar-icons": <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4v16M5.5 8h.01M5.5 12h.01M5.5 16h.01"/></>,
  "sidebar-hidden": <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="m11 9-3 3 3 3M8 12h8"/></>,
};

export function WorkspaceIcon({ name, size = 20 }: { name: WorkspaceIconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

const sections: { title: string; items: { id: WorkspaceSection; label: string }[] }[] = [
  { title: "문서 작업", items: [{ id: "documents", label: "문서함" }, { id: "reports", label: "보고서 작성" }] },
  { title: "서식 관리", items: [{ id: "standards", label: "문서 표준 편집" }, { id: "templates", label: "양식 보관함" }] },
];

type SidebarMode = "expanded" | "icons" | "hidden";
const modeKey = "hwp-workspace-sidebar-mode";
let sessionMode: SidebarMode | undefined;
const modeListeners = new Set<() => void>();
function readMode(): SidebarMode {
  if (sessionMode) return sessionMode;
  try {
    const value = localStorage.getItem(modeKey);
    if (value === "expanded" || value === "icons" || value === "hidden") return sessionMode = value;
  } catch { /* The choice still persists between screens when storage is unavailable. */ }
  return sessionMode = typeof window !== "undefined" && window.innerWidth <= 860 ? "icons" : "expanded";
}
function subscribeMode(listener: () => void) {
  modeListeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === modeKey || event.key === null) { sessionMode = undefined; listener(); }
  };
  window.addEventListener("storage", onStorage);
  return () => { modeListeners.delete(listener); window.removeEventListener("storage", onStorage); };
}
function setMode(mode: SidebarMode) {
  sessionMode = mode;
  try { localStorage.setItem(modeKey, mode); } catch { /* Session preference remains usable. */ }
  modeListeners.forEach(listener => listener());
}

export function WorkspaceNav({ active, onNavigate, onBack, backLabel = "← 문서 목록", children, footer, disabled = false, className = "", label = "작업 공간" }: {
  active: WorkspaceSection;
  /** Retained for old callers; a screen must never override the user's sidebar choice. */
  compact?: boolean;
  onNavigate?: (section: WorkspaceSection) => void;
  onBack?: () => void;
  backLabel?: string;
  children?: ReactNode;
  footer?: ReactNode;
  disabled?: boolean;
  className?: string;
  label?: string;
}) {
  const mode = useSyncExternalStore(subscribeMode, readMode, () => "expanded" as const);
  const aiConnection = useAiConnection();
  const [edgeY, setEdgeY] = useState(() => window.innerHeight / 2);
  const hidden = mode === "hidden";
  const chooseMode = setMode;
  return <>
    {hidden && <div className="workspace-nav-edge-zone" onPointerEnter={event => setEdgeY(Math.max(32, Math.min(event.clientY, window.innerHeight - 32)))} onPointerMove={event => { if (event.target === event.currentTarget) setEdgeY(Math.max(32, Math.min(event.clientY, window.innerHeight - 32))); }}>
      <button className="workspace-nav-edge" style={{ top: edgeY }} aria-label="사이드바 열기" aria-expanded={false} title="클릭하여 사이드바 펼치기" onClick={() => chooseMode("expanded")}><WorkspaceIcon name="sidebar-expanded"/></button>
    </div>}
    <nav className={`workspace-nav ${className}`} data-mode={mode} data-hidden={hidden || undefined} aria-label={label}>
    <div className="workspace-brand" title="HWP Assistant"><span className="workspace-brand-mark" aria-hidden="true">H</span><span className="workspace-brand-name">HWP Assistant<small>문서 작업 공간</small></span></div>
    <div className="workspace-nav-mode" role="group" aria-label="사이드바 표시">
      <button aria-label="사이드바 펼침" title="아이콘과 메뉴 이름 표시" aria-pressed={mode === "expanded"} onClick={() => chooseMode("expanded")}><WorkspaceIcon name="sidebar-expanded" size={18}/><span>펼침</span></button>
      <button aria-label="사이드바 아이콘만" title="아이콘만 표시" aria-pressed={mode === "icons"} onClick={() => chooseMode("icons")}><WorkspaceIcon name="sidebar-icons" size={18}/><span>아이콘</span></button>
      <button aria-label="사이드바 숨기기" title="사이드바 숨기기" aria-pressed={false} onClick={() => chooseMode("hidden")}><WorkspaceIcon name="sidebar-hidden" size={18}/><span>숨김</span></button>
    </div>
    {onBack && !onNavigate && <button className="workspace-nav-back" aria-label={backLabel} title={backLabel} disabled={disabled} onClick={onBack}><WorkspaceIcon name="back"/><span>문서 목록으로</span></button>}
    {onNavigate && <div className="workspace-nav-routes" role="group" aria-label="화면 이동">{sections.map(group => <div className="workspace-nav-section" key={group.title} role="group" aria-label={group.title}><p className="workspace-nav-caption">{group.title}</p>{group.items.map(section => <button key={section.id} className="workspace-nav-route" aria-label={section.label} title={section.label} aria-current={section.id === active ? "page" : undefined} disabled={disabled} onClick={() => { if (section.id !== active) onNavigate(section.id); }}><WorkspaceIcon name={section.id}/><span>{section.label}</span></button>)}</div>)}</div>}
    {children && <div className="workspace-nav-tools" role="group" aria-label="현재 화면 도구"><p className="workspace-nav-caption">현재 화면 도구</p>{children}</div>}
    {aiConnection.available && <div className="workspace-nav-connection"><button className="workspace-nav-route" type="button" aria-label="AI 연결" title="AI 연결 설정" onClick={aiConnection.open}><WorkspaceIcon name="settings"/><span>AI 연결</span></button></div>}
    {footer && <div className="workspace-nav-footer">{footer}</div>}
  </nav></>;
}
