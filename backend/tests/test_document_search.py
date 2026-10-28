"""Isolated synthetic search data: authorization, current version and transport."""
import asyncio
import io
import json
import logging
import socket
import threading
import time
import uuid
import zipfile
from xml.sax.saxutils import escape

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import document_search as search
from app import document_search_embeddings as embeddings
from app.accounts import UserAccount
from app.db import Access, Base, Document, DocumentTrash, Version, make_engine, transaction
from app.hwpx import digest


def hwpx(text="장애 발생 시 복구 담당자에게 연락함.", *, table=False):
    def paragraph(value):
        return f"<p><run><t>{escape(value)}</t></run></p>"
    section = paragraph(text)
    if table:
        section += paragraph("시스템 복구 일정") + "<p><run><tbl>"
        for row in (("시스템", "담당", "기한"), ("결제서버", "운영팀", "10월 30일")):
            section += "<tr>" + "".join("<tc><subList>" + paragraph(cell) + "</subList></tc>" for cell in row) + "</tr>"
        section += "</tbl></run></p>"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in {"mimetype": "application/hwp+zip", "Contents/header.xml": "<head/>",
                            "Contents/content.hpf": "<package/>", "Contents/section0.xml": "<section>" + section + "</section>"}.items():
            archive.writestr(name, value)
    return output.getvalue()


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    for key in ("HWP_SEARCH_EMBEDDING_URL", "HWP_SEARCH_EMBEDDING_MODEL", "HWP_SEARCH_EMBEDDING_ALLOW_HTTP", "HWP_SEARCH_MIN_SIMILARITY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "private")
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "0")
    engine = make_engine("sqlite:///" + str(tmp_path / "search.db"))
    Base.metadata.create_all(engine)
    folder = tmp_path / "files"
    folder.mkdir()
    yield engine, folder
    engine.dispose()


def document(corpus, name="운영 매뉴얼.hwpx", text="장애 발생 시 복구 담당자에게 연락함.", owner="alice", *, table=False):
    engine, folder = corpus
    document_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    data = hwpx(text, table=table)
    key = version_id + ".hwpx"
    (folder / key).write_bytes(data)
    with transaction(engine) as db:
        db.add(Document(id=document_id, name=name, current_version=version_id, created_at=time.time(), lease_until=0))
        db.flush()
        db.add(Version(id=version_id, document_id=document_id, storage_key=key, size=len(data), sha256=digest(data),
                       created_at=time.time(), cause="upload", request_id=version_id, fingerprint="synthetic", session_hash="synthetic"))
        db.add(Access(document_id=document_id, user_id=owner, role="owner"))
    return document_id, version_id


def new_version(corpus, document_id, text):
    engine, folder = corpus
    version_id = str(uuid.uuid4())
    data = hwpx(text)
    key = version_id + ".hwpx"
    (folder / key).write_bytes(data)
    with transaction(engine) as db:
        doc = db.get(Document, document_id)
        db.add(Version(id=version_id, document_id=document_id, base_version=doc.current_version, storage_key=key,
            size=len(data), sha256=digest(data), created_at=time.time(), cause="web-edit", request_id=version_id,
            fingerprint="synthetic", session_hash="synthetic"))
        doc.current_version = version_id
    return version_id


def account(engine, user, *, active=True, approved=True):
    with transaction(engine) as db:
        db.add(UserAccount(user_id=user, password_hash="synthetic-unused", name="합성 계정", kind="member", is_active=active,
            approval_status="approved" if approved else "pending", session_revision="synthetic", created_at=time.time()))


def configure(monkeypatch):
    monkeypatch.setenv("HWP_SEARCH_EMBEDDING_URL", "http://models.internal/v1/embeddings")
    monkeypatch.setenv("HWP_SEARCH_EMBEDDING_MODEL", "synthetic-embedding")
    monkeypatch.setenv("HWP_SEARCH_EMBEDDING_ALLOW_HTTP", "1")


