import { test, expect, type Page, type Route } from "@playwright/test";

const json = (route: Route, value: unknown) => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
const proposal = { kind: "design", id: "reference-table", sha256: "c".repeat(64), name: "합성 표 디자인", design_kind: "table",
  reason: "머리글을 구분하여 읽기 쉽게 표시합니다.", target: { kind: "table", section: 0, paragraph: 3, control: 0 } };
const turn = { id: "design-turn", conversation_id: "design-thread", document_id: "design-document", base_version: "saved-version",
  message: "현재 표에 어울리는 디자인을 추천해줘", status: "ready", answer: "이 표에 사용할 디자인을 제안합니다.",
  rewrite_instruction: null, selection: null, design_proposals: [proposal],
  document_context: { sha256: "a".repeat(64), truncated: false, revision: 7, epoch: 2, characters: 15 },
  design_context: { source_sha256: "b".repeat(64), target: proposal.target },
  error: "", execution: { provider: "llm", completed: true }, created_at: 1, stale: false, can_cancel: false };

test("MOCK context extraction: whole-document body scope preserves fixed styled leads and exposes excluded content", async ({ page }) => {
  await openPanel(page);
  const result = await page.evaluate(async () => {
    const p = (value: string, style = "0") => `<hp:run charPrIDRef="${style}"><hp:t>${value}</hp:t></hp:run>`;
    const section = `<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
      <hp:p>${p("1. 제목")}</hp:p><hp:p>${p("□ ")}${p("(현황)", "1")}${p(" 30개 시스템을 점검함.")}</hp:p>
      <hp:p>${p("ㅇ 단일 서식 본문")}</hp:p><hp:p>${p("□ 중간 ")}${p("강조", "1")}${p(" 내용")}</hp:p>
      <hp:p>${p("주1) 설명")}</hp:p><hp:p><hp:run charPrIDRef="0"><hp:tbl/></hp:run></hp:p>
      <hp:p styleIDRef="9">${p("□ 제목 디자인이 적용된 제목")}</hp:p></hs:sec>`;
    const header = '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"><hh:style id="9" name="HWP Assistant editor title"/></hh:head>';
    return { normal: await (window as any).testRewriteContext(section, header), big: await (window as any).testRewriteContext(section, header, true) };
  });
  expect(result.normal.paragraphs).toHaveLength(3);
  expect(result.normal.paragraphs.map((p: any) => [p.paragraph, p.fixed_prefix])).toEqual([[1, "□ (현황)"], [2, ""], [3, ""]]);
  expect(result.normal.paragraphs[2].protected_text).toEqual(["강조"]);
  expect(result.normal.preserved["혼합 서식 문단"]).toBeUndefined();
  expect(result.normal.preserved["제목·주석·기타 문단"]).toBe(3);
  expect(result.normal.preserved["표·개체·주석 포함 문단"]).toBe(1);
  expect(result.big.paragraphs).toEqual([]);
  expect(result.big.unavailable).toContain("질문은 가능합니다");
});

async function openPanel(page: Page, row: unknown = turn) {
  await page.route("**/api/document-assistant-provider", route => json(route, { provider: "llm", model: "synthetic", configured: true, allowed: true, reason: "" }));
  await page.route("**/api/capabilities", route => json(route, { features: {} }));
  await page.route("**/api/threads/design-thread/assistant-turns", route => json(route, [row]));
  await page.route("**/assistant-design-test.html", route => route.fulfill({ contentType: "text/html",
    body: '<!doctype html><html lang="ko"><meta charset="utf-8"><div id="root"></div><script type="module" src="/tests/fixtures/assistant-design-proposals.tsx"></script></html>' }));
  await page.goto("/assistant-design-test.html");
  await expect(page.getByRole("region", { name: "문서와 AI 대화" })).toBeVisible();
}

test("MOCK design advice requires a click and preserves the frozen unsaved source and target", async ({ page }) => {
  let writes = 0;
  page.on("request", request => { if (request.method() !== "GET") writes++; });
  await openPanel(page);
  const output = page.getByTestId("design-preview-request");
  await expect(page.getByText("합성 표 디자인", { exact: true })).toBeVisible();
  await expect(output).toBeEmpty();
  await page.getByRole("button", { name: "디자인 제안 보기", exact: true }).click();
  await expect(output).not.toBeEmpty();
  expect(JSON.parse(await output.textContent() || "{}")).toEqual({ proposal, design_context: turn.design_context,
    revision: 7, epoch: 2, base_version: "saved-version" });
  expect(writes).toBe(0); // Opening a proposal does not dispatch AI or save a document.
});

test("MOCK readers can inspect design advice but cannot open an applying workflow", async ({ page }) => {
  await openPanel(page);
  await page.getByRole("button", { name: "읽기 권한 전환", exact: true }).click();
  await expect(page.getByRole("button", { name: "디자인 제안 보기", exact: true })).toBeDisabled();
  await expect(page.getByText("읽기 권한으로는 디자인을 적용할 수 없습니다.", { exact: true })).toBeVisible();
  await expect(page.getByTestId("design-preview-request")).toBeEmpty();
});

