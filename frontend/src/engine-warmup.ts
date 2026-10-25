import { publicPath } from "./public-path";

export type WarmState = { phase: "idle" | "loading" | "ready" | "deferred" | "failed"; completed: number; total: number };
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

async function consume(response: Response, limit: number, signal: AbortSignal) {
  if (!response.ok || response.redirected || response.headers.get("content-type")?.includes("text/html")) throw new Error("Engine asset unavailable");
  const length = Number(response.headers.get("content-length") || 0);
  if (!Number.isFinite(length) || length > limit) throw new Error("Engine asset size limit");
  if (!response.body) throw new Error("No asset body");
  const reader = response.body.getReader();
  let bytes = 0;
  try {
    for (;;) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      const chunk = await reader.read();
      if (chunk.done) return bytes;
      bytes += chunk.value.byteLength;
      if (bytes > limit) throw new Error("Engine asset size limit");
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

/** Download only public, pinned engine bytes. Never mount an editor, execute
 * its JavaScript, load a document, or keep an account's data in CacheStorage.
 * HTTP cache is shared with the iframe's exact URLs, unlike a second WASM copy.
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
  publish({ phase: "loading", completed: 0, total: 0 });
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
      publish({ phase: "loading", completed: 0, total: assets.length });
      // Two bounded streams keep authentication and the page responsive. No
      // arrayBuffer retained; SRI checks the bytes against the current receipt.
      let next = 0, totalBytes = 0, completed = 0;
      const worker = async () => {
        while (next < assets.length) {
          const asset = assets[next++];
          const result = await fetch(asset.path, { credentials: "same-origin", redirect: "error",
            cache: "default", integrity: asset.integrity, signal: controller.signal,
            ...({ priority: "low" } as RequestInit) });
          totalBytes += await consume(result, maximumBytes, controller.signal);
          if (totalBytes > 64 * 1024 * 1024) throw new Error("Warmup size limit");
          completed++;
          publish({ phase: "loading", completed, total: assets.length });
        }
      };
      const results = await Promise.allSettled([worker(), worker()]);
      if (results.some(result => result.status === "rejected")) throw new Error("Incomplete warmup");
      publish({ phase: "ready", completed: assets.length, total: assets.length });
    } catch {
      // Optional work never blocks login or changes normal editor startup.
      publish({ phase: "failed", completed: state.completed, total: state.total });
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