def test_current_body_search_and_no_implicit_model(corpus, monkeypatch):
    doc, version = document(corpus)
    monkeypatch.setattr(embeddings.socket, "getaddrinfo", lambda *a, **k: pytest.fail("unconfigured search made a network request"))
    assert search.refresh_search_index(*corpus)["indexed"] == 1
    result = search.search_documents(*corpus, "alice", "복구")
    assert result["total"] == 1 and result["semantic_available"] is False
    assert result["results"][0]["document_id"] == doc
    assert result["results"][0]["version_id"] == version
    assert "복구 담당자" in result["results"][0]["excerpt"]
    assert result["results"][0]["matched_by"] == "keyword"
    assert result["indexing"] is False
    assert search.refresh_search_index(*corpus)["indexed"] == 0
    monkeypatch.setenv("HWP_SEARCH_MIN_SIMILARITY", "0")
    assert search.search_documents(*corpus, "alice", "복구")["results"][0]["matched_by"] == "keyword"


def test_private_acl_shared_approval_and_trash_precede_snippets(corpus, monkeypatch):
    engine, _ = corpus
    doc, _ = document(corpus, text="매우비밀인문구")
    search.refresh_search_index(*corpus)
    assert search.search_documents(*corpus, "bob", "매우비밀인문구")["total"] == 0
    account(engine, "bob")
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "1")
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "shared")
    assert search.search_documents(*corpus, "bob", "매우비밀인문구")["total"] == 1
    with transaction(engine) as db:
        db.get(UserAccount, "bob").approval_status = "pending"
    assert search.search_documents(*corpus, "bob", "매우비밀인문구")["results"] == []
    with transaction(engine) as db:
        db.get(UserAccount, "bob").approval_status = "approved"
        db.add(DocumentTrash(id=str(uuid.uuid4()), document_id=doc, deleted_by="alice", deleted_at=time.time()))
    assert search.search_documents(*corpus, "alice", "매우비밀인문구")["total"] == 0
    assert search.search_documents(*corpus, "bob", "매우비밀인문구")["total"] == 0


def test_table_excerpt_keeps_caption_and_columns(corpus):
    document(corpus, text="보고 대상 시스템", table=True)
    search.refresh_search_index(*corpus)
    result = search.search_documents(*corpus, "alice", "결제서버")
    excerpt = result["results"][0]["excerpt"]
    assert all(value in excerpt for value in ("시스템 복구 일정", "시스템", "담당", "기한", "결제서버", "운영팀", "10월 30일"))


def test_hwpx_inline_spaces_and_layout_cache_preserve_complete_text():
    raw = hwpx("placeholder")
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(out, "w") as archive:
        for name in original.namelist():
            content = (b"<section><p><run><t>Alpha<lineBreak/>Beta<tab/>Gamma<nbSpace/>Delta</t></run>"
                       b"<linesegarray><lineseg textpos='0'/></linesegarray></p></section>"
                       if name == "Contents/section0.xml" else original.read(name))
            archive.writestr(name, content)
    chunks, partial = search.extract_search_chunks(out.getvalue())
    assert chunks == [{"text": "Alpha Beta Gamma Delta", "kind": "paragraph"}]
    assert partial is False


def test_old_index_never_answers_for_new_version(corpus):
    doc, _ = document(corpus, text="구버전삭제문구")
    search.refresh_search_index(*corpus)
    current = new_version(corpus, doc, "최신확정문구")
    old = search.search_documents(*corpus, "alice", "구버전삭제문구")
    assert old["results"] == [] and old["indexing"] is True
    search.refresh_search_index(*corpus)
    result = search.search_documents(*corpus, "alice", "최신확정문구")
    assert result["results"][0]["version_id"] == current
    assert search.search_documents(*corpus, "alice", "구버전삭제문구")["total"] == 0


def test_current_version_pointer_cannot_cross_document_acl(corpus):
    visible, _ = document(corpus, name="공개 대상.hwpx", owner="alice")
    _, private_version = document(corpus, name="비공개.hwpx", text="다른사람비밀", owner="bob")
    with transaction(corpus[0]) as db:
        db.get(Document, visible).current_version = private_version
    search.refresh_search_index(*corpus)
    assert search.search_documents(*corpus, "alice", "다른사람비밀")["total"] == 0
    assert search.search_documents(*corpus, "alice", "공개 대상")["total"] == 0


