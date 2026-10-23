// This cache belongs to one mounted template market. Never persist document
// previews or reuse an entry before a fresh authorized file GET and SHA check.
export type CachedPreview = { svgs: string[]; pageCount: number };
type Entry = CachedPreview & { engineSignature: string; expires: number; bytes: number };

export class PreviewCache {
  private entries = new Map<string, Entry>();
  private bytes = 0;

  constructor(
    private readonly maxEntries = 3,
    private readonly maxBytes = 8 * 1024 * 1024,
    private readonly ttlMs = 120000,
    private readonly now = () => Date.now(),
  ) {}

  private remove(key: string) {
    const entry = this.entries.get(key);
    if (entry) this.bytes -= entry.bytes;
    this.entries.delete(key);
  }

  private expire() {
    const now = this.now();
    for (const [key, entry] of this.entries) if (entry.expires <= now) this.remove(key);
  }

  has(key: string) {
    this.expire();
    return this.entries.has(key);
  }

  get(key: string, engineSignature: string): CachedPreview | null {
    this.expire();
    const entry = this.entries.get(key);
    if (!engineSignature || !entry || entry.engineSignature !== engineSignature) return null;
    this.entries.delete(key);
    this.entries.set(key, entry);
    return { svgs: [...entry.svgs], pageCount: entry.pageCount };
  }

  set(key: string, engineSignature: string, preview: CachedPreview) {
    this.expire();
    this.remove(key);
    if (!engineSignature || !preview.svgs.length || !Number.isSafeInteger(preview.pageCount) || preview.pageCount < preview.svgs.length) return;
    // Account for both UTF-16 string storage and UTF-8 Blob backing bytes.
    const bytes = preview.svgs.reduce((total, svg) => total + svg.length * 2 + new Blob([svg]).size, 0);
    if (bytes > this.maxBytes || this.maxEntries < 1) return;
    while (this.entries.size >= this.maxEntries || this.bytes + bytes > this.maxBytes) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) return;
      this.remove(oldest);
    }
    this.entries.set(key, { svgs: [...preview.svgs], pageCount: preview.pageCount, engineSignature, expires: this.now() + this.ttlMs, bytes });
    this.bytes += bytes;
  }

  clear() {
    this.entries.clear();
    this.bytes = 0;
  }
}
