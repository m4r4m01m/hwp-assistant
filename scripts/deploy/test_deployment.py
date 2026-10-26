"""Offline unit checks. No Docker/real engine/network/model is simulated as success."""
import importlib.util
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))
from app.production import validate_environment
from config import DEFAULT_ALLOWED_HOSTS, check_env, read_env
spec = importlib.util.spec_from_file_location("deploy_init", Path(__file__).with_name("init.py"))
init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init)


def mapping_block(text, key, indent=0):
    """Scope source assertions to one of this Compose file's block mappings."""
    prefix = " " * indent
    match = re.search(r"(?m)^" + prefix + re.escape(key) + r":[ \t]*\n", text)
    if match is None:
        raise AssertionError(f"Missing Compose mapping: {key}")
    return re.split(r"(?m)^" + prefix + r"\S", text[match.end():], maxsplit=1)[0]


class ProductionSettingsTests(unittest.TestCase):
    def good(self):
        return {"HWP_AUTH_TOKENS": json.dumps({"owner": "a" * 48}),
                "DATABASE_URL": "postgresql+psycopg://hwp_assistant:" + "b" * 64 + "@db:5432/hwp_assistant",
                "STORAGE_DIR": "/data/files", "HWP_ALLOWED_HOSTS": DEFAULT_ALLOWED_HOSTS}

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
        for key, value in (("DATABASE_URL", "sqlite:///data/test.db"), ("STORAGE_DIR", "./data/files"),
                           ("HWP_ALLOWED_HOSTS", ""), ("HWP_ALLOWED_HOSTS", "*"),
                           ("HWP_ALLOWED_HOSTS", "https://example.com")):
            with self.assertRaises(ValueError):
                validate_environment({**self.good(), key: value})

    def test_initial_config_valid_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "production.env"
            path.write_text(init.settings("yoseob")); path.chmod(0o600)
            values = check_env(path)
            self.assertIn("yoseob", json.loads(values["HWP_AUTH_TOKENS"]))
            self.assertEqual(len(values["POSTGRES_PASSWORD"]), 64)
            self.assertEqual(values["HWP_ALLOWED_HOSTS"], DEFAULT_ALLOWED_HOSTS)
            self.assertEqual(values["HWP_AUTH_SESSION_TTL_SECONDS"], "28800")
            self.assertEqual(values["HWP_AUTH_SESSION_IDLE_SECONDS"], "1800")
            self.assertEqual(values["HWP_AUTH_SESSION_MAX_PER_USER"], "20")
            self.assertEqual(values["HWP_AI_REQUESTS_PER_MINUTE"], "20")
            self.assertEqual(values["HWP_LLM_ALLOWED_USERS"], "yoseob")
            # Existing deployments can keep their private env file unchanged.
            legacy = "\n".join(line for line in path.read_text().splitlines()
                               if not line.startswith(("HWP_ALLOWED_HOSTS=", "HWP_LLM_ALLOWED_USERS="))) + "\n"
            for suffix in ("", "HWP_ALLOWED_HOSTS=\nHWP_LLM_ALLOWED_USERS=\n"):
                path.write_text(legacy + suffix)
                compatible = check_env(path)
                self.assertEqual(compatible["HWP_ALLOWED_HOSTS"], DEFAULT_ALLOWED_HOSTS)
                self.assertEqual(compatible.get("HWP_LLM_ALLOWED_USERS", ""), "")
            if sys.platform != "win32":
                path.chmod(0o644)
                with self.assertRaises(ValueError): read_env(path)

    def test_settings_never_reuse_secrets(self):
        self.assertNotEqual(init.settings("owner"), init.settings("owner"))

    def test_host_preflight_needs_only_python_standard_library(self):
        # deploy/backup invoke host python3 before Docker; API packages are only
        # installed inside the image. -I -S removes env/user/site-package access.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "production.env"
            configured = init.settings("owner")
            for hosts, expected in ((DEFAULT_ALLOWED_HOSTS, 0), ("https://invalid.example", 1)):
                with self.subTest(hosts=hosts):
                    path.write_text(configured.replace("HWP_ALLOWED_HOSTS=" + DEFAULT_ALLOWED_HOSTS,
                                                       "HWP_ALLOWED_HOSTS=" + hosts))
                    path.chmod(0o600)
                    result = subprocess.run(
                        [sys.executable, "-I", "-S", "-B", str(Path(__file__).with_name("config.py")), str(path)],
                        capture_output=True, text=True, timeout=15,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)
                    if expected == 0:
                        self.assertIn("Production configuration validated", result.stdout)
                    else:
                        self.assertIn("HWP_ALLOWED_HOSTS", result.stderr)
                        self.assertNotIn("Traceback", result.stderr)

    def test_env_is_data_not_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "production.env"
            path.write_text("FOO='$(touch unsafe)'\n"); path.chmod(0o600)
            self.assertEqual(read_env(path)["FOO"], "$(touch unsafe)")

    def test_compose_boundaries(self):
        text = (ROOT / "compose.deploy.yaml").read_text()
        self.assertIn('"127.0.0.1:23000:80"', text)
        self.assertNotIn('"5432:5432"', text)
        self.assertNotIn('"8000:8000"', text)
        self.assertIn('command: ["python", "-m", "app.production"]', text)
        self.assertIn('HWP_REWRITE_PROVIDER: ${HWP_REWRITE_PROVIDER:-codex}', text)
        self.assertIn('codex-state:/home/app/.codex', text)
        self.assertIn('HWP_CODEX_CHECK_AUTH: "1"', text)
        self.assertIn('${HWP_ALLOWED_HOSTS:-' + DEFAULT_ALLOWED_HOSTS + '}', text)
        for key, default in (("HWP_AUTH_SESSION_TTL_SECONDS", "28800"),
                             ("HWP_AUTH_SESSION_IDLE_SECONDS", "1800"),
                             ("HWP_AUTH_SESSION_MAX_PER_USER", "20"),
                             ("HWP_AI_REQUESTS_PER_MINUTE", "20"),
                             ("HWP_LLM_ALLOWED_USERS", "")):
            self.assertIn('${' + key + ':-' + default + '}', text)
        postgres = 'postgres:17.11-alpine@sha256:1bcc18d81671b02906296aad470ec9778a6b71016ce953092024b680bad007b0'
        self.assertIn(postgres, text)
        self.assertIn(postgres, (ROOT / "compose.yaml").read_text())
        self.assertIn('nginx:1.30.6-alpine@sha256:c59d1f8b1e0e42da75be973424cd72e23f7f8adfb54e982f4d1cd0216a5a5576',
                      (ROOT / "compose.ci-gateway.yaml").read_text())

    def test_persistent_volumes_remain_external_with_stable_names(self):
        volumes = mapping_block((ROOT / "compose.deploy.yaml").read_text(), "volumes")
        expected = {"metadata", "documents", "codex-state"}
        self.assertEqual(set(re.findall(r"(?m)^  ([\w-]+):", volumes)), expected)
        for name in sorted(expected):
            with self.subTest(volume=name):
                block = mapping_block(volumes, name, indent=2)
                self.assertRegex(block, r"(?m)^    external: true$")
                self.assertRegex(block, r"(?m)^    name: hwp-assistant-prod-" + re.escape(name) + r"$")

    def test_mattermost_network_is_external_and_only_attached_to_api(self):
        text = (ROOT / "compose.deploy.yaml").read_text()
        networks = mapping_block(text, "networks")
        webhook = mapping_block(networks, "mattermost_hook", indent=2)
        self.assertEqual({line.strip() for line in webhook.splitlines()
                          if line.strip() and not line.lstrip().startswith("#")},
                         {"external: true", "name: hwp-mattermost-hook"})
        self.assertRegex(mapping_block(networks, "data", indent=2), r"(?m)^    internal: true$")
        services = mapping_block(text, "services")
        for service, expected in (("api", {"app", "data", "mattermost_hook"}),
                                  ("db", {"data"}), ("web", {"app"})):
            with self.subTest(service=service):
                block = mapping_block(services, service, indent=2)
                attached = re.search(r"(?m)^    networks: \[([^\]\n]+)\]$", block)
                self.assertIsNotNone(attached, f"Missing networks for {service}")
                self.assertEqual({name.strip() for name in attached.group(1).split(",")}, expected)
                if service in {"api", "db"}:
                    self.assertNotRegex(block, r"(?m)^    ports:")

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

    def test_database_replacement_follows_consistent_backup(self):
        text = (ROOT / "scripts/deploy/deploy.sh").read_text()
        keep_old = text.index('dc up -d --no-build --no-recreate --pull never --wait --wait-timeout 180 db')
        backup = text.index('HWP_BACKUP_RESTART=0 bash "$ROOT/scripts/deploy/backup.sh"')
        replace_db = text.index('if ! dc up -d --no-build --pull never --wait --wait-timeout 180 db; then')
        replace_app = text.index('if ! dc up -d --no-build --pull never --wait --wait-timeout 180 api web; then')
        self.assertLess(text.index('dc pull db'), keep_old)
        self.assertLess(keep_old, backup)
        self.assertLess(backup, replace_db)
        self.assertLess(replace_db, replace_app)
        failure = text[replace_db:replace_app]
        self.assertIn('Application writers remain stopped.', failure)
        self.assertIn('No automatic DB downgrade was attempted.', failure)
        self.assertIn('exit 1', failure)
        self.assertNotIn('dc start', failure)
        backup_script = (ROOT / "scripts/deploy/backup.sh").read_text()
        self.assertIn('|| (( ! completed ))', backup_script)
        self.assertIn('dc start "${running[@]}"', backup_script)


if __name__ == "__main__":
    unittest.main()
