export type Document = {
  id: string;
  name: string;
  current_version: string;
  created_at: number;
  role: "owner" | "editor" | "reader";
};
export type Version = {
  id: string;
  document_id: string;
  base_version: string | null;
  size: number;
  sha256: string;
  cause: string;
  created_at: number;
};
export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch("/api" + path, init);
  if (!response.ok) {
    const value = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status,
      `${response.status === 409 ? "충돌: " : ""}${typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail)}`,
    );
  }
  return response.json();
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
  `/api/documents/${d.id}/versions/${v}/file`;
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
