"""Governed computer-use primitives for W1 Nexus.

Step 36 extends the bounded Step 35 foundation with local computer perception
and secure ephemeral text input.  Sensitive screen/UI reads remain governed,
selector-bound input is fingerprint checked at execution time, and free text is
never embedded in an ActionRequest or persisted in the action journal.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import platform
import secrets
import struct
import threading
import time
import zlib
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol, Sequence


NAVIGATION_KEYS = frozenset(
    {
        "tab",
        "enter",
        "escape",
        "space",
        "left",
        "right",
        "up",
        "down",
        "home",
        "end",
        "pageup",
        "pagedown",
    }
)
POINTER_BUTTONS = frozenset({"left", "right", "middle"})


class ComputerUseError(RuntimeError):
    code = "computer_use_error"


class ComputerBackendUnavailable(ComputerUseError):
    code = "computer_backend_unavailable"


class ComputerInputDenied(ComputerUseError):
    code = "computer_input_denied"


class ComputerSelectorError(ComputerUseError):
    code = "computer_selector_error"


class ComputerElementNotFound(ComputerSelectorError):
    code = "computer_element_not_found"


class ComputerElementAmbiguous(ComputerSelectorError):
    code = "computer_element_ambiguous"


class ComputerElementChanged(ComputerSelectorError):
    code = "computer_element_changed"


class EphemeralInputError(ComputerUseError):
    code = "ephemeral_input_error"


@dataclass(frozen=True)
class ComputerObservation:
    backend: str
    platform: str
    screen_width: int
    screen_height: int
    cursor_x: int
    cursor_y: int
    active_window_title: str | None
    captured_at_monotonic: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputerScreenshot:
    backend: str
    screen_width: int
    screen_height: int
    png_bytes: bytes
    captured_at_monotonic: float

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.png_bytes).hexdigest()

    def audit_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "bytes": len(self.png_bytes),
            "sha256": self.sha256,
            "captured_at_monotonic": self.captured_at_monotonic,
        }


@dataclass(frozen=True)
class ComputerElement:
    element_id: str
    backend: str
    role: str
    name: str
    class_name: str
    bounds: tuple[int, int, int, int]
    visible: bool = True
    enabled: bool = True
    parent_id: str | None = None
    control_id: int | None = None
    process_id: int | None = None
    window_title: str | None = None

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)

    @property
    def fingerprint(self) -> str:
        # Deliberately includes geometry.  A moved/replaced target requires a
        # fresh inspection/approval instead of silently replaying a click.
        material = {
            "backend": self.backend,
            "role": self.role,
            "name": self.name,
            "class_name": self.class_name,
            "bounds": list(self.bounds),
            "control_id": self.control_id,
            "process_id": self.process_id,
            "window_title": self.window_title,
        }
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["bounds"] = list(self.bounds)
        payload["fingerprint"] = self.fingerprint
        payload["center"] = list(self.center)
        return payload


@dataclass(frozen=True)
class EphemeralInputRef:
    handle: str
    binding: str
    length: int
    expires_at_monotonic: float

    def as_request_parameters(self) -> dict[str, Any]:
        return {
            "input_handle": self.handle,
            "input_binding": self.binding,
            "text_length": self.length,
        }


@dataclass
class _EphemeralEntry:
    data: bytearray
    binding: str
    expires_at_monotonic: float


class EphemeralInputVault:
    """Process-local one-shot text channel.

    The text is held only in memory as a mutable UTF-8 bytearray.  Journalled
    requests contain a random handle, opaque HMAC binding and length only.  The
    entry is consumed once and the bytearray is overwritten best-effort.
    """

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._entries: dict[str, _EphemeralEntry] = {}
        self._lock = threading.RLock()

    def put(self, text: str, *, ttl_seconds: int = 120, max_chars: int = 4096) -> EphemeralInputRef:
        if not isinstance(text, str):
            raise TypeError("ephemeral input must be text")
        if not text:
            raise EphemeralInputError("ephemeral input must not be empty")
        if len(text) > int(max_chars):
            raise EphemeralInputError(f"ephemeral input exceeds {int(max_chars)} characters")
        if not 1 <= int(ttl_seconds) <= 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600")
        handle = "input-" + secrets.token_hex(16)
        data = bytearray(text.encode("utf-8"))
        binding = hmac.new(self._key, handle.encode("utf-8") + b"\0" + bytes(data), hashlib.sha256).hexdigest()
        expires = time.monotonic() + int(ttl_seconds)
        with self._lock:
            self._entries[handle] = _EphemeralEntry(data=data, binding=binding, expires_at_monotonic=expires)
        return EphemeralInputRef(handle=handle, binding=binding, length=len(text), expires_at_monotonic=expires)

    def consume(self, handle: str, *, binding: str, expected_length: int) -> str:
        with self._lock:
            entry = self._entries.pop(str(handle), None)
        if entry is None:
            raise EphemeralInputError("ephemeral input handle is missing, expired, or already consumed")
        try:
            if time.monotonic() > entry.expires_at_monotonic:
                raise EphemeralInputError("ephemeral input expired")
            if not hmac.compare_digest(entry.binding, str(binding)):
                raise EphemeralInputError("ephemeral input binding mismatch")
            text = bytes(entry.data).decode("utf-8")
            if len(text) != int(expected_length):
                raise EphemeralInputError("ephemeral input length mismatch")
            return text
        finally:
            for index in range(len(entry.data)):
                entry.data[index] = 0

    def discard(self, handle: str) -> None:
        with self._lock:
            entry = self._entries.pop(str(handle), None)
        if entry is not None:
            for index in range(len(entry.data)):
                entry.data[index] = 0


class ComputerDriver(Protocol):
    backend_name: str

    @property
    def available(self) -> bool: ...

    def observe(self) -> ComputerObservation: ...

    def capture_screenshot(self) -> ComputerScreenshot: ...

    def list_elements(self, *, limit: int = 2048) -> list[ComputerElement]: ...

    def move_pointer(self, x: int, y: int) -> None: ...

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> None: ...

    def scroll(self, x: int, y: int, *, delta: int) -> None: ...

    def press_key(self, key: str) -> None: ...

    def type_text(self, text: str) -> None: ...


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png_rgb(width: int, height: int, rows: Sequence[bytes]) -> bytes:
    if width <= 0 or height <= 0 or len(rows) != height:
        raise ValueError("invalid PNG dimensions")
    raw = bytearray()
    expected = width * 3
    for row in rows:
        if len(row) != expected:
            raise ValueError("invalid PNG row length")
        raw.append(0)  # no filter
        raw.extend(row)
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _png_chunk(b"IEND", b"")


def element_matches(element: ComputerElement, selector: Mapping[str, Any]) -> bool:
    if not isinstance(selector, Mapping):
        raise ComputerSelectorError("selector must be an object")
    supported = {"element_id", "role", "name", "name_glob", "class_name", "control_id", "process_id", "window_title", "window_glob"}
    unknown = set(selector) - supported
    if unknown:
        raise ComputerSelectorError("unknown selector fields: " + ", ".join(sorted(str(item) for item in unknown)))
    if not selector:
        raise ComputerSelectorError("selector must contain at least one field")
    for key in ("element_id", "role", "name", "class_name", "window_title"):
        if key in selector and str(getattr(element, key)) != str(selector[key]):
            return False
    if "control_id" in selector and element.control_id != int(selector["control_id"]):
        return False
    if "process_id" in selector and element.process_id != int(selector["process_id"]):
        return False
    if "name_glob" in selector and not fnmatch.fnmatchcase(element.name, str(selector["name_glob"])):
        return False
    if "window_glob" in selector and not fnmatch.fnmatchcase(element.window_title or "", str(selector["window_glob"])):
        return False
    return True


def find_elements(driver: ComputerDriver, selector: Mapping[str, Any], *, limit: int = 2048) -> list[ComputerElement]:
    return [item for item in driver.list_elements(limit=limit) if element_matches(item, selector)]


def resolve_unique_element(
    driver: ComputerDriver,
    selector: Mapping[str, Any],
    *,
    expected_fingerprint: str | None = None,
    limit: int = 2048,
) -> ComputerElement:
    matches = find_elements(driver, selector, limit=limit)
    if not matches:
        raise ComputerElementNotFound("selector matched no UI element")
    if len(matches) != 1:
        raise ComputerElementAmbiguous(f"selector matched {len(matches)} UI elements; refine it before acting")
    element = matches[0]
    if expected_fingerprint is not None and not hmac.compare_digest(element.fingerprint, str(expected_fingerprint)):
        raise ComputerElementChanged("target fingerprint changed since inspection; refusing stale selector action")
    return element


class UnavailableComputerDriver:
    backend_name = "unavailable"

    def __init__(self, reason: str = "No supported native computer-use backend is available") -> None:
        self.reason = reason

    @property
    def available(self) -> bool:
        return False

    def _raise(self) -> None:
        raise ComputerBackendUnavailable(self.reason)

    def observe(self) -> ComputerObservation:
        self._raise()
        raise AssertionError("unreachable")

    def capture_screenshot(self) -> ComputerScreenshot:
        self._raise()
        raise AssertionError("unreachable")

    def list_elements(self, *, limit: int = 2048) -> list[ComputerElement]:
        self._raise()
        raise AssertionError("unreachable")

    def move_pointer(self, x: int, y: int) -> None:
        self._raise()

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
        self._raise()

    def scroll(self, x: int, y: int, *, delta: int) -> None:
        self._raise()

    def press_key(self, key: str) -> None:
        self._raise()

    def type_text(self, text: str) -> None:
        self._raise()


class VirtualComputerDriver:
    """Deterministic in-memory driver used by tests and capability probes."""

    backend_name = "virtual"

    def __init__(self, *, width: int = 1920, height: int = 1080) -> None:
        self.width = int(width)
        self.height = int(height)
        self.cursor_x = 0
        self.cursor_y = 0
        self.active_window_title = "W1 Virtual App"
        self.events: list[dict[str, Any]] = []
        margin = max(8, min(40, self.width // 8, self.height // 8))
        left = max(margin + 8, self.width // 6)
        right = max(left + 20, min(self.width - margin - 1, left + max(80, self.width // 2)))
        input_top = max(margin + 8, self.height // 3 - 15)
        input_bottom = min(self.height - margin - 1, input_top + max(24, self.height // 8))
        button_top = max(input_bottom + 8, (self.height * 2) // 3 - 12)
        button_bottom = min(self.height - margin - 1, button_top + max(24, self.height // 8))
        if button_bottom <= button_top:
            button_top = max(margin + 1, self.height - margin - 28)
            button_bottom = max(button_top + 1, self.height - margin - 1)
        self.elements: list[ComputerElement] = [
            ComputerElement(
                element_id="virtual-window-main", backend=self.backend_name, role="window", name="W1 Virtual App",
                class_name="VirtualWindow", bounds=(margin, margin, self.width - margin, self.height - margin),
                process_id=1001, window_title="W1 Virtual App",
            ),
            ComputerElement(
                element_id="virtual-input-name", backend=self.backend_name, role="textbox", name="Name",
                class_name="VirtualEdit", bounds=(left, input_top, right, input_bottom),
                parent_id="virtual-window-main", control_id=101, process_id=1001, window_title="W1 Virtual App",
            ),
            ComputerElement(
                element_id="virtual-button-submit", backend=self.backend_name, role="button", name="Submit",
                class_name="VirtualButton", bounds=(left, button_top, min(right, left + max(60, self.width // 4)), button_bottom),
                parent_id="virtual-window-main", control_id=102, process_id=1001, window_title="W1 Virtual App",
            ),
        ]

    @property
    def available(self) -> bool:
        return True

    def _point(self, x: int, y: int) -> tuple[int, int]:
        if not (0 <= int(x) < self.width and 0 <= int(y) < self.height):
            raise ComputerInputDenied("pointer coordinate outside virtual screen")
        return int(x), int(y)

    def observe(self) -> ComputerObservation:
        return ComputerObservation(
            backend=self.backend_name,
            platform="virtual",
            screen_width=self.width,
            screen_height=self.height,
            cursor_x=self.cursor_x,
            cursor_y=self.cursor_y,
            active_window_title=self.active_window_title,
            captured_at_monotonic=time.monotonic(),
        )

    def capture_screenshot(self) -> ComputerScreenshot:
        # Deterministic lightweight representation of the virtual desktop.
        rows: list[bytes] = []
        for y in range(self.height):
            row = bytearray()
            for x in range(self.width):
                inside_window = 40 <= x < self.width - 40 and 40 <= y < self.height - 40
                if inside_window:
                    row.extend((232, 236, 241))
                else:
                    row.extend((35, 42, 52))
            rows.append(bytes(row))
        png = encode_png_rgb(self.width, self.height, rows)
        return ComputerScreenshot(
            backend=self.backend_name,
            screen_width=self.width,
            screen_height=self.height,
            png_bytes=png,
            captured_at_monotonic=time.monotonic(),
        )

    def list_elements(self, *, limit: int = 2048) -> list[ComputerElement]:
        return list(self.elements[: max(0, int(limit))])

    def move_pointer(self, x: int, y: int) -> None:
        x, y = self._point(x, y)
        self.cursor_x, self.cursor_y = x, y
        self.events.append({"kind": "move", "x": x, "y": y})

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
        x, y = self._point(x, y)
        if button not in POINTER_BUTTONS:
            raise ComputerInputDenied("unsupported pointer button")
        if not 1 <= int(clicks) <= 3:
            raise ComputerInputDenied("click count outside bounded policy")
        self.cursor_x, self.cursor_y = x, y
        self.events.append({"kind": "click", "x": x, "y": y, "button": button, "clicks": int(clicks)})

    def scroll(self, x: int, y: int, *, delta: int) -> None:
        x, y = self._point(x, y)
        self.cursor_x, self.cursor_y = x, y
        self.events.append({"kind": "scroll", "x": x, "y": y, "delta": int(delta)})

    def press_key(self, key: str) -> None:
        key = str(key).lower()
        if key not in NAVIGATION_KEYS:
            raise ComputerInputDenied("only bounded navigation keys are supported through press_key")
        self.events.append({"kind": "key", "key": key})

    def type_text(self, text: str) -> None:
        if not isinstance(text, str) or not text:
            raise ComputerInputDenied("text input must be a non-empty string")
        # Never retain plaintext, even in the virtual test event log.
        self.events.append({"kind": "type_text", "length": len(text)})


class WindowsNativeComputerDriver:
    """Dependency-free Windows backend based on Win32 user32/gdi32 APIs.

    The element surface is a conservative Win32 accessibility-lite tree based
    on visible windows/child controls.  It does not claim parity with the full
    Microsoft UI Automation provider model.
    """

    backend_name = "windows-native"

    _VK = {
        "tab": 0x09,
        "enter": 0x0D,
        "escape": 0x1B,
        "space": 0x20,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "home": 0x24,
        "end": 0x23,
        "pageup": 0x21,
        "pagedown": 0x22,
    }
    _BUTTON_FLAGS = {
        "left": (0x0002, 0x0004),
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }
    _ROLE_BY_CLASS = {
        "button": "button",
        "edit": "textbox",
        "richedit": "textbox",
        "richedit20w": "textbox",
        "combobox": "combobox",
        "listbox": "list",
        "syslistview32": "list",
        "systreeview32": "tree",
        "static": "text",
        "scrollbar": "scrollbar",
    }

    def __init__(self) -> None:
        if platform.system().lower() != "windows":
            raise ComputerBackendUnavailable("Windows native backend requested on a non-Windows host")
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32

    @property
    def available(self) -> bool:
        return True

    def _screen_size(self) -> tuple[int, int]:
        return int(self._user32.GetSystemMetrics(0)), int(self._user32.GetSystemMetrics(1))

    def _validate_point(self, x: int, y: int) -> tuple[int, int]:
        width, height = self._screen_size()
        x, y = int(x), int(y)
        if not (0 <= x < width and 0 <= y < height):
            raise ComputerInputDenied("pointer coordinate outside primary screen bounds")
        return x, y

    def _window_text(self, hwnd: int) -> str:
        length = int(self._user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buffer = self._ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()

    def _class_name(self, hwnd: int) -> str:
        buffer = self._ctypes.create_unicode_buffer(256)
        copied = int(self._user32.GetClassNameW(hwnd, buffer, 256))
        return buffer.value[:copied] if copied > 0 else ""

    def _active_window_title(self) -> str | None:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None
        title = self._window_text(hwnd)
        return title or None

    def observe(self) -> ComputerObservation:
        class POINT(self._ctypes.Structure):
            _fields_ = [("x", self._wintypes.LONG), ("y", self._wintypes.LONG)]

        point = POINT()
        if not self._user32.GetCursorPos(self._ctypes.byref(point)):
            raise ComputerUseError("GetCursorPos failed")
        width, height = self._screen_size()
        return ComputerObservation(
            backend=self.backend_name,
            platform=platform.platform(),
            screen_width=width,
            screen_height=height,
            cursor_x=int(point.x),
            cursor_y=int(point.y),
            active_window_title=self._active_window_title(),
            captured_at_monotonic=time.monotonic(),
        )

    def capture_screenshot(self) -> ComputerScreenshot:
        ctypes, wintypes = self._ctypes, self._wintypes
        width, height = self._screen_size()
        screen_dc = self._user32.GetDC(0)
        if not screen_dc:
            raise ComputerUseError("GetDC failed")
        memory_dc = self._gdi32.CreateCompatibleDC(screen_dc)
        bitmap = self._gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        old = self._gdi32.SelectObject(memory_dc, bitmap)
        try:
            SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
            if not self._gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, 0, 0, SRCCOPY | CAPTUREBLT):
                raise ComputerUseError("BitBlt screenshot capture failed")

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
                ]

            class RGBQUAD(ctypes.Structure):
                _fields_ = [("rgbBlue", ctypes.c_ubyte), ("rgbGreen", ctypes.c_ubyte), ("rgbRed", ctypes.c_ubyte), ("rgbReserved", ctypes.c_ubyte)]

            class BITMAPINFO(ctypes.Structure):
                _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]

            stride = ((width * 3 + 3) // 4) * 4
            size = stride * height
            buffer = (ctypes.c_ubyte * size)()
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height  # top-down
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 24
            info.bmiHeader.biCompression = 0
            copied = self._gdi32.GetDIBits(memory_dc, bitmap, 0, height, ctypes.byref(buffer), ctypes.byref(info), 0)
            if copied != height:
                raise ComputerUseError("GetDIBits screenshot read failed")
            raw = bytes(buffer)
            rows: list[bytes] = []
            for y in range(height):
                source = raw[y * stride : y * stride + width * 3]
                row = bytearray(width * 3)
                for offset in range(0, width * 3, 3):
                    row[offset] = source[offset + 2]
                    row[offset + 1] = source[offset + 1]
                    row[offset + 2] = source[offset]
                rows.append(bytes(row))
            png = encode_png_rgb(width, height, rows)
            return ComputerScreenshot(self.backend_name, width, height, png, time.monotonic())
        finally:
            if old:
                self._gdi32.SelectObject(memory_dc, old)
            if bitmap:
                self._gdi32.DeleteObject(bitmap)
            if memory_dc:
                self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(0, screen_dc)

    def list_elements(self, *, limit: int = 2048) -> list[ComputerElement]:
        ctypes, wintypes = self._ctypes, self._wintypes
        limit = max(1, min(int(limit), 10000))
        elements: list[ComputerElement] = []
        seen: set[int] = set()

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        def add(hwnd: int, parent_hwnd: int | None) -> bool:
            hwnd = int(hwnd)
            if hwnd in seen or len(elements) >= limit or not self._user32.IsWindowVisible(hwnd):
                return len(elements) < limit
            seen.add(hwnd)
            rect = RECT()
            if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return len(elements) < limit
            if rect.right <= rect.left or rect.bottom <= rect.top:
                return len(elements) < limit
            class_name = self._class_name(hwnd)
            name = self._window_text(hwnd)
            lowered = class_name.lower()
            role = self._ROLE_BY_CLASS.get(lowered, "window" if parent_hwnd is None else "control")
            control_id = int(self._user32.GetDlgCtrlID(hwnd)) if parent_hwnd is not None else None
            pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            top = hwnd
            while self._user32.GetParent(top):
                top = int(self._user32.GetParent(top))
            window_title = self._window_text(top) or None
            elements.append(
                ComputerElement(
                    element_id=f"win32-{hwnd:x}", backend=self.backend_name, role=role, name=name,
                    class_name=class_name, bounds=(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
                    visible=True, enabled=bool(self._user32.IsWindowEnabled(hwnd)),
                    parent_id=f"win32-{int(parent_hwnd):x}" if parent_hwnd else None,
                    control_id=control_id, process_id=int(pid.value), window_title=window_title,
                )
            )
            return len(elements) < limit

        CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @CALLBACK
        def child_callback(hwnd, lparam):
            parent = self._user32.GetParent(hwnd)
            return bool(add(int(hwnd), int(parent) if parent else None))

        @CALLBACK
        def top_callback(hwnd, lparam):
            if not add(int(hwnd), None):
                return False
            self._user32.EnumChildWindows(hwnd, child_callback, 0)
            return len(elements) < limit

        self._user32.EnumWindows(top_callback, 0)
        return elements[:limit]

    def move_pointer(self, x: int, y: int) -> None:
        x, y = self._validate_point(x, y)
        if not self._user32.SetCursorPos(x, y):
            raise ComputerUseError("SetCursorPos failed")

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
        x, y = self._validate_point(x, y)
        button = str(button).lower()
        if button not in self._BUTTON_FLAGS:
            raise ComputerInputDenied("unsupported pointer button")
        clicks = int(clicks)
        if not 1 <= clicks <= 3:
            raise ComputerInputDenied("click count must be between 1 and 3")
        self.move_pointer(x, y)
        down, up = self._BUTTON_FLAGS[button]
        for _ in range(clicks):
            self._user32.mouse_event(down, 0, 0, 0, 0)
            self._user32.mouse_event(up, 0, 0, 0, 0)

    def scroll(self, x: int, y: int, *, delta: int) -> None:
        x, y = self._validate_point(x, y)
        self.move_pointer(x, y)
        self._user32.mouse_event(0x0800, 0, 0, int(delta), 0)

    def press_key(self, key: str) -> None:
        key = str(key).lower()
        vk = self._VK.get(key)
        if vk is None:
            raise ComputerInputDenied("only bounded navigation keys are supported through press_key")
        self._user32.keybd_event(vk, 0, 0, 0)
        self._user32.keybd_event(vk, 0, 0x0002, 0)

    def type_text(self, text: str) -> None:
        if not isinstance(text, str) or not text:
            raise ComputerInputDenied("text input must be a non-empty string")
        KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
        units = text.encode("utf-16-le")
        for offset in range(0, len(units), 2):
            code_unit = units[offset] | (units[offset + 1] << 8)
            if code_unit == 0:
                continue
            self._user32.keybd_event(0, code_unit, KEYEVENTF_UNICODE, 0)
            self._user32.keybd_event(0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)


def create_native_computer_driver() -> ComputerDriver:
    if platform.system().lower() == "windows":
        try:
            return WindowsNativeComputerDriver()
        except Exception as exc:
            return UnavailableComputerDriver(str(exc))
    return UnavailableComputerDriver(f"Native computer use is not implemented for {platform.system() or 'this platform'}")


def detect_computer_backend() -> dict[str, Any]:
    driver = create_native_computer_driver()
    payload: dict[str, Any] = {
        "backend": getattr(driver, "backend_name", "unknown"),
        "available": bool(driver.available),
        "platform": platform.system() or "unknown",
        "capabilities": (
            ["observe", "screenshot", "ui_elements", "selector_click", "ephemeral_text", "pointer_move", "pointer_click", "scroll", "navigation_key"]
            if driver.available
            else []
        ),
        "free_text_input": bool(driver.available),
        "free_text_persistence": False,
        "screenshot_capture": bool(driver.available),
        "accessibility_surface": "win32-accessibility-lite" if driver.available and platform.system().lower() == "windows" else None,
        "secret_input_channel": bool(driver.available),
        "multi_platform_native_adapters": False,
    }
    if not driver.available and isinstance(driver, UnavailableComputerDriver):
        payload["reason"] = driver.reason
    return payload


def run_computer_use_benchmark() -> dict[str, Any]:
    """Run deterministic driver/perception probes without touching the host desktop."""

    driver = VirtualComputerDriver(width=320, height=180)
    first = driver.observe()
    screenshot = driver.capture_screenshot()
    elements = driver.list_elements()
    target = resolve_unique_element(driver, {"role": "button", "name": "Submit"})
    driver.move_pointer(100, 120)
    driver.click(*target.center, button="left", clicks=1)
    driver.scroll(*target.center, delta=-120)
    driver.press_key("tab")
    driver.type_text("ephemeral-demo")
    final = driver.observe()
    denied_text_key = False
    try:
        driver.press_key("a")
    except ComputerInputDenied:
        denied_text_key = True
    stale_fingerprint_denied = False
    try:
        resolve_unique_element(driver, {"role": "button", "name": "Submit"}, expected_fingerprint="0" * 64)
    except ComputerElementChanged:
        stale_fingerprint_denied = True
    probes = {
        "observation_available": first.screen_width == 320 and first.screen_height == 180,
        "screenshot_png_available": screenshot.png_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(screenshot.sha256) == 64,
        "ui_elements_available": len(elements) >= 3 and target.role == "button",
        "selector_fingerprint_available": len(target.fingerprint) == 64,
        "stale_fingerprint_denied": stale_fingerprint_denied,
        "pointer_move_available": any(item["kind"] == "move" for item in driver.events),
        "bounded_click_available": any(item["kind"] == "click" for item in driver.events),
        "bounded_scroll_available": any(item["kind"] == "scroll" for item in driver.events),
        "navigation_key_available": any(item["kind"] == "key" for item in driver.events),
        "ephemeral_text_driver_available": any(item["kind"] == "type_text" and "text" not in item for item in driver.events),
        "free_text_key_denied": denied_text_key,
        "final_cursor_observable": (final.cursor_x, final.cursor_y) == target.center,
    }
    return {
        "passed": all(probes.values()),
        "probes": probes,
        "metrics": {"event_count": len(driver.events), "element_count": len(elements), "screenshot_bytes": len(screenshot.png_bytes)},
        "scope": "deterministic virtual perception/input benchmark; host desktop is not modified",
    }
