"""Version-fenced document search. Indexing never runs inside document saves.

The durable index is a derived copy of current saved HWPX only. No report-task
draft, conversation, writer memo or unsaved editor buffer enters this index.
"""
import asyncio
import json
import math
import os
import re
import threading
import time
import unicodedata
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import Float, ForeignKey, String, Text, and_, false, or_, select
from sqlalchemy.orm import Mapped, Session, defer, mapped_column

from .db import Access, Base, Document, Version, document_is_trashed, transaction
from .hwpx import digest, validate
from .library_access import shared_library
from .document_search_embeddings import EmbeddingUnavailable, embed_texts, embedding_settings

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_CHUNKS = 64
MAX_CHUNK_CHARS = 700
MAX_CANDIDATES = 200
_refresh_lock = threading.Lock()


class DocumentSearchIndex(Base):
    __tablename__ = "document_search_indexes"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(36), index=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    document_name: Mapped[str] = mapped_column(String(240))
    chunks: Mapped[str] = mapped_column(Text, default="[]")
    lexical_text: Mapped[str] = mapped_column(Text, default="")
    vectors: Mapped[str] = mapped_column(Text, default="[]")
    embedding_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="ready")
    coverage: Mapped[str] = mapped_column(String(16), default="complete")
    updated_at: Mapped[float] = mapped_column(Float)
    retry_after: Mapped[float] = mapped_column(Float, default=0)


