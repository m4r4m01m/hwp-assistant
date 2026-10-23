import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { fileUrl, type Document } from "./api";
import { downloadHancom2018 } from "./hancom-download";
import { WorkspaceIcon } from "./workspace-nav";
import "./saved-download.css";

/** Export only a saved, immutable version; never read or change the live editor. */
export function SavedDownload({ document, versionId = document.current_version, label = "다운로드", rail = false, icon = true }: {
  document: Document; versionId?: string; label?: string; rail?: boolean; icon?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null), toggle = useRef<HTMLButtonElement>(null), panel = useRef<HTMLDivElement>(null);
  const working = useRef(false), mounted = useRef(false);
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const id = useId();
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // Close this child before the enclosing history dialog handles Escape.
      event.preventDefault(); event.stopImmediatePropagation(); setOpen(false); toggle.current?.focus();
    };
    window.addEventListener("pointerdown", outside, true);
    window.addEventListener("keydown", escape, true);
    panel.current?.querySelector<HTMLElement>("a, button:not(:disabled)")?.focus();
    return () => { window.removeEventListener("pointerdown", outside, true); window.removeEventListener("keydown", escape, true); };
  }, [open]);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = toggle.current?.getBoundingClientRect(), popup = panel.current?.getBoundingClientRect();
      if (!anchor || !popup) return;
      const left = Math.max(8, Math.min(rail ? anchor.right + 8 : anchor.right - popup.width, window.innerWidth - popup.width - 8));
      const top = Math.max(8, Math.min(anchor.bottom + 6, window.innerHeight - popup.height - 8));
      setPosition(previous => previous.top === top && previous.left === left ? previous : { top, left });
    };
    place(); window.addEventListener("resize", place); window.addEventListener("scroll", place, true);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [open, busy, error, notice, rail]);
  async function convert() {
    if (working.current) return;
    working.current = true; setBusy(true); setError(""); setNotice("");
    // Capture both values before awaiting: a later server save cannot change this export.
    const source = { ...document }, version = versionId;
    try {
      await downloadHancom2018(source, version);
      if (mounted.current) setNotice("HWP 다운로드를 시작했습니다.");
    } catch (value) {
      if (mounted.current) setError(value instanceof Error ? value.message : String(value));
    } finally {
      working.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return <div ref={root} className={`saved-download${rail ? " saved-download-rail" : ""}`}>
    <a className={`button saved-download-primary${rail ? " editor-rail-action editor-download" : ""}`} href={fileUrl(document, versionId)} aria-label={label} title="서버 저장본 HWPX 다운로드">
      {icon && <WorkspaceIcon name="download" size={17}/>}<span>{label}</span>
    </a>
    <button ref={toggle} className="saved-download-toggle" type="button" title="다운로드 형식 선택" aria-label="다운로드 형식 선택" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(value => !value)}>
      <span aria-hidden="true">▾</span>
    </button>
    {open && <div ref={panel} id={id} className="saved-download-panel" role="group" aria-label="다운로드 형식" style={position}>
      <strong>저장본 다운로드</strong>
      <a className="button" href={fileUrl(document, versionId)}>HWPX 원본</a>
      <button type="button" disabled={busy} onClick={() => void convert()}>{busy ? "HWP 변환·검증 중…" : "한컴 2018용 HWP"}</button>
      <p>서버에 저장된 버전을 내려받습니다. 미저장 편집 내용은 포함되지 않습니다.</p>
      <p>HWP 변환은 원본을 보존합니다. 한컴 2018에서 열어 색상과 배치를 확인하세요.</p>
      {busy && <p role="status">선택한 저장본을 변환하고 내용을 확인하고 있습니다.</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p className="saved-download-error" role="alert">{error}</p>}
    </div>}
  </div>;
}
