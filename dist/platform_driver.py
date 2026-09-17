import os
import sys
import time
import platform
import threading
from pathlib import Path

class BasePlatformDriver:
    """Base interface for OS-specific window chrome, movement, and state management."""

    def configure_engine(self) -> None:
        """Called at process startup before webview starts."""
        pass

    def setup_window(self, window) -> None:
        """Called once the native window is shown to configure borders and properties."""
        pass

    def start_move(self, window, screen_x: int = 0, screen_y: int = 0) -> None:
        """Initiate native window drag from titlebar."""
        pass

    def start_resize(self, window, edge: str, screen_x: int = 0, screen_y: int = 0) -> None:
        """Initiate native window resize from border edge."""
        if not window or self.is_fullscreen(window) or self.is_maximized(window):
            return

    def apply_geometry(self, window, x: int, y: int, width: int, height: int) -> None:
        """Apply window position and size across platforms."""
        if hasattr(window, "resize"):
            window.resize(width, height)
        if hasattr(window, "move") and self.positioning_supported():
            window.move(int(x), int(y))

    def get_geometry(self, window):
        """Return (x, y, width, height) in logical units or None if unavailable."""
        return None, None, None, None

    def maximize(self, window) -> None:
        """Maximize the native window."""
        if hasattr(window, "maximize"):
            window.maximize()

    def restore(self, window) -> None:
        """Restore the window from maximized or fullscreen state."""
        if hasattr(window, "restore"):
            window.restore()

    def enter_fullscreen(self, window) -> None:
        """Enter fullscreen mode."""
        if hasattr(window, "toggle_fullscreen"):
            window.toggle_fullscreen()

    def exit_fullscreen(self, window) -> None:
        """Exit fullscreen mode."""
        if hasattr(window, "toggle_fullscreen"):
            window.toggle_fullscreen()

    def close(self, window) -> None:
        """Close/destroy the native window."""
        if window and hasattr(window, "destroy"):
            try:
                window.destroy()
            except Exception:
                pass

    def is_maximized(self, window) -> bool:
        native = getattr(window, "native", None) if window else None
        if native is None:
            return False
        for name in ("is_maximized", "isMaximized", "isZoomed"):
            val = getattr(native, name, None)
            if val is not None:
                try:
                    return bool(val() if callable(val) else val)
                except Exception:
                    pass
        return False

    def is_fullscreen(self, window) -> bool:
        native = getattr(window, "native", None) if window else None
        if native is None:
            return bool(getattr(window, "fullscreen", False)) if window else False
        for name in ("is_fullscreen", "isFullScreen"):
            val = getattr(native, name, None)
            if val is not None:
                try:
                    return bool(val() if callable(val) else val)
                except Exception:
                    pass
        return bool(getattr(window, "fullscreen", False)) if window else False

    def is_minimized(self, window) -> bool:
        native = getattr(window, "native", None) if window else None
        if native is None:
            return False
        for name in ("is_minimized", "isMinimized", "is_iconified", "isMiniaturized"):
            val = getattr(native, name, None)
            if val is not None:
                try:
                    return bool(val() if callable(val) else val)
                except Exception:
                    pass
        return False

    def positioning_supported(self) -> bool:
        return True

    def setup_drag_drop(self, window) -> None:
        """Hook point for OS-specific native drag-and-drop registration."""
        pass

    def teardown_drag_drop(self, window) -> None:
        """Hook point for OS-specific native drag-and-drop cleanup."""
        pass

    def setup_snap_overlay(self, window) -> None:
        """Hook point for OS-specific native snap layout overlay registration."""
        pass

    def teardown_snap_overlay(self, window) -> None:
        """Hook point for OS-specific native snap layout overlay cleanup."""
        pass

    def _handle_native_file_dropped(self, window, file_path: str) -> None:
        """Universal platform handler for native dropped files across Windows, Linux, and macOS."""
        if not file_path:
            return
        path_str = str(file_path).strip()

        # Debounce rapid duplicate drop triggers (e.g. from both Form and child control within 500ms)
        now = time.time()
        last_time, last_path = getattr(self, "_last_drop_event", (0.0, ""))
        if path_str.lower() == last_path.lower() and (now - last_time) < 0.5:
            print(f"[NATIVE DROP] Debounced duplicate drop trigger for: {path_str}")
            return
        self._last_drop_event = (now, path_str)

        print(f"[NATIVE DROP] Universal handler received: {path_str}")

        def _async_process():
            try:
                import json
                from app.files_api import add_book_to_shelf
                lower = path_str.lower()
                if lower.endswith(".epub"):
                    res = add_book_to_shelf(path_str)
                    book_id = res.get("book", {}).get("id") if isinstance(res, dict) else None
                    print(f"[NATIVE DROP] Shelf binding result: status={res.get('status')}, book_id={book_id}")
                    if res.get("status") in ("ok", "success", "already_in_shelf") and res.get("book"):
                        book_json = json.dumps(res["book"], ensure_ascii=False)
                        dispatch_script = f"""
                        (function() {{
                            try {{
                                if (typeof window.__lrOnNativeEpubDrop === 'function') {{
                                    window.__lrOnNativeEpubDrop({book_json});
                                    return true;
                                }}
                                window.__lrPendingNativeDrops = window.__lrPendingNativeDrops || [];
                                window.__lrPendingNativeDrops.push({{ type: 'epub', book: {book_json} }});
                                return false;
                            }} catch (e) {{
                                return false;
                            }}
                        }})();
                        """
                        for _ in range(12):
                            ok = window.evaluate_js(dispatch_script)
                            if ok:
                                break
                            time.sleep(0.25)
                    else:
                        err_msg = res.get("message") or res.get("detail") or "Could not bind EPUB to shelf"
                        window.evaluate_js(f"window.showToast && window.showToast({json.dumps(err_msg)}, 'error');")
                elif lower.endswith(".pdf"):
                    escaped_path = json.dumps(path_str, ensure_ascii=False)
                    dispatch_script = f"""
                    (function() {{
                        try {{
                            if (typeof window.__lrOnNativePdfDrop === 'function') {{
                                window.__lrOnNativePdfDrop({escaped_path});
                                return true;
                            }}
                            window.__lrPendingNativeDrops = window.__lrPendingNativeDrops || [];
                            window.__lrPendingNativeDrops.push({{ type: 'pdf', filePath: {escaped_path} }});
                            return false;
                        }} catch (e) {{
                            return false;
                        }}
                    }})();
                    """
                    for _ in range(12):
                        ok = window.evaluate_js(dispatch_script)
                        if ok:
                            break
                        time.sleep(0.25)
                else:
                    window.evaluate_js("window.showToast && window.showToast('Please drop an EPUB or PDF file.', 'warning');")
            except Exception as ex:
                err_detail = getattr(ex, "detail", str(ex))
                print(f"[NATIVE DROP] Processing error: {err_detail}")
                try:
                    import json
                    window.evaluate_js(f"window.showToast && window.showToast({json.dumps(f'Drop error: {err_detail}')}, 'error');")
                except Exception:
                    pass

        threading.Thread(target=_async_process, daemon=True).start()




