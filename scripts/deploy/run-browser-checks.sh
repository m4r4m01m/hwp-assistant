#!/usr/bin/env bash
# Trust only this run's localhost certificate in a disposable browser home.
# Never modify host/system trust, skip certificate checks, or disable workers.
set -Eeuo pipefail
if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_ENVIRONMENT:-}" != github-hosted ]]; then
  echo 'This TLS setup is only for a disposable GitHub-hosted runner.' >&2
  exit 1
fi
if [[ "${HWP_DEPLOY_TEST_URL:-https://localhost:24443/hwp-assistant/}" != https://localhost:24443/hwp-assistant/ ]]; then
  echo 'CI certificate trust is restricted to the localhost deployment test.' >&2
  exit 1
fi
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
: "${HWP_CI_CERT_DIR:?Set the isolated CI certificate directory}"
: "${RUNNER_TEMP:?Set the disposable runner temporary directory}"
: "${PLAYWRIGHT_BROWSERS_PATH:?Set an absolute Playwright installation directory}"
[[ "$PLAYWRIGHT_BROWSERS_PATH" == /* ]] || { echo 'Use an absolute browser path.' >&2; exit 1; }
cert=$(realpath -- "$HWP_CI_CERT_DIR/cert.pem")
[[ -f "$cert" ]] || { echo 'CI certificate is missing.' >&2; exit 1; }
command -v certutil >/dev/null
openssl x509 -in "$cert" -noout -checkend 0 >/dev/null
openssl x509 -in "$cert" -noout -checkhost localhost >/dev/null
openssl x509 -in "$cert" -noout -checkip 127.0.0.1 >/dev/null
umask 077
browser_home=$(mktemp -d "$RUNNER_TEMP/hwp-browser-home.XXXXXXXX")
trap 'rm -rf -- "$browser_home"' EXIT
mkdir -p "$browser_home/.pki/nssdb"
certutil -N -d "sql:$browser_home/.pki/nssdb" --empty-password
# P trusts this exact peer certificate, not a new general-purpose CA.
certutil -A -d "sql:$browser_home/.pki/nssdb" -n hwp-ci-localhost \
  -t 'P,,' -i "$cert"
cd "$ROOT/frontend"
# HOME isolates Chromium's NSS DB. The explicit browser path preserves the
# installation made before changing HOME. Node trusts the same cert for API calls.
HOME="$browser_home" NODE_EXTRA_CA_CERTS="$cert" HWP_CI_TLS=1 \
  node node_modules/@playwright/test/cli.js test --config playwright.deploy.config.ts "$@"
