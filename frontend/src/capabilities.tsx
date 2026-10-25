import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, errorMessage, isAbortError } from "./api";

export type FeatureName = "rewrite" | "chat" | "review" | "reports";
export type FeatureCapability = {
  provider?: string;
  configured: boolean;
  reachable: boolean | null;
  allowed: boolean;
  reason_code: string | null;
  message: string;
  supports_stream: boolean;
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