def test_coedit_commit_during_extraction_discards_stale_result(corpus, monkeypatch):
    doc, _ = document(corpus, text="이전내용")
    original = search.extract_search_chunks
    def changes_during_extract(raw):
        new_version(corpus, doc, "새저장내용")
        return original(raw)
    monkeypatch.setattr(search, "extract_search_chunks", changes_during_extract)
    result = search.refresh_search_index(*corpus)
    assert result["stale"] == 1 and result["indexed"] == 0
    with Session(corpus[0]) as db:
        assert db.get(search.DocumentSearchIndex, doc) is None
    monkeypatch.setattr(search, "extract_search_chunks", original)
    assert search.refresh_search_index(*corpus)["indexed"] == 1
    assert search.search_documents(*corpus, "alice", "새저장내용")["total"] == 1


def test_sha_fence_and_retry_backoff_preserve_original_blob(corpus):
    doc, version = document(corpus)
    blob = corpus[1] / (version + ".hwpx")
    before = blob.read_bytes()
    with transaction(corpus[0]) as db:
        db.get(Version, version).sha256 = "0" * 64
    assert search.refresh_search_index(*corpus)["failed"] == 1
    assert search.refresh_search_index(*corpus)["failed"] == 0
    assert search.search_documents(*corpus, "alice", "복구")["results"] == []
    assert blob.read_bytes() == before


@pytest.mark.parametrize("change", ["revoke", "trash", "version"])
def test_semantic_relation_and_access_version_recheck_after_model(corpus, monkeypatch, change):
    engine, _ = corpus
    doc, _ = document(corpus, text="장애 발생 시 복구 담당자에게 연락함.")
    configure(monkeypatch)
    monkeypatch.setattr(search, "embed_texts", lambda settings, texts: [[1.0, 0.0] for _ in texts])
    search.refresh_search_index(*corpus)
    result = search.search_documents(*corpus, "alice", "서비스가 멈췄어요")
    assert result["semantic_available"] is True
    assert result["results"][0]["matched_by"] == "semantic"
    def revoke_during_query(settings, texts):
        if change == "version":
            new_version(corpus, doc, "최신본에 이전 복구 내용 없음")
        else:
            with transaction(engine) as db:
                if change == "revoke":
                    db.delete(db.get(Access, (doc, "alice")))
                else:
                    db.add(DocumentTrash(id=str(uuid.uuid4()), document_id=doc, deleted_by="alice", deleted_at=time.time()))
        return [[1.0, 0.0]]
    monkeypatch.setattr(search, "embed_texts", revoke_during_query)
    result = search.search_documents(*corpus, "alice", "서비스가 멈췄어요")
    assert result["results"] == [] and result["total"] == 0


def test_changed_embedding_model_never_reuses_old_vectors(corpus, monkeypatch):
    document(corpus)
    configure(monkeypatch)
    monkeypatch.setattr(search, "embed_texts", lambda settings, texts: [[1.0, 0.0] for _ in texts])
    search.refresh_search_index(*corpus)
    monkeypatch.setenv("HWP_SEARCH_EMBEDDING_MODEL", "different-synthetic-model")
    waiting = search.search_documents(*corpus, "alice", "복구")
    assert waiting["indexing"] and not waiting["semantic_available"]
    assert waiting["results"][0]["matched_by"] == "keyword"
    search.refresh_search_index(*corpus)
    assert search.search_documents(*corpus, "alice", "서비스가 멈췄어요")["results"][0]["matched_by"] == "semantic"


def test_extraction_limit_marks_partial_coverage(corpus):
    document(corpus, text="복구 절차의 합성 설명. " * 6000)
    search.refresh_search_index(*corpus)
    result = search.search_documents(*corpus, "alice", "복구")
    assert result["results"][0]["content_partial"] is True
    with Session(corpus[0]) as db:
        index = db.scalar(select(search.DocumentSearchIndex))
        assert len(json.loads(index.chunks)) == search.MAX_CHUNKS


