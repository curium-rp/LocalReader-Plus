import json
import os
import sys
import threading
import time
from pathlib import Path

DEFAULT_WIDTH = 1200
DEFAULT_HEIGHT = 800
MIN_WIDTH = 850
MIN_HEIGHT = 550
SAVE_DEBOUNCE_SEC = 0.5
# Windows restored windows often sit a few pixels past the monitor edge (resize border).
SCREEN_EDGE_SLACK = 64
MIN_VISIBLE_PX = 80


def _as_int(value, default=None):
    if value is None:
        return default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _native(window):
    return getattr(window, "native", None) if window is not None else None


def _native_flag(native, *names):
    if native is None:
        return None
    for name in names:
        value = getattr(native, name, None)
        if value is None:
            continue
        try:
            return bool(value() if callable(value) else value)
        except Exception:
            continue
    return None


def native_is_fullscreen(window) -> bool:
    native = _native(window)
    flag = _native_flag(native, "is_fullscreen", "isFullScreen")
    if flag is not None:
        return flag
    try:
        style = getattr(native, "styleMask", None)
        if callable(style):
            # NSWindowStyleMaskFullScreen = 1 << 14
            return bool(int(style()) & 16384)
    except Exception:
        pass
    return bool(getattr(window, "fullscreen", False)) if window is not None else False


def native_is_maximized(window) -> bool:
    native = _native(window)
    if native is None:
        return False
    flag = _native_flag(native, "is_maximized", "isMaximized", "isZoomed")
    if flag is not None:
        return flag
    try:
        state = getattr(native, "WindowState", None)
        if state is not None:
            text = str(state)
            if "Maximized" in text:
                return True
            if "Normal" in text or "Minimized" in text:
                return False
            return int(state) == 2
    except Exception:
        pass
    return False


def native_is_minimized(window) -> bool:
    native = _native(window)
    if native is None:
        return False
    flag = _native_flag(native, "is_minimized", "isMinimized", "is_iconified", "isMiniaturized")
    if flag is not None:
        return flag
    try:
        state = getattr(native, "WindowState", None)
        if state is not None:
            return "Minimized" in str(state)
    except Exception:
        pass
    return False


def positioning_supported() -> bool:
    """Wayland compositors ignore absolute client placement requests."""
    if sys.platform != "linux":
        return True
    gdk_backend = os.environ.get("GDK_BACKEND", "").strip().lower()
    if gdk_backend == "x11":
        return True
    if gdk_backend == "wayland":
        return False
    if os.environ.get("WAYLAND_DISPLAY"):
        return False
    return os.environ.get("XDG_SESSION_TYPE", "").strip().lower() != "wayland"


def _active_screens():
    try:
        import webview

        return list(webview.screens or [])
    except Exception:
        return []


def _geometry_visible(x, y, width, height, screens) -> bool:
    if x is None or y is None or not screens:
        return True
    w = max(_as_int(width, MIN_WIDTH), 1)
    h = max(_as_int(height, MIN_HEIGHT), 1)
    for screen in screens:
        sx, sy = int(screen.x), int(screen.y)
        sw, sh = int(screen.width), int(screen.height)
        # Origin sits on (or just past) this display.
        if (sx - SCREEN_EDGE_SLACK <= x < sx + sw + SCREEN_EDGE_SLACK) and (
            sy - SCREEN_EDGE_SLACK <= y < sy + sh + SCREEN_EDGE_SLACK
        ):
            return True
        ix1 = max(x, sx)
        iy1 = max(y, sy)
        ix2 = min(x + w, sx + sw)
        iy2 = min(y + h, sy + sh)
        if (ix2 - ix1) >= MIN_VISIBLE_PX and (iy2 - iy1) >= 1:
            return True
    return False


def _read_geometry(window):
    try:
        return (
            _as_int(getattr(window, "x", None)),
            _as_int(getattr(window, "y", None)),
            _as_int(getattr(window, "width", None)),
            _as_int(getattr(window, "height", None)),
        )
    except Exception:
        return None, None, None, None


