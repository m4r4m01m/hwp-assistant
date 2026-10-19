import { expect, type Page } from "@playwright/test";
import { unzipSync, strFromU8 } from "fflate";

/** Verify the rendered HWPX title style, never an HTML draft approximation. */
export async function expectNativeTitleRules(page: Page, title: string) {
  const handle = await page.locator(".report-editor-slot.active iframe").elementHandle();
  const engine = await handle?.contentFrame();
  if (!engine) throw new Error("제목선을 확인할 실제 보고서 엔진이 없습니다.");
  const bytes = Buffer.from(await engine.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  const zip = unzipSync(bytes);
  const colors = await page.evaluate(({ title, section, header }) => {
    const parser = new DOMParser(), s = parser.parseFromString(section, "application/xml"), h = parser.parseFromString(header, "application/xml");
    const text = (p: Element) => Array.from(p.children).filter(n => n.localName === "run")
      .flatMap(run => Array.from(run.children).filter(n => n.localName === "t").map(n => n.textContent || "")).join("");
    const paragraph = Array.from(s.getElementsByTagNameNS("*", "p")).find(p => text(p) === title);
    const para = Array.from(h.getElementsByTagNameNS("*", "paraPr")).find(p => p.getAttribute("id") === paragraph?.getAttribute("paraPrIDRef"));
    let cell: Element | null = paragraph || null;
    while (cell && cell.localName !== "tc") cell = cell.parentElement;
    // New source-based title frames use the original one-cell table; frozen
    // legacy drafts keep their paragraph border. Match the actual title in both.
    const borderId = cell?.getAttribute("borderFillIDRef") || para?.getElementsByTagNameNS("*", "border")[0]?.getAttribute("borderFillIDRef");
    const border = Array.from(h.getElementsByTagNameNS("*", "borderFill")).find(b => b.getAttribute("id") === borderId);
    return { top: border?.getElementsByTagNameNS("*", "topBorder")[0]?.getAttribute("color"),
      bottom: border?.getElementsByTagNameNS("*", "bottomBorder")[0]?.getAttribute("color") };
  }, { title, section: strFromU8(zip["Contents/section0.xml"]), header: strFromU8(zip["Contents/header.xml"]) });
  expect(colors).toEqual({ top: "#437FC1", bottom: "#437FC1" });
}
