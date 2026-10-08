"""Start the API, run the smoke suite against it, then shut it down.

Usage:
    python scripts/run_api_smoke.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = int(os.environ.get("SMOKE_PORT", "8000"))
BASE = f"http://{HOST}:{PORT}"


def main() -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    from load_env import load  # noqa: PLC0415

    env = dict(os.environ)
    env.update(load())
    env["PYTHONUNBUFFERED"] = "1"
    # Trust the platform's own proxy settings rather than any inherited ones.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        env.pop(key, None)

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "app.serve",
            "--host", HOST, "--port", str(PORT), "--log-level", "warning", "--workers", "1",
        ],
        cwd=str(ROOT / "apps" / "api"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    client = httpx.Client(timeout=20.0, trust_env=False)
    ready = False
    for _ in range(45):
        time.sleep(1)
        if proc.poll() is not None:
            print("server exited during startup:")
            print(proc.stdout.read()[-4000:] if proc.stdout else "")
            return 1
        try:
            if client.get(f"{BASE}/api/v1/health").status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            continue

    if not ready:
        proc.terminate()
        print("server did not become ready")
        return 1

    print("server ready\n")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke_api.py")],
        cwd=str(ROOT),
        env={**env, "API_BASE_URL": BASE},
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.stderr.strip():
        print("stderr:", result.stderr[-2000:])

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    client.close()
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
