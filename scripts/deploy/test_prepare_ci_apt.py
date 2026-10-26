"""Offline source transformations and guards; never invokes sudo or APT."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("prepare-ci-apt.py")
SPEC = importlib.util.spec_from_file_location("prepare_ci_apt", SCRIPT)
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)
ENV = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux"}
SOURCE = "Types: deb\nURIs: http://azure.archive.ubuntu.com/ubuntu/\nSuites: noble noble-updates noble-backports noble-security\nComponents: main restricted universe multiverse\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"


class PrepareAptTests(unittest.TestCase):
    def fixture(self, root, source=SOURCE, release='ID=ubuntu\nVERSION_ID="24.04"\n'):
        (root / "etc/apt/sources.list.d").mkdir(parents=True)
        (root / "etc/apt/apt.conf.d").mkdir()
        (root / "etc/os-release").write_text(release)
        source_path = root / "etc/apt/sources.list.d/ubuntu.sources"
        source_path.write_text(source)
        return source_path

    def test_deb822_only_uri_changes_and_trust_fields_stay_exact(self):
        self.assertEqual(PREPARE.rewrite_sources(SOURCE), SOURCE.replace("http://azure.archive.ubuntu.com", "https://archive.ubuntu.com"))

    def test_github_runner_mirror_reference_changes_but_list_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(PREPARE.platform, "machine", return_value="x86_64"):
            root = Path(directory)
            original = SOURCE.replace("http://azure.archive.ubuntu.com/ubuntu/", "mirror+file:/etc/apt/apt-mirrors.txt").replace("\n", "\r\n")
            source = self.fixture(root)
            source.write_bytes(original.encode("utf-8"))
            mirror = root / "etc/apt/apt-mirrors.txt"
            mirror.write_bytes(b"http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\nhttps://archive.ubuntu.com/ubuntu/\tpriority:2\nhttps://security.ubuntu.com/ubuntu/\tpriority:3\n")
            before = mirror.read_bytes()
            PREPARE.prepare(root, ENV)
            expected = original.replace("mirror+file:/etc/apt/apt-mirrors.txt", "https://archive.ubuntu.com/ubuntu/")
            self.assertEqual(source.read_bytes(), expected.encode("utf-8"))
            self.assertEqual(mirror.read_bytes(), before)

    def test_other_mirror_references_and_suffixes_are_not_rewritten(self):
        for uri in ("mirror+file:/etc/apt/apt-mirrors-security.txt", "mirror+file:/etc/apt/apt-mirrors.txt.other",
                    "mirror+file:/etc/apt/private-mirrors.txt", "mirror+https://example.invalid/ubuntu"):
            with self.subTest(uri=uri):
                source = "URIs: " + uri + "\n"
                self.assertEqual(PREPARE.rewrite_sources(source), source)

    def test_legacy_urls_keep_options_security_pocket_and_unrelated_hosts(self):
        original = ("deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://azure.archive.ubuntu.com/ubuntu noble main\n"
                    "deb http://archive.ubuntu.com/ubuntu noble-updates universe\n"
                    "deb http://security.ubuntu.com/ubuntu noble-security main\n"
                    "deb https://packages.microsoft.com/repos/code stable main\n"
                    "deb https://archive.ubuntu.com/ubuntu-other noble main\n")
        expected = original.replace("https://azure.archive.ubuntu.com/", "https://archive.ubuntu.com/").replace(
            "http://archive.ubuntu.com/", "https://archive.ubuntu.com/").replace("http://security.ubuntu.com/", "https://security.ubuntu.com/")
        self.assertEqual(PREPARE.rewrite_sources(original), expected)

    def test_prepare_is_idempotent_and_does_not_touch_other_source_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(PREPARE.platform, "machine", return_value="x86_64"):
            root = Path(directory); source = self.fixture(root)
            third_party = root / "etc/apt/sources.list.d/vendor.list"
            third_party.write_text("deb https://example.invalid stable main\n")
            PREPARE.prepare(root, ENV)
            first = source.read_bytes()
            PREPARE.prepare(root, ENV)
            self.assertEqual(source.read_bytes(), first)
            self.assertEqual(third_party.read_text(), "deb https://example.invalid stable main\n")
            config = (root / "etc/apt/apt.conf.d/99hwp-ci-network").read_text()
            self.assertEqual(config, PREPARE.APT_NETWORK)
            self.assertNotRegex(config, r"(?i)trust|insecure|verify|authenticate")

    def test_non_hosted_non_linux_and_non_actions_environments_are_rejected_before_writes(self):
        for changes in ({"RUNNER_ENVIRONMENT": "self-hosted"}, {"RUNNER_OS": "Windows"}, {"GITHUB_ACTIONS": "false"}, {}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source = self.fixture(root)
                before = source.read_bytes()
                with self.assertRaises(ValueError):
                    PREPARE.prepare(root, {**ENV, **changes} if changes else {})
                self.assertEqual(source.read_bytes(), before)
                self.assertFalse((root / "etc/apt/apt.conf.d/99hwp-ci-network").exists())

    def test_debian_other_ubuntu_and_other_architectures_are_rejected(self):
        for release, arch in (("ID=debian\nVERSION_ID=12\n", "x86_64"), ("ID=ubuntu\nVERSION_ID=22.04\n", "x86_64"),
                              ('ID=ubuntu\nVERSION_ID="24.04"\n', "aarch64")):
            with self.subTest(release=release, arch=arch), tempfile.TemporaryDirectory() as directory, patch.object(PREPARE.platform, "machine", return_value=arch):
                root = Path(directory); source = self.fixture(root, release=release)
                with self.assertRaises(ValueError): PREPARE.prepare(root, ENV)
                self.assertEqual(source.read_text(), SOURCE)
                self.assertFalse((root / "etc/apt/apt.conf.d/99hwp-ci-network").exists())

    def test_unknown_source_layout_fails_without_config_changes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(PREPARE.platform, "machine", return_value="x86_64"):
            root = Path(directory); self.fixture(root, "deb https://example.invalid/ubuntu noble main\n")
            with self.assertRaises(ValueError): PREPARE.prepare(root, ENV)
            self.assertFalse((root / "etc/apt/apt.conf.d/99hwp-ci-network").exists())


if __name__ == "__main__":
    unittest.main()
