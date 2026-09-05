import os
import sys
import glob
import time
import socket
import threading
import uvicorn
import platform
from pathlib import Path
if platform.system() == "Linux":
    # Prevents WebKitGTK blank window crashes on both VMs and physical NVIDIA hardware
    os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"] = "1"
    os.environ["__NV_DISABLE_EXPLICIT_SYNC"] = "1"
import webview
# --- 1. ARCHITECTURAL SETUP: ABSOLUTE PATH ANCHORING ---
base_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(base_dir))

from window_manager import WindowStateManager, native_is_fullscreen, native_is_maximized

# --- 2. UNIVERSAL HARDWARE DETECTION & ORT PROVIDER LINKING ---
def setup_hardware():
    from app.utils import has_onnxruntime_gpu

    if not has_onnxruntime_gpu():
        print("[ORT] onnxruntime-gpu not installed, skip GPU execution providers")
        os.environ["ORT_AUTO_PROVIDERS"] = "CPUExecutionProvider"
        print(" OS Hardware Acceleration Priority: CPU")
        return

    optimized_providers = []

    if platform.system() == "Windows":
        print("[Check OS] Windows detected.")
        print("[ORT] onnxruntime-gpu found, linking CUDA")
        preload_successful = False
        try:
            for p in sys.path:
                nvidia_dir = os.path.join(p, "nvidia")
                if os.path.isdir(nvidia_dir):
                    for bin_path in glob.glob(os.path.join(nvidia_dir, "*", "bin")):
                        if os.path.isdir(bin_path):
                            os.add_dll_directory(bin_path)
                            os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")
                    print("[NVIDIA] Successfully linked CUDA/cuDNN from Python 'nvidia' package.")
                    optimized_providers.append("CUDAExecutionProvider")
                    preload_successful = True
                    break
        except Exception as e:
            print(f"[NVIDIA] Python environment DLLs not found ({e}). Falling back to System Scan...")

        if not preload_successful:
            found_paths = []
            cuda_paths = glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.*\bin")
            if cuda_paths:
                cuda_paths.sort(reverse=True)
                if os.path.exists(cuda_paths[0]): found_paths.append(cuda_paths[0])
            cudnn_paths = glob.glob(r"C:\Program Files\NVIDIA\CUDNN\v9.*\bin\*\x64")
            if not cudnn_paths:
                cudnn_paths = glob.glob(r"C:\Program Files\NVIDIA\CUDNN\v9.*\bin")
            if cudnn_paths:
                cudnn_paths.sort(reverse=True)
                if os.path.exists(cudnn_paths[0]): found_paths.append(cudnn_paths[0])
            if found_paths:
                for p in found_paths:
                    try:
                        os.add_dll_directory(p)
                        os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
                    except Exception:
                        pass
                print(f"NVIDIA DLLs Linked -> {' | '.join(found_paths)}")
                optimized_providers.append("CUDAExecutionProvider")

        optimized_providers.append("DmlExecutionProvider")
        optimized_providers.append("OpenVINOExecutionProvider")

    elif platform.system() == "Darwin":
        print("[Check OS] macOS detected. Link Core ML")
        optimized_providers.append("CoreMLExecutionProvider")

    elif platform.system() == "Linux":
        print("[CHeck OS] Linux detected. Configuring GPU support...")
        optimized_providers.append("CUDAExecutionProvider")
        optimized_providers.append("ROCMExecutionProvider")
        optimized_providers.append("OpenVINOExecutionProvider")

    os.environ["ORT_AUTO_PROVIDERS"] = ",".join(optimized_providers)
    smart_names = [p.replace("ExecutionProvider", "") for p in optimized_providers]
    print(f" OS Hardware Acceleration Priority: {' -> '.join(smart_names)}")



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


def _invoke_on_ui(form, fn):
    """Run fn on the WinForms UI thread. pywebview JS-API calls arrive on a worker."""
    try:
        if getattr(form, "InvokeRequired", False):
            from System import Func, Type

            form.Invoke(Func[Type](fn))
            return
    except Exception as e:
        print(f"[WINDOW] UI invoke failed: {e}")
        return
    fn()


