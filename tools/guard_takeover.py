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


def parent_pid_of(pid):
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').ParentProcessId"],
            capture_output=True, timeout=15,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    try:
        return int(out.decode("ascii", "replace").strip() or 0)
    except ValueError:
        return 0


def supervised_pids():
    """PIDs the supervisor owns, so a normal restart is never mistaken for a takeover.

    The supervisor launches its child as ``server.py --host … --port …`` — the
    same shape as a hand-started process — so the child must be recognised by
    its *parent* (and by status.json), never by the absence of ``--worker-market``.
    """
    owned = set()
    try:
        state = guard.read_state()
        if str(state.get("status") or "") in {"running", "starting", "recovering"} and state.get("pid"):
            owned.add(int(state["pid"]))
    except (TypeError, ValueError):
        pass
    for pid in listener_pids():
        if "service_guard.py" in cmdline_of(parent_pid_of(pid)):
            owned.add(pid)
    return owned


def is_bare_server(cmdline, pid=None, owned=frozenset()):
    text = (cmdline or "").lower()
    if "server.py" not in text:
        return False
    if "--worker-market" in text:
        return False  # the market worker belongs to the supervisor
    if pid is not None and int(pid) in owned:
        return False  # supervised child: leave it to the supervisor
    return True


def main():
    if guard.probe(HOST, PORT):
        owned = supervised_pids()
        pids = listener_pids()
        victims = [pid for pid in pids if is_bare_server(cmdline_of(pid), pid, owned)]
        if not victims:
            print("Port 8765 is served by a supervisor-managed process already.")
            print("Nothing to take over. Use 查看服务状态.cmd / restart instead.")
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
