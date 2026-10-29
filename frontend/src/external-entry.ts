/** Initial navigation only. URL events never replace an open editing buffer. */
export type ExternalEntry =
  | { kind: "document"; documentId: string; versionId?: string }
  | { kind: "report"; taskId: string }
  | { kind: "mattermost"; token: string }
  | { kind: "error"; message: string };

const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const invalid = (): ExternalEntry => ({ kind: "error", message: "올바르지 않은 문서함 링크입니다. 원래 안내에서 링크를 다시 열어 주세요." });

export function parseExternalEntry(url: URL): ExternalEntry | null {
  const fields = ["document", "version", "report"];
  const fragment = new URLSearchParams(url.hash.slice(1));
  const linking = fragment.has("mattermost-link");
  if (fields.some(key => url.searchParams.getAll(key).length > 1) || fragment.getAll("mattermost-link").length > 1) return invalid();
  const document = url.searchParams.get("document"), version = url.searchParams.get("version"), report = url.searchParams.get("report");
  if (linking) {
    const token = fragment.get("mattermost-link") || "";
    if (fields.some(key => url.searchParams.has(key)) || !/^[A-Za-z0-9_-]{32,256}$/.test(token)) return invalid();
    return { kind: "mattermost", token };
  }
  if (url.searchParams.has("document")) {
    if (!document || !uuid.test(document) || report !== null || (version !== null && !uuid.test(version))) return invalid();
    return { kind: "document", documentId: document.toLowerCase(), ...(version ? { versionId: version.toLowerCase() } : {}) };
  }
  if (url.searchParams.has("report")) {
    if (!report || !uuid.test(report) || version !== null) return invalid();
    return { kind: "report", taskId: report.toLowerCase() };
  }
  return version !== null ? invalid() : null;
}

export function captureExternalEntry(browser: Pick<Window, "location" | "history">): ExternalEntry | null {
  const url = new URL(browser.location.href);
  const entry = parseExternalEntry(url);
  const fragment = new URLSearchParams(url.hash.slice(1));
  if (fragment.has("mattermost-link")) {
    // Even invalid or ambiguous link tokens are removed before login renders.
    fragment.delete("mattermost-link");
    url.hash = fragment.toString();
    browser.history.replaceState(browser.history.state, "", url.pathname + url.search + url.hash);
  }
  return entry;
}

export function clearExternalEntryUrl() {
  const url = new URL(window.location.href);
  for (const key of ["document", "version", "report"]) url.searchParams.delete(key);
  window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
}
