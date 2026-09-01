from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import time
from typing import Callable, Iterable


SQLITE_HEADER = b"SQLite format 3\x00"
SQLCIPHER_PAGE_SIZE = 4096
SQLCIPHER_RESERVED_BYTES = 80
KEY_WITH_SALT_RE = re.compile(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'")


class WechatDatabaseError(RuntimeError):
    pass


class _MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def discover_wechat_data_root(home: str | os.PathLike[str] | None = None) -> Path:
    base = Path(home or Path.home()) / "xwechat_files"
    candidates = [
        path
        for path in base.glob("*/db_storage")
        if path.is_dir() and (path / "message").is_dir()
    ]
    if not candidates:
        raise WechatDatabaseError("没有找到微信 4.x 数据目录")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def discover_monitor_databases(data_root: str | os.PathLike[str]) -> list[Path]:
    root = Path(data_root)
    paths: list[Path] = []
    paths.extend(sorted(root.glob("contact/contact.db")))
    paths.extend(sorted(root.glob("session/session.db")))
    paths.extend(sorted(root.glob("message/message_[0-9]*.db")))
    return [path for path in paths if path.is_file() and path.stat().st_size >= SQLCIPHER_PAGE_SIZE]


def database_salt(path: str | os.PathLike[str]) -> bytes:
    with Path(path).open("rb") as handle:
        salt = handle.read(16)
    if len(salt) != 16:
        raise WechatDatabaseError(f"数据库文件不完整：{Path(path).name}")
    return salt


def _data_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_ubyte]]:
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi_transform(value: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise WechatDatabaseError("本机密钥保护目前只支持 Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.restype = wintypes.BOOL
    source, source_buffer = _data_blob(value)
    entropy, entropy_buffer = _data_blob(b"market-hot-dashboard/wechat-db-keys/v1")
    output = _DataBlob()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = function(
            ctypes.byref(source),
            "market-hot-dashboard",
            ctypes.byref(entropy),
            None,
            None,
            flags,
            ctypes.byref(output),
        )
    else:
        description = ctypes.c_void_p()
        ok = function(
            ctypes.byref(source),
            ctypes.byref(description),
            ctypes.byref(entropy),
            None,
            None,
            flags,
            ctypes.byref(output),
        )
        if description.value:
            kernel32.LocalFree(description)
    # Keep the backing buffers alive until the native call returns.
    _ = source_buffer, entropy_buffer
    if not ok:
        error = ctypes.get_last_error()
        raise WechatDatabaseError(f"Windows 本机密钥保护失败（错误 {error}）")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def save_protected_database_keys(
    keys: dict[Path, bytes],
    data_root: Path,
    destination: Path,
) -> None:
    payload = {
        "version": 1,
        "createdAt": int(time.time()),
        "keys": {
            path.resolve().relative_to(data_root.resolve()).as_posix(): key.hex()
            for path, key in keys.items()
        },
    }
    protected = _dpapi_transform(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        protect=True,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(protected)
    os.replace(temporary, destination)


def load_protected_database_keys(
    data_root: Path,
    source: Path,
) -> dict[Path, bytes]:
    if not source.is_file():
        return {}
    payload = json.loads(_dpapi_transform(source.read_bytes(), protect=False).decode("utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("keys"), dict):
        raise WechatDatabaseError("微信数据库密钥缓存格式不受支持")
    root = data_root.resolve()
    result: dict[Path, bytes] = {}
    for relative, key_hex in payload["keys"].items():
        path = (root / str(relative)).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise WechatDatabaseError("微信数据库密钥缓存包含越界路径") from exc
        key = bytes.fromhex(str(key_hex))
        if len(key) != 32:
            raise WechatDatabaseError("微信数据库密钥缓存包含无效密钥")
        result[path] = key
    return result


def save_protected_key_candidates(values: Iterable[bytes], destination: Path) -> None:
    unique_values = sorted({value for value in values if len(value) == 32})
    payload = {
        "version": 1,
        "createdAt": int(time.time()),
        "keys": [value.hex() for value in unique_values],
    }
    protected = _dpapi_transform(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        protect=True,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(protected)
    os.replace(temporary, destination)


def load_protected_key_candidates(source: Path) -> list[bytes]:
    if not source.is_file():
        return []
    payload = json.loads(_dpapi_transform(source.read_bytes(), protect=False).decode("utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("keys"), list):
        raise WechatDatabaseError("微信候选密钥缓存格式不受支持")
    values = [bytes.fromhex(str(value)) for value in payload["keys"]]
    if any(len(value) != 32 for value in values):
        raise WechatDatabaseError("微信候选密钥缓存包含无效密钥")
    return values


def find_wechat_database_process(database_paths: Iterable[Path]) -> int:
    try:
        import psutil  # type: ignore
    except Exception as exc:  # pragma: no cover - environment-specific dependency
        raise WechatDatabaseError("缺少进程读取组件 psutil") from exc

    wanted = {os.path.normcase(str(Path(path).resolve())) for path in database_paths}
    for process in psutil.process_iter(("pid", "name")):
        try:
            if str(process.info.get("name") or "").lower() != "weixin.exe":
                continue
            opened = {
                os.path.normcase(str(Path(item.path).resolve()))
                for item in process.open_files()
                if item.path
            }
            if wanted.intersection(opened):
                return int(process.info["pid"])
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    raise WechatDatabaseError("微信未打开，或当前微信进程没有加载消息数据库")


def capture_database_keys_with_login_hook(
    hook_library: str | os.PathLike[str],
    database_paths: Iterable[Path],
    *,
    process_wait_timeout: float = 120.0,
    process_stabilize_time: float = 2.0,
    capture_timeout: float = 180.0,
    on_status: Callable[[str], None] | None = None,
    on_keys: Callable[[dict[Path, bytes]], None] | None = None,
    on_candidate: Callable[[bytes], None] | None = None,
) -> dict[Path, bytes]:
    """Capture transient WeChat 4.x database keys during login.

    The hook library is loaded only when this function is explicitly called. Candidate
    values are never reported to status callbacks and are accepted only after a local
    SQLCipher page-header validation.
    """
    if os.name != "nt":
        raise WechatDatabaseError("微信登录密钥捕获目前只支持 Windows")
    try:
        import psutil  # type: ignore
    except Exception as exc:  # pragma: no cover - environment-specific dependency
        raise WechatDatabaseError("缺少进程读取组件 psutil") from exc

    library_path = Path(hook_library).resolve()
    if not library_path.is_file():
        raise WechatDatabaseError(f"微信密钥捕获组件不存在：{library_path}")
    if not str(library_path).isascii():
        raise WechatDatabaseError("微信密钥捕获组件必须位于纯英文路径")

    targets = [Path(path).resolve() for path in database_paths]
    if not targets:
        return {}

    library = ctypes.WinDLL(str(library_path), use_last_error=True)
    initialize = library.InitializeHook
    initialize.argtypes = (wintypes.DWORD,)
    initialize.restype = wintypes.BOOL
    poll_key = library.PollKeyData
    poll_key.argtypes = (ctypes.POINTER(ctypes.c_char), ctypes.c_int)
    poll_key.restype = wintypes.BOOL
    get_status = library.GetStatusMessage
    get_status.argtypes = (
        ctypes.POINTER(ctypes.c_char),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    )
    get_status.restype = wintypes.BOOL
    cleanup = library.CleanupHook
    cleanup.argtypes = ()
    cleanup.restype = wintypes.BOOL
    get_error = library.GetLastErrorMsg
    get_error.argtypes = ()
    get_error.restype = ctypes.c_char_p

    def report(message: str) -> None:
        if on_status:
            on_status(re.sub(r"(?i)\b[0-9a-f]{64}\b", "<redacted-key>", message))

    report("等待最新版微信进程启动")
    process_id: int | None = None
    first_seen_at: float | None = None
    wait_deadline = time.monotonic() + process_wait_timeout
    while time.monotonic() < wait_deadline and process_id is None:
        candidates: list[tuple[int, int, float]] = []
        for process in psutil.process_iter(("pid", "ppid", "name", "create_time")):
            try:
                if str(process.info.get("name") or "").lower() == "weixin.exe":
                    candidates.append(
                        (
                            int(process.info["pid"]),
                            int(process.info.get("ppid") or 0),
                            float(process.info.get("create_time") or 0.0),
                        )
                    )
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue
        if candidates:
            if first_seen_at is None:
                first_seen_at = time.monotonic()
                report("微信启动器已出现，等待主进程稳定")
            if time.monotonic() - first_seen_at >= process_stabilize_time:
                process_ids = {candidate[0] for candidate in candidates}
                roots = [candidate for candidate in candidates if candidate[1] not in process_ids]
                process_id = min(roots or candidates, key=lambda candidate: candidate[2])[0]
        else:
            first_seen_at = None
        if process_id is None:
            time.sleep(0.05)
    if process_id is None:
        raise WechatDatabaseError("等待最新版微信启动超时")

    report(f"检测到微信进程 {process_id}，正在安装登录密钥捕获器")
    if not initialize(process_id):
        raw_error = get_error()
        error = raw_error.decode("utf-8", "replace") if raw_error else "未知错误"
        raise WechatDatabaseError(f"登录密钥捕获器初始化失败：{error}")

    report("登录密钥捕获器已就绪，请完成微信登录")
    found: dict[Path, bytes] = {}
    seen_candidates: set[bytes] = set()
    capture_deadline = time.monotonic() + capture_timeout
    try:
        while time.monotonic() < capture_deadline and len(found) < len(targets):
            key_buffer = ctypes.create_string_buffer(256)
            if poll_key(key_buffer, len(key_buffer)):
                candidate_hex = key_buffer.value.decode("ascii", "ignore").strip()
                if re.fullmatch(r"(?i)[0-9a-f]{64}", candidate_hex):
                    candidate = bytes.fromhex(candidate_hex)
                    if candidate not in seen_candidates:
                        seen_candidates.add(candidate)
                        if on_candidate:
                            on_candidate(candidate)
                        matched = {
                            path: candidate
                            for path in targets
                            if path not in found and validate_sqlcipher4_key(path, candidate)
                        }
                        if matched:
                            found.update(matched)
                            if on_keys:
                                on_keys(dict(found))
                            report(
                                "已验证数据库密钥："
                                + "、".join(sorted(path.name for path in matched))
                            )
                        else:
                            report("捕获到一个候选密钥，但未匹配目标数据库")

            for _ in range(8):
                status_buffer = ctypes.create_string_buffer(512)
                status_level = ctypes.c_int()
                if not get_status(
                    status_buffer,
                    len(status_buffer),
                    ctypes.byref(status_level),
                ):
                    break
                message = status_buffer.value.decode("utf-8", "replace").strip()
                if message:
                    report(message)
            time.sleep(0.1)
    finally:
        try:
            cleanup()
        except OSError:
            pass
    return found


def scan_process_for_database_keys(
    process_id: int,
    database_paths: Iterable[Path],
    *,
    max_region_size: int = 200 * 1024 * 1024,
    chunk_size: int = 1024 * 1024,
    adjacent_search_radius: int = 512,
    on_diagnostic: Callable[[dict[str, int]], None] | None = None,
) -> dict[Path, bytes]:
    """Find and verify SQLCipher page keys associated with requested database salts.

    Newer WeChat builds do not always retain the textual ``x'<key><salt>'`` token.
    The scanner therefore also anchors on each database's binary/ASCII salt and tests
    nearby 32-byte values. It never returns unrelated process memory and never persists
    unverified candidates.
    """
    if os.name != "nt":
        raise WechatDatabaseError("微信进程读取目前只支持 Windows")

    salt_to_paths: dict[str, list[Path]] = {}
    for source in database_paths:
        path = Path(source)
        salt_to_paths.setdefault(database_salt(path).hex(), []).append(path)
    if not salt_to_paths:
        return {}

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.VirtualQueryEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(_MemoryBasicInformation),
        ctypes.c_size_t,
    )
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.ReadProcessMemory.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    )
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    process_vm_read = 0x0010
    process_query_information = 0x0400
    mem_commit = 0x1000
    page_guard = 0x100
    page_noaccess = 0x01
    readable = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
    overlap_size = max(112, int(adjacent_search_radius) + 160)

    handle = kernel32.OpenProcess(
        process_vm_read | process_query_information,
        False,
        int(process_id),
    )
    if not handle:
        error = ctypes.get_last_error()
        raise WechatDatabaseError(f"无法只读访问微信进程（Windows 错误 {error}）")

    found: dict[Path, bytes] = {}
    tested: dict[Path, set[bytes]] = {
        path: set()
        for paths in salt_to_paths.values()
        for path in paths
    }
    diagnostic = {"rawSaltOccurrences": 0, "asciiSaltOccurrences": 0, "wideSaltOccurrences": 0}

    def test_candidate(candidate: bytes, paths: Iterable[Path]) -> None:
        if len(candidate) != 32 or candidate == b"\x00" * 32:
            return
        for path in paths:
            if path in found or candidate in tested[path]:
                continue
            tested[path].add(candidate)
            if validate_sqlcipher4_key(path, candidate):
                found[path] = candidate

    def test_salt_neighborhood(data: bytes, salt_hex: str, paths: list[Path]) -> None:
        raw_salt = bytes.fromhex(salt_hex)
        ascii_salt = salt_hex.encode("ascii")
        wide_salt = ascii_salt.decode("ascii").encode("utf-16-le")

        start = 0
        while True:
            position = data.find(raw_salt, start)
            if position < 0:
                break
            diagnostic["rawSaltOccurrences"] += 1
            direct_offsets = (position - 32, position + len(raw_salt))
            for offset in direct_offsets:
                if 0 <= offset <= len(data) - 32:
                    test_candidate(data[offset : offset + 32], paths)
            nearby_start = max(0, position - adjacent_search_radius)
            nearby_end = min(len(data) - 32, position + len(raw_salt) + adjacent_search_radius)
            for offset in range(nearby_start, nearby_end + 1, 4):
                test_candidate(data[offset : offset + 32], paths)
                if all(path in found for path in paths):
                    return
            start = position + 1

        start = 0
        while True:
            position = data.lower().find(ascii_salt, start)
            if position < 0:
                break
            diagnostic["asciiSaltOccurrences"] += 1
            for offset in (position - 64, position + len(ascii_salt)):
                if 0 <= offset <= len(data) - 64:
                    encoded = data[offset : offset + 64]
                    if re.fullmatch(rb"[0-9a-fA-F]{64}", encoded):
                        test_candidate(bytes.fromhex(encoded.decode("ascii")), paths)
            start = position + 1

        start = 0
        lowered_wide = data.lower()
        while True:
            position = lowered_wide.find(wide_salt, start)
            if position < 0:
                break
            diagnostic["wideSaltOccurrences"] += 1
            offset = position - 128
            if offset >= 0:
                encoded = data[offset:position]
                try:
                    text_key = encoded.decode("utf-16-le")
                except UnicodeDecodeError:
                    text_key = ""
                if re.fullmatch(r"[0-9a-fA-F]{64}", text_key):
                    test_candidate(bytes.fromhex(text_key), paths)
            start = position + 2
    address = 0
    maximum_address = 0x7FFFFFFFFFFF
    try:
        while address < maximum_address and len(found) < sum(map(len, salt_to_paths.values())):
            info = _MemoryBasicInformation()
            queried = kernel32.VirtualQueryEx(
                handle,
                ctypes.c_void_p(address),
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not queried:
                break
            base = int(info.BaseAddress or address)
            region_size = int(info.RegionSize or 0)
            next_address = base + max(region_size, 0x1000)
            protection = int(info.Protect)
            can_read = (
                int(info.State) == mem_commit
                and 0 < region_size <= max_region_size
                and not (protection & page_guard)
                and not (protection & page_noaccess)
                and (protection & 0xFF) in readable
            )
            if can_read:
                previous = b""
                offset = 0
                while offset < region_size:
                    requested = min(chunk_size, region_size - offset)
                    buffer = ctypes.create_string_buffer(requested)
                    bytes_read = ctypes.c_size_t()
                    ok = kernel32.ReadProcessMemory(
                        handle,
                        ctypes.c_void_p(base + offset),
                        buffer,
                        requested,
                        ctypes.byref(bytes_read),
                    )
                    if ok and bytes_read.value:
                        data = previous + buffer.raw[: bytes_read.value]
                        for match in KEY_WITH_SALT_RE.finditer(data):
                            salt_hex = match.group(2).decode("ascii").lower()
                            test_candidate(
                                bytes.fromhex(match.group(1).decode("ascii")),
                                salt_to_paths.get(salt_hex, []),
                            )
                        for salt_hex, paths in salt_to_paths.items():
                            if not all(path in found for path in paths):
                                test_salt_neighborhood(data, salt_hex, paths)
                        previous = data[-overlap_size:]
                    else:
                        previous = b""
                    if len(found) >= sum(map(len, salt_to_paths.values())):
                        break
                    offset += requested
            address = next_address
    finally:
        kernel32.CloseHandle(handle)
    if on_diagnostic:
        on_diagnostic(dict(diagnostic))
    return found


def scan_process_for_sqlcipher_context_keys(
    process_id: int,
    database_paths: Iterable[Path],
    *,
    max_region_size: int = 200 * 1024 * 1024,
    chunk_size: int = 4 * 1024 * 1024,
    on_diagnostic: Callable[[dict[str, int]], None] | None = None,
) -> dict[Path, bytes]:
    """Recover verified page keys by following SQLCipher 4.1 codec context pointers.

    Weixin 4.1.12 embeds SQLCipher 4.1.0. On 64-bit Windows its ``codec_ctx``
    stores the database salt pointer at offset 64 and read/write ``cipher_ctx``
    pointers at offsets 96/104. Each cipher context stores its derived page-key
    pointer at offset 8. Only keys that decrypt a requested database header are
    returned; no unrelated memory is exposed or persisted.
    """
    if os.name != "nt":
        raise WechatDatabaseError("微信进程读取目前只支持 Windows")

    salt_to_paths: dict[bytes, list[Path]] = {}
    for source in database_paths:
        path = Path(source)
        salt_to_paths.setdefault(database_salt(path), []).append(path)
    if not salt_to_paths:
        return {}

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.VirtualQueryEx.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MemoryBasicInformation), ctypes.c_size_t,
    )
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.ReadProcessMemory.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    )
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.OpenProcess(0x0010 | 0x0400, False, int(process_id))
    if not handle:
        error = ctypes.get_last_error()
        raise WechatDatabaseError(f"无法只读访问微信进程（Windows 错误 {error}）")

    def read_at(address: int, size: int) -> bytes:
        if address < 0x10000 or address > 0x7FFFFFFFFFFF or size <= 0:
            return b""
        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(bytes_read),
        ):
            return b""
        return buffer.raw[: bytes_read.value]

    # codec_ctx offsets 12..27: kdf_salt_sz, key_sz, iv_sz, block_sz.
    # Page and HMAC reserve settings vary across WeChat builds, so they are read
    # from the following fields instead of being baked into the signature.
    context_signature = struct.pack("<4I", 16, 32, 16, 16)
    found: dict[Path, bytes] = {}
    tested_keys: set[tuple[bytes, int, int]] = set()
    diagnostic = {
        "contextSignatures": 0,
        "saltPointerMatches": 0,
        "cipherContexts": 0,
        "candidateKeysTested": 0,
    }
    mem_commit = 0x1000
    page_guard = 0x100
    page_noaccess = 0x01
    readable = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
    address = 0
    maximum_address = 0x7FFFFFFFFFFF

    try:
        while address < maximum_address and len(found) < sum(map(len, salt_to_paths.values())):
            info = _MemoryBasicInformation()
            queried = kernel32.VirtualQueryEx(
                handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info),
            )
            if not queried:
                break
            base = int(info.BaseAddress or address)
            region_size = int(info.RegionSize or 0)
            next_address = base + max(region_size, 0x1000)
            protection = int(info.Protect)
            can_read = (
                int(info.State) == mem_commit
                and 0 < region_size <= max_region_size
                and not (protection & page_guard)
                and not (protection & page_noaccess)
                and (protection & 0xFF) in readable
            )
            if can_read:
                previous = b""
                offset = 0
                while offset < region_size:
                    requested = min(chunk_size, region_size - offset)
                    buffer = ctypes.create_string_buffer(requested)
                    bytes_read = ctypes.c_size_t()
                    ok = kernel32.ReadProcessMemory(
                        handle,
                        ctypes.c_void_p(base + offset),
                        buffer,
                        requested,
                        ctypes.byref(bytes_read),
                    )
                    if ok and bytes_read.value:
                        data = previous + buffer.raw[: bytes_read.value]
                        search_from = 0
                        while True:
                            signature_position = data.find(context_signature, search_from)
                            if signature_position < 0:
                                break
                            diagnostic["contextSignatures"] += 1
                            context_position = signature_position - 12
                            if context_position >= 0 and context_position + 256 <= len(data):
                                context = data[context_position : context_position + 256]
                                try:
                                    page_size = struct.unpack_from("<I", context, 28)[0]
                                    reserve_size = struct.unpack_from("<I", context, 32)[0]
                                except struct.error:
                                    page_size = reserve_size = 0
                                if page_size not in {512, 1024, 2048, 4096, 8192, 16384, 32768, 65536}:
                                    search_from = signature_position + 1
                                    continue
                                if reserve_size < 0 or reserve_size >= page_size or reserve_size % 16:
                                    search_from = signature_position + 1
                                    continue
                                pointer_values = {
                                    struct.unpack_from("<Q", context, pointer_offset)[0]
                                    for pointer_offset in range(56, len(context) - 7, 8)
                                }
                                paths: list[Path] = []
                                for pointer in pointer_values:
                                    pointed_salt = read_at(pointer, 16)
                                    matched_paths = salt_to_paths.get(pointed_salt, [])
                                    if not matched_paths and len(pointed_salt) == 16:
                                        unmasked_salt = bytes(value ^ 0x3A for value in pointed_salt)
                                        matched_paths = salt_to_paths.get(unmasked_salt, [])
                                    for path in matched_paths:
                                        if path not in paths:
                                            paths.append(path)
                                if paths:
                                    diagnostic["saltPointerMatches"] += 1
                                    candidates: set[bytes] = set()
                                    for cipher_pointer in pointer_values:
                                        cipher_context = read_at(cipher_pointer, 64)
                                        if len(cipher_context) < 32:
                                            continue
                                        diagnostic["cipherContexts"] += 1
                                        candidates.add(cipher_context[:32])
                                        for inner_offset in range(0, len(cipher_context) - 7, 8):
                                            inner_pointer = struct.unpack_from("<Q", cipher_context, inner_offset)[0]
                                            pointed_key = read_at(inner_pointer, 32)
                                            if len(pointed_key) == 32:
                                                candidates.add(pointed_key)
                                    settings = {
                                        (page_size, reserve_size),
                                        (4096, 80), (4096, 64), (4096, 48),
                                        (1024, 48), (1024, 80),
                                    }
                                    for candidate in candidates:
                                        if candidate == b"\x00" * 32:
                                            continue
                                        for candidate_page_size, candidate_reserve_size in settings:
                                            if candidate_reserve_size < 16 or candidate_reserve_size >= candidate_page_size:
                                                continue
                                            tested = (candidate, candidate_page_size, candidate_reserve_size)
                                            if tested in tested_keys:
                                                continue
                                            tested_keys.add(tested)
                                            diagnostic["candidateKeysTested"] += 1
                                            for path in paths:
                                                if path not in found and validate_sqlcipher4_key(
                                                    path,
                                                    candidate,
                                                    page_size=candidate_page_size,
                                                    reserved_bytes=candidate_reserve_size,
                                                ):
                                                    found[path] = candidate
                            search_from = signature_position + 1
                        previous = data[-320:]
                    else:
                        previous = b""
                    if len(found) >= sum(map(len, salt_to_paths.values())):
                        break
                    offset += requested
            address = next_address
    finally:
        kernel32.CloseHandle(handle)
    if on_diagnostic:
        on_diagnostic(dict(diagnostic))
    return found


def scan_process_for_salt_pointer_graph_keys(
    process_id: int,
    database_paths: Iterable[Path],
    *,
    max_region_size: int = 200 * 1024 * 1024,
    chunk_size: int = 4 * 1024 * 1024,
    derive_candidate_passwords: bool = False,
    on_diagnostic: Callable[[dict[str, int]], None] | None = None,
) -> dict[Path, bytes]:
    """Follow in-process references from database salts to possible page-key buffers."""
    if os.name != "nt":
        raise WechatDatabaseError("微信进程读取目前只支持 Windows")

    salt_to_paths: dict[bytes, list[Path]] = {}
    for source in database_paths:
        path = Path(source)
        salt_to_paths.setdefault(database_salt(path), []).append(path)
    if not salt_to_paths:
        return {}

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.VirtualQueryEx.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MemoryBasicInformation), ctypes.c_size_t,
    )
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.ReadProcessMemory.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    )
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.OpenProcess(0x0010 | 0x0400, False, int(process_id))
    if not handle:
        error = ctypes.get_last_error()
        raise WechatDatabaseError(f"无法只读访问微信进程（Windows 错误 {error}）")

    def read_at(address: int, size: int) -> bytes:
        if address < 0x10000 or address > 0x7FFFFFFFFFFF or size <= 0:
            return b""
        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(bytes_read),
        ):
            return b""
        return buffer.raw[: bytes_read.value]

    mem_commit = 0x1000
    page_guard = 0x100
    page_noaccess = 0x01
    readable = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}

    def scan_chunks(visit: Callable[[int, bytes], None], overlap_size: int) -> None:
        address = 0
        maximum_address = 0x7FFFFFFFFFFF
        while address < maximum_address:
            info = _MemoryBasicInformation()
            queried = kernel32.VirtualQueryEx(
                handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info),
            )
            if not queried:
                break
            base = int(info.BaseAddress or address)
            region_size = int(info.RegionSize or 0)
            next_address = base + max(region_size, 0x1000)
            protection = int(info.Protect)
            can_read = (
                int(info.State) == mem_commit
                and 0 < region_size <= max_region_size
                and not (protection & page_guard)
                and not (protection & page_noaccess)
                and (protection & 0xFF) in readable
            )
            if can_read:
                previous = b""
                offset = 0
                while offset < region_size:
                    requested = min(chunk_size, region_size - offset)
                    buffer = ctypes.create_string_buffer(requested)
                    bytes_read = ctypes.c_size_t()
                    ok = kernel32.ReadProcessMemory(
                        handle,
                        ctypes.c_void_p(base + offset),
                        buffer,
                        requested,
                        ctypes.byref(bytes_read),
                    )
                    if ok and bytes_read.value:
                        data = previous + buffer.raw[: bytes_read.value]
                        data_address = base + offset - len(previous)
                        visit(data_address, data)
                        previous = data[-overlap_size:]
                    else:
                        previous = b""
                    offset += requested
            address = next_address

    diagnostic = {
        "saltAddresses": 0,
        "saltPointerReferences": 0,
        "candidateKeysTested": 0,
        "derivedCandidates": 0,
    }
    salt_addresses: dict[int, list[Path]] = {}

    def collect_salt_addresses(data_address: int, data: bytes) -> None:
        for salt, paths in salt_to_paths.items():
            start = 0
            while True:
                position = data.find(salt, start)
                if position < 0:
                    break
                salt_addresses.setdefault(data_address + position, paths)
                start = position + 1

    found: dict[Path, bytes] = {}
    tested: set[tuple[bytes, int, int]] = set()
    try:
        scan_chunks(collect_salt_addresses, 32)
        diagnostic["saltAddresses"] = len(salt_addresses)
        pointer_patterns = {
            struct.pack("<Q", address): (address, paths)
            for address, paths in salt_addresses.items()
        }

        def follow_salt_references(data_address: int, data: bytes) -> None:
            for pointer_pattern, (_salt_address, paths) in pointer_patterns.items():
                start = 0
                while True:
                    position = data.find(pointer_pattern, start)
                    if position < 0:
                        break
                    diagnostic["saltPointerReferences"] += 1
                    reference_address = data_address + position
                    context_start = max(0x10000, reference_address - 384)
                    context = read_at(context_start, 768)
                    candidates: set[bytes] = set()
                    for pointer_offset in range(0, max(0, len(context) - 7), 8):
                        pointer = struct.unpack_from("<Q", context, pointer_offset)[0]
                        pointed = read_at(pointer, 80)
                        if len(pointed) < 32:
                            continue
                        candidates.add(pointed[:32])
                        for inner_offset in range(0, min(64, len(pointed) - 7), 8):
                            inner_pointer = struct.unpack_from("<Q", pointed, inner_offset)[0]
                            inner_value = read_at(inner_pointer, 32)
                            if len(inner_value) == 32:
                                candidates.add(inner_value)
                    settings = tuple(
                        (page_size, reserve_size)
                        for page_size in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536)
                        for reserve_size in range(16, min(256, page_size), 16)
                    )
                    expanded_candidates = set(candidates)
                    if derive_candidate_passwords:
                        for candidate in candidates:
                            if candidate == b"\x00" * 32:
                                continue
                            for path in paths:
                                salt = database_salt(path)
                                for algorithm, iterations in (
                                    ("sha512", 256000),
                                    ("sha256", 256000),
                                    ("sha1", 256000),
                                    ("sha1", 64000),
                                ):
                                    expanded_candidates.add(
                                        hashlib.pbkdf2_hmac(algorithm, candidate, salt, iterations, dklen=32)
                                    )
                        diagnostic["derivedCandidates"] += len(expanded_candidates - candidates)
                    for candidate in expanded_candidates:
                        if candidate == b"\x00" * 32:
                            continue
                        for page_size, reserve_size in settings:
                            identity = (candidate, page_size, reserve_size)
                            if identity in tested:
                                continue
                            tested.add(identity)
                            diagnostic["candidateKeysTested"] += 1
                            for path in paths:
                                if path not in found and validate_sqlcipher4_key(
                                    path,
                                    candidate,
                                    page_size=page_size,
                                    reserved_bytes=reserve_size,
                                ):
                                    found[path] = candidate
                    start = position + 1

        if pointer_patterns:
            scan_chunks(follow_salt_references, 16)
    finally:
        kernel32.CloseHandle(handle)
    if on_diagnostic:
        on_diagnostic(dict(diagnostic))
    return found