def _normal(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _tag(node):
    return node.tag.split("}")[-1]


def extract_search_chunks(data):
    """Actual prose and table rows, retaining adjacent text and column context."""
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("document_limit")
    _, roots = validate(data)
    if sum(sum(1 for _ in root.iter()) for root in roots.values()) > 100_000:
        raise ValueError("document_limit")
    chunks, partial = [], False

    def add(value, kind, context=""):
        nonlocal partial
        value = " ".join(value.split())
        context = " ".join(context.split())
        if len(context) > 160:
            partial = True
            context = context[:160]
        if not value:
            return
        prefix = context + "\n" if context and context != value else ""
        width = MAX_CHUNK_CHARS - len(prefix)
        while value:
            if len(chunks) >= MAX_CHUNKS:
                partial = True
                return
            cut = len(value) if len(value) <= width else value.rfind(" ", width // 2, width)
            if cut <= 0:
                cut = width
            chunks.append({"text": prefix + value[:cut], "kind": kind})
            value = value[cut:].lstrip()

    def direct_text(paragraph):
        # Use text runs, not itertext() over a paragraph: that would flatten
        # embedded tables into unrelated body prose and duplicate every cell.
        runs = [node for node in paragraph if _tag(node) == "run"]
        if not runs:
            return paragraph.text or ""
        parts = []
        for run in runs:
            for node in run:
                if _tag(node) != "t":
                    continue
                parts.append(node.text or "")
                for child in node:
                    parts.append(" " if _tag(child) in {"tab", "lineBreak", "nbSpace", "fwSpace"} else "".join(child.itertext()))
                    parts.append(child.tail or "")
        return "".join(parts)

    def table_rows(table, context, depth):
        nonlocal partial
        if depth > 8:
            partial = True
            return
        rows = []
        for row in table:
            if _tag(row) != "tr":
                continue
            cells = []
            for cell in row:
                if _tag(cell) != "tc":
                    continue
                paragraphs = [p for sub in cell if _tag(sub) == "subList" for p in sub if _tag(p) == "p"]
                cells.append(" ".join(direct_text(p) for p in paragraphs))
                for p in paragraphs:
                    for run in p:
                        for child in run:
                            if _tag(child) == "tbl":
                                table_rows(child, context, depth + 1)
            rows.append(" | ".join(cells))
        if rows:
            add(rows[0], "table", context)
            for row in rows[1:]:
                add(row, "table", "\n".join(value for value in (context, rows[0]) if value))

    sections = sorted((name for name in roots if re.fullmatch(r"Contents/section\d+\.xml", name)),
                      key=lambda name: int(re.search(r"section(\d+)", name)[1]))
    for name in sections:
        previous = ""
        for paragraph in roots[name]:
            if _tag(paragraph) != "p":
                continue
            text = direct_text(paragraph)
            add(text, "paragraph")
            # Ordinary HWPX tables are direct run children. Nested tables are
            # handled above; drawings/images are explicitly outside coverage.
            for run in paragraph:
                if _tag(run) != "run":
                    continue
                for node in run:
                    kind = _tag(node)
                    if kind == "tbl":
                        table_rows(node, text or previous, 0)
                    elif kind not in {"t", "secPr", "colPr", "linesegarray"}:
                        if kind == "ctrl" and all(_tag(c) in {"secPr", "colPr"} for c in node):
                            continue
                        partial = True
            if text.strip():
                previous = text
    return chunks, partial


def _scope(db, user):
    from .accounts import UserAccount
    account = db.get(UserAccount, user, populate_existing=True)
    if account is not None and (not account.is_active or account.approval_status != "approved"):
        return false()
    if shared_library(db, user):
        return ~document_is_trashed(Document.id)
    return and_(~document_is_trashed(Document.id), select(Access.document_id).where(
        Access.document_id == Document.id, Access.user_id == user,
        Access.role.in_(("reader", "editor", "owner"))).exists())


def _current_index():
    return and_(DocumentSearchIndex.version_id == Document.current_version,
                DocumentSearchIndex.source_sha256 == Version.sha256,
                DocumentSearchIndex.document_name == Document.name,
                DocumentSearchIndex.status == "ready")


def refresh_search_index(engine, folder, *, max_documents=2, stop_event=None):
    """Coalesce to current versions; bounded extraction/network outside writes."""
    if not _refresh_lock.acquire(blocking=False):
        return {"indexed": 0, "failed": 0, "stale": 0, "busy": True}
    result = {"indexed": 0, "failed": 0, "stale": 0, "busy": False}
    try:
        settings = embedding_settings()
        key = settings.fingerprint if settings else ""
        now = time.time()
        with Session(engine) as db:
            changed = or_(DocumentSearchIndex.document_id.is_(None), DocumentSearchIndex.version_id != Document.current_version,
                          DocumentSearchIndex.source_sha256 != Version.sha256, DocumentSearchIndex.document_name != Document.name)
            retry = and_(DocumentSearchIndex.retry_after <= now, or_(DocumentSearchIndex.status != "ready",
                         DocumentSearchIndex.embedding_fingerprint != key if key else false()))
            selected = list(db.execute(select(Document.id, Document.name, Version.id, Version.sha256, Version.storage_key)
                .join(Version, and_(Version.id == Document.current_version, Version.document_id == Document.id))
                .outerjoin(DocumentSearchIndex, DocumentSearchIndex.document_id == Document.id)
                .where(~document_is_trashed(Document.id), or_(changed, retry))
                .order_by(DocumentSearchIndex.updated_at.asc().nullsfirst(), Document.created_at.asc()).limit(max(1, min(int(max_documents), 10)))))
        for document_id, name, version_id, sha, storage_key in selected:
            if stop_event is not None and stop_event.is_set():
                break
            chunks, vectors, actual_key, status, coverage, retry_after = [], [], "", "ready", "complete", 0
            try:
                root = Path(folder).resolve()
                path = (root / storage_key).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("invalid_blob")
                with path.open("rb") as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES or digest(data) != sha:
                    raise ValueError("invalid_blob")
                chunks, partial = extract_search_chunks(data)
                coverage = "partial" if partial else "complete"
                if settings and chunks:
                    try:
                        vectors = embed_texts(settings, [name + "\n" + chunk["text"] for chunk in chunks])
                        actual_key = key
                    except EmbeddingUnavailable:
                        retry_after = time.time() + 60
                elif settings:
                    actual_key = key
            except Exception:
                # Only a fixed status persists; exception strings may expose
                # document text, filesystem paths or upstream response details.
                chunks, vectors, status, coverage, retry_after = [], [], "failed", "partial", time.time() + 300
            with transaction(engine) as db:
                doc = db.scalar(select(Document).where(Document.id == document_id).with_for_update().execution_options(populate_existing=True))
                version = db.get(Version, version_id)
                if (not doc or doc.current_version != version_id or doc.name != name or not version or version.document_id != doc.id or version.sha256 != sha
                        or db.scalar(select(document_is_trashed(document_id)))):
                    result["stale"] += 1
                    continue
                index = db.get(DocumentSearchIndex, document_id) or DocumentSearchIndex(document_id=document_id)
                index.version_id, index.source_sha256, index.document_name = version_id, sha, name
                index.chunks, index.vectors = json.dumps(chunks, ensure_ascii=False), json.dumps(vectors)
                index.lexical_text = _normal("\n".join(chunk["text"] for chunk in chunks))
                index.embedding_fingerprint, index.status, index.coverage = actual_key, status, coverage
                index.updated_at, index.retry_after = time.time(), retry_after
                db.add(index)
            result["indexed" if status == "ready" else "failed"] += 1
        return result
    finally:
        _refresh_lock.release()


async def search_index_loop(engine, folder, stop_event):
    """Root lifecycle enables this explicitly; no unbounded daemon or task queue."""
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(refresh_search_index, engine, folder, stop_event=stop_event)
        except Exception:
            # Retry bounded database/startup failures without recording payloads.
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5)
        except TimeoutError:
            pass


def _lexical_score(name, text, query, terms):
    title, body = _normal(name), _normal(text)
    title_hits = sum(term in title for term in terms)
    body_hits = sum(term in body for term in terms)
    return 4 * title_hits + body_hits + (8 if query in title else 0) + (2 if query in body else 0)


def _read_chunks(index):
    try:
        chunks = json.loads(index.chunks)
        if (not isinstance(chunks, list) or len(chunks) > MAX_CHUNKS
                or any(not isinstance(c, dict) or not isinstance(c.get("text"), str) or len(c["text"]) > MAX_CHUNK_CHARS for c in chunks)):
            return []
        return chunks
    except (ValueError, TypeError):
        return []


def search_documents(engine, folder, user, query, *, limit=5, offset=0):
    """Return only authorized, current-version excerpts; no write or AI generation."""
    if not isinstance(query, str) or not query.strip() or len(query) > 500 or any(ord(c) < 32 for c in query):
        raise HTTPException(422, "검색어는 1~500자의 한 줄로 입력하세요.")
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or not 0 <= offset <= 1000:
        raise HTTPException(422, "검색 페이지 범위를 확인하세요.")
    normalized = _normal(query)
    terms = list(dict.fromkeys(re.findall(r"[\w]+", normalized)))[:20] or [normalized]
    settings = embedding_settings()
    records, limited, indexing = {}, False, False
    with Session(engine) as db:
        scope = _scope(db, user)
        base = select(Document, Version, DocumentSearchIndex).join(Version, and_(Version.id == Document.current_version, Version.document_id == Document.id)).outerjoin(
            DocumentSearchIndex, DocumentSearchIndex.document_id == Document.id).where(scope).options(defer(DocumentSearchIndex.vectors))
        semantic_pending = and_(DocumentSearchIndex.embedding_fingerprint != settings.fingerprint,
                               DocumentSearchIndex.chunks != "[]") if settings else false()
        indexing = bool(db.scalar(select(Document.id).join(Version, and_(Version.id == Document.current_version, Version.document_id == Document.id)).outerjoin(
            DocumentSearchIndex, DocumentSearchIndex.document_id == Document.id).where(scope, or_(DocumentSearchIndex.document_id.is_(None), ~_current_index(), semantic_pending)).limit(1)))
        match = or_(*[Document.name.icontains(term, autoescape=True) for term in terms],
                    and_(_current_index(), or_(*[DocumentSearchIndex.lexical_text.contains(term, autoescape=True) for term in terms])))
        lexical = list(db.execute(base.where(match).order_by(Document.created_at.desc()).limit(MAX_CANDIDATES + 1)))
        semantic = list(db.execute(base.where(_current_index(), DocumentSearchIndex.embedding_fingerprint == settings.fingerprint)
            .order_by(Document.created_at.desc()).limit(MAX_CANDIDATES + 1))) if settings else []
        limited = len(lexical) > MAX_CANDIDATES or len(semantic) > MAX_CANDIDATES
        for doc, version, index in lexical[:MAX_CANDIDATES] + semantic[:MAX_CANDIDATES]:
            current = bool(index and index.version_id == doc.current_version and index.source_sha256 == version.sha256
                           and index.document_name == doc.name and index.status == "ready")
            records[doc.id] = {"name": doc.name, "version_id": version.id, "sha": version.sha256,
                "chunks": _read_chunks(index) if current else [],
                "key": index.embedding_fingerprint if current else "", "partial": not current or index.coverage != "complete"}
    query_vector, semantic_available = None, False
    if settings and semantic:
        try:
            query_vector = embed_texts(settings, [query])[0]
            semantic_available = True
        except EmbeddingUnavailable:
            pass
    lexical_rank, semantic_rank = [], []
    try:
        threshold = float(os.getenv("HWP_SEARCH_MIN_SIMILARITY", "0.35"))
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            threshold = 0.35
    except ValueError:
        threshold = 0.35
    for document_id, item in records.items():
        chunks = item["chunks"]
        scored = [(i, _lexical_score(item["name"], c["text"], normalized, terms)) for i, c in enumerate(chunks)]
        best, score = max(scored, key=lambda entry: entry[1], default=(-1, _lexical_score(item["name"], "", normalized, terms)))
        item.update(lexical=score, lexical_chunk=best, semantic=0, semantic_chunk=-1)
        if score > 0:
            lexical_rank.append((document_id, score))
    if query_vector:
        # Load one bounded vector document at a time. Keeping every dense vector
        # JSON in the candidate dictionary would multiply memory by corpus size.
        with Session(engine) as db:
            indexes = db.scalars(select(DocumentSearchIndex).where(DocumentSearchIndex.document_id.in_(list(records)),
                DocumentSearchIndex.embedding_fingerprint == settings.fingerprint).execution_options(yield_per=1))
            for index in indexes:
                document_id = index.document_id
                item = records[document_id]
                if index.version_id != item["version_id"] or index.source_sha256 != item["sha"] or index.document_name != item["name"]:
                    indexing = True
                    continue
                try:
                    vectors = json.loads(index.vectors)
                except (ValueError, TypeError):
                    continue
                chunks = item["chunks"]
                if not isinstance(vectors, list) or len(vectors) != len(chunks):
                    continue
                for i, vector in enumerate(vectors):
                    if (not isinstance(vector, list) or len(vector) != len(query_vector)
                            or any(type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 1.000001 for n in vector)):
                        continue
                    similarity = math.fsum(a * b for a, b in zip(vector, query_vector))
                    if similarity > item["semantic"]:
                        item.update(semantic=similarity, semantic_chunk=i)
                if item["semantic_chunk"] >= 0 and item["semantic"] >= threshold:
                    semantic_rank.append((document_id, item["semantic"]))
    ranks = {}
    for ranking in (lexical_rank, semantic_rank):
        for rank, (document_id, _) in enumerate(sorted(ranking, key=lambda entry: (-entry[1], entry[0])), 1):
            ranks[document_id] = ranks.get(document_id, 0) + 1 / (60 + rank)
    # Recheck after model latency. Revoked ACLs, trash, renamed documents and
    # coediting commits must not leak stale excerpts or even their result counts.
    with Session(engine) as db:
        allowed = {doc.id: (doc, version) for doc, version in db.execute(select(Document, Version)
            .join(Version, and_(Version.id == Document.current_version, Version.document_id == Document.id)).where(_scope(db, user), Document.id.in_(list(ranks))))}
        results = []
        for document_id in sorted(ranks, key=lambda identity: (-ranks[identity], identity)):
            item = records[document_id]
            current = allowed.get(document_id)
            if not current or current[0].name != item["name"] or current[1].id != item["version_id"] or current[1].sha256 != item["sha"]:
                indexing = True
                continue
            semantic_match = bool(query_vector and item["semantic_chunk"] >= 0 and item["semantic"] >= threshold)
            chunk_index = item["semantic_chunk"] if semantic_match and not item["lexical"] else item["lexical_chunk"]
            excerpt = item["chunks"][chunk_index]["text"] if chunk_index >= 0 else ""
            results.append({"document_id": document_id, "name": item["name"], "version_id": item["version_id"],
                "excerpt": excerpt, "matched_by": "hybrid" if semantic_match and item["lexical"] else "semantic" if semantic_match else "keyword",
                "content_partial": item["partial"]})
    warnings = []
    if not semantic_available:
        warnings.append("현재는 제목·본문 단어 검색 결과입니다. 의미 검색은 내부 모델 설정과 연결이 필요합니다.")
    if indexing:
        warnings.append("일부 문서의 본문 검색 자료를 갱신 중이며, 이전 버전 본문은 표시하지 않습니다.")
    if limited:
        warnings.append("검색 처리 범위에 도달했습니다. 검색어를 구체화해 주세요. 전체 문서 결과 수가 아닙니다.")
    return {"results": results[offset:offset + limit], "total": len(results), "has_more": offset + limit < len(results),
            "semantic_available": semantic_available, "indexing": indexing, "search_limited": limited, "warning": " ".join(warnings)}
