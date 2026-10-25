// A superseded sample must release the renderer queue even if a browser image
// or native request has not settled. Only the disposable preview owns this wait.
export async function waitForStandardPreview<T>(pending: Promise<T>, signal: AbortSignal, timeoutMs?: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel = () => {};
  try {
    return await Promise.race([pending, new Promise<never>((_, reject) => {
      cancel = () => reject(new DOMException("예시 갱신이 취소되었습니다.", "AbortError"));
      if (signal.aborted) { cancel(); return; }
      signal.addEventListener("abort", cancel, { once: true });
      if (timeoutMs !== undefined) timer = setTimeout(() => reject(new Error("예시 화면을 불러오는 시간이 길어졌습니다. 설정은 유지했으니 미리보기를 다시 확인하세요.")), timeoutMs);
    })]);
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}
