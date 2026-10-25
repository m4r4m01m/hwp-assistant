"""CI-only offline runtime check. No login, prompt, turn/start or inference."""
import os
from pathlib import Path
import subprocess
import tempfile
from .codex_runner import capture_settings, command
from .report_rpc import ReportRpc
from .report_stream import stream_command


def main():
    with tempfile.TemporaryDirectory(prefix="hwp-codex-offline-") as directory:
        os.environ["CODEX_HOME"] = directory
        settings = capture_settings()
        assert settings.executable, "Codex executable missing"
        version = subprocess.run([settings.executable, "--version"], check=True, capture_output=True, text=True, timeout=10)
        assert "0.153.0" in version.stdout
        help_result = subprocess.run([settings.executable, "exec", "--help"], check=True, capture_output=True, text=True, timeout=10)
        assert "--ignore-user-config" in help_result.stdout and "--ephemeral" in help_result.stdout
        # Validate every configured feature name without making a model request.
        args = command(settings.executable, directory, settings)
        flags = [item for index, item in enumerate(args) if item in ("--disable", "--enable") or index > 0 and args[index-1] in ("--disable", "--enable")]
        subprocess.run([settings.executable, *flags, "features", "list"], check=True, capture_output=True, timeout=10)
        instructions = Path(directory) / "instructions.txt"
        instructions.write_text("Offline protocol configuration check. Never start a turn.")
        with ReportRpc(stream_command(settings, instructions, False), environment=dict(settings.environment), cwd=directory, timeout=20) as rpc:
            rpc.send({"id":1,"method":"initialize","params":{"clientInfo":{"name":"hwp_offline_check","version":"1"}}})
            while True:
                event = rpc.receive()
                if event.get("id") == 1:
                    assert "result" in event, "initialize rejected"
                    break
            rpc.send({"method":"initialized"})
            rpc.send({"id":2,"method":"account/read","params":{"refreshToken":False}})
            while True:
                event = rpc.receive()
                if event.get("id") == 2:
                    assert "result" in event and event["result"].get("account") is None
                    break
        import app.rewrite_mcp  # noqa: F401 -- validates packaged MCP dependencies
    print("PASS: pinned Codex binary, exec flags, app-server initialize/account-read and packaged MCP; inference calls=0")


if __name__ == "__main__":
    main()
