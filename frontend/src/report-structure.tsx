export const REPORT_STRUCTURES = [
  { id: "response", name: "상황·대응 보고", headings: ["개요", "현황", "대응", "향후 계획"] },
  { id: "decision", name: "의사결정 보고", headings: ["검토 배경", "대안 비교", "권고안", "승인 요청 사항"] },
];

export function structureError(value?: string[]): string {
  if (!value) return "";
  if (value.length < 3 || value.length > 6) return "대제목은 3~6개로 구성해 주세요.";
  const titles = value.map(title => title.trim());
  if (titles.some(title => !title)) return "비어 있는 대제목을 입력해 주세요.";
  if (titles.some(title => title.length > 120 || /[\r\n\t]/.test(title))) return "대제목은 한 줄, 120자 이내로 입력해 주세요.";
  if (titles.some(title => /^\d+[.)]\s*/.test(title))) return "번호는 자동으로 붙습니다. 대제목만 입력해 주세요.";
  if (new Set(titles.map(title => title.toLocaleLowerCase())).size !== titles.length) return "대제목을 서로 구별되게 입력해 주세요.";
  return "";
}

export function ReportStructure({ value, onChange, disabled }: {
  value?: string[]; onChange: (value: string[] | undefined) => void; disabled: boolean;
}) {
  const selected = !value ? "auto" : REPORT_STRUCTURES.find(preset =>
    JSON.stringify(preset.headings) === JSON.stringify(value))?.id || "custom";
  const error = structureError(value);
  function move(index: number, delta: number) {
    if (!value) return;
    const next = [...value];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    onChange(next);
  }
  return <fieldset className="report-structure" disabled={disabled}>
    <legend>대제목 구성</legend>
    <select id="report-structure-preset" aria-label="대제목 구성 방식" value={selected} onChange={event => {
      const id = event.target.value;
      onChange(id === "auto" ? undefined : [...(REPORT_STRUCTURES.find(preset => preset.id === id)?.headings
        || ["개요", "주요 내용", "향후 계획"])]);
    }}>
      <option value="auto">내용에 맞게 자동 구성</option>
      {REPORT_STRUCTURES.map(preset => <option key={preset.id} value={preset.id}>{preset.name}</option>)}
      <option value="custom">직접 구성</option>
    </select>
    {value ? <>
      <ol className="report-structure-headings">
        {value.map((title, index) => <li key={index}>
          <label><span>{index + 1}.</span><input aria-label={`${index + 1}번째 대제목`} maxLength={120}
            value={title} placeholder="대제목 입력" aria-invalid={!title.trim() || undefined}
            onChange={event => onChange(value.map((item, position) => position === index ? event.target.value : item))} /></label>
          <div className="report-structure-order">
            <button type="button" aria-label={`${index + 1}번째 대제목 위로`} title="위로" disabled={disabled || index === 0} onClick={() => move(index, -1)}>↑</button>
            <button type="button" aria-label={`${index + 1}번째 대제목 아래로`} title="아래로" disabled={disabled || index === value.length - 1} onClick={() => move(index, 1)}>↓</button>
            <button type="button" aria-label={`${index + 1}번째 대제목 삭제`} title="삭제" disabled={disabled || value.length <= 3} onClick={() => onChange(value.filter((_, position) => position !== index))}>×</button>
          </div>
        </li>)}
      </ol>
      <button type="button" className="report-structure-add" disabled={disabled || value.length >= 6} onClick={() => onChange([...value, ""])}>+ 대제목 추가</button>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="hint">이 제목과 순서로 작성합니다. 제목은 바꿀 수 있으며 번호는 자동으로 붙습니다.</p>
    </> : <p className="hint">AI가 주제와 작성 목적에 맞춰 제목과 순서를 정합니다.</p>}
  </fieldset>;
}
