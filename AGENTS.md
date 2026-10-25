# HWP Assistant

## Start here

- Read [current project context](docs/PROJECT_CONTEXT.md) before planning work. It is the shared handoff for ChatGPT Work and Codex; account memory is not a substitute. Check the actual branch, working tree and latest CI before relying on its dated snapshot.
- Evidence order: current code/locks/workflows and exact-commit CI results, then dated evidence/PRs, then historical plans or chat claims. Preserve older records; explain superseded statements rather than treating a plan as completion.
- Preserve uncommitted work, running services, private data and source branches. Work on a focused branch from current `origin/main`; inspect before reusing a checkout. Never reset/clean or terminate unrelated processes.
- Prefer merge commits for integration so both branch histories remain visible. Do not squash/rebase existing shared history or delete a source branch without a user request. Merge only the reviewed head after applicable checks pass and only within the user's authorization.

## Document and data contracts

- HWPX is canonical. Never mutate original or historical blobs. Restore creates a version.
- All new reports MUST follow `docs/document-writing-standard.md` and the hash-pinned original `backend/app/assets/document-standard/reference.hwpx`. Reuse `backend/app/document_standard.py` and `assets/document-standard/profile.json`; preserve the source and historical versions. Title frames have blue TOP/BOTTOM rules, never black side bars; retain hierarchical paragraph indents.
- New revision-3 reference standards follow the 2026-10-20 section of `docs/document-writing-standard.md`: source-derived 14pt justified body, □ → ㅇ → - default, and appendix 1/2/3 composition. The older 2026-10-07 15pt supplement remains for legacy/frozen requests. TOC entries reference headings and verified rendered pages, never hand-spaced dot strings; appendix 1 has no automatic TOC.
- New draft square items should occupy about 2–3 lines; use another square for another point or a longer item. Preserve facts/citations and existing frozen drafts. Show actual generation/application/layout stages; never simulate model token streaming or fabricated progress.
- Report generation uses Codex app-server text deltas. Partial JSON is display-only; only a completed, validated ReportDraft may enter the HWPX preview. Keep owner/status fences, bounded process cleanup and frozen save retries. See `docs/report-stream-2026-10-08.md`.
- New report UI pauses at content review before preparing the formatted preview. Feedback creates a separate task with a frozen source draft/SHA and inherited settings, never replaces the source or a manually edited HWPX. Optional cover date/department are user-supplied text, never copied from the reference examples. See `docs/report-review-workflow-2026-10-20.md`.
- New report drafts may replace prose with bounded tables and editable flow diagrams. Use `report_contract.py` and `report_rich.py`: every cell/label is fenced before layout, parenthetical lead labels are bold, emphasis must match actual content, and frozen legacy previews retain their layout policy. See `docs/rich-report-2026-10-08.md`.
- Confirm blob persistence and DB commit before reporting saved. Export is not a server save.
- Lock document row, validate lease and base version, then publish in one transaction.
- Preserve user changes; no reset/clean of the checkout. Do not add paid AI fallbacks.
- Only approved AI text uses rhwp exact commands with document/selection/preimage fences.
- The user's request for automatic formatted editable drafts authorizes fenced text application in a separate temporary report preview. This never targets an existing editor or publishes a document; explicit “초안 확정·서버 저장” approves the visible output including manual edits. Preserve the frozen preview, receipts, and save key on retries.
- `backend/app`: FastAPI, SQLAlchemy, filesystem, LlmProvider. `frontend/src`: React + EditorAdapter. Native development uses SQLite; production uses PostgreSQL plus immutable filesystem blobs. Production is one API worker; do not scale workers without shared admission/rate-limit coordination.
- `scripts/build-engine.mjs`: pinned self-hosted rhwp; downstream read-only/export bridge is `assistant-bridge.ts.txt`.
- `docs/internal-doc-assistant-plan-v0.4.md` stays a requirements baseline, not a completion report.
- Report real-engine tests separately from mock AI tests. Hancom and isolated-network verification remain manual until evidence exists.

## Security and operations

