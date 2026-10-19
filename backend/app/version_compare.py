"""Version comparison and conservative, save-based paragraph attribution."""
from collections import defaultdict
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session, object_session
from .auth import require_access
from .db import Version, VersionAuthor
from .hwpx import digest
from .compare_hwpx import snapshot, align, change_kind, moved_pairs

MAX_HISTORY = 80
MAX_HISTORY_BYTES = 80 * 1024 * 1024
MAX_HISTORY_BLOCKS = 16_000


def author_dict(version):
    db = object_session(version)
    row = db.get(VersionAuthor, version.id) if db is not None else None
    return {"user_id": row.user_id, "identity_kind": row.identity_kind} if row else None


def compare_history(versions, snapshots, complete):
    """Build per-block lineage. Events identify the account that saved a change,
    never the external file's author or an inferred person in local mode.
    """
    states, events = {}, defaultdict(list)
    previous = []
    for ordinal, version in enumerate(versions):
        current = [dict(b) for b in snapshots[version["id"]]["blocks"]]
        def event(kind):
            return {"version_id": version["id"], "author": version["author"], "created_at": version["created_at"],
                    "cause": version["cause"], "kind": kind}
        if ordinal:
            pairs, removed, added = align(previous, current)
            moves = moved_pairs(pairs)
            for li, ri, ambiguous in pairs:
                old, new = previous[li], current[ri]
                kind = change_kind(old, new) or ("moved" if (li, ri) in moves else None)
                new["lineage"] = old["lineage"]
                new["uncertain"] = ambiguous or old["uncertain"]
                new["last_change"] = event(kind) if kind else old["last_change"]
                if kind:
                    events[new["lineage"]].append(event(kind))
            for li in removed:
                events[previous[li]["lineage"]].append(event("removed"))
        else:
            added = range(len(current))
        for ri in added:
            current[ri].update(lineage=f"{version['id']}:{ri}", uncertain=False,
                               last_change=event("added") if ordinal or complete else None)
            if ordinal or complete:
                events[current[ri]["lineage"]].append(event("added"))
        states[version["id"]] = current
        previous = current
    return states, events


def compare_states(left, right, events, interval):
    pairs, removed, added = align(left, right)
    moves = moved_pairs(pairs)
    changes = []
    def add(kind, l, r, ambiguous=False):
        tokens = {b["lineage"] for b in (l, r) if b}
        history = [e for token in tokens for e in events[token] if e["version_id"] in interval]
        history = list({(e["version_id"], e["kind"]): e for e in history}.values())
        history.sort(key=lambda e: (interval[e["version_id"]], e["kind"]))
        def public(block):
            return {k: v for k, v in block.items() if k not in ("format", "lineage")} if block else None
        changes.append({"id": str(len(changes)), "kind": kind, "left": public(l), "right": public(r),
                        "history": history, "uncertain": ambiguous or any(b and b["uncertain"] for b in (l, r))})
    for li, ri, ambiguous in pairs:
        kind = change_kind(left[li], right[ri]) or ("moved" if (li, ri) in moves else None)
        if kind:
            add(kind, left[li], right[ri], ambiguous)
    for li in removed:
        add("removed", left[li], None)
    for ri in added:
        add("added", None, right[ri])
    # Keep the comparison in document order, including deletions on the left.
    changes.sort(key=lambda item: tuple((item["right"] or item["left"])["anchor"][k] for k in ("section", "paragraph")))
    return changes


def install_version_compare(app, version_dict):
    @app.get("/api/documents/{doc_id}/compare")
    def compare(doc_id: str, left: UUID, right: UUID, request: Request):
        left_id, right_id = str(left), str(right)
        with Session(app.state.engine) as db:
            require_access(db, doc_id, request.state.user)
            selected = [db.get(Version, ident) for ident in (left_id, right_id)]
            if any(not v or v.document_id != doc_id for v in selected):
                raise HTTPException(404, "비교할 문서 버전을 찾을 수 없습니다.")
            selected_meta = [version_dict(v) for v in selected]
            if left_id == right_id:
                return {"left": selected_meta[0], "right": selected_meta[1], "changes": [], "warnings": [],
                        "history_complete": True, "history_versions": 1, "reverse": False}
            # Normal publication is a single base-version chain (including restore).
            # Establish ancestry explicitly instead of assuming timestamps identify it.
            candidates = []
            for start, goal in ((selected[0], right_id), (selected[1], left_id)):
                chain, seen, total = [], set(), 0
                current = start
                while current and len(chain) < MAX_HISTORY and current.id not in seen:
                    if current.document_id != doc_id or total + current.size > MAX_HISTORY_BYTES:
                        break
                    chain.append(current); seen.add(current.id); total += current.size
                    if current.base_version is None:
                        break
                    current = db.get(Version, current.base_version)
                if goal in seen:
                    candidates.append(chain)
            if candidates:
                chain = max(candidates, key=len)
                complete = chain[-1].base_version is None
            else:
                # Still compare the two selected files; do not fabricate a history.
                chain, complete = [], False
            rows = {v.id: v for v in [*chain, *selected]}
            parsed, warnings, total_blocks = {}, set(), 0
            for v in reversed(list(rows.values())):
                if v.id in parsed:
                    continue
                data = (app.state.folder / v.storage_key).read_bytes()
                if len(data) != v.size or digest(data) != v.sha256:
                    raise HTTPException(409, "저장된 버전의 무결성을 확인하지 못했습니다.")
                try:
                    snap = snapshot(data)
                except HTTPException:
                    if v.id in (left_id, right_id):
                        raise
                    chain, complete = [], False
                    break
                total_blocks += len(snap["blocks"])
                parsed[v.id] = snap; warnings.update(snap["warnings"])
                if total_blocks > MAX_HISTORY_BLOCKS and v.id not in (left_id, right_id):
                    # Only the comparison needs to succeed; lineage has explicit limits.
                    chain, complete = [], False
                    break
            for v in selected:
                if v.id not in parsed:
                    data = (app.state.folder / v.storage_key).read_bytes()
                    if digest(data) != v.sha256 or len(data) != v.size:
                        raise HTTPException(409, "저장된 버전의 무결성을 확인하지 못했습니다.")
                    parsed[v.id] = snapshot(data)
                    warnings.update(parsed[v.id]["warnings"])
            if chain:
                timeline = [version_dict(v) for v in reversed(chain)]
                states, events = compare_history(timeline, parsed, complete)
                positions = {v["id"]: i for i, v in enumerate(timeline)}
                lo, hi = sorted((positions[left_id], positions[right_id]))
                interval = {v["id"]: i for i, v in enumerate(timeline) if lo < i <= hi}
                reverse = positions[left_id] > positions[right_id]
            else:
                states, events, interval, reverse = {}, defaultdict(list), {}, False
                for v in selected_meta:
                    state, _ = compare_history([v], parsed, False)
                    states.update(state)
            if not complete:
                warnings.add("수정 이력 추적 범위(최대 80버전·80MB·16,000문단)를 벗어났거나 연결된 이력이 없습니다. 확인할 수 없는 수정자는 표시하지 않습니다.")
            changes = compare_states(states[left_id], states[right_id], events, interval)
            # Revocation during parsing must also prevent delivery of the result.
            db.expire_all()
            require_access(db, doc_id, request.state.user, fresh=True)
            return {"left": selected_meta[0], "right": selected_meta[1], "changes": changes,
                    "warnings": sorted(warnings), "history_complete": complete,
                    "history_versions": len(chain), "reverse": reverse}