class WindowStateManager:
    def __init__(self):
        self.config_path = self._resolve_config_path()
        self.save_timer = None
        self.lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._transitioning = False
        self.state, needs_save = self._load()
        if needs_save:
            self._flush_to_disk()

    def _resolve_config_path(self) -> Path:
        from app.config import userdata_dir

        userdata_dir.mkdir(parents=True, exist_ok=True)
        return userdata_dir / "window_state.json"

    def _sanitize_state(self, state: dict) -> bool:
        """
        Guarantees mutually exclusive window state flags.
        A window cannot be both maximized and fullscreen at the same time.
        If both flags are True, automatically resets them to prevent lockout.
        Returns True if state was modified/reset.
        """
        if not isinstance(state, dict):
            return False
        modified = False
        is_max = bool(state.get("is_maximized", False))
        is_fs = bool(state.get("is_fullscreen", False))

        if is_max and is_fs:
            print(
                "[WINDOW] Invalid state detected in window state: both 'is_maximized' and 'is_fullscreen' are True. "
                "Automatically resetting to non-maximized, non-fullscreen normal state."
            )
            state["is_maximized"] = False
            state["is_fullscreen"] = False
            state["pre_fullscreen_state"] = "normal"
            modified = True

        pre = str(state.get("pre_fullscreen_state") or "normal").lower()
        if pre not in ("maximized", "normal"):
            state["pre_fullscreen_state"] = "normal"
            modified = True

        return modified

    def _load(self) -> tuple[dict, bool]:
        fallback = {
            "x": None,
            "y": None,
            "width": DEFAULT_WIDTH,
            "height": DEFAULT_HEIGHT,
            "is_maximized": False,
            "is_fullscreen": False,
            "pre_fullscreen_state": "normal",
        }
        if not self.config_path.exists():
            return fallback, False
        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return fallback, True
            merged = {**fallback, **data}
            merged["x"] = _as_int(merged.get("x"), None)
            merged["y"] = _as_int(merged.get("y"), None)
            merged["width"] = max(MIN_WIDTH, _as_int(merged.get("width"), DEFAULT_WIDTH))
            merged["height"] = max(MIN_HEIGHT, _as_int(merged.get("height"), DEFAULT_HEIGHT))
            merged["is_maximized"] = bool(merged.get("is_maximized", False))
            merged["is_fullscreen"] = bool(merged.get("is_fullscreen", False))
            pre = str(merged.get("pre_fullscreen_state") or "normal").lower()
            merged["pre_fullscreen_state"] = "maximized" if pre == "maximized" else "normal"

            needs_save = self._sanitize_state(merged)
            return merged, needs_save
        except Exception:
            return fallback, False

    def get_initial_bounds(self):
        with self.lock:
            self._sanitize_state(self.state)
            screens = _active_screens()
            x = self.state.get("x")
            y = self.state.get("y")
            width = max(MIN_WIDTH, _as_int(self.state.get("width"), DEFAULT_WIDTH))
            height = max(MIN_HEIGHT, _as_int(self.state.get("height"), DEFAULT_HEIGHT))

            if screens:
                max_w = max(int(s.width) for s in screens)
                max_h = max(int(s.height) for s in screens)
                width = min(width, max(MIN_WIDTH, max_w))
                height = min(height, max(MIN_HEIGHT, max_h))
                if not _geometry_visible(x, y, width, height, screens):
                    x = None
                    y = None

            if not positioning_supported():
                x = None
                y = None

            is_fs = bool(self.state.get("is_fullscreen", False))
            is_max = False if is_fs else bool(self.state.get("is_maximized", False))

            return {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "fullscreen": is_fs,
                "maximized": is_max,
                "pre_fullscreen_state": self.state.get("pre_fullscreen_state", "normal"),
            }

    def begin_transition(self):
        with self.lock:
            self._transitioning = True
            if self.save_timer:
                self.save_timer.cancel()
                self.save_timer = None

    def end_transition(self):
        with self.lock:
            self._transitioning = False
        self._flush_to_disk()

    def update_from_window(self, window, width=None, height=None, x=None, y=None, *, read_live=False):
        if window is None:
            return

        is_fullscreen = native_is_fullscreen(window)
        is_maximized = native_is_maximized(window)
        is_minimized = native_is_minimized(window)

        with self.lock:
            if is_minimized:
                return
            if self._transitioning and not read_live:
                return

            if read_live:
                self.state["is_fullscreen"] = is_fullscreen
                if not is_fullscreen:
                    self.state["is_maximized"] = bool(is_maximized)
                else:
                    self.state["is_maximized"] = False
            else:
                # Transition races can report maximized while entering fullscreen.
                if is_fullscreen:
                    self.state["is_fullscreen"] = True
                    self.state["is_maximized"] = False
                elif is_maximized:
                    self.state["is_maximized"] = True
                    self.state["is_fullscreen"] = False
                else:
                    self.state["is_fullscreen"] = False
                    self.state["is_maximized"] = False

            # Never save screen-sized coordinates into floating bounds.
            if is_fullscreen or is_maximized or self.state.get("is_fullscreen"):
                return

            if read_live:
                nx, ny, nw, nh = _read_geometry(window)
                x = nx if x is None else x
                y = ny if y is None else y
                width = nw if width is None else width
                height = nh if height is None else height

            width = _as_int(width)
            height = _as_int(height)
            x = _as_int(x)
            y = _as_int(y)

            if width is not None and width >= MIN_WIDTH:
                self.state["width"] = width
            if height is not None and height >= MIN_HEIGHT:
                self.state["height"] = height
            if x is not None:
                self.state["x"] = x
            if y is not None:
                self.state["y"] = y

    def debounce_save(self, window, width=None, height=None, x=None, y=None):
        with self.lock:
            if self._transitioning:
                return
        self.update_from_window(window, width=width, height=height, x=x, y=y)
        with self.lock:
            if self.save_timer:
                self.save_timer.cancel()
            self.save_timer = threading.Timer(SAVE_DEBOUNCE_SEC, self._flush_to_disk)
            self.save_timer.daemon = True
            self.save_timer.start()

    def save_immediate(self, window):
        self.update_from_window(window, read_live=True)
        with self.lock:
            if self.save_timer:
                self.save_timer.cancel()
                self.save_timer = None
        self._flush_to_disk()

    def stamp_maximized(self, enabled: bool):
        target = bool(enabled)
        with self.lock:
            if self.state.get("is_maximized") == target and (not target or not self.state.get("is_fullscreen")):
                return
            self.state["is_maximized"] = target
            if target:
                self.state["is_fullscreen"] = False
        self._flush_to_disk()

    def enter_fullscreen(self, pre_state: str):
        pre = "maximized" if pre_state == "maximized" else "normal"
        with self.lock:
            if self.state.get("is_fullscreen") and self.state.get("pre_fullscreen_state") == pre:
                return
            self.state["pre_fullscreen_state"] = pre
            self.state["is_fullscreen"] = True
            self.state["is_maximized"] = False
        self._flush_to_disk()

    def exit_fullscreen(self):
        with self.lock:
            if not self.state.get("is_fullscreen"):
                return
            self.state["is_fullscreen"] = False
        self._flush_to_disk()

    def apply_restored_bounds(self, window):
        if window is None:
            return
        with self.lock:
            width = max(MIN_WIDTH, _as_int(self.state.get("width"), DEFAULT_WIDTH))
            height = max(MIN_HEIGHT, _as_int(self.state.get("height"), DEFAULT_HEIGHT))
            x = self.state.get("x")
            y = self.state.get("y")
        try:
            if hasattr(window, "resize"):
                window.resize(width, height)
            if x is not None and y is not None and hasattr(window, "move") and positioning_supported():
                window.move(int(x), int(y))
        except Exception as exc:
            print(f"[WINDOW] Failed to apply restored bounds: {exc}")

    def _flush_to_disk(self):
        with self.lock:
            self._sanitize_state(self.state)
            payload = dict(self.state)
            path = self.config_path

        with self._io_lock:
            temp_path = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = path.with_name(
                    f"{path.stem}_{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.tmp"
                )
                with open(temp_path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())

                replaced = False
                for attempt in range(3):
                    try:
                        temp_path.replace(path)
                        replaced = True
                        break
                    except (PermissionError, OSError):
                        time.sleep(0.02 * (2 ** attempt))

                if not replaced:
                    # Fallback for network shares / mapped drives (e.g. SMB Z:) where atomic replace fails
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump(payload, handle, indent=2)
                        handle.flush()
                        os.fsync(handle.fileno())
            except Exception as exc:
                print(f"[WINDOW] Failed to save window state: {exc}")
            finally:
                if temp_path and temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass

