import { test, expect } from "@playwright/test";

// Mock HTTP and engine exercise the real save/retry/recovery lifecycle. No model,
// user document, native browser session or persistent server save is involved.
for (const mode of ["clean", "retry", "later-edit"] as const) test(`MOCK report save transition: ${mode}`, async ({ page }) => {
  const id = "00000000-0000-4000-8000-000000000001";
  const doc = { id: "saved-document", name: "합성 보고서.hwpx", current_version: "saved-version", created_at: 1, role: "owner" };
  const job = { id, input: { prompt: "합성 검증 보고서", research: false }, status: "ready", draft_sha256: "a".repeat(64),
    draft: { title: "합성 검증 보고서", summary: "검증 요약", sections: [{ heading: "확인 사항", paragraphs: ["□ 합성 본문"], tables: [], diagrams: [] }], sources: [], limitations: [], emphasis: [] },
    approval: null, created_at: 1, execution: null };
  const preview = { id: "frozen-preview", name: doc.name, layout_base64: "AQID", layout_sha256: "b".repeat(64), scaffold_base64: "AQID", plan: [] };
  const saves: unknown[] = [];
  let release!: () => void;
  const responseGate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/report-save-test.html", route => route.fulfill({ contentType: "text/html", body: '<html><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/report-save-transition.tsx"></script></body></html>' }));
  await page.route("**/src/editor.ts*", route => route.fulfill({ contentType: "text/javascript", body: `
    export class EditorAdapter {
      static async mount(host) { return new EditorAdapter(host); }
      constructor(host) {
        this.rev = 0; this.listeners = new Set();
        this.sdk = { loadFile: async () => {}, pageCount: async () => 1, element: host, destroy: () => {} };
        this.bridge = { subscribe: fn => { this.listeners.add(fn); return () => this.listeners.delete(fn); } };
        window.__mutatePreview = () => { this.rev++; this.listeners.forEach(fn => fn()); };
      }
      exportHwpx() { return { bytes: new Uint8Array([1, 2, 3, this.rev]), contentLoss: {count: 0, losses: []}, revision: this.rev, epoch: 1 }; }
      destroy() {}
    }
  ` }));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    const respond = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (request.method() === "POST") {
      if (path !== `/report-tasks/${id}/preview/save`) return respond({ detail: "Unexpected mutation" }, 409);
      saves.push(request.postDataJSON());
      if (mode === "retry" && saves.length === 1) return route.abort("failed");
      if (mode === "later-edit") await responseGate;
      return respond(doc);
    }
    if (path === "/report-tasks") return respond([job]);
    if (path === `/report-tasks/${id}`) return respond(job);
    if (path === `/report-tasks/${id}/preview`) return respond(preview);
    if (path === "/report-templates") return respond({ items: [], default_id: "standard-business" });
    if (path === "/visual-standards") return respond({ items: [] });
    if (path === "/capabilities") return respond({ features: { reports: { configured: true, reachable: true, allowed: true, message: "합성 연결" } } });
    return respond({ detail: "Unexpected request" }, 404);
  });
  await page.addInitScript(task => { sessionStorage.setItem("hwp-report-task", task); sessionStorage.setItem("hwp-report-format-task", task); }, id);
  try {
    await page.goto("/report-save-test.html");
    await expect(page.locator(".formatted-report-surface")).toHaveAttribute("data-ready", "true");
    await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
    if (mode === "retry") {
      await page.getByRole("button", { name: "같은 보고서 저장 재확인", exact: true }).click();
      expect(saves).toHaveLength(2);
      expect(saves[1]).toEqual(saves[0]);
    }
    if (mode === "later-edit") {
      await expect.poll(() => saves.length).toBe(1);
      await page.evaluate(() => (window as any).__mutatePreview());
      release();
      await expect(page.getByRole("button", { name: "복구 파일 다운로드", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "복구 파일 저장 확인·저장본 열기", exact: true })).toBeDisabled();
      await expect(page.getByTestId("opened-document")).toHaveCount(0);
      await expect(page.locator(".formatted-report-surface")).toHaveCount(1);
    } else {
      await expect(page.getByTestId("opened-document")).toHaveText(doc.id);
      await expect(page.locator(".formatted-report-surface")).toHaveCount(0);
    }
    expect(saves).toHaveLength(mode === "retry" ? 2 : 1);
  } finally { release(); await page.unrouteAll({ behavior: "wait" }); }
});
