"""Read-only SQLite + immutable blob audit. Never repairs, migrates, saves or deletes.

Only IDs, counts and error codes are reported; document text, names, auth and leases
are not exported. This is a consistency check, not a backup or HWPX layout test.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import sys
import time
import uuid


def checksum(path):
    hasher = hashlib.sha256()
    before = path.stat()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    after = path.stat()
    stable = (before.st_size, before.st_mtime_ns, before.st_ino) == (after.st_size, after.st_mtime_ns, after.st_ino)
    return size, hasher.hexdigest(), stable


def audit(database, storage):
    database, storage = Path(database).resolve(strict=True), Path(storage).resolve(strict=True)
    if not database.is_file() or not storage.is_dir():
        raise ValueError("A database file and storage directory are required")
    started = time.time()
    # Use SQLite's snapshot API, not a raw copy of a potentially live DB/WAL pair.
    # All expensive checks run against the in-memory copy after releasing the source.
    snapshot = sqlite3.connect(":memory:")
    snapshot.row_factory = sqlite3.Row
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
            source.execute("PRAGMA query_only=ON")
            source.backup(snapshot)
        issues = []

        def issue(code, identifier=None):
            issues.append({"code": code, **({"id": identifier} if identifier is not None else {})})

        tables = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"documents", "versions"} <= tables:
            raise ValueError("Not an HWP Assistant metadata database")
        integrity = [row[0] for row in snapshot.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            issue("sqlite_integrity_failed")
        for row in snapshot.execute("PRAGMA foreign_key_check"):
            # Do not print a full row or schema diagnostic that could contain user data.
            issue("foreign_key_violation", f"{row[0]}:{row[1]}")
        documents = {row["id"]: row for row in snapshot.execute("SELECT id, current_version FROM documents")}
        versions = {row["id"]: row for row in snapshot.execute(
            "SELECT id, document_id, base_version, storage_key, size, sha256 FROM versions")}
        traversed = set()
        for identifier in versions:
            chain, current = set(), identifier
            while current in versions and current not in traversed:
                if current in chain:
                    issue("version_lineage_cycle", current)
                    break
                chain.add(current)
                current = versions[current]["base_version"]
            traversed.update(chain)
        for doc in documents.values():
            current = versions.get(doc["current_version"])
            if not current or current["document_id"] != doc["id"]:
                issue("invalid_current_version", doc["id"])
        verified, total_bytes, referenced = 0, 0, set()
        for version in versions.values():
            identifier, key = version["id"], version["storage_key"]
            if version["document_id"] not in documents:
                issue("missing_document", identifier)
            if version["base_version"]:
                base = versions.get(version["base_version"])
                if not base or base["document_id"] != version["document_id"] or base["id"] == identifier:
                    issue("invalid_base_version", identifier)
            try:
                if not isinstance(key, str) or not key.endswith(".hwpx") or str(uuid.UUID(key[:-5])) != key[:-5].lower():
                    raise ValueError("unsafe key")
                path = storage / key
                if path.is_symlink() or path.resolve().parent != storage:
                    raise ValueError("unsafe path")
                attributes = getattr(path.lstat(), "st_file_attributes", 0)
                if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                    raise ValueError("reparse point")
            except FileNotFoundError:
                issue("missing_blob", identifier)
                referenced.add(key)
                continue
            except (ValueError, TypeError, OSError):
                issue("unsafe_storage_key", identifier)
                continue
            referenced.add(key)
            if not path.is_file():
                issue("blob_not_file", identifier)
                continue
            try:
                size, digest, stable = checksum(path)
            except OSError:
                issue("unreadable_blob", identifier)
                continue
            valid = True
            for condition, code in ((stable, "blob_changed_during_read"), (size == version["size"], "size_mismatch"),
                                    (digest == version["sha256"], "hash_mismatch")):
                if not condition:
                    issue(code, identifier)
                    valid = False
            if valid:
                verified += 1
                total_bytes += size
        # FK constraints do not enforce that related objects belong to the same document.
        checks = [
            ("templates", "SELECT t.id FROM templates t JOIN versions v ON v.id=t.version_id WHERE t.document_id<>v.document_id", "template_document_mismatch"),
            ("rewrite_tasks", "SELECT t.id FROM rewrite_tasks t JOIN versions v ON v.id=t.base_version JOIN conversations c ON c.id=t.conversation_id WHERE t.document_id<>v.document_id OR t.document_id<>c.document_id", "task_document_mismatch"),
            ("rewrite_applications", "SELECT a.id FROM rewrite_applications a JOIN rewrite_candidates c ON c.id=a.candidate_id WHERE a.task_id<>c.task_id", "application_candidate_mismatch"),
            ("rewrite_applications", "SELECT a.id FROM rewrite_applications a JOIN rewrite_tasks t ON t.id=a.task_id JOIN versions v ON v.id=a.saved_version WHERE v.document_id<>t.document_id OR v.sha256<>a.export_sha256 OR a.export_sha256 IS NULL OR a.status<>'saved'", "application_saved_version_mismatch"),
            ("rewrite_applications", "SELECT id FROM rewrite_applications WHERE status='saved' AND saved_version IS NULL", "application_saved_version_missing"),
        ]
        for table, sql, code in checks:
            if table in tables:
                for row in snapshot.execute(sql):
                    issue(code, row[0])
        unreferenced = sum(1 for path in storage.iterdir() if path.is_file() and path.name not in referenced)
        return {"kind": "read-only SQLite/blob audit", "result": "failed" if issues else "passed",
                "started_at": started, "finished_at": time.time(), "documents": len(documents),
                "versions": len(versions), "verified_blobs": verified, "verified_bytes": total_bytes,
                "issues": issues, "unreferenced_files": unreferenced,
                "unreferenced_note": "Not deleted. May include quarantined, interrupted or concurrent writes.",
                "limits": ["SQLite metadata snapshot; blobs are read afterwards and expected immutable",
                           "No HWPX layout/content semantics, real model, PostgreSQL or backup/restore validation"]}
    finally:
        snapshot.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--storage", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Optional NEW JSON path outside the source storage; never overwrites")
    args = parser.parse_args()
    try:
        if args.report:
            destination = args.report.resolve()
            if destination.exists() or destination == args.database.resolve() or args.storage.resolve() in destination.parents:
                raise ValueError("Report must be new and outside the source database/storage")
        result = audit(args.database, args.storage)
        if args.report:
            with args.report.open("x", encoding="utf-8") as report:
                json.dump(result, report, ensure_ascii=False, indent=2)
                report.write("\n")
        print(json.dumps({key: value for key, value in result.items() if key not in ("issues", "limits")}, ensure_ascii=False))
        return 0 if result["result"] == "passed" else 1
    except (OSError, sqlite3.Error, ValueError) as exc:
        # No raw SQL, connection details, document text, or filesystem paths in console errors.
        print(json.dumps({"result": "error", "error_type": type(exc).__name__,
                          "error": "Audit could not complete. Check input paths/schema and choose a new report path."}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
