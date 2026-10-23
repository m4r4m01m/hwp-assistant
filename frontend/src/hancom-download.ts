import type { HwpDocument } from "@rhwp/core";
import { api, fileUrl, type Document, type Version } from "./api";
import { publicPath } from "./public-path";

const MAX_BYTES = 30 * 1024 * 1024;
const sha256 = async (bytes: Uint8Array) => Array.from(new Uint8Array(
  await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)),
), byte => byte.toString(16).padStart(2, "0")).join("");
let loading: Promise<typeof import("@rhwp/core")> | undefined;

// Uses the same pinned binding/WASM as the comparison viewer, in a separate
// document instance. It never exports or changes the active editor's buffer.
async function engine() {
  if (!loading) loading = (async () => {
    const response = await fetch(publicPath("comparison-engine.json"));
    if (!response.ok) throw Error("문서 변환 엔진을 불러오지 못했습니다.");
    const manifest = await response.json();
    const prefix = publicPath("rhwp/assets/");
    if (typeof manifest.wasm !== "string" || !manifest.wasm.startsWith(prefix)
      || !/^rhwp_bg-[\w-]+\.wasm$/.test(manifest.wasm.slice(prefix.length))
      || !/^[a-f0-9]{64}$/.test(manifest.wasm_sha256)
      || !/^[a-f0-9]{64}$/.test(manifest.binding_sha256))
      throw Error("문서 변환 엔진의 검증 정보가 올바르지 않습니다.");
    const wasmResponse = await fetch(manifest.wasm);
    if (!wasmResponse.ok) throw Error("문서 변환 엔진을 불러오지 못했습니다.");
    const wasm = new Uint8Array(await wasmResponse.arrayBuffer());
    if (await sha256(wasm) !== manifest.wasm_sha256)
      throw Error("문서 변환 엔진의 파일 검증에 실패했습니다.");
    const url = publicPath("comparison-core.js");
    const bindingResponse = await fetch(url);
    if (!bindingResponse.ok || await sha256(new Uint8Array(await bindingResponse.arrayBuffer())) !== manifest.binding_sha256)
      throw Error("문서 변환 엔진의 연결 코드 검증에 실패했습니다.");
    const core: typeof import("@rhwp/core") = await import(/* @vite-ignore */ url);
    await core.default({ module_or_path: wasm });
    return core;
  })().catch(error => { loading = undefined; throw error; });
  return loading;
}

export async function downloadHancom2018(document: Document, versionId = document.current_version) {
  const versions = await api<Version[]>(`/documents/${document.id}/versions`);
  const version = versions.find(item => item.id === versionId && item.document_id === document.id);
  if (!version || version.size > MAX_BYTES) throw Error("다운로드할 저장본을 확인할 수 없습니다.");
  const response = await fetch(fileUrl(document, versionId));
  if (!response.ok) throw Error("저장본을 불러오지 못했습니다.");
  const source = new Uint8Array(await response.arrayBuffer());
  if (source.length !== version.size || source.length > MAX_BYTES || await sha256(source) !== version.sha256)
    throw Error("저장본의 크기 또는 해시가 일치하지 않습니다.");
  const core = await engine();
  let original: HwpDocument | undefined, reopened: HwpDocument | undefined;
  let output: ReturnType<HwpDocument["exportHwpWithReport"]> | undefined;
  try {
    original = new core.HwpDocument(source);
    const pages = original.pageCount();
    if (pages < 1 || pages > 500) throw Error("HWP 변환은 500쪽까지 지원합니다.");
    const text = original.getTextFileText();
    output = original.exportHwpWithReport();
    const loss = JSON.parse(output.contentLoss());
    if (loss.schemaVersion !== 1 || loss.outputFormat !== "hwp" || loss.count !== 0
      || !Array.isArray(loss.losses) || loss.losses.length)
      throw Error("HWP 변환 중 내용 손실이 감지되었습니다. HWPX 원본으로 다운로드하세요.");
    const bytes = output.takeBytes();
    if (bytes.length > MAX_BYTES || ![0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1].every((value, index) => bytes[index] === value))
      throw Error("변환된 HWP 파일을 검증하지 못했습니다.");
    reopened = new core.HwpDocument(bytes);
    if (reopened.pageCount() !== pages || reopened.getTextFileText() !== text)
      throw Error("HWP 변환 후 본문 또는 쪽수가 달라졌습니다. HWPX 원본으로 다운로드하세요.");
    const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: "application/x-hwp" }));
    const link = window.document.createElement("a");
    link.href = url;
    link.download = document.name.replace(/\.(?:hwpx|hwp)$/i, "") + ".hwp";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } finally {
    reopened?.free(); output?.free(); original?.free();
  }
}