def setup_standard_borderless(window):
    """Platform-specific frame chrome configuration for native resize and snap."""
    native_win = getattr(window, "native", None)
    if not native_win:
        return

    sys_platform = platform.system()

    if sys_platform == "Windows":
        try:
            import ctypes

            hwnd = _native_hwnd(native_win)
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            dwmapi = ctypes.windll.dwmapi

            GWL_STYLE = -16
            WS_THICKFRAME = 0x00040000
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020

            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            user32.SetWindowLongW(
                hwnd, GWL_STYLE, style | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
            )
            user32.SetWindowPos(
                hwnd, None, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED
            )

            # Win11 rounded corners + snap layouts
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

    elif sys_platform == "Darwin":
        try:
            # 1 = NSWindowStyleMaskTitled
            # 8 = NSWindowStyleMaskResizable
            # 32768 = NSWindowStyleMaskFullSizeContentView
            mask = 1 | 8 | 32768
            native_win.setStyleMask_(mask)
            native_win.setTitlebarAppearsTransparent_(True)
            native_win.setTitleVisibility_(1)  # NSWindowTitleHidden = 1

            for btn_id in (0, 1, 2):  # Close, Miniaturize, Zoom buttons
                btn = native_win.standardWindowButton_(btn_id)
                if btn:
                    btn.setHidden_(True)
        except Exception as e:
            print(f"[WINDOW] macOS borderless setup failed: {e}")

    elif sys_platform == "Linux":
        try:
            if hasattr(native_win, "set_resizable"):
                native_win.set_resizable(True)
        except Exception as e:
            print(f"[WINDOW] Linux borderless setup failed: {e}")