- Never commit passwords (including guest), password hashes, tokens, API keys, private env contents, Codex authentication, real company documents, DB dumps or private runtime artifacts. Use synthetic fixtures and minimal personal data. Do not print full `docker inspect`, expanded Compose configuration or credential files into chats/CI.
- Company/isolated-network operation and personal-network PoC are separate environments. Do not assume access between them or send internal material to external models. No paid AI fallback; an explicitly configured local provider is a separate choice.
- `HWP_CODEX_USER` / `HWP_CODEX_ALLOWED_USERS` are app-ID ACLs, not ChatGPT identities. A nonempty list replaces the single-user fallback. Signup/guest never automatically grants AI access. Server Codex login is independent of app login; no per-user Codex credentials are implemented.
- Preserve identity ownership and reserved/retired IDs. Manage password accounts through `app.account_admin`; never overwrite/reset an existing guest on startup. Initial seeding may create a missing account only. A shared guest identity has a shared document library.
- Production uses **standalone** `compose.deploy.yaml`, never merged with development `compose.yaml`. Preserve `/hwp-assistant/`, loopback web port `23000`, unexposed API/DB ports, and all three external volumes (`metadata`, `documents`, `codex-state`). Never delete/prune production volumes or rerun first-install initialization on an existing server.
- Keep `/opt/hwp-assistant/production.env` private. Compose defaults can produce `local`/empty even when those ACL keys are absent from that file. Environment changes need container recreation; a restart alone does not apply them. Pin the currently running image for an environment-only change; see the handoff procedure.
- Back up DB and document files together before replacement. Preserve private env separately; the normal backup excludes Codex credentials. Restore into isolated volumes and verify before cutover; no automatic schema downgrade or destructive restore.
- PR/main checks do not publish/deploy. Heavy image build/publication and home deployment are manual workflows, main-only; PR code must never execute on the production runner. A merged PR, published image, successful deployment job, authenticated Codex and real model success are different evidence.

## Setup and verification commands

Run from the repository root unless stated otherwise. Use an unused environment if an existing one serves a running app. CI locks Python 3.14 and Node 24.15.0; preserve dependency and engine locks.

```sh
# macOS/Linux
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock.txt -r backend/requirements-mcp.lock.txt
.venv/bin/python -m pytest -q
```

On Windows use `python -m venv .venv` and `.venv/Scripts/python` for both Python commands. The MCP lock is needed for MCP/Codex checks; it is included in the production API image.

```sh
# frontend/ working directory
npm ci
npm exec tsc -- --noEmit
npm run test:unit
npx playwright install chromium
npm run test:ui
# Requires a prepared, verified optimized engine:
npm run engine:core
npm run engine
npm run build
```

- Engine build requires the documented Docker/Rust toolchain; `npm ci` alone does not prepare it. Follow [developer setup](docs/developer-quickstart.md) / [engine build](docs/engine-empty-fill-fix.md). A diagnostic build must be labeled as such and must not replace the release core. The older downloadable developer kit may not match the current bridge.
- `npm run test:e2e` (in `frontend/`) needs the real engine, API at `8000` and Vite at `5173` running; mocked `test:ui` starts its own isolated server. Do not confuse these results.
- Development Compose: `docker compose up --build`, UI `http://127.0.0.1:8080`. It is a separate local deployment, not a production update.
- For deployment changes run the Python checks named in [.github/workflows/hwp-deploy.yml](.github/workflows/hwp-deploy.yml) and [deployment-only checks](.github/workflows/deploy-only-checks.yml); frontend/build changes also need the Node regressions in those workflows. Real accounts/PostgreSQL/HTTPS/restart/engine checks are in [.github/workflows/poc-integration.yml](.github/workflows/poc-integration.yml).
- For documentation-only changes verify relative links/anchors, command names, source assertions, whitespace and secret exclusion, then inspect PR CI. Do not claim a skipped path-filtered workflow ran. No new mirror tests are needed.

## Document index and handoff upkeep

- [PROJECT_CONTEXT](docs/PROJECT_CONTEXT.md): dated status, chronology, decisions, account/runtime recovery, next work, cross-tool loading and verified links.
- [Requirements v0.4](docs/internal-doc-assistant-plan-v0.4.md): preserved baseline; [writing standard](docs/document-writing-standard.md), [report review](docs/report-review-workflow-2026-10-20.md), [streaming](docs/report-stream-2026-10-08.md), [rich report](docs/rich-report-2026-10-08.md): document contracts.
- [Collaboration/MCP](docs/collaboration-and-mcp.md), [local model](docs/local-model-rewrite.md), [storage audit](docs/storage-audit.md), [version comparison](docs/version-comparison.md): technical details; check their dates against current code.
- [Deployment runbook](docs/deployment-runbook.md), [first activation](docs/deployment-first-activation.md), [published-image deployment](docs/deploy-published-images.md), [accounts/Codex/prewarm](docs/poc-codex-accounts-prewarm-2026-10-25.md): operational details and historical scope notes.
- Update `docs/PROJECT_CONTEXT.md` after meaningful decisions/merges/verification: date, exact source SHA and links, observed environment, actual checks, remaining limits and next steps. Never convert a chat assertion into verified completion without evidence.
