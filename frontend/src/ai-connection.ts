/** Configuration discovery is distinct from an actual inference or catalog check. */
export type AIConnection = {
  configured_model: string;
  models?: string[];
  model_available: boolean | null;
  model_configured?: boolean;
  connectivity?: "unchecked" | "catalog_checked";
};

export function canRequestAI(value: AIConnection | null): boolean {
  return !!value && value.model_available !== false &&
    (value.model_configured === true || value.model_available === true);
}

export function aiConnectionMessage(value: AIConnection): string {
  if (!canRequestAI(value)) return "설정한 모델을 사용할 수 없습니다. 서버의 모델 설정을 확인하세요.";
  return value.connectivity === "unchecked"
    ? `설정된 모델: ${value.configured_model} · 실제 연결과 사용 권한은 요청 시 확인합니다.`
    : `서버 모델 목록 확인: ${value.configured_model}`;
}
