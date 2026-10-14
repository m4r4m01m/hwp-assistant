import React from "react";

export type Emphasis = { text: string; style: "bold" | "blue" | "bold_blue" };
export type ReportBlock = { text: string; kind: string; style_kind?: string; emphasis?: Emphasis[];
  columns?: string[]; rows?: string[][]; steps?: string[];
  visual_id?: string; visual_kind?: string; visual_row?: number; visual_column?: number; visual_columns?: number; visual_caption?: string };

export function ReportText({ text, emphasis = [] }: { text: string; emphasis?: Emphasis[] }) {
  const flags = new Array<number>(text.length).fill(0);
  const label = text.match(/^\s*(?:[□ㅇ○-]\s*)?(\([^()]{1,24}\))/);
  if (label) { const start = label[0].lastIndexOf(label[1]); for (let i = start; i < start + label[1].length; i++) flags[i] |= 1; }
  for (const item of emphasis) {
    if (!item.text) continue;
    const bits = item.style === "bold" ? 1 : item.style === "blue" ? 2 : 3;
    for (let start = text.indexOf(item.text); start >= 0; start = text.indexOf(item.text, start + item.text.length))
      for (let i = start; i < start + item.text.length; i++) flags[i] |= bits;
  }
  const parts: React.ReactNode[] = [];
  for (let start = 0; start < text.length;) {
    let end = start + 1; while (end < text.length && flags[end] === flags[start]) end++;
    const value = text.slice(start, end), blue = flags[start] & 2 ? "report-emphasis-blue" : undefined;
    parts.push(flags[start] & 1 ? <strong className={blue} key={start}>{value}</strong> : <span className={blue} key={start}>{value}</span>);
    start = end;
  }
  return <>{parts}</>;
}

// The staging plan uses individual, fenced cell paragraphs. Group only received
// items for display; this view never supplies text to the document or save request.
function displayBlocks(blocks: ReportBlock[]) {
  const result: ReportBlock[] = [];
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i];
    if (!block.visual_id) { result.push(block); continue; }
    const entries = [block];
    while (blocks[i + 1]?.visual_id === block.visual_id) entries.push(blocks[++i]);
    const caption = result[result.length - 1]?.visual_caption ? result.pop()!.text : "";
    const rows: string[][] = [];
    for (const entry of entries) (rows[entry.visual_row!] ||= [])[entry.visual_column!] = entry.text;
    const emphasis = entries.flatMap(e => e.emphasis || []);
    result.push({ text: caption, kind: block.visual_kind!, emphasis, columns: rows[0], rows: rows.slice(1),
      steps: block.visual_kind === "diagram" ? rows[0].filter((_, x) => x % 2 === 0) : undefined });
  }
  return result;
}

export function ReportContent({ blocks, testId }: { blocks: ReportBlock[]; testId?: string }) {
  return <>{displayBlocks(blocks).map((block, i) => {
    const text = (value: string) => <ReportText text={value} emphasis={block.emphasis} />;
    if (block.kind === "table") return <figure className="report-visual" key={i} data-testid="report-content-table">
      <figcaption>{text(block.text)}</figcaption><div className="report-table-scroll"><table>
        <thead><tr>{block.columns?.map((c, x) => <th scope="col" key={x}>{text(c)}</th>)}</tr></thead>
        <tbody>{block.rows?.map((row, y) => <tr key={y}>{row.map((c, x) => <td key={x}>{text(c)}</td>)}</tr>)}</tbody>
      </table></div></figure>;
    if (block.kind === "diagram") return <figure className="report-visual" key={i} data-testid="report-content-diagram">
      <figcaption>{text(block.text)}</figcaption><ol className="report-flow" aria-label="단계별 흐름도">
        {block.steps?.map((s, x) => <li key={x}>{x > 0 && <span className="report-flow-arrow" aria-hidden="true">→</span>}<div>{text(s)}</div></li>)}
      </ol></figure>;
    return <p key={i} className={`live-${block.style_kind || block.kind}`} data-testid={testId}>{text(block.text)}</p>;
  })}</>;
}
