"""Read-only operational audit against synthetic SQLite/files; no engine or actual model."""
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest
from test_storage import env, create, save, package

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/audit-storage.py"
spec = importlib.util.spec_from_file_location("storage_audit", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def database_path(folder):
    return folder.parent / "test.db"


def state(path):
    return {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in path.rglob("*") if file.is_file()}


def test_audit_preserves_database_and_blobs_and_keeps_unreferenced_files(env):
    client, _, _, folder = env
    doc, lease = create(client)
    assert save(client, doc, lease).status_code == 200
    orphan = folder / f"{uuid.uuid4()}.hwpx"
    orphan.write_bytes(package("참조 없는 합성 후보"))
    before = state(folder.parent)
    result = module.audit(database_path(folder), folder)
    assert result["result"] == "passed" and result["verified_blobs"] == 2
    assert result["documents"] == 1 and result["versions"] == 2
    assert result["unreferenced_files"] == 1 and result["issues"] == []
    assert state(folder.parent) == before
    assert "lease" not in str(result) and "합성 후보" not in str(result)


def test_audit_detects_longer_lineage_cycle(env):
    client, _, _, folder = env
    doc, lease = create(client)
    second = save(client, doc, lease).json()
    with sqlite3.connect(database_path(folder)) as connection:
        connection.execute("UPDATE versions SET base_version=? WHERE id=?", (second["id"], doc["current_version"]))
    result = module.audit(database_path(folder), folder)
    assert result["result"] == "failed"
    assert "version_lineage_cycle" in {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize("fault,code", [("missing", "missing_blob"), ("bytes", "hash_mismatch"),
                                       ("path", "unsafe_storage_key"), ("current", "invalid_current_version"),
                                       ("base", "invalid_base_version")])
def test_audit_reports_corruption_without_repair(env, fault, code):
    client, _, _, folder = env
    doc, _ = create(client)
    db = database_path(folder)
    with sqlite3.connect(db) as connection:
        key = connection.execute("SELECT storage_key FROM versions").fetchone()[0]
        if fault == "missing":
            (folder / key).rename(folder.parent / "removed-synthetic.hwpx")
        elif fault == "bytes":
            (folder / key).write_bytes(b"synthetic corruption")
        elif fault == "path":
            connection.execute("UPDATE versions SET storage_key='../outside.hwpx'")
        elif fault == "current":
            connection.execute("UPDATE documents SET current_version=?", (str(uuid.uuid4()),))
        else:
            other, _ = create(client)
            connection.execute("UPDATE versions SET base_version=? WHERE id=?", (other["current_version"], doc["current_version"]))
    before = state(folder.parent)
    result = module.audit(db, folder)
    assert result["result"] == "failed"
    assert code in {issue["code"] for issue in result["issues"]}
    assert state(folder.parent) == before


def test_missing_source_does_not_create_a_database(tmp_path):
    missing = tmp_path / "does-not-exist.db"
    with pytest.raises(FileNotFoundError):
        module.audit(missing, tmp_path)
    assert not missing.exists()


def test_cli_refuses_existing_report_and_storage_destination(env, tmp_path):
    client, _, _, folder = env
    create(client)
    report = tmp_path / "existing.json"
    report.write_text("keep prior evidence", encoding="utf-8")
    for destination in (report, folder / "report.json"):
        result = subprocess.run([sys.executable, str(SCRIPT), "--database", str(database_path(folder)),
                                 "--storage", str(folder), "--report", str(destination)], capture_output=True, text=True)
        assert result.returncode == 2
    assert report.read_text(encoding="utf-8") == "keep prior evidence"
    assert not (folder / "report.json").exists()
