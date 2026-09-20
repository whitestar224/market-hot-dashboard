from __future__ import annotations

import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, MutableMapping
from urllib.parse import urlsplit, urlunsplit

import requests


DEFAULT_PROBE_URLS = (
    "https://openapi.gmgn.ai",
    "https://web3.binance.com",
    "https://api.deepseek.com",
)
DEFAULT_LOCAL_PROXY_PORTS = (
    7890,
    7891,
    7897,
    7898,
    10809,
    1080,
    10808,
    20171,
    8080,
    8118,
    8888,
    9090,
)
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
LOCAL_BYPASS_HOSTS = ("localhost", "127.0.0.1", "::1")


@dataclass(frozen=True)
class RouteProbe:
    proxy_url: str
    reachable: int
    total: int
    errors: tuple[str, ...] = ()


def normalize_proxy_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""
    if not parsed.hostname or not port:
        return ""
    return raw


def masked_proxy_url(value: str) -> str:
    raw = normalize_proxy_url(value)
    if not raw:
        return "direct"
    parsed = urlsplit(raw)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunsplit((parsed.scheme or "http", netloc, "", "", ""))


def _split_candidates(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\s]+", str(value or "")) if part.strip()]


def _append_no_proxy(environment: MutableMapping[str, str]) -> None:
    current = str(environment.get("NO_PROXY") or environment.get("no_proxy") or "")
    values = [part.strip() for part in current.split(",") if part.strip()]
    seen = {part.casefold() for part in values}
    for host in LOCAL_BYPASS_HOSTS:
        if host.casefold() not in seen:
            values.append(host)
            seen.add(host.casefold())
    combined = ",".join(values)
    environment["NO_PROXY"] = combined
    environment["no_proxy"] = combined


def apply_proxy_environment(environment: MutableMapping[str, str], proxy_url: str) -> None:
    for key in PROXY_ENV_KEYS:
        environment.pop(key, None)
    normalized = normalize_proxy_url(proxy_url)
    if normalized:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            environment[key] = normalized
    _append_no_proxy(environment)


def windows_proxy_candidates() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)
            raw_proxy = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()
    except (ImportError, OSError, ValueError):
        return []
    if not enabled or not raw_proxy:
        return []
    if "=" not in raw_proxy:
        return [raw_proxy]
    entries: dict[str, str] = {}
    for part in raw_proxy.split(";"):
        protocol, separator, address = part.partition("=")
        if separator and address.strip():
            entries[protocol.strip().casefold()] = address.strip()
    return [entries[key] for key in ("https", "http", "socks") if entries.get(key)]


def local_proxy_candidates(
    ports: Iterable[int] = DEFAULT_LOCAL_PROXY_PORTS,
    *,
    timeout: float = 0.12,
) -> list[str]:
    candidates: list[str] = []
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
                candidates.append(f"http://127.0.0.1:{int(port)}")
        except OSError:
            continue
    return candidates


def collect_proxy_candidates(
    environment: Mapping[str, str],
    *,
    original_proxy_values: Iterable[str] = (),
    windows_candidates: Iterable[str] | None = None,
    local_candidates: Iterable[str] | None = None,
) -> list[str]:
    raw_candidates: list[str] = []
    raw_candidates.extend(_split_candidates(environment.get("XINGYUN_PROXY_CANDIDATES", "")))
    raw_candidates.extend(original_proxy_values)
    for key in PROXY_ENV_KEYS:
        if environment.get(key):
            raw_candidates.append(environment[key])
    raw_candidates.extend(windows_proxy_candidates() if windows_candidates is None else windows_candidates)
    raw_candidates.extend(local_proxy_candidates() if local_candidates is None else local_candidates)

    result: list[str] = []
    seen: set[str] = set()
    for value in raw_candidates:
        normalized = normalize_proxy_url(value)
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        result.append(normalized)
    return result


def probe_route(
    proxy_url: str,
    *,
    urls: Iterable[str] = DEFAULT_PROBE_URLS,
    timeout: tuple[float, float] = (2.5, 4.0),
) -> RouteProbe:
    normalized = normalize_proxy_url(proxy_url)
    proxies = {"http": normalized, "https": normalized} if normalized else None
    targets = tuple(urls)
    errors: list[str] = []

    def probe_one(url: str) -> tuple[bool, str]:
        session = requests.Session()
        session.trust_env = False
        try:
            try:
                response = session.head(
                    url,
                    allow_redirects=False,
                    proxies=proxies,
                    timeout=timeout,
                )
                response.close()
                return True, ""
            except requests.RequestException as exc:
                return False, f"{urlsplit(url).hostname or url}: {type(exc).__name__}"
        finally:
            session.close()

    reachable = 0
    with ThreadPoolExecutor(max_workers=max(1, min(3, len(targets)))) as executor:
        futures = [executor.submit(probe_one, url) for url in targets]
        for future in as_completed(futures):
            ok, error = future.result()
            if ok:
                reachable += 1
            elif error:
                errors.append(error)
    return RouteProbe(normalized, reachable, len(targets), tuple(errors))


