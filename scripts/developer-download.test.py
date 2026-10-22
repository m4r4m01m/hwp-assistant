"""Network-free regression checks for private-release credential redirects."""
import ast
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

# Load only the pure redirect class; the real downloader's main flow must never
# read credentials or access the network in this test.
source = Path(__file__).with_name('download-developer-engine.py').read_text(encoding='utf-8')
tree = ast.parse(source)
redirect_definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'SafeRedirect')
namespace = {'urllib': __import__('urllib')}
exec(compile(ast.Module(body=[redirect_definition], type_ignores=[]), '<redirect-only>', 'exec'), namespace)
SafeRedirect = namespace['SafeRedirect']

class BridgePreflightTests(unittest.TestCase):
    def test_stale_kit_stops_before_credentials_network_or_download_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / 'scripts'
            scripts.mkdir()
            downloader = scripts / 'download-developer-engine.py'
            shutil.copyfile(Path(__file__).with_name(downloader.name), downloader)
            (scripts / 'assistant-bridge.ts.txt').write_text('// current bridge', encoding='utf-8')
            (scripts / 'developer-engine.lock.json').write_text(json.dumps({
                'repository': 'example/repo', 'tag': 'stale-kit', 'asset': 'engine.zip',
                'sha256': 'a' * 64, 'bridge_sha256': 'b' * 64,
            }), encoding='utf-8')
            with patch('sys.argv', [str(downloader)]), \
                 patch('subprocess.run', side_effect=AssertionError('credentials must not be read')), \
                 patch('urllib.request.build_opener', side_effect=AssertionError('network must not be used')):
                with self.assertRaisesRegex(SystemExit, r'does not match.*-BuildEngine Docker'):
                    runpy.run_path(str(downloader), run_name='__main__')
            self.assertFalse((root / '.cache').exists())

    def test_matching_bridge_passes_preflight_without_relaxing_archive_validation(self):
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                          and node.name == 'require_matching_bridge')
        environment = {'hashlib': hashlib}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), '<preflight-only>', 'exec'), environment)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            bridge = b'// current bridge'
            (root / 'scripts/assistant-bridge.ts.txt').write_bytes(bridge)
            environment['require_matching_bridge'](root, {'bridge_sha256': hashlib.sha256(bridge).hexdigest()})
            with self.assertRaises(SystemExit):
                environment['require_matching_bridge'](root, {})

class RedirectTests(unittest.TestCase):
    def request(self):
        return urllib.request.Request('https://api.github.com/repos/example/repo/releases/assets/1',
                                      headers={'Authorization': 'Bearer synthetic-test-secret'})

    def test_github_asset_redirect_removes_api_credential(self):
        redirected = SafeRedirect().redirect_request(self.request(), None, 302, 'Found', {},
                                                     'https://release-assets.githubusercontent.com/example/file.zip')
        self.assertIsNone(redirected.get_header('Authorization'))

    def test_api_same_origin_redirect_can_keep_api_credential(self):
        redirected = SafeRedirect().redirect_request(self.request(), None, 302, 'Found', {},
                                                     'https://api.github.com/repos/example/repo/releases/assets/2')
        self.assertEqual(redirected.get_header('Authorization'), 'Bearer synthetic-test-secret')

    def test_unrelated_hostname_is_rejected(self):
        with self.assertRaises(RuntimeError):
            SafeRedirect().redirect_request(self.request(), None, 302, 'Found', {}, 'https://example.com/asset.zip')

    def test_http_and_nonstandard_ports_are_rejected(self):
        for url in ('http://api.github.com/asset.zip', 'https://api.github.com:8443/asset.zip'):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                SafeRedirect().redirect_request(self.request(), None, 302, 'Found', {}, url)

if __name__ == '__main__':
    unittest.main()