class WindowApi:
    def __init__(self, window_state=None):
        self._window = None
        self._maximized = False
        self._fullscreen = False
        self._window_state = window_state

    def set_window(self, window):
        self._window = window
        window.events.maximized += self._on_native_maximized
        window.events.restored += self._on_native_restored

    def _logical_maximized(self):
        if native_is_fullscreen(self._window):
            if self._window_state:
                return self._window_state.state.get("pre_fullscreen_state") == "maximized" or bool(
                    self._window_state.state.get("is_maximized")
                )
            return self._maximized
        if getattr(self._window, "native", None):
            return native_is_maximized(self._window)
        return self._maximized

    def _is_expanded(self):
        if not self._window:
            return self._maximized or self._fullscreen
        return native_is_fullscreen(self._window) or native_is_maximized(self._window)

    def _native_is_maximized(self):
        return self._logical_maximized()

    def _sync_chrome(self, maximized):
        self._maximized = bool(maximized)
        if not self._window:
            return
        flag = "true" if self._maximized else "false"
        try:
            self._window.evaluate_js(
                f"window.__lrSetMaximized && window.__lrSetMaximized({flag})"
            )
        except Exception:
            pass

    def _sync_fullscreen_chrome(self, fullscreen):
        self._fullscreen = bool(fullscreen)
        if not self._window:
            return
        flag = "true" if self._fullscreen else "false"
        try:
            self._window.evaluate_js(
                f"window.__lrSetFullscreen && window.__lrSetFullscreen({flag})"
            )
        except Exception:
            pass

    def _sync_all_chrome(self):
        fs = native_is_fullscreen(self._window) if self._window else False
        self._sync_fullscreen_chrome(fs)
        self._sync_chrome(self._logical_maximized())

    def _on_native_maximized(self):
        if native_is_fullscreen(self._window):
            return
        self._sync_chrome(True)
        if self._window_state:
            self._window_state.stamp_maximized(True)

    def _on_native_restored(self):
        if native_is_fullscreen(self._window):
            return
        self._sync_chrome(False)
        if self._window_state:
            self._window_state.stamp_maximized(False)

    def _finish_transition(self):
        if self._window_state:
            threading.Timer(0.35, self._window_state.end_transition).start()

    def minimize(self):
        if self._window:
            self._window.minimize()

    def get_state(self):
        fs = native_is_fullscreen(self._window) if self._window else False
        is_max = False if fs else bool(self._logical_maximized())
        return {
            "maximized": is_max,
            "fullscreen": bool(fs),
        }

    def is_maximized(self):
        return self._logical_maximized()

    def maximize_toggle(self):
        if not self._window:
            return self._maximized
        if native_is_fullscreen(self._window):
            return self._logical_maximized()
        if native_is_maximized(self._window):
            if self._window_state:
                self._window_state.begin_transition()
            self._window.restore()
            if self._window_state:
                self._window_state.apply_restored_bounds(self._window)
                self._window_state.stamp_maximized(False)
                self._finish_transition()
            self._sync_chrome(False)
            return False
        if self._window_state:
            self._window_state.begin_transition()
        self._window.maximize()
        if self._window_state:
            self._window_state.stamp_maximized(True)
            self._finish_transition()
        self._sync_chrome(True)
        return True

    def fullscreen_toggle(self):
        if not self._window:
            return False
        entering = not native_is_fullscreen(self._window)
        if self._window_state:
            self._window_state.begin_transition()
        if entering:
            pre = "maximized" if native_is_maximized(self._window) else "normal"
            if self._window_state:
                self._window_state.enter_fullscreen(pre)
            self._window.toggle_fullscreen()
            self._sync_fullscreen_chrome(True)
            self._sync_chrome(pre == "maximized")
        else:
            pre = "normal"
            if self._window_state:
                pre = self._window_state.state.get("pre_fullscreen_state", "normal")
                self._window_state.exit_fullscreen()
            self._window.toggle_fullscreen()
            if pre == "maximized":
                self._window.maximize()
                if self._window_state:
                    self._window_state.stamp_maximized(True)
                self._sync_chrome(True)
            else:
                if self._window_state:
                    self._window_state.apply_restored_bounds(self._window)
                    self._window_state.stamp_maximized(False)
                self._sync_chrome(False)
            self._sync_fullscreen_chrome(False)
        self._finish_transition()
        return entering

    def close(self):
        if self._window:
            self._window.destroy()

    def open_external(self, url: str) -> None:
        """Open a URL in the system default browser, never in the webview."""
        import webbrowser
        if url and str(url).startswith(("http://", "https://", "mailto:")):
            webbrowser.open(str(url))

    def _resize_windows(self, edge: str):
        form = getattr(self._window, "native", None)
        if not form:
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
                import ctypes
                hwnd = _native_hwnd(form)
                if not hwnd:
                    return
                user32 = ctypes.windll.user32
                user32.ReleaseCapture()
                user32.SendMessageW(hwnd, 0x00A1, hit, 0)  # WM_NCLBUTTONDOWN
            except Exception as e:
                print(f"[WINDOW] Windows native resize failed: {e}")

        _invoke_on_ui(form, _run)

    def _resize_linux(self, edge: str, screen_x: int, screen_y: int):
        gtk_win = getattr(self._window, "native", None)
        if not gtk_win:
            return

        # Gdk.WindowEdge enum:
        # NORTH_WEST = 0, NORTH = 1, NORTH_EAST = 2, WEST = 3,
        # EAST = 4, SOUTH_WEST = 5, SOUTH = 6, SOUTH_EAST = 7
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

                gtk_win.begin_resize_drag(
                    edge_enum,
                    1,        # Mouse button 1 (left click)
                    int(rx),
                    int(ry),
                    0         # Current event timestamp
                )
            except Exception as e:
                print(f"[WINDOW] Linux begin_resize_drag failed: {e}")
            return False

        try:
            from gi.repository import GLib
            GLib.idle_add(_do_drag)
        except Exception:
            _do_drag()

    def _resize_darwin(self, edge: str):
        ns_win = getattr(self._window, "native", None)
        if not ns_win:
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
                if hasattr(self._window, "min_size") and self._window.min_size:
                    min_w, min_h = self._window.min_size

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

                        # Horizontal sizing
                        if "right" in clean_edge:
                            new_w = max(min_w, w + dx)
                        elif "left" in clean_edge:
                            new_w = max(min_w, w - dx)
                            x = x + (w - new_w)
                        else:
                            new_w = w

                        # Vertical sizing (Cocoa y=0 at screen bottom)
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

    def start_native_resize(self, edge: str, screen_x: int = 0, screen_y: int = 0):
        if not self._window or self._is_expanded():
            return

        sys_platform = platform.system()
        if sys_platform == "Windows":
            self._resize_windows(edge)
        elif sys_platform == "Linux":
            self._resize_linux(edge, screen_x, screen_y)
        elif sys_platform == "Darwin":
            self._resize_darwin(edge)
        # Each platform helper is self-contained; no fallback block here.