def test_disabled_registered_account_cannot_use_historical_acl(corpus):
    document(corpus)
    account(corpus[0], "alice", active=False)
    search.refresh_search_index(*corpus)
    result = search.search_documents(*corpus, "alice", "복구")
    assert result["total"] == 0 and result["indexing"] is False


def test_semantic_failure_keeps_keyword_results(corpus, monkeypatch):
    document(corpus)
    configure(monkeypatch)
    def unavailable(*args):
        raise embeddings.EmbeddingUnavailable("embedding_unavailable")
    monkeypatch.setattr(search, "embed_texts", unavailable)
    assert search.refresh_search_index(*corpus)["indexed"] == 1
    result = search.search_documents(*corpus, "alice", "복구")
    assert result["total"] == 1 and result["semantic_available"] is False
    assert search.refresh_search_index(*corpus)["indexed"] == 0


def test_query_limits_and_literal_sql_wildcards(corpus):
    document(corpus)
    search.refresh_search_index(*corpus)
    assert search.search_documents(*corpus, "alice", "%")["results"] == []
    for query in ("", "x" * 501, "secret\nquery"):
        with pytest.raises(Exception) as exc:
            search.search_documents(*corpus, "alice", query)
        assert exc.value.status_code == 422


def test_capped_search_is_explicit_and_pages_deduplicate_documents(corpus, monkeypatch):
    for i in range(4):
        document(corpus, name=f"복구 문서 {i}.hwpx")
    search.refresh_search_index(*corpus, max_documents=10)
    monkeypatch.setattr(search, "MAX_CANDIDATES", 3)
    first = search.search_documents(*corpus, "alice", "복구", limit=2)
    second = search.search_documents(*corpus, "alice", "복구", limit=2, offset=2)
    assert first["search_limited"] and "전체 문서 결과 수가 아닙니다" in first["warning"]
    assert first["total"] == 3 and first["has_more"] and not second["has_more"]
    assert len({r["document_id"] for r in first["results"] + second["results"]}) == 3


def test_loop_stops_without_indexing_when_already_stopped(corpus, monkeypatch):
    monkeypatch.setattr(search, "refresh_search_index", lambda *a: pytest.fail("already stopped"))
    async def run():
        event = asyncio.Event()
        event.set()
        await search.search_index_loop(*corpus, event)
    asyncio.run(run())


@pytest.mark.parametrize("url", ["https://u:p@models.internal/v1/embeddings", "https://models.internal/v1/embeddings?q=secret",
    "https://models.internal/v1/embeddings#secret", "file:///v1/embeddings", "http://localhost/v1/embeddings", "https://models.internal/v1/chat/completions"])
def test_embedding_config_rejects_unsafe_urls(monkeypatch, url):
    monkeypatch.setenv("HWP_SEARCH_EMBEDDING_URL", url)
    monkeypatch.setenv("HWP_SEARCH_EMBEDDING_MODEL", "synthetic")
    monkeypatch.delenv("HWP_SEARCH_EMBEDDING_ALLOW_HTTP", raising=False)
    assert embeddings.embedding_settings() is None


def mock_transport(monkeypatch, handler, addresses=("127.0.0.1",)):
    configure(monkeypatch)
    monkeypatch.setattr(embeddings.socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80)) for address in addresses])
    client = httpx.Client
    monkeypatch.setattr(embeddings.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    return embeddings.embedding_settings()


def test_embedding_pins_internal_address_normalizes_vectors_and_redacts_logs(monkeypatch, caplog):
    seen = []
    def handler(request):
        seen.append(request)
        assert request.url.host == "127.0.0.1" and request.headers["host"] == "models.internal"
        assert request.headers["accept-encoding"] == "identity"
        assert request.extensions["sni_hostname"] == "models.internal"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 7]}, {"index": 0, "embedding": [3, 4]}]})
    settings = mock_transport(monkeypatch, handler)
    caplog.set_level(logging.DEBUG)
    assert embeddings.embed_texts(settings, ["비공개문장A", "비공개문장B"]) == [[0.6, 0.8], [0.0, 1.0]]
    assert len(seen) == 1
    assert "models.internal" not in caplog.text and "비공개문장" not in caplog.text


@pytest.mark.parametrize("addresses", [("8.8.8.8",), ("127.0.0.1", "8.8.8.8"), ("169.254.169.254",), ("0.0.0.0",)])
def test_public_mixed_metadata_and_unspecified_targets_rejected(monkeypatch, addresses):
    settings = mock_transport(monkeypatch, lambda request: pytest.fail("request must not leave internal addresses"), addresses)
    with pytest.raises(embeddings.EmbeddingUnavailable, match="internal_endpoint_required"):
        embeddings.embed_texts(settings, ["private"])


@pytest.mark.parametrize("data", [[{"index": 0, "embedding": [0, 0]}], [{"index": 0, "embedding": [True, 1]}],
    [{"index": 0, "embedding": [1] * 2049}], [{"index": 1, "embedding": [1, 0]}],
    [{"index": 0, "embedding": [1e20, 1]}], [{"index": 0, "embedding": "not-a-vector"}]])
def test_invalid_embedding_contract(monkeypatch, data):
    settings = mock_transport(monkeypatch, lambda request: httpx.Response(200, json={"data": data}))
    with pytest.raises(embeddings.EmbeddingUnavailable):
        embeddings.embed_texts(settings, ["private"])


def test_embedding_redirect_is_never_followed(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(307, headers={"location": "https://public.example/v1/embeddings"})
    settings = mock_transport(monkeypatch, handler)
    with pytest.raises(embeddings.EmbeddingUnavailable):
        embeddings.embed_texts(settings, ["private"])
    assert len(seen) == 1


def test_embedding_response_size_and_nonfinite_vectors_rejected(monkeypatch):
    settings = mock_transport(monkeypatch, lambda request: httpx.Response(200, content=b"x" * 100))
    monkeypatch.setattr(embeddings, "MAX_RESPONSE", 50)
    with pytest.raises(embeddings.EmbeddingUnavailable, match="response_limit"):
        embeddings.embed_texts(settings, ["private"])
    with pytest.raises(embeddings.EmbeddingUnavailable, match="invalid_vector"):
        embeddings.normalize_vector([float("nan"), 1])
    with pytest.raises(embeddings.EmbeddingUnavailable, match="invalid_vector"):
        embeddings.normalize_vector([float("inf"), 1])


def test_internal_ipv6_loopback_is_supported():
    assert embeddings._internal_address("::1")
    assert embeddings._internal_address("fd00::1")
    assert not embeddings._internal_address("fe80::1")


def test_embedding_dns_wait_is_bounded(monkeypatch):
    configure(monkeypatch)
    release = threading.Event()
    monkeypatch.setattr(embeddings, "DNS_TIMEOUT", .01)
    monkeypatch.setattr(embeddings.socket, "getaddrinfo", lambda *a, **k: release.wait(1))
    try:
        start = time.monotonic()
        with pytest.raises(embeddings.EmbeddingUnavailable):
            embeddings.embed_texts(embeddings.embedding_settings(), ["private"])
        assert time.monotonic() - start < .5
    finally:
        release.set()


def test_embedding_unexpected_compression_rejected_before_read(monkeypatch):
    class NeverRead(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("compressed response must be rejected before decoding")
            yield b""
    settings = mock_transport(monkeypatch, lambda request: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=NeverRead()))
    with pytest.raises(embeddings.EmbeddingUnavailable):
        embeddings.embed_texts(settings, ["private"])


def test_shutdown_stops_before_second_document(corpus, monkeypatch):
    document(corpus)
    document(corpus)
    event = threading.Event()
    original = search.extract_search_chunks
    def stop_after_first(raw):
        result = original(raw)
        event.set()
        return result
    monkeypatch.setattr(search, "extract_search_chunks", stop_after_first)
    assert search.refresh_search_index(*corpus, max_documents=2, stop_event=event)["indexed"] == 1