def _native_hwnd(form):
    handle = getattr(form, "Handle", None)
    if handle is None:
        return 0
    to_int64 = getattr(handle, "ToInt64", None)
    if callable(to_int64):
        return int(to_int64())
    to_int32 = getattr(handle, "ToInt32", None)
    if callable(to_int32):
        return int(to_int32())
    return int(handle)


def _win32_left_button_down() -> bool:
    """Physical left-button state, independent of WebView2's swallowed mouse-up."""
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)


def _invoke_on_ui(form, fn):
    """Run fn on the WinForms UI thread asynchronously to prevent deadlocks."""
    try:
        if getattr(form, "InvokeRequired", False):
            from System import Action

            form.BeginInvoke(Action(fn))
            return
    except Exception as e:
        print(f"[WINDOW] UI invoke failed: {e}")
        return
    fn()


class WindowsDriver(BasePlatformDriver):
    """Windows implementation using Win32 API, DWM, and WinForms."""

    def configure_engine(self) -> None:
        # Suppress verbose Chromium diagnostics and errors to stderr
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--log-level=3 --disable-logging"

        # Suppress ObjectDisposedException when evaluate_js runs during window teardown
        try:
            import logging

            class DisposedFilter(logging.Filter):
                def filter(self, record):
                    if record.exc_info:
                        exc = record.exc_info[1]
                        if exc and "disposed" in str(exc).lower():
                            return False
                    if "disposed" in str(record.getMessage()).lower():
                        return False
                    return True

            logging.getLogger("pywebview").addFilter(DisposedFilter())

            from webview.platforms import edgechromium

            _orig_evaluate_js = edgechromium.EdgeChrome.evaluate_js

            def _safe_evaluate_js(instance, script: str, parse_json: bool = True):
                wv = getattr(instance, "webview", None)
                if (
                    wv is None
                    or not getattr(wv, "IsHandleCreated", False)
                    or getattr(wv, "Disposing", False)
                    or getattr(wv, "IsDisposed", False)
                ):
                    return None
                try:
                    return _orig_evaluate_js(instance, script, parse_json)
                except Exception as ex:
                    if "disposed" in str(ex).lower():
                        return None
                    raise

            edgechromium.EdgeChrome.evaluate_js = _safe_evaluate_js

            _orig_edge_init = edgechromium.EdgeChrome.__init__

            def _patched_edge_init(instance, form, window, cache_dir):
                _orig_edge_init(instance, form, window, cache_dir)
                try:
                    instance.webview.AllowExternalDrop = False
                except Exception:
                    pass

                def _on_core_ready(sender, args):
                    try:
                        instance.webview.AllowExternalDrop = False
                    except Exception:
                        pass
                    try:
                        import ctypes
                        driver = get_platform_driver()
                        if hasattr(driver, "_native_shell_dll") and getattr(driver, "_native_hwnd", None):
                            driver._native_shell_dll.HookChildWindows.argtypes = [ctypes.c_void_p]
                            driver._native_shell_dll.HookChildWindows.restype = ctypes.c_bool
                            driver._native_shell_dll.HookChildWindows(driver._native_hwnd)
                    except Exception:
                        pass

                try:
                    instance.webview.CoreWebView2InitializationCompleted += _on_core_ready
                except Exception:
                    pass

            edgechromium.EdgeChrome.__init__ = _patched_edge_init
        except Exception:
            pass


    def _apply_win32_chrome(self, form) -> None:
        """Re-apply caption styles and DWM frame without re-registering native hooks."""
        try:
            import ctypes

            hwnd = _native_hwnd(form)
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            dwmapi = ctypes.windll.dwmapi

            GWL_STYLE = -16
            WS_THICKFRAME = 0x00040000
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            WS_CAPTION = 0x00C00000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020

            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            user32.SetWindowLongW(
                hwnd, GWL_STYLE, style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
            )
            user32.SetWindowPos(
                hwnd, None, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED
            )

            corner = ctypes.c_int(2)
            dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))

            class MARGINS(ctypes.Structure):
                _fields_ = [
                    ("cxLeftWidth", ctypes.c_int),
                    ("cxRightWidth", ctypes.c_int),
                    ("cyTopHeight", ctypes.c_int),
                    ("cyBottomHeight", ctypes.c_int),
                ]

            margins = MARGINS(0, 0, 0, 1)
            dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        except Exception as e:
            print(f"[WINDOW] Windows borderless setup failed: {e}")

    def setup_window(self, window) -> None:
        form = getattr(window, "native", None)
        if not form:
            return
        self._apply_win32_chrome(form)
        self.setup_drag_drop(window)
        self.setup_snap_overlay(window)

    def setup_drag_drop(self, window) -> None:
        """Windows native OLE IDropTarget hook using native_shell.dll."""
        form = getattr(window, "native", None)
        if not form:
            return
        try:
            import ctypes
            hwnd = _native_hwnd(form)
            if not hwnd:
                return

            dll_path = Path(__file__).parent / "app" / "native_api" / "native_shell.dll"
            if not dll_path.exists():
                return

            # Ensure Form accepts drops and child WebView2 control yields to Form drop target
            try:
                form.AllowDrop = True
                for ctrl in getattr(form, "Controls", []):
                    if hasattr(ctrl, "AllowExternalDrop"):
                        ctrl.AllowExternalDrop = False
                    if hasattr(ctrl, "AllowDrop"):
                        ctrl.AllowDrop = False
            except Exception:
                pass

            if getattr(self, "_native_shell_dll", None) and getattr(self, "_native_hwnd", None) == hwnd:
                return

            shell_dll = ctypes.CDLL(str(dll_path))

            NATIVE_DROP_CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.c_wchar_p)

            def _on_file_dropped(file_path):
                self._handle_native_file_dropped(window, file_path)

            self._native_drop_cb = NATIVE_DROP_CALLBACK(_on_file_dropped)
            shell_dll.RegisterNativeDrop.argtypes = [ctypes.c_void_p, NATIVE_DROP_CALLBACK]
            shell_dll.RegisterNativeDrop.restype = ctypes.c_bool
            success = shell_dll.RegisterNativeDrop(hwnd, self._native_drop_cb)

            self._native_shell_dll = shell_dll
            self._native_hwnd = hwnd
            print(f"[NATIVE DROP] Windows OLE IDropTarget registered on HWND {hwnd}: {success}")

            # Fast staggered child hook schedule to capture WebView2 at every stage of lifecycle
            def _hook_children_staggered():
                for delay in (0.05, 0.15, 0.35, 0.75, 1.5, 3.0):
                    time.sleep(delay)
                    try:
                        shell_dll.HookChildWindows.argtypes = [ctypes.c_void_p]
                        shell_dll.HookChildWindows.restype = ctypes.c_bool
                        shell_dll.HookChildWindows(hwnd)
                    except Exception:
                        pass

            threading.Thread(target=_hook_children_staggered, daemon=True).start()
        except Exception as e:
            print(f"[NATIVE DROP] Windows registration failed: {e}")

    def teardown_drag_drop(self, window) -> None:
        if hasattr(self, "_native_shell_dll") and getattr(self, "_native_hwnd", None):
            try:
                import ctypes
                self._native_shell_dll.UnregisterNativeDrop.argtypes = [ctypes.c_void_p]
                self._native_shell_dll.UnregisterNativeDrop(self._native_hwnd)
            except Exception:
                pass

    def _bind_snap_dll_exports(self, snap_dll) -> None:
        import ctypes

        snap_dll.RegisterSnapOverlay.argtypes = [ctypes.c_void_p]
        snap_dll.RegisterSnapOverlay.restype = ctypes.c_bool
        snap_dll.UnregisterSnapOverlay.argtypes = [ctypes.c_void_p]
        snap_dll.UnregisterSnapOverlay.restype = ctypes.c_bool
        snap_dll.StartNativeMove.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        snap_dll.StartNativeMove.restype = ctypes.c_bool
        snap_dll.StartNativeResize.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        snap_dll.StartNativeResize.restype = ctypes.c_bool
        snap_dll.SnapMaximize.argtypes = [ctypes.c_void_p]
        snap_dll.SnapMaximize.restype = ctypes.c_bool
        snap_dll.SnapRestore.argtypes = [ctypes.c_void_p]
        snap_dll.SnapRestore.restype = ctypes.c_bool
        snap_dll.SnapMinimize.argtypes = [ctypes.c_void_p]
        snap_dll.SnapMinimize.restype = ctypes.c_bool
        snap_dll.SnapIsMaximized.argtypes = [ctypes.c_void_p]
        snap_dll.SnapIsMaximized.restype = ctypes.c_bool
        snap_dll.SnapIsMinimized.argtypes = [ctypes.c_void_p]
        snap_dll.SnapIsMinimized.restype = ctypes.c_bool

    def setup_snap_overlay(self, window) -> None:
        """Install WM_NCCALCSIZE subclass via native_snap.dll (frameless Aero Snap)."""
        form = getattr(window, "native", None)
        if not form:
            return

        def _do_setup():
            try:
                import ctypes
                hwnd = _native_hwnd(form)
                if not hwnd:
                    return

                dll_path = Path(__file__).parent / "app" / "native_api" / "native_snap.dll"
                if not dll_path.exists():
                    print(f"[WINDOW] native_snap.dll not found at {dll_path}")
                    return

                snap_dll = getattr(self, "_native_snap_dll", None)
                if snap_dll is None:
                    snap_dll = ctypes.CDLL(str(dll_path))
                    self._bind_snap_dll_exports(snap_dll)
                    self._native_snap_dll = snap_dll

                success = snap_dll.RegisterSnapOverlay(hwnd)
                self._snap_hwnd = hwnd
                self._snap_registered = bool(success)
                if not success:
                    print("[WINDOW] RegisterSnapOverlay failed")
            except Exception as e:
                print(f"[WINDOW] Snap overlay setup failed: {e}")

        _invoke_on_ui(form, _do_setup)

    def teardown_snap_overlay(self, window) -> None:
        form = getattr(window, "native", None)
        def _do_teardown():
            if hasattr(self, "_native_snap_dll") and getattr(self, "_snap_hwnd", None):
                try:
                    self._native_snap_dll.UnregisterSnapOverlay(self._snap_hwnd)
                except Exception:
                    pass
                self._snap_registered = False
        if form:
            _invoke_on_ui(form, _do_teardown)
        else:
            _do_teardown()



    def start_move(self, window, screen_x: int = 0, screen_y: int = 0) -> None:
        form = getattr(window, "native", None)
        if not form:
            return

        def _run():
            try:
                hwnd = _native_hwnd(form)
                if not hwnd:
                    return
                if hasattr(self, "_native_snap_dll") and getattr(self, "_native_snap_dll", None):
                    try:
                        if self._native_snap_dll.StartNativeMove(hwnd, int(screen_x), int(screen_y)):
                            return
                    except Exception as exc:
                        print(f"[WINDOW] StartNativeMove failed: {exc}")

                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                lparam = (pt.y << 16) | (pt.x & 0xFFFF)
                user32.ReleaseCapture()
                if not _win32_left_button_down():
                    return
                user32.SendMessageW(hwnd, 0x00A1, 2, lparam)  # WM_NCLBUTTONDOWN, HTCAPTION = 2
                user32.ReleaseCapture()
            except Exception as e:
                print(f"[WINDOW] Windows native drag failed: {e}")

        _invoke_on_ui(form, _run)

    def start_resize(self, window, edge: str, screen_x: int = 0, screen_y: int = 0) -> None:
        form = getattr(window, "native", None)
        if not form:
            return
        if self.is_fullscreen(window) or self.is_maximized(window):
            return
        edge_map = {
            "left": 10,
            "right": 11,
            "top": 12,
            "topleft": 13,
            "topright": 14,
            "bottom": 15,
            "bottomleft": 16,
            "bottomright": 17,
        }
        hit = edge_map.get(str(edge or "").lower().replace("-", ""))
        if not hit:
            return

        def _run():
            try:
                hwnd = _native_hwnd(form)
                if not hwnd:
                    return
                if hasattr(self, "_native_snap_dll") and getattr(self, "_native_snap_dll", None):
                    try:
                        if self._native_snap_dll.StartNativeResize(hwnd, hit, int(screen_x), int(screen_y)):
                            return
                    except Exception as exc:
                        print(f"[WINDOW] StartNativeResize failed: {exc}")

                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                lparam = (pt.y << 16) | (pt.x & 0xFFFF)
                user32.ReleaseCapture()
                if not _win32_left_button_down():
                    return
                user32.SendMessageW(hwnd, 0x00A1, hit, lparam)  # WM_NCLBUTTONDOWN
                user32.ReleaseCapture()
            except Exception as e:
                print(f"[WINDOW] Windows native resize failed: {e}")

        _invoke_on_ui(form, _run)

    def maximize(self, window) -> None:
        form = getattr(window, "native", None)
        if form is not None:
            def _do():
                try:
                    hwnd = _native_hwnd(form)
                    if hasattr(self, "_native_snap_dll") and getattr(self, "_native_snap_dll", None) and hwnd:
                        self._native_snap_dll.SnapMaximize(hwnd)
                        return
                    from System.Windows.Forms import FormWindowState
                    form.WindowState = FormWindowState.Maximized
                except Exception as e:
                    print(f"[WINDOW] Windows maximize failed: {e}")
            _invoke_on_ui(form, _do)
        else:
            super().maximize(window)

    def restore(self, window) -> None:
        form = getattr(window, "native", None)
        if form is not None:
            def _do():
                try:
                    if getattr(window, "fullscreen", False) or getattr(form, "is_fullscreen", False):
                        window.toggle_fullscreen()
                    hwnd = _native_hwnd(form)
                    if hasattr(self, "_native_snap_dll") and getattr(self, "_native_snap_dll", None) and hwnd:
                        self._native_snap_dll.SnapRestore(hwnd)
                        return
                    from System.Windows.Forms import FormWindowState
                    form.WindowState = FormWindowState.Normal
                except Exception as e:
                    print(f"[WINDOW] Windows restore failed: {e}")
            _invoke_on_ui(form, _do)
        else:
            super().restore(window)

    def enter_fullscreen(self, window) -> None:
        form = getattr(window, "native", None)
        if form is not None:
            def _do():
                try:
                    window.toggle_fullscreen()
                except Exception as e:
                    print(f"[WINDOW] Windows fullscreen failed: {e}")
            _invoke_on_ui(form, _do)
        else:
            super().enter_fullscreen(window)

    def exit_fullscreen(self, window) -> None:
        form = getattr(window, "native", None)
        if form is not None:
            def _do():
                try:
                    if getattr(window, "fullscreen", False) or getattr(form, "is_fullscreen", False):
                        window.toggle_fullscreen()
                    from System.Windows.Forms import FormWindowState
                    form.WindowState = FormWindowState.Normal
                    self._apply_win32_chrome(form)
                    self.setup_snap_overlay(window)
                except Exception as e:
                    print(f"[WINDOW] Windows exit fullscreen failed: {e}")
            _invoke_on_ui(form, _do)
        else:
            super().exit_fullscreen(window)

    def apply_geometry(self, window, x: int, y: int, width: int, height: int) -> None:
        form = getattr(window, "native", None)
        if form is not None:
            def _do():
                try:
                    from System.Drawing import Size, Point
                    scale = getattr(form, "_scale", 1.0)
                    phys_w = int(width * scale)
                    phys_h = int(height * scale)
                    phys_x = int(x * scale)
                    phys_y = int(y * scale)

                    form.Size = Size(phys_w, phys_h)
                    form.Location = Point(phys_x, phys_y)
                except Exception as e:
                    print(f"[WINDOW] Windows apply geometry failed: {e}")
            _invoke_on_ui(form, _do)
        else:
            super().apply_geometry(window, x, y, width, height)

    def get_geometry(self, window):
        form = getattr(window, "native", None)
        if form is not None:
            try:
                scale = getattr(form, "_scale", 1.0)
                w = int(form.Size.Width / scale)
                h = int(form.Size.Height / scale)
                x = int(form.Location.X / scale)
                y = int(form.Location.Y / scale)
                return x, y, w, h
            except Exception:
                pass
        return super().get_geometry(window)

    def close(self, window) -> None:
        self.teardown_snap_overlay(window)
        self.teardown_drag_drop(window)
        form = getattr(window, "native", None)

        if form is not None:
            def _do():
                try:
                    form.Close()
                except Exception:
                    pass
            _invoke_on_ui(form, _do)
        else:
            super().close(window)


    def is_maximized(self, window) -> bool:
        native = getattr(window, "native", None) if window else None
        if native is None:
            return False
        hwnd = _native_hwnd(native)
        if hasattr(self, "_native_snap_dll") and getattr(self, "_native_snap_dll", None) and hwnd:
            try:
                return bool(self._native_snap_dll.SnapIsMaximized(hwnd))
            except Exception:
                pass
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
        return super().is_maximized(window)

    def is_minimized(self, window) -> bool:
        native = getattr(window, "native", None) if window else None
        if native is None:
            return False
        hwnd = _native_hwnd(native)
        if hasattr(self, "_native_snap_dll") and getattr(self, "_native_snap_dll", None) and hwnd:
            try:
                return bool(self._native_snap_dll.SnapIsMinimized(hwnd))
            except Exception:
                pass
        try:
            state = getattr(native, "WindowState", None)
            if state is not None:
                text = str(state)
                if "Minimized" in text:
                    return True
                if "Normal" in text or "Maximized" in text:
                    return False
                return int(state) == 1
        except Exception:
            pass
        return super().is_minimized(window)