class NetworkProxyAdapter:
    def __init__(
        self,
        *,
        environment: MutableMapping[str, str] | None = None,
        probe: Callable[..., RouteProbe] = probe_route,
        probe_urls: Iterable[str] = DEFAULT_PROBE_URLS,
    ) -> None:
        self.environment = environment if environment is not None else os.environ
        self.probe = probe
        self.probe_urls = tuple(probe_urls)
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._original_proxy_values = tuple(
            str(self.environment.get(key) or "").strip()
            for key in PROXY_ENV_KEYS
            if str(self.environment.get(key) or "").strip()
        )
        self._state: dict[str, object] = {
            "started": False,
            "mode": "auto",
            "route": "direct",
            "proxyUrl": "",
            "healthy": False,
            "reachableTargets": 0,
            "targetCount": len(self.probe_urls),
            "checkedAt": 0,
            "generation": 0,
            "reason": "尚未检查网络路径",
        }

    def _mode(self) -> str:
        return str(self.environment.get("XINGYUN_NETWORK_PROXY") or "auto").strip() or "auto"

    def _candidate_routes(self, mode: str) -> list[str]:
        normalized_mode = mode.casefold()
        if normalized_mode in {"direct", "off", "none", "false", "0"}:
            return [""]
        explicit = normalize_proxy_url(mode) if normalized_mode != "auto" else ""
        if explicit:
            return [explicit]
        return [
            "",
            *collect_proxy_candidates(
                self.environment,
                original_proxy_values=self._original_proxy_values,
            ),
        ]

    def refresh(self) -> dict[str, object]:
        with self._refresh_lock:
            mode = self._mode()
            routes = self._candidate_routes(mode)
            probes = [self.probe(route, urls=self.probe_urls) for route in routes]
            # Direct/TUN routing wins a tie because it survives local proxy app changes.
            best = max(probes, key=lambda item: (item.reachable, not bool(item.proxy_url)))
            healthy = best.reachable == best.total and best.total > 0
            selected_proxy = best.proxy_url if best.reachable > 0 else ""
            reason = (
                f"{best.reachable}/{best.total} 个公共接口可达"
                if best.reachable
                else "所有候选路径暂不可达，已避开失效的本地代理并使用直连重试"
            )
            apply_proxy_environment(self.environment, selected_proxy)
            checked_at = int(time.time() * 1000)
            with self._lock:
                previous_proxy = str(self._state.get("proxyUrl") or "")
                generation = int(self._state.get("generation") or 0)
                if previous_proxy != selected_proxy:
                    generation += 1
                self._state.update(
                    {
                        "started": True,
                        "mode": mode,
                        "route": masked_proxy_url(selected_proxy),
                        "proxyUrl": selected_proxy,
                        "healthy": healthy,
                        "reachableTargets": best.reachable,
                        "targetCount": best.total,
                        "checkedAt": checked_at,
                        "generation": generation,
                        "reason": reason,
                    }
                )
                snapshot = dict(self._state)
            if previous_proxy != selected_proxy:
                print(
                    f"Network route switched to {snapshot['route']} ({reason})",
                    flush=True,
                )
            return self.public_status()

    def start(self) -> dict[str, object]:
        status = self.refresh()
        with self._lock:
            if self._thread and self._thread.is_alive():
                return status
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="network-proxy-adapter",
            )
            self._thread.start()
        return status

    def _run(self) -> None:
        try:
            interval = float(self.environment.get("XINGYUN_PROXY_CHECK_SECONDS") or "60")
        except (TypeError, ValueError):
            interval = 60.0
        interval = min(600.0, max(15.0, interval))
        while not self._stop.wait(interval):
            try:
                self.refresh()
            except Exception as exc:
                print(f"Network route refresh failed: {type(exc).__name__}: {exc}", flush=True)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    def proxy_url(self) -> str:
        with self._lock:
            return str(self._state.get("proxyUrl") or "")

    def generation(self) -> int:
        with self._lock:
            return int(self._state.get("generation") or 0)

    def public_status(self) -> dict[str, object]:
        with self._lock:
            return {
                key: value
                for key, value in self._state.items()
                if key != "proxyUrl"
            }


NETWORK_PROXY_ADAPTER = NetworkProxyAdapter()


def start_network_proxy_adapter() -> dict[str, object]:
    return NETWORK_PROXY_ADAPTER.start()


def stop_network_proxy_adapter() -> None:
    NETWORK_PROXY_ADAPTER.stop()


def network_proxy_status() -> dict[str, object]:
    return NETWORK_PROXY_ADAPTER.public_status()


def network_proxy_url() -> str:
    return NETWORK_PROXY_ADAPTER.proxy_url()


def network_proxy_generation() -> int:
    return NETWORK_PROXY_ADAPTER.generation()
