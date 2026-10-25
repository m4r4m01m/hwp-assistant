import { publicPath } from "./public-path";

export type WarmState = { phase: "idle" | "loading" | "ready" | "deferred" | "failed"; stage?: "download" | "compile"; completed: number; total: number };
type Asset = { path: string; integrity: string };
const maximumBytes = 32 * 1024 * 1024;
let state: WarmState = { phase: "idle", completed: 0, total: 0 };
const listeners = new Set<() => void>();
let pending: Promise<void> | null = null;
let attempts = 0;

export function warmState() { return state; }
export function subscribeWarmup(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
function publish(next: WarmState) { state = next; listeners.forEach(listener => listener()); }

export function warmAssets(value: unknown): Asset[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid engine manifest");
  const manifest = value as { publicBase?: unknown; bundle?: unknown };
  if (manifest.publicBase !== publicPath("rhwp/") || !manifest.bundle || typeof manifest.bundle !== "object" || Array.isArray(manifest.bundle))
    throw new Error("Engine base or manifest mismatch");
  const assets = Object.entries(manifest.bundle).filter(([path]) =>
    /^assets\/[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8,}\.(wasm|js|css)$/.test(path));
  if (assets.length > 24 || assets.filter(([path]) => path.endsWith(".wasm")).length !== 1)
    throw new Error("Unexpected engine asset count");
  return assets.sort(([a], [b]) => Number(b.endsWith(".wasm")) - Number(a.endsWith(".wasm"))).map(([path, hash]) => {
    if (typeof hash !== "string" || !/^[a-f0-9]{64}$/.test(hash)) throw new Error("Invalid engine hash");
    const digest = btoa(String.fromCharCode(...hash.match(/../g)!.map(byte => parseInt(byte, 16))));
    return { path: publicPath(`rhwp/${path}`), integrity: `sha256-${digest}` };
  });
}

export function deferWarmup(connection?: { saveData?: boolean; effectiveType?: string }, online = true, visible = true) {
  return !online || !visible || !!connection?.saveData || ["slow-2g", "2g"].includes(connection?.effectiveType || "");
}

async function consume(response: Response, limit: number, signal: AbortSignal, claim: (bytes: number) => void, retain = false) {
  if (!response.ok || response.redirected || response.headers.get("content-type")?.includes("text/html")) throw new Error("Engine asset unavailable");
  const length = Number(response.headers.get("content-length") || 0);
  if (!Number.isFinite(length) || length < 0 || length > limit) throw new Error("Engine asset size limit");
  if (!response.body) throw new Error("No asset body");
  const reader = response.body.getReader();
  let bytes = 0;
  const chunks: Uint8Array[] = [];
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", cancel, { once: true });
  try {
    for (;;) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      const chunk = await reader.read();
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      if (chunk.done) break;
      const nextBytes = bytes + chunk.value.byteLength;
      if (nextBytes > limit) throw new Error("Engine asset size limit");
      // Claim each chunk synchronously across both workers. A read-modify-write
      // around an await would overwrite the other worker's shared byte count.
      claim(chunk.value.byteLength);
      bytes = nextBytes;
      if (retain) chunks.push(chunk.value);
    }
    if (!retain) return null;
    const contents = new Uint8Array(bytes);
    let offset = 0;
    for (const chunk of chunks) { contents.set(chunk, offset); offset += chunk.byteLength; }
    return contents;
  } finally { signal.removeEventListener("abort", cancel); await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

async function compile(bytes: Uint8Array<ArrayBuffer>, signal: AbortSignal) {
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  let cancel = () => {};
  const stopped = new Promise<never>((_resolve, reject) => {
    cancel = () => reject(new DOMException("Aborted", "AbortError"));
    signal.addEventListener("abort", cancel, { once: true });
  });
  try {
    // Compilation does not instantiate the module or run its imports. Native
    // compilation cannot be canceled, so bound our wait and ignore late results.
    await Promise.race([WebAssembly.compile(bytes), stopped]);
  } finally { signal.removeEventListener("abort", cancel); }
}

/** Download and compile only public, pinned engine bytes. Never mount an editor,
 * execute its JavaScript, instantiate WASM, load a document, or keep account data
 * in CacheStorage. HTTP cache uses the iframe's exact URLs. Native compiled-code
 * cache reuse across iframe startup depends on the browser; it is not guaranteed.
 */
export function startEngineWarmup(force = false): Promise<void> {
  if (pending) return pending;
  if (state.phase === "ready" || !force && attempts >= 2) return Promise.resolve();
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
  if (!force && deferWarmup(connection, navigator.onLine, document.visibilityState === "visible")) {
    publish({ phase: "deferred", completed: 0, total: 0 });
    return Promise.resolve();
  }
  attempts++;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45_000);
  publish({ phase: "loading", stage: "download", completed: 0, total: 0 });
  pending = (async () => {
    try {
      const response = await fetch(publicPath("rhwp/build-info.json"), {
        credentials: "same-origin", redirect: "error", cache: "no-cache", signal: controller.signal,
      });
      if (!response.ok || response.redirected) throw new Error("Manifest unavailable");
      // Bound the tiny manifest before JSON parsing (including missing length).
      const reader = response.body?.getReader();
      if (!reader) throw new Error("Manifest unavailable");
      let raw = "", bytes = 0; const decoder = new TextDecoder("utf-8", { fatal: true });
      try {
        for (;;) {
          const part = await reader.read();
          if (part.done) break;
          bytes += part.value.byteLength;
          if (bytes > 128 * 1024) throw new Error("Manifest too large");
          raw += decoder.decode(part.value, { stream: true });
        }
        raw += decoder.decode();
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      const assets = warmAssets(JSON.parse(raw));
      publish({ phase: "loading", stage: "download", completed: 0, total: assets.length });
      // Two bounded streams keep authentication responsive. Only the bounded
      // WASM bytes are retained for compilation after SRI verification.
      let next = 0, totalBytes = 0, completed = 0;
      let wasmBytes: Uint8Array<ArrayBuffer> | null = null;
      const claim = (bytes: number) => {
        totalBytes += bytes;
        if (totalBytes > 64 * 1024 * 1024) {
          controller.abort();
          throw new Error("Warmup size limit");
        }
      };
      const worker = async () => {
        try {
          while (next < assets.length) {
            if (controller.signal.aborted) throw new DOMException("Aborted", "AbortError");
            const asset = assets[next++];
            const result = await fetch(asset.path, { credentials: "same-origin", redirect: "error",
              cache: "default", integrity: asset.integrity, signal: controller.signal,
              ...({ priority: "low" } as RequestInit) });
            const bytes = await consume(result, maximumBytes, controller.signal, claim, asset.path.endsWith(".wasm"));
            if (bytes) wasmBytes = bytes;
            completed++;
            publish({ phase: "loading", stage: "download", completed, total: assets.length });
          }
        } catch (cause) { controller.abort(); throw cause; }
      };
      const results = await Promise.allSettled([worker(), worker()]);
      if (results.some(result => result.status === "rejected")) throw new Error("Incomplete warmup");
      if (!wasmBytes) throw new Error("WASM asset unavailable");
      publish({ phase: "loading", stage: "compile", completed, total: assets.length });
      await compile(wasmBytes, controller.signal);
      publish({ phase: "ready", completed: assets.length, total: assets.length });
    } catch {
      // Optional work never blocks login or changes normal editor startup.
      publish({ phase: "failed", stage: state.stage, completed: state.completed, total: state.total });
    } finally { clearTimeout(timeout); controller.abort(); pending = null; }
  })();
  return pending;
}

export function scheduleEngineWarmup() {
  // Paint the login UI first; no requestIdleCallback dependency on Safari.
  const timer = setTimeout(() => { void startEngineWarmup(); }, 100);
  const retry = () => { if (document.visibilityState === "visible") void startEngineWarmup(); };
  window.addEventListener("online", retry);
  document.addEventListener("visibilitychange", retry);
  return () => { clearTimeout(timer); window.removeEventListener("online", retry); document.removeEventListener("visibilitychange", retry); };
}