def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]

def is_port_in_use(port):
    """Reliable socket check - guarantees the window opens the millisecond Uvicorn binds"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def run_server(port: int):
    try:
        setup_hardware()
        from app.server import app
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical")
        server = uvicorn.Server(config)
        server.run()
    except Exception as e:
        print(f"[ERROR] Server error: {e}")
        sys.exit(1)

SPLASH_HTML = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
    "<body style='margin:0;background:#181818'></body></html>"
)

def main():
    print("=" * 50)
    print("      LocalReader Plus - Starting")
    print("=" * 50)
    print(f"Project root: {base_dir}")

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"\n[INIT] Starting FastAPI server on 127.0.0.1:{port}...")
    print("[INIT] Creating application window...")
    threading.Thread(target=run_server, args=(port,), daemon=True).start()

    storage_path = base_dir / "webview_data"
    window_state = WindowStateManager()
    api = WindowApi(window_state)
    bounds = window_state.get_initial_bounds()
    print(f"[WINDOW] Restoring bounds from {window_state.config_path}")

    try:
        window = webview.create_window(
            "LocalReader Plus",
            html=SPLASH_HTML,
            width=bounds["width"],
            height=bounds["height"],
            x=bounds["x"],
            y=bounds["y"],
            fullscreen=False,
            maximized=False,
            background_color="#000000",
            min_size=(850, 550),
            frameless=True,
            easy_drag=False, # Didn't know why it not work but anyway it fixed by code
            shadow=True,
            resizable=True,
            transparent=False,
            js_api=api,
        )
        api.set_window(window)
        print("[OK] Window created successfully")
        print("=" * 50)

        def attach_ui():
            for _ in range(250):
                if is_port_in_use(port):
                    window.load_url(url)
                    return
                time.sleep(0.02)
            print(f"[CRITICAL] Server failed to bind port {port}.")

        def on_shown():
            setup_standard_borderless(window)
            window_state.begin_transition()
            want_fs = bool(bounds.get("fullscreen", False))
            want_max = bool(bounds.get("maximized", False))
            if want_fs and want_max:
                print("[WINDOW] Conflict in startup bounds (both fullscreen and maximized). Resetting to normal.")
                want_fs = False
                want_max = False
                window_state.stamp_maximized(False)
            if want_fs:
                window.toggle_fullscreen()
                api._sync_fullscreen_chrome(True)
                api._sync_chrome(False)
            elif want_max:
                window.maximize()
                api._sync_fullscreen_chrome(False)
                api._sync_chrome(True)
            else:
                api._sync_fullscreen_chrome(False)
                api._sync_chrome(False)
            api._finish_transition()
            threading.Thread(target=attach_ui, daemon=True).start()

        def on_resized(width=None, height=None):
            window_state.debounce_save(window, width=width, height=height)

        def on_moved(x=None, y=None):
            window_state.debounce_save(window, x=x, y=y)

        def on_chrome_state():
            window_state.debounce_save(window)

        def on_closing():
            window_state.save_immediate(window)

        window.events.shown += on_shown
        window.events.resized += on_resized
        window.events.moved += on_moved
        window.events.maximized += on_chrome_state
        window.events.restored += on_chrome_state
        window.events.closing += on_closing
        webview.start(debug=False, storage_path=str(storage_path))

    except Exception as e:
        print(f"[CRITICAL] Failed to create window: {e}")
        sys.exit(1)

    print("\n[EXIT] Shutting down...")
    os._exit(0)

if __name__ == "__main__":
    main()