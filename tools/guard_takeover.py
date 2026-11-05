"""Free port 8765 from a hand-started ``python server.py`` so the supervisor
can take over.

Only run by 切换守护模式.cmd. Safety rules:

* the port must answer ``/api/service-liveness`` as this project, otherwise we
  are looking at a stranger's process and refuse to touch it;
* the process must look like a bare ``server.py`` launch (no ``--worker-market``,
  which would be the supervisor's own child);
* nothing is killed until the caller has already confirmed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import service_guard as guard  # noqa: E402

PORT = 8765
HOST = "127.0.0.1"


def listener_pids(port=PORT):
    """Listening PIDs on ``port``. netstat output is ASCII, so bytes are safe."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], cwd=str(ROOT), capture_output=True).stdout
    except OSError:
        return set()
    pids = set()
    for raw in out.splitlines():
        parts = raw.decode("ascii", "replace").split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
            try:
                pids.add(int(parts[4]))
            except ValueError:
                pass
    return pids


def cmdline_of(pid):
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
            capture_output=True, timeout=15,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.decode("utf-8", "replace").strip()


def is_bare_server(cmdline):
    text = (cmdline or "").lower()
    if "server.py" not in text:
        return False
    if "--worker-market" in text:
        return False  # that one belongs to the supervisor; leave it alone
    return True


def main():
    if guard.probe(HOST, PORT):
        pids = listener_pids()
        victims = [pid for pid in pids if is_bare_server(cmdline_of(pid))]
        if not victims:
            print("Port 8765 is served by a supervisor-managed process already.")
            print("Nothing to take over.")
            return 0
        print(f"Found a hand-started server.py on port {PORT}: pid(s) {victims}")
        for pid in victims:
            print(f"  stopping pid {pid} ...")
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"  could not stop pid {pid}: {exc}")
                return 4
        deadline = time.time() + 20
        while time.time() < deadline and guard.probe(HOST, PORT):
            time.sleep(0.5)
        if guard.probe(HOST, PORT):
            print("Port is still answering; aborting before starting a supervisor.")
            return 5
        print("Port released.")
        return 0
    if listener_pids():
        print(f"Port {PORT} is occupied by something that is not this dashboard.")
        print("Refusing to touch it. Close that program, then run this again.")
        return 6
    print("Port is free.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
