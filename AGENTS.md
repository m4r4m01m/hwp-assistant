# HWP Assistant

- HWPX is canonical. Never mutate original or historical blobs. Restore creates a version.
- Confirm blob persistence and DB commit before reporting saved. Export is not a server save.
- Lock document row, validate lease and base version, then publish in one transaction.
- Preserve user changes; no reset/clean of the checkout. Do not add paid AI fallbacks.
- Only approved AI text uses rhwp exact commands with document/selection/preimage fences.
- `backend/app`: FastAPI, SQLAlchemy, filesystem, LlmProvider. `frontend/src`: React + EditorAdapter.
- `scripts/build-engine.mjs`: pinned self-hosted rhwp; downstream read-only/export bridge is `assistant-bridge.ts.txt`.
- `docs/internal-doc-assistant-plan-v0.4.md` stays a requirements baseline, not a completion report.
- Install: `python -m venv .venv`, `.venv/Scripts/python -m pip install -r backend/requirements.lock.txt`; `cd frontend && npm ci && npm run engine`.
- Test from root: `.venv/Scripts/python -m pytest -q`. Frontend: `npm run build`, `npm run test:e2e` with API:8000 and Vite:5173 running.
- Compose: `docker compose up --build`; local UI `http://127.0.0.1:8080`.
- Report real-engine tests separately from mock AI tests. Hancom and isolated-network verification remain manual until evidence exists.
