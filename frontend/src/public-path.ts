import { withPublicBase } from "../../scripts/public-base.mjs";

// Vite replaces BASE_URL at build time, including public subpath deployments.
export const publicPath = (path: string): string =>
  withPublicBase(import.meta.env.BASE_URL, path);