class LinuxDriver(BasePlatformDriver):
    """Linux implementation using GTK3 and WebKit2GTK."""

    def configure_engine(self) -> None:
        # Prevents WebKitGTK blank window crashes on VMs and physical NVIDIA hardware
        os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"] = "1"
        os.environ["__NV_DISABLE_EXPLICIT_SYNC"] = "1"
        try:
            from webview.platforms import gtk
            _orig_init = gtk.BrowserView.__init__

            def _patched_init(instance, *args, **kwargs):
                _orig_init(instance, *args, **kwargs)
                try:
                    settings = instance.webview.get_settings()
                    settings.set_enable_html5_local_storage(True)
                    settings.set_enable_html5_database(True)
                except Exception as err:
                    print(f"[WINDOW] WebKitGTK storage setting failed: {err}")

            gtk.BrowserView.__init__ = _patched_init
        except Exception:
            pass

    def setup_window(self, window) -> None:
        native_win = getattr(window, "native", None)
        if hasattr(native_win, "set_resizable"):
            try:
                native_win.set_resizable(True)
            except Exception:
                pass
        try:
            from webview.platforms.gtk import BrowserView
            bv = BrowserView.instances.get(window.uid)
            if bv and hasattr(bv, "webview"):
                s = bv.webview.get_settings()
                s.set_enable_html5_local_storage(True)
                s.set_enable_html5_database(True)
        except Exception as e:
            print(f"[WINDOW] Linux webview configuration failed: {e}")

        # --- Native Drag and Drop Hook for Linux GTK3 / WebKit2GTK ---
        self.setup_drag_drop(window)

    def setup_drag_drop(self, window) -> None:
        """Linux GTK3 / WebKit2GTK native drag-and-drop hook."""
        try:
            from webview.platforms.gtk import BrowserView
            bv = BrowserView.instances.get(window.uid)
            if not bv or not hasattr(bv, "webview"):
                return

            def _on_gtk_drag_data(widget, drag_context, x, y, data, info, time_val):
                try:
                    text = data.get_text()
                    if text:
                        import urllib.parse
                        for line in text.splitlines():
                            line = line.strip()
                            if line.startswith("file://"):
                                file_path = urllib.parse.unquote(line.replace("file://", ""))
                            elif line.startswith("/") and os.path.exists(line):
                                file_path = line
                            else:
                                continue
                            self._handle_native_file_dropped(window, file_path)
                except Exception as err:
                    print(f"[LINUX DROP] Error reading drop data: {err}")
                return False

            bv.webview.connect("drag-data-received", _on_gtk_drag_data)
            print(f"[LINUX DROP] Hooked WebKitGTK drag-data-received on window {window.uid}")
        except Exception as e:
            print(f"[LINUX DROP] Hook failed: {e}")

    def teardown_drag_drop(self, window) -> None:
        pass


    def start_move(self, window, screen_x: int = 0, screen_y: int = 0) -> None:
        gtk_win = getattr(window, "native", None)
        if not gtk_win:
            return

        def _do_drag():
            try:
                rx = int(screen_x)
                ry = int(screen_y)
                if rx == 0 and ry == 0:
                    display = gtk_win.get_display()
                    seat = display.get_default_seat() if hasattr(display, "get_default_seat") else None
                    device = seat.get_pointer() if seat else None
                    if device:
                        _, rx, ry = device.get_position()
                    else:
                        _, rx, ry, _ = display.get_pointer()
                gtk_win.begin_move_drag(1, int(rx), int(ry), 0)
            except Exception as e:
                print(f"[WINDOW] Linux begin_move_drag failed: {e}")
            return False

        try:
            from gi.repository import GLib
            GLib.idle_add(_do_drag)
        except Exception:
            _do_drag()

    def start_resize(self, window, edge: str, screen_x: int = 0, screen_y: int = 0) -> None:
        gtk_win = getattr(window, "native", None)
        if not gtk_win:
            return
        if self.is_fullscreen(window) or self.is_maximized(window):
            return

        edge_map = {
            "topleft": 0,
            "top": 1,
            "topright": 2,
            "left": 3,
            "right": 4,
            "bottomleft": 5,
            "bottom": 6,
            "bottomright": 7,
        }
        edge_code = edge_map.get(str(edge or "").lower().replace("-", ""))
        if edge_code is None:
            return

        def _do_drag():
            try:
                import gi
                try:
                    gi.require_version("Gdk", "3.0")
                except Exception:
                    pass
                from gi.repository import Gdk

                edge_enum = Gdk.WindowEdge(edge_code) if hasattr(Gdk, "WindowEdge") else edge_code
                rx = int(screen_x)
                ry = int(screen_y)

                if rx == 0 and ry == 0:
                    display = gtk_win.get_display()
                    seat = display.get_default_seat() if hasattr(display, "get_default_seat") else None
                    device = seat.get_pointer() if seat else None
                    if device:
                        _, rx, ry = device.get_position()
                    else:
                        _, rx, ry, _ = display.get_pointer()

                gtk_win.begin_resize_drag(edge_enum, 1, int(rx), int(ry), 0)
            except Exception as e:
                print(f"[WINDOW] Linux begin_resize_drag failed: {e}")
            return False

        try:
            from gi.repository import GLib
            GLib.idle_add(_do_drag)
        except Exception:
            _do_drag()

    def maximize(self, window) -> None:
        native_win = getattr(window, "native", None)
        if native_win and hasattr(native_win, "maximize"):
            try:
                native_win.maximize()
            except Exception as e:
                print(f"[WINDOW] Linux maximize failed: {e}")
        super().maximize(window)

    def restore(self, window) -> None:
        native_win = getattr(window, "native", None)
        if native_win:
            if hasattr(native_win, "unfullscreen") and self.is_fullscreen(window):
                try:
                    native_win.unfullscreen()
                except Exception:
                    pass
            if hasattr(native_win, "unmaximize"):
                try:
                    native_win.unmaximize()
                except Exception as e:
                    print(f"[WINDOW] Linux unmaximize failed: {e}")
        super().restore(window)

    def enter_fullscreen(self, window) -> None:
        native_win = getattr(window, "native", None)
        if native_win and hasattr(native_win, "fullscreen"):
            try:
                native_win.fullscreen()
            except Exception as e:
                print(f"[WINDOW] Linux fullscreen failed: {e}")
        super().enter_fullscreen(window)

    def exit_fullscreen(self, window) -> None:
        native_win = getattr(window, "native", None)
        if native_win and hasattr(native_win, "unfullscreen"):
            try:
                native_win.unfullscreen()
            except Exception:
                pass
        super().exit_fullscreen(window)

    def get_geometry(self, window):
        gtk_win = getattr(window, "native", None)
        if gtk_win is not None:
            try:
                x, y = gtk_win.get_position()
                w, h = gtk_win.get_size()
                return x, y, w, h
            except Exception:
                pass
        return super().get_geometry(window)

    def close(self, window) -> None:
        gtk_win = getattr(window, "native", None)
        if gtk_win is not None:
            def _do_close():
                try:
                    app = gtk_win.get_application()
                    gtk_win.destroy()
                    if app is not None:
                        app.quit()
                except Exception:
                    pass
                return False

            try:
                from gi.repository import GLib
                GLib.idle_add(_do_close)
            except Exception:
                pass
        else:
            super().close(window)

    def is_maximized(self, window) -> bool:
        native = getattr(window, "native", None) if window else None
        if native and hasattr(native, "is_maximized"):
            try:
                return bool(native.is_maximized())
            except Exception:
                pass
        return super().is_maximized(window)

    def is_fullscreen(self, window) -> bool:
        native = getattr(window, "native", None) if window else None
        if native:
            try:
                gdk_win = getattr(native, "get_window", None)
                if callable(gdk_win):
                    gw = gdk_win()
                    if gw and hasattr(gw, "get_state"):
                        # Gdk.WindowState.FULLSCREEN = 16
                        return bool(int(gw.get_state()) & 16)
            except Exception:
                pass
        return super().is_fullscreen(window)

    def positioning_supported(self) -> bool:
        gdk_backend = os.environ.get("GDK_BACKEND", "").strip().lower()
        if gdk_backend == "x11":
            return True
        if gdk_backend == "wayland":
            return False
        if os.environ.get("WAYLAND_DISPLAY"):
            return False
        return os.environ.get("XDG_SESSION_TYPE", "").strip().lower() != "wayland"


