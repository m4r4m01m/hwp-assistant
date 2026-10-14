# HWP Assistant

- HWPX is canonical. Never mutate original or historical blobs. Restore creates a version.
- All new reports MUST follow `docs/document-writing-standard.md` and the hash-pinned original `backend/app/assets/document-standard/reference.hwpx`. Reuse `backend/app/document_standard.py` and `assets/document-standard/profile.json`; preserve the source and historical versions. Title frames have blue TOP/BOTTOM rules, never black side bars; retain hierarchical paragraph indents.
- Apply the 2026-10-07 guide supplement for new report prose: □ → ㅇ → - hierarchy, useful parenthesized labels, 15pt left-aligned body and hanging indents. TOC entries must reference headings and verified rendered pages, never hand-spaced dot strings. Template expansion is designed in `docs/report-template-design-2026-10-07.md`; do not claim unimplemented automatic TOC/template selection.
- New draft square items should occupy about 2–3 lines; use another square for another point or a longer item. Preserve facts/citations and existing frozen drafts. Show actual generation/application/layout stages; never simulate model token streaming or fabricated progress.
- Report generation uses Codex app-server text deltas. Partial JSON is display-only; only a completed, validated ReportDraft may enter the HWPX preview. Keep owner/status fences, bounded process cleanup and frozen save retries. See `docs/report-stream-2026-10-08.md`.
- New report drafts may replace prose with bounded tables and editable flow diagrams. Use `report_contract.py` and `report_rich.py`: every cell/label is fenced before layout, parenthetical lead labels are bold, emphasis must match actual content, and frozen legacy previews retain their layout policy. See `docs/rich-report-2026-10-08.md`.
- Confirm blob persistence and DB commit before reporting saved. Export is not a server save.
- Lock document row, validate lease and base version, then publish in one transaction.
- Preserve user changes; no reset/clean of the checkout. Do not add paid AI fallbacks.
- Only approved AI text uses rhwp exact commands with document/selection/preimage fences.
- The user's request for automatic formatted editable drafts authorizes fenced text application in a separate temporary report preview. This never targets an existing editor or publishes a document; explicit “초안 확정·서버 저장” approves the visible output including manual edits. Preserve the frozen preview, receipts, and save key on retries.
- `backend/app`: FastAPI, SQLAlchemy, filesystem, LlmProvider. `frontend/src`: React + EditorAdapter.
- `scripts/build-engine.mjs`: pinned self-hosted rhwp; downstream read-only/export bridge is `assistant-bridge.ts.txt`.
- `docs/internal-doc-assistant-plan-v0.4.md` stays a requirements baseline, not a completion report.
- Install: `python -m venv .venv`, `.venv/Scripts/python -m pip install -r backend/requirements.lock.txt`; `cd frontend && npm ci && npm run engine`.
- Test from root: `.venv/Scripts/python -m pytest -q`. Frontend: `npm run build`, `npm run test:e2e` with API:8000 and Vite:5173 running.
- Compose: `docker compose up --build`; local UI `http://127.0.0.1:8080`.
- Report real-engine tests separately from mock AI tests. Hancom and isolated-network verification remain manual until evidence exists.
