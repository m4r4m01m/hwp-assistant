"""Network-free regression checks for private-release credential redirects."""
import ast
from pathlib import Path
import unittest
import urllib.request

# Load only the pure redirect class; the real downloader's main flow must never
# read credentials or access the network in this test.
source = Path(__file__).with_name('download-developer-engine.py').read_text(encoding='utf-8')
tree = ast.parse(source)
redirect_definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'SafeRedirect')
namespace = {'urllib': __import__('urllib')}
exec(compile(ast.Module(body=[redirect_definition], type_ignores=[]), '<redirect-only>', 'exec'), namespace)
SafeRedirect = namespace['SafeRedirect']

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