class DarwinDriver(BasePlatformDriver):
    """macOS implementation using Cocoa AppKit."""

    def setup_window(self, window) -> None:
        native_win = getattr(window, "native", None)
        if not native_win:
            return
        try:
            # 1 = NSWindowStyleMaskTitled
            # 2 = NSWindowStyleMaskClosable
            # 4 = NSWindowStyleMaskMiniaturizable
            # 8 = NSWindowStyleMaskResizable
            # 32768 = NSWindowStyleMaskFullSizeContentView
            mask = 1 | 2 | 4 | 8 | 32768
            native_win.setStyleMask_(mask)
            native_win.setTitlebarAppearsTransparent_(True)
            native_win.setTitleVisibility_(1)  # NSWindowTitleHidden = 1

            for btn_id in (0, 1, 2):  # Close, Miniaturize, Zoom buttons
                btn = native_win.standardWindowButton_(btn_id)
                if btn:
                    btn.setHidden_(True)
        except Exception as e:
            print(f"[WINDOW] macOS borderless setup failed: {e}")

        # --- Native Drag and Drop Hook for macOS Cocoa / WebKit ---
        self.setup_drag_drop(window)

    def setup_drag_drop(self, window) -> None:
        """macOS Cocoa WebKit native drag-and-drop hook point."""
        try:
            from webview.platforms.cocoa import BrowserView
            bv = BrowserView.instances.get(window.uid)
            if bv and hasattr(bv, "webview"):
                print(f"[MAC DROP] Native WebKit drag-drop active for window {window.uid}")
        except Exception as e:
            print(f"[MAC DROP] Hook notice: {e}")

    def teardown_drag_drop(self, window) -> None:
        pass


    def start_move(self, window, screen_x: int = 0, screen_y: int = 0) -> None:
        ns_win = getattr(window, "native", None)
        if not ns_win:
            return

        def _run_cocoa():
            try:
                from AppKit import NSApp
                event = NSApp.currentEvent()
                if event and hasattr(ns_win, "performWindowDragWithEvent_"):
                    ns_win.performWindowDragWithEvent_(event)
                    return
            except Exception as e:
                print(f"[WINDOW] macOS performWindowDragWithEvent failed, falling back: {e}")

            try:
                from AppKit import (
                    NSApp,
                    NSEvent,
                    NSEventMaskLeftMouseDragged,
                    NSEventMaskLeftMouseUp,
                    NSEventTrackingRunLoopMode,
                )
                from Foundation import NSDate, NSPoint

                initial_origin = ns_win.frame().origin
                start_mouse = NSEvent.mouseLocation()
                mask = NSEventMaskLeftMouseDragged | NSEventMaskLeftMouseUp

                while True:
                    event = NSApp.nextEventMatchingMask_untilDate_inMode_dequeue_(
                        mask,
                        NSDate.distantFuture(),
                        NSEventTrackingRunLoopMode,
                        True,
                    )
                    if not event or event.type() == 2:  # NSLeftMouseUp
                        break
                    if event.type() == 6:  # NSLeftMouseDragged
                        curr = NSEvent.mouseLocation()
                        dx = curr.x - start_mouse.x
                        dy = curr.y - start_mouse.y
                        ns_win.setFrameOrigin_(NSPoint(initial_origin.x + dx, initial_origin.y + dy))
            except Exception as e:
                print(f"[WINDOW] macOS native move loop failed: {e}")

        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_run_cocoa)
        except Exception:
            threading.Thread(target=_run_cocoa, daemon=True).start()

    def maximize(self, window) -> None:
        ns_win = getattr(window, "native", None)
        if ns_win and hasattr(ns_win, "zoom_"):
            try:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(ns_win.zoom_, None)
                return
            except Exception:
                try:
                    ns_win.zoom_(None)
                    return
                except Exception:
                    pass
        super().maximize(window)

    def restore(self, window) -> None:
        ns_win = getattr(window, "native", None)
        if ns_win and hasattr(ns_win, "zoom_"):
            try:
                is_zoomed = False
                if hasattr(ns_win, "isZoomed"):
                    val = ns_win.isZoomed()
                    is_zoomed = bool(val() if callable(val) else val)
                if is_zoomed:
                    from PyObjCTools import AppHelper
                    try:
                        AppHelper.callAfter(ns_win.zoom_, None)
                        return
                    except Exception:
                        ns_win.zoom_(None)
                        return
            except Exception:
                pass
        super().restore(window)

    def is_maximized(self, window) -> bool:
        ns_win = getattr(window, "native", None)
        if ns_win and hasattr(ns_win, "isZoomed"):
            try:
                val = ns_win.isZoomed()
                return bool(val() if callable(val) else val)
            except Exception:
                pass
        return super().is_maximized(window)

    def start_resize(self, window, edge: str, screen_x: int = 0, screen_y: int = 0) -> None:
        ns_win = getattr(window, "native", None)
        if not ns_win:
            return
        if self.is_fullscreen(window) or self.is_maximized(window):
            return

        clean_edge = str(edge or "").lower().replace("-", "")

        def _run_cocoa():
            try:
                from AppKit import (
                    NSApp,
                    NSEvent,
                    NSEventMaskLeftMouseDragged,
                    NSEventMaskLeftMouseUp,
                    NSEventTrackingRunLoopMode,
                )
                from Foundation import NSDate, NSMakeRect

                initial_frame = ns_win.frame()
                start_mouse = NSEvent.mouseLocation()

                min_w = 850
                min_h = 550
                if hasattr(window, "min_size") and window.min_size:
                    min_w, min_h = window.min_size

                mask = NSEventMaskLeftMouseDragged | NSEventMaskLeftMouseUp

                while True:
                    event = NSApp.nextEventMatchingMask_untilDate_inMode_dequeue_(
                        mask,
                        NSDate.distantFuture(),
                        NSEventTrackingRunLoopMode,
                        True,
                    )
                    if not event:
                        break
                    etype = event.type()
                    if etype == 2:  # NSLeftMouseUp
                        break
                    if etype == 6:  # NSLeftMouseDragged
                        curr = NSEvent.mouseLocation()
                        dx = curr.x - start_mouse.x
                        dy = curr.y - start_mouse.y

                        x = initial_frame.origin.x
                        y = initial_frame.origin.y
                        w = initial_frame.size.width
                        h = initial_frame.size.height

                        if "right" in clean_edge:
                            new_w = max(min_w, w + dx)
                        elif "left" in clean_edge:
                            new_w = max(min_w, w - dx)
                            x = x + (w - new_w)
                        else:
                            new_w = w

                        if "top" in clean_edge:
                            new_h = max(min_h, h + dy)
                        elif "bottom" in clean_edge:
                            new_h = max(min_h, h - dy)
                            y = y + (h - new_h)
                        else:
                            new_h = h

                        ns_win.setFrame_display_(NSMakeRect(x, y, new_w, new_h), True)
            except Exception as e:
                print(f"[WINDOW] macOS native resize loop failed: {e}")

        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_run_cocoa)
        except Exception:
            threading.Thread(target=_run_cocoa, daemon=True).start()

    def is_fullscreen(self, window) -> bool:
        native = getattr(window, "native", None) if window else None
        if native:
            try:
                style = getattr(native, "styleMask", None)
                if callable(style):
                    # NSWindowStyleMaskFullScreen = 1 << 14
                    return bool(int(style()) & 16384)
            except Exception:
                pass
        return super().is_fullscreen(window)


_DRIVER_INSTANCE = None

def get_platform_driver() -> BasePlatformDriver:
    """Singleton factory returning the OS-appropriate platform driver."""
    global _DRIVER_INSTANCE
    if _DRIVER_INSTANCE is not None:
        return _DRIVER_INSTANCE

    sys_name = platform.system()
    if sys_name == "Windows":
        _DRIVER_INSTANCE = WindowsDriver()
    elif sys_name == "Linux":
        _DRIVER_INSTANCE = LinuxDriver()
    elif sys_name == "Darwin":
        _DRIVER_INSTANCE = DarwinDriver()
    else:
        _DRIVER_INSTANCE = BasePlatformDriver()

    return _DRIVER_INSTANCE
