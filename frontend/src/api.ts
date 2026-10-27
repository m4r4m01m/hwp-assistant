import { publicPath } from "./public-path";
let authContext: string | undefined;
export function pinAuthContext(value?: string) { authContext = value; }

export type Document = {
  id: string;
  name: string;
  current_version: string;
  created_at: number;
  role: "owner" | "editor" | "reader";
  can_trash?: boolean;
};
export type Version = {
  id: string;
  document_id: string;
  base_version: string | null;
  size: number;
  sha256: string;
  cause: string;
  created_at: number;
  author?: { user_id: string; identity_kind: "local" | "authenticated" } | null;
  restore_source?: { version_id: string; created_at: number; author?: Version["author"] } | null;
};
export class ApiError extends Error {
  constructor(readonly status: number, message: string, readonly retryAfterMs?: number) {
    super(message);
    this.name = "ApiError";
  }
}
export class RequestTimeoutError extends Error {
  constructor(readonly write: boolean) {
    super(write
      ? "서버의 처리 결과를 아직 확인하지 못했습니다. 목록이나 저장 상태를 확인한 뒤 보관한 요청으로 다시 확인하세요."
      : "서버 응답이 늦어 요청을 중단했습니다. 연결을 확인하고 다시 시도하세요.");
    this.name = "RequestTimeoutError";
  }
}
export type ApiRequestInit = RequestInit & { timeoutMs?: number };
export const isAbortError = (error: unknown) => error instanceof Error && error.name === "AbortError";
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);
function responseMessage(status: number, detail: unknown) {
  if (status >= 500) return "서버에서 요청을 처리하지 못했습니다. 잠시 뒤 다시 시도하세요.";
  const message = typeof detail === "string" && detail.trim() ? detail : ({
    401: "로그인이 필요합니다. 다시 로그인해 주세요.",
    403: "이 작업을 수행할 권한이 없습니다.",
    404: "요청한 항목을 찾지 못했습니다.",
    409: "문서 상태가 변경되었습니다. 현재 상태를 확인해 주세요.",
    429: "요청이 많습니다. 잠시 뒤 다시 시도하세요.",
  } as Record<number, string>)[status] || "요청을 처리하지 못했습니다. 입력과 연결 상태를 확인하세요.";
  return `${status === 409 ? "충돌: " : ""}${message}`;
}
async function readRequest<T>(url: string, init: ApiRequestInit, read: (response: Response) => Promise<T>): Promise<T> {
  const { timeoutMs, signal: caller, ...options } = init;
  const write = !["GET", "HEAD"].includes((options.method || "GET").toUpperCase());
  const timeout = timeoutMs ?? (write ? 180000 : 20000);
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  if (caller?.aborted) controller.abort();
  else caller?.addEventListener("abort", cancel, { once: true });
  const timer = timeout > 0 ? setTimeout(() => { timedOut = true; controller.abort(); }, timeout) : undefined;
  try {
    const headers = new Headers(options.headers);
    if (authContext && !url.includes("/auth/")) headers.set("X-Auth-Context", authContext);
    const response = await fetch(url, { ...options, headers, signal: controller.signal });
    if (!response.ok) {
      const value = await response.json().catch(cause => { if (controller.signal.aborted) throw cause; return null; });
      const retryAfter = response.headers.get("Retry-After");
      const retryAfterMs = retryAfter ? Math.max(0, /^\d+$/.test(retryAfter) ? Number(retryAfter) * 1000 : Date.parse(retryAfter) - Date.now()) : undefined;
      throw new ApiError(response.status, responseMessage(response.status, value?.detail), Number.isFinite(retryAfterMs) ? retryAfterMs : undefined);
    }
    return await read(response);
  } catch (cause) {
    if (caller?.aborted) throw new DOMException("요청을 취소했습니다.", "AbortError");
    if (timedOut) throw new RequestTimeoutError(write);
    if (cause instanceof TypeError) throw new Error(write
      ? "서버 연결이 끊겨 처리 결과를 확인하지 못했습니다. 목록이나 저장 상태를 확인한 뒤 보관한 요청으로 다시 확인하세요."
      : "서버에 연결하지 못했습니다. 네트워크 연결을 확인하고 다시 시도하세요.");
    throw cause;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    caller?.removeEventListener("abort", cancel);
  }
}
export function api<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  return readRequest(publicPath("api" + path), init, response => response.status === 204 ? Promise.resolve(undefined as T) : response.json());
}
export function downloadFile(url: string, init: ApiRequestInit = {}): Promise<ArrayBuffer> {
  return readRequest(url, { timeoutMs: 120000, ...init }, response => response.arrayBuffer());
}
export function jsonRequest(method: string, value: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  };
}

export async function deltaBody(before: Uint8Array, after: Uint8Array) {
  let prefix = 0,
    suffix = 0;
  while (
    prefix < before.length &&
    prefix < after.length &&
    before[prefix] === after[prefix]
  )
    prefix++;
  while (
    suffix < before.length - prefix &&
    suffix < after.length - prefix &&
    before[before.length - suffix - 1] === after[after.length - suffix - 1]
  )
    suffix++;
  const hash = async (bytes: Uint8Array) =>
    Array.from(
      new Uint8Array(
        await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)),
      ),
    )
      .map((n) => n.toString(16).padStart(2, "0"))
      .join("");
  const inserted = after.subarray(prefix, after.length - suffix);
  const parts: string[] = [];
  for (let i = 0; i < inserted.length; i += 8192)
    parts.push(String.fromCharCode(...inserted.subarray(i, i + 8192)));
  return JSON.stringify({
    base_sha256: await hash(before),
    prefix,
    remove: before.length - prefix - suffix,
    removed_sha256: await hash(before.subarray(prefix, before.length - suffix)),
    inserted_base64: btoa(parts.join("")),
    result_sha256: await hash(after),
  });
}
export const fileUrl = (d: Document, v = d.current_version) =>
  publicPath(`api/documents/${d.id}/versions/${v}/file`);
export async function upload(
  bytes: Uint8Array,
  name: string,
  id: string = crypto.randomUUID(),
  key: string = crypto.randomUUID(),
) {
  return api<Document>(
    `/documents?name=${encodeURIComponent(name)}&document_id=${id}`,
    {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: new Blob([new Uint8Array(bytes)]),
    },
  );
}
export function downloadBytes(bytes: Uint8Array, name: string) {
  const url = URL.createObjectURL(
    new Blob([new Uint8Array(bytes)], { type: "application/hwp+zip" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