def decrypt_sqlcipher4_database(
    path: str | os.PathLike[str],
    key: bytes,
    *,
    page_size: int = SQLCIPHER_PAGE_SIZE,
    reserved_bytes: int = SQLCIPHER_RESERVED_BYTES,
) -> bytes:
    """Decrypt a SQLCipher 4 database into RAM without writing a plaintext file."""
    if len(key) != 32:
        raise WechatDatabaseError("SQLCipher 密钥长度无效")
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception as exc:  # pragma: no cover - declared project dependency
        raise WechatDatabaseError("缺少数据库解密组件 cryptography") from exc

    encrypted = Path(path).read_bytes()
    if not encrypted or len(encrypted) % page_size:
        raise WechatDatabaseError(f"数据库页大小异常：{Path(path).name}")
    cipher_end = page_size - reserved_bytes
    output = bytearray(len(encrypted))

    for page_number, page_start in enumerate(range(0, len(encrypted), page_size), start=1):
        page = encrypted[page_start : page_start + page_size]
        cipher_start = 16 if page_number == 1 else 0
        ciphertext = page[cipher_start:cipher_end]
        iv = page[cipher_end : cipher_end + 16]
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        if page_number == 1:
            output[page_start : page_start + 16] = SQLITE_HEADER
            output[page_start + 16 : page_start + cipher_end] = plaintext
        else:
            output[page_start : page_start + cipher_end] = plaintext

    header = bytes(output[:32])
    page_size_header = b"\x00\x01" if page_size == 65536 else page_size.to_bytes(2, "big")
    if not (
        header[:16] == SQLITE_HEADER
        and header[16:18] == page_size_header
        and header[18] in {1, 2}
        and header[19] in {1, 2}
        and header[20] == reserved_bytes
        and header[21:24] == b"\x40\x20\x20"
    ):
        raise WechatDatabaseError(f"数据库密钥验证失败：{Path(path).name}")
    return bytes(output)