for (const stale of [true, false]) {
  test(`MOCK changed saved version blocks design proposal: ${stale ? "server stale" : "base mismatch"}`, async ({ page }) => {
    await openPanel(page, { ...turn, stale, base_version: stale ? turn.base_version : "obsolete-version" });
    await expect(page.getByRole("button", { name: "디자인 제안 보기", exact: true })).toBeDisabled();
    await expect(page.getByTestId("design-preview-request")).toBeEmpty();
  });
}

test("MOCK capture reads the exact table after mixed controls and recognizes a title frame", async ({ page }) => {
  await openPanel(page);
  const context = await page.evaluate(async () => {
    const hp = "http://www.hancom.co.kr/hwpml/2011/paragraph", hh = "http://www.hancom.co.kr/hwpml/2011/head";
    const section = `<section xmlns:hp="${hp}"><hp:p><hp:run><hp:secPr><hp:colPr/></hp:secPr><hp:rect/><hp:tbl rowCnt="1" colCnt="1"><hp:tr><hp:tc borderFillIDRef="7"><hp:subList><hp:p><hp:run><hp:t>이 제목 상자의 실제 내용</hp:t></hp:run></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p><hp:p><hp:run><hp:t>다른 본문</hp:t></hp:run></hp:p></section>`;
    const header = `<hh:head xmlns:hh="${hh}"><hh:borderFill id="7"><hh:topBorder type="SOLID" color="#437FC1"/><hh:bottomBorder type="SOLID" color="#437FC1"/><hh:leftBorder type="NONE"/><hh:rightBorder type="NONE"/></hh:borderFill></hh:head>`;
    const capture = (window as unknown as { testDesignContext: (section: string, header: string, target: unknown) => Promise<unknown> }).testDesignContext;
    return { selected: await capture(section, header, { kind: "table", section: 0, paragraph: 0, control: 3 }),
      wrongOrdinal: await capture(section, header, { kind: "table", section: 0, paragraph: 0, control: 0 }),
      paragraph: await capture(section, header, { kind: "paragraph", section: 0, paragraph: 1 }),
      ambiguous: await capture(section.replace("<hp:rect/>", "<hp:unknown/>"), header, { kind: "table", section: 0, paragraph: 0, control: 3 }) };
  }) as { selected: Record<string, unknown>; wrongOrdinal: Record<string, unknown>; paragraph: Record<string, unknown>; ambiguous: Record<string, unknown> };
  expect(context.selected.target_text).toBe("이 제목 상자의 실제 내용");
  expect(context.selected.target_is_title_frame).toBe(true);
  expect(context.selected.target_text_truncated).toBe(false);
  expect(context.selected.source_sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(context.wrongOrdinal.target).toBeNull();
  expect(context.wrongOrdinal.target_text).toBe("");
  expect(context.paragraph.target_text).toBe("다른 본문");
  expect(context.paragraph.source_sha256).toBe(context.selected.source_sha256);
  expect(context.ambiguous.target).toBeNull();
});

test("MOCK selected content is bounded without splitting Unicode and unsupported targets stay empty", async ({ page }) => {
  await openPanel(page);
  const result = await page.evaluate(async () => {
    const hp = "http://www.hancom.co.kr/hwpml/2011/paragraph";
    const section = `<section xmlns:hp="${hp}"><hp:p><hp:run><hp:t>${"가".repeat(7999)}😀끝</hp:t></hp:run></hp:p></section>`;
    const capture = (window as unknown as { testDesignContext: (section: string, header: string, target: unknown) => Promise<unknown> }).testDesignContext;
    return { selected: await capture(section, "<head/>", { kind: "paragraph", section: 0, paragraph: 0 }),
      unsupported: await capture(section, "<head/>", { kind: "paragraph", section: 1, paragraph: 0 }) };
  }) as { selected: { target_text: string; target_text_truncated: boolean }; unsupported: { target: unknown; target_text: string } };
  expect(result.selected.target_text).toBe("가".repeat(7999));
  expect(result.selected.target_text_truncated).toBe(true);
  expect(result.unsupported.target).toBeNull();
  expect(result.unsupported.target_text).toBe("");
});

test("MOCK first title retains its target with section metadata but excludes visual objects", async ({ page }) => {
  await openPanel(page);
  const result = await page.evaluate(async () => {
    const hp = "http://www.hancom.co.kr/hwpml/2011/paragraph";
    const section = `<section xmlns:hp="${hp}"><hp:p><hp:run><hp:secPr><hp:colPr/></hp:secPr><hp:ctrl><hp:pageNum/><hp:pageNumCtrl/><hp:newNum/><hp:pageHiding/></hp:ctrl><hp:t>첫 문단의 문서 제목</hp:t></hp:run></hp:p></section>`;
    const capture = (window as unknown as { testDesignContext: (section: string, header: string, target: unknown) => Promise<unknown> }).testDesignContext;
    return { title: await capture(section, "<head/>", { kind: "paragraph", section: 0, paragraph: 0 }),
      object: await capture(section.replace("<hp:t>", "<hp:rect/><hp:t>"), "<head/>", { kind: "paragraph", section: 0, paragraph: 0 }) };
  }) as { title: { target: unknown; target_text: string }; object: { target: unknown; target_text: string } };
  expect(result.title.target).toEqual({ kind: "paragraph", section: 0, paragraph: 0 });
  expect(result.title.target_text).toBe("첫 문단의 문서 제목");
  expect(result.object.target).toBeNull();
  expect(result.object.target_text).toBe("");
});
