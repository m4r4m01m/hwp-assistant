import { preview } from '../frontend/node_modules/vite/dist/node/index.js';

// Desktop launchers outlive their starting terminal. Vite otherwise interprets
// stdin EOF as a parent shutdown and silently exits, even with healthy API/data.
// Its CI mode disables that stdin hook while keeping SIGTERM/server.close intact.
export async function startPreviewService(config) {
  const previous = process.env.CI;
  process.env.CI = 'true';
  try {
    return await preview(config);
  } finally {
    if (previous === undefined) delete process.env.CI;
    else process.env.CI = previous;
  }
}
