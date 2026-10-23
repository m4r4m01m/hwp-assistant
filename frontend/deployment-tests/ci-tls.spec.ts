import { test, expect } from "@playwright/test";
import { createServer, type Server } from "node:https";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

async function serve(key: Buffer, cert: Buffer) {
  const server = createServer({ key, cert }, (request, response) => {
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("Content-Type", request.url === "/sw.js" ? "application/javascript" : "text/html");
    response.end(request.url === "/sw.js"
      ? "self.addEventListener('install', () => self.skipWaiting()); self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));"
      : "<!doctype html><title>Isolated CI TLS check</title>");
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => { server.off("error", reject); resolve(); });
  });
  const address = server.address();
  if (!address || typeof address === "string") throw Error("Missing local TLS port");
  return { server, url: `https://127.0.0.1:${address.port}/` };
}
async function stop(server: Server) {
  server.closeAllConnections();
  await new Promise<void>(resolve => server.close(() => resolve()));
}

test("CI TLS: trusted local certificate supports Service Workers and rejects another certificate", async ({ page, context }) => {
  test.skip(process.env.HWP_CI_TLS !== "1", "Only the isolated CI certificate setup is under test");
  test.setTimeout(30000);
  const certDir = process.env.HWP_CI_CERT_DIR;
  if (!certDir) throw Error("Set HWP_CI_CERT_DIR");
  const temp = mkdtempSync(join(tmpdir(), "hwp-untrusted-cert-"));
  const servers: Server[] = [];
  try {
    const trusted = await serve(readFileSync(join(certDir, "key.pem")), readFileSync(join(certDir, "cert.pem")));
    servers.push(trusted.server);
    await page.goto(trusted.url);
    await page.evaluate(async () => {
      await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
    });
    await expect.poll(() => page.evaluate(async () =>
      (await navigator.serviceWorker.getRegistration())?.active?.state)).toBe("activated");
    expect((await context.request.get(trusted.url)).ok()).toBe(true);

    execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
      "-keyout", join(temp, "key.pem"), "-out", join(temp, "cert.pem"), "-subj", "/CN=localhost",
      "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], { stdio: "ignore", timeout: 10000 });
    const untrusted = await serve(readFileSync(join(temp, "key.pem")), readFileSync(join(temp, "cert.pem")));
    servers.push(untrusted.server);
    // Do not accept an unrelated self-signed certificate, even on localhost.
    await expect(page.goto(untrusted.url, { timeout: 10000 })).rejects.toThrow(/ERR_CERT_/);
    await expect(context.request.get(untrusted.url, { timeout: 10000 })).rejects.toThrow(/certificate/i);
  } finally {
    await Promise.all(servers.map(stop));
    rmSync(temp, { recursive: true, force: true });
  }
});
