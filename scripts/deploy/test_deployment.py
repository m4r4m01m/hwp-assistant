"""Offline unit checks. No Docker/real engine/network/model is simulated as success."""
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))
from app.production import validate_environment
from config import check_env, read_env
spec = importlib.util.spec_from_file_location("deploy_init", Path(__file__).with_name("init.py"))
init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init)


class ProductionSettingsTests(unittest.TestCase):
    def good(self):
        return {"HWP_AUTH_TOKENS": json.dumps({"owner": "a" * 48}),
                "DATABASE_URL": "postgresql+psycopg://hwp_assistant:" + "b" * 64 + "@db:5432/hwp_assistant",
                "STORAGE_DIR": "/data/files"}

    def test_valid(self):
        self.assertEqual(list(validate_environment(self.good())), ["owner"])

    def test_missing_auth(self):
        for raw in ("", "{}", "[]", "null", "invalid", '{"owner":"short"}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_environment({**self.good(), "HWP_AUTH_TOKENS": raw})

    def test_duplicate_or_reserved_tokens(self):
        for users in ({"a": "a" * 40, "b": "a" * 40}, {"a": "hwp-run-" + "a" * 40}):
            with self.assertRaises(ValueError):
                validate_environment({**self.good(), "HWP_AUTH_TOKENS": json.dumps(users)})

    def test_no_sqlite_or_unmounted_path(self):
        for key, value in (("DATABASE_URL", "sqlite:///data/test.db"), ("STORAGE_DIR", "./data/files")):
            with self.assertRaises(ValueError):
                validate_environment({**self.good(), key: value})

    def test_initial_config_valid_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "production.env"
            path.write_text(init.settings("yoseob")); path.chmod(0o600)
            values = check_env(path)
            self.assertIn("yoseob", json.loads(values["HWP_AUTH_TOKENS"]))
            self.assertEqual(len(values["POSTGRES_PASSWORD"]), 64)
            if sys.platform != "win32":
                path.chmod(0o644)
                with self.assertRaises(ValueError): read_env(path)

    def test_settings_never_reuse_secrets(self):
        self.assertNotEqual(init.settings("owner"), init.settings("owner"))

    def test_env_is_data_not_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "production.env"
            path.write_text("FOO='$(touch unsafe)'\n"); path.chmod(0o600)
            self.assertEqual(read_env(path)["FOO"], "$(touch unsafe)")

    def test_compose_boundaries(self):
        text = (ROOT / "compose.deploy.yaml").read_text()
        self.assertIn('"127.0.0.1:23000:80"', text)
        self.assertEqual(text.count("external: true"), 2)
        self.assertNotIn('"5432:5432"', text)
        self.assertNotIn('"8000:8000"', text)
        self.assertIn('command: ["python", "-m", "app.production"]', text)
        self.assertIn('HWP_REWRITE_PROVIDER: llm', text)

    def test_cookie_and_proxy_contract(self):
        text = (ROOT / "deploy/nginx.hwp-assistant.conf").read_text()
        self.assertIn("proxy_cookie_path /api /hwp-assistant/api;", text)
        self.assertIn("proxy_set_header X-Forwarded-Proto $hwp_forwarded_proto;", text)
        self.assertIn("resolver 127.0.0.11", text)
        host = (ROOT / "deploy/host-nginx-location.conf.example").read_text()
        self.assertIn("proxy_pass http://127.0.0.1:23000/;", host)
        self.assertNotIn("include /etc/nginx/proxy_params;", host)

    def test_first_backup_mounts_the_image_storage_path(self):
        text = (ROOT / "scripts/deploy/backup.sh").read_text()
        self.assertIn("dst=/data/files,readonly", text)
        self.assertIn("-C /data/files -czf - .", text)
        self.assertNotIn("dst=/documents", text)

    def test_no_destructive_update_command(self):
        for name in ("deploy.sh", "backup.sh"):
            text = (ROOT / "scripts/deploy" / name).read_text()
            for forbidden in ("down -v", "volume rm", "system prune", "reset --hard"):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
