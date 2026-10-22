// Shared by Vite, the independently built Studio, and browser URL helpers.
// Keep this module free of Node-only imports: it also ships in the browser.
export function normalizePublicBase(value = '/') {
  if (typeof value !== 'string' || !value) throw Error('HWP_PUBLIC_BASE_PATH must be a path');
  const base = value.endsWith('/') ? value : value + '/';
  if (!/^\/(?:[A-Za-z0-9_-]+\/)*$/.test(base))
    throw Error('HWP_PUBLIC_BASE_PATH must be / or an absolute path such as /hwp-assistant/');
  return base;
}

export function withPublicBase(base, path) {
  const prefix = normalizePublicBase(base);
  if (typeof path !== 'string' || path.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(path))
    throw Error('Expected an application-relative path');
  return prefix + path.replace(/^\//, '');
}