def validate_sqlcipher4_key(
    path: str | os.PathLike[str],
    key: bytes,
    *,
    page_size: int = SQLCIPHER_PAGE_SIZE,
    reserved_bytes: int = SQLCIPHER_RESERVED_BYTES,
) -> bool:
    """Validate a raw SQLCipher page key without decrypting the full database."""
    if len(key) != 32:
        return False
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception as exc:  # pragma: no cover - declared project dependency
        raise WechatDatabaseError("缺少数据库解密组件 cryptography") from exc

    with Path(path).open("rb") as handle:
        page = handle.read(page_size)
    if len(page) != page_size:
        return False

    cipher_end = page_size - reserved_bytes
    ciphertext = page[16:cipher_end]
    iv = page[cipher_end : cipher_end + 16]
    try:
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    except ValueError:
        return False

    header = SQLITE_HEADER + plaintext[:16]
    page_size_header = b"\x00\x01" if page_size == 65536 else page_size.to_bytes(2, "big")
    return (
        header[:16] == SQLITE_HEADER
        and header[16:18] == page_size_header
        and header[18] in {1, 2}
        and header[19] in {1, 2}
        and header[20] == reserved_bytes
        and header[21:24] == b"\x40\x20\x20"
    )


def open_decrypted_database(path: Path, key: bytes) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(decrypt_sqlcipher4_database(path, key))
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        connection.row_factory = sqlite3.Row
        return connection
    except Exception:
        connection.close()
        raise
