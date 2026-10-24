import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, errorMessage, isAbortError } from "./api";

export type FeatureName = "rewrite" | "chat" | "review" | "reports";
export type FeatureCapability = {
  configured: boolean;
  reachable: boolean | null;
  allowed: boolean;
  reason_code: string | null;
  message: string;
  supports_stream: boolean;
  supports_research?: boolean;
  backend?: "codex" | "llm";
  provider?: string;
  model?: string;
  external?: boolean;
};
type Capabilities = { features: Record<FeatureName, FeatureCapability> };
type State = { data: Capabilities | null; loading: boolean; error: string; refresh: () => void };
const Context = createContext<State>({ data: null, loading: true, error: "", refresh: () => {} });

export function CapabilitiesProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Capabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const refresh = useCallback(() => {
    controller.current?.abort();
    const pending = new AbortController();
    controller.current = pending;
    setLoading(true);
    setError("");
    api<Capabilities>("/capabilities", { signal: pending.signal }).then(value => {
      if (!pending.signal.aborted) setData(value);
    }).catch(cause => {
      if (!pending.signal.aborted && !isAbortError(cause)) {
        setData(null);
        setError(errorMessage(cause));
      }
    }).finally(() => {
      if (!pending.signal.aborted) setLoading(false);
    });
  }, []);
  useEffect(() => { refresh(); return () => controller.current?.abort(); }, [refresh]);
  return <Context.Provider value={{ data, loading, error, refresh }}>{children}</Context.Provider>;
}

export function useFeature(name: FeatureName) {
  const state = useContext(Context);
  const capability = state.data?.features[name];
  const available = !!capability && capability.configured && capability.allowed && capability.reachable !== false;
  const message = state.loading ? "사용 가능 여부를 확인하는 중…" : state.error || capability?.message || "이 기능의 사용 가능 여부를 확인하지 못했습니다.";
  return { ...state, capability, available, message };
}

export function CapabilityNotice({ feature, label }: { feature: FeatureName; label: string }) {
  const state = useFeature(feature);
  if (state.available && !state.loading) return null;
  return <div className="capability-notice" aria-live="polite">
    <strong>{label}</strong>
    <p>{state.message}</p>
    {!state.loading && <button type="button" onClick={state.refresh}>사용 가능 여부 다시 확인</button>}
  </div>;
}

export function AIDataNotice({ feature, scope }: { feature: FeatureName; scope: string }) {
  const { capability } = useFeature(feature);
  if (!capability?.configured || !capability.provider) return null;
  const names: Record<string, string> = { openrouter: "OpenRouter", nvidia: "NVIDIA", openai: "OpenAI", "openai-compatible": "연결한 AI" };
  return <p className="hint" data-testid={`${feature}-ai-data-notice`}>
    사용 AI: {names[capability.provider] || capability.provider}{capability.model ? ` · ${capability.model}` : ""}.
    {capability.external ? ` ${scope} 외부 AI 공급자에게 전송합니다.` : ` ${scope} 관리자가 설정한 AI 서버에 전송합니다.`}
  </p>;
}
