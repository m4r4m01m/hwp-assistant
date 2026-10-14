// Presentation only. Never normalize, truncate or modify approved candidate text.
export type TextPart = { kind: "same" | "removed" | "added"; text: string };
const segmenter = new Intl.Segmenter("ko", { granularity: "grapheme" });
const characters = (text: string) => Array.from(segmenter.segment(text), item => item.segment);

export function textDifference(before: string, after: string): TextPart[] {
  const a = characters(before), b = characters(after), result: TextPart[] = [];
  const push = (kind: TextPart["kind"], text: string) => {
    if (!text) return;
    const last = result[result.length - 1];
    if (last?.kind === kind) last.text += text;
    else result.push({ kind, text });
  };
  let start = 0, endA = a.length, endB = b.length;
  while (start < endA && start < endB && a[start] === b[start]) start++;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  push("same", a.slice(0, start).join(""));
  const n = endA - start, m = endB - start;
  // Bound both memory and work for long unrelated paragraphs.
  if ((n + 1) * (m + 1) > 250000) {
    push("removed", a.slice(start, endA).join("")); push("added", b.slice(start, endB).join(""));
  } else {
    const width = m + 1, lengths = new Uint16Array((n + 1) * width);
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
      lengths[i * width + j] = a[start + i] === b[start + j] ? 1 + lengths[(i + 1) * width + j + 1]
        : Math.max(lengths[(i + 1) * width + j], lengths[i * width + j + 1]);
    let i = 0, j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && a[start + i] === b[start + j]) { push("same", a[start + i]); i++; j++; }
      else if (i < n && (j === m || lengths[(i + 1) * width + j] >= lengths[i * width + j + 1])) push("removed", a[start + i++]);
      else push("added", b[start + j++]);
    }
  }
  push("same", a.slice(endA).join(""));
  return result;
}
