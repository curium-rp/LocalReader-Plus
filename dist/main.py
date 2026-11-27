import os
import sys
import time
import signal
import threading
import uvicorn
import platform
from pathlib import Path

# --- 1. ARCHITECTURAL SETUP: ABSOLUTE PATH ANCHORING ---
base_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(base_dir))
from app.runtime import runtime
from app.config import DEBUG
from app.logic.local_server import bind_server_socket, server_url

from platform_driver import get_platform_driver
get_platform_driver().configure_engine()

import webview
from window_manager import WindowStateManager, native_is_fullscreen, native_is_maximized

def setup_standard_borderless(window):
    """Platform-specific frame chrome configuration for native resize and snap."""
    get_platform_driver().setup_window(window)


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
        if native_is_fullscreen(self._window) or self._fullscreen:
            return False
        if getattr(self._window, "native", None):
            return native_is_maximized(self._window)
        return self._maximized

    def _is_expanded(self):
        if not self._window:
            return bool(self._maximized or self._fullscreen)
        return bool(
            native_is_fullscreen(self._window)
            or native_is_maximized(self._window)
            or self._maximized
            or self._fullscreen
        )

    def _restore_window(self):
        if not self._window:
            return False
        if self._window_state:
            self._window_state.begin_transition()

        is_fs = native_is_fullscreen(self._window) or self._fullscreen
        if is_fs:
            get_platform_driver().exit_fullscreen(self._window)
            if self._window_state:
                self._window_state.exit_fullscreen()
        else:
            get_platform_driver().restore(self._window)
            if self._window_state:
                self._window_state.stamp_maximized(False)

        if self._window_state:
            self._window_state.apply_restored_bounds(self._window)

        self._maximized = False
        self._fullscreen = False
        self._finish_transition()
        self._sync_fullscreen_chrome(False)
        self._sync_chrome(False)
        return False

    def _maximize_window(self):
        if not self._window:
            return False
        if self._window_state:
            self._window_state.begin_transition()

        get_platform_driver().maximize(self._window)
        self._maximized = True
        self._fullscreen = False
        if self._window_state:
            self._window_state.stamp_maximized(True)
        self._finish_transition()
        self._sync_fullscreen_chrome(False)
        self._sync_chrome(True)
        return True

    def _enter_fullscreen(self):
        if not self._window:
            return False
        if self._window_state:
            self._window_state.begin_transition()
            self._window_state.enter_fullscreen("normal")

        get_platform_driver().enter_fullscreen(self._window)
        self._fullscreen = True
        self._maximized = False
        self._finish_transition()
        self._sync_fullscreen_chrome(True)
        self._sync_chrome(False)
        return True

    def _native_is_maximized(self):
        return self._logical_maximized()

    def _sync_chrome(self, maximized):
        self._maximized = bool(maximized)
        if not self._window:
            return
        flag = "true" if self._maximized else "false"

        def _run():
            try:
                self._window.evaluate_js(
                    f"window.__lrSetMaximized && window.__lrSetMaximized({flag})"
                )
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _sync_fullscreen_chrome(self, fullscreen):
        self._fullscreen = bool(fullscreen)
        if not self._window:
            return
        flag = "true" if self._fullscreen else "false"

        def _run():
            try:
                self._window.evaluate_js(
                    f"window.__lrSetFullscreen && window.__lrSetFullscreen({flag})"
                )
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _sync_all_chrome(self):
        fs = native_is_fullscreen(self._window) if self._window else False
        self._sync_fullscreen_chrome(fs)
        self._sync_chrome(self._logical_maximized())

    def _on_native_maximized(self):
        if native_is_fullscreen(self._window) or self._fullscreen or (self._window_state and self._window_state.state.get("is_fullscreen")):
            return
        self._sync_chrome(True)
        if self._window_state:
            self._window_state.stamp_maximized(True)

    def _on_native_restored(self):
        if native_is_fullscreen(self._window) or self._fullscreen or (self._window_state and self._window_state.state.get("is_fullscreen")):
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
        fs = (native_is_fullscreen(self._window) or self._fullscreen) if self._window else False
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
        if self._is_expanded():
            return self._restore_window()
        return self._maximize_window()

    def fullscreen_toggle(self):
        if not self._window:
            return False
        if self._is_expanded():
            self._restore_window()
            return False
        return self._enter_fullscreen()

    def close(self):
        if self._window:
            def _do_close():
                get_platform_driver().close(self._window)
            threading.Timer(0.05, _do_close).start()

    def open_external(self, url: str) -> None:
        """Open a URL in the system default browser, never in the webview."""
        import webbrowser
        if url and str(url).startswith(("http://", "https://", "mailto:")):
            webbrowser.open(str(url))

    def start_native_move(self, screen_x: int = 0, screen_y: int = 0):
        if not self._window or self._is_expanded():
            return
        get_platform_driver().start_move(self._window, screen_x, screen_y)

    def start_native_resize(self, edge: str, screen_x: int = 0, screen_y: int = 0):
        if not self._window or self._is_expanded():
            return
        get_platform_driver().start_resize(self._window, edge, screen_x, screen_y)

    def start_titlebar_drag(self, screen_x: int, screen_y: int, client_x: int, client_y: int, current_width: int = 0):
        if not self._window:
            return

        is_fs = native_is_fullscreen(self._window)
        is_max = native_is_maximized(self._window)

        if not is_fs and not is_max:
            return

        if self._window_state:
            self._window_state.begin_transition()

        new_x, new_y, target_w, target_h = (
            self._window_state.calculate_drag_restore_geometry(screen_x, screen_y, client_x, client_y, current_width)
            if self._window_state
            else (screen_x - 200, max(0, screen_y - 20), 1200, 800)
        )

        if is_fs:
            get_platform_driver().exit_fullscreen(self._window)
            if self._window_state:
                self._window_state.exit_fullscreen()
        else:
            get_platform_driver().restore(self._window)
            if self._window_state:
                self._window_state.stamp_maximized(False)

        self._maximized = False
        self._fullscreen = False

        if self._window_state:
            self._window_state.apply_drag_restore(self._window, new_x, new_y, target_w, target_h)
        self._finish_transition()
        self._sync_fullscreen_chrome(False)
        self._sync_chrome(False)
        get_platform_driver().start_move(self._window, screen_x, screen_y)


_server = None

def run_server(listener):
    global _server
    try:
        from speech_core.hardware import link_execution_providers
        link_execution_providers()
        from app.server import app
        host, port = listener.getsockname()
        config = uvicorn.Config(app, host=host, port=port, log_level="critical")
        _server = uvicorn.Server(config)
        _server.run(sockets=[listener])
    except Exception as e:
        print(f"[ERROR] Server error: {e}")
        sys.exit(1)
    finally:
        listener.close()

def wait_for_server(server_thread):
    """True once uvicorn is serving; False if its thread ended first. No fixed cap:
    cold imports (onnxruntime/Kokoro) may take longer than any guessed delay."""
    while server_thread.is_alive():
        if _server is not None and _server.started:
            return True
        time.sleep(0.02)
    return _server is not None and _server.started


SPLASH_HTML = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
    "<body style='margin:0;background:#181818'></body></html>"
)

def app_icon_path():
    """The window/taskbar/dock icon in the format this OS's pywebview backend loads:
    WinForms reads .ico, Cocoa reads .icns (dock), GTK/Qt read .png."""
    icons = base_dir / "localreader_icons"
    system = platform.system()
    if system == "Windows":
        icon = icons / "windows" / "LocalReader.ico"
    elif system == "Darwin":
        icon = icons / "macos" / "LocalReader.icns"
    else:
        icon = icons / "linux" / "LocalReader.png"
    if not icon.is_file():
        print(f"[WARN] App icon missing: {icon}")
        return None
    return str(icon)

def main():
    print("=" * 50)
    print("      LocalReader Plus - Starting")
    print("=" * 50)
    print(f"Project root: {base_dir}")

    listener = bind_server_socket(runtime.host)
    host, port = listener.getsockname()
    url = server_url(host, port, embedded=runtime.submodule)
    print(f"\n[INIT] Starting FastAPI server on {runtime.host}:{port}...")
    if runtime.submodule:
        print(f"[INIT] FalcoType submodule; read-only models: {runtime.models_dir}")
    print("[INIT] Creating application window...")
    server_thread = threading.Thread(target=run_server, args=(listener,), daemon=True)
    server_thread.start()

    def announce():
        # Managed mode announces server readiness itself, independent of when
        # (or whether) the window's shown event fires.
        if wait_for_server(server_thread):
            print(f"[FALCOTYPE_URL] {url}", flush=True)
        else:
            print(f"[CRITICAL] Server failed to bind port {port}.", flush=True)

    if runtime.submodule:
        threading.Thread(target=announce, daemon=True, name="falcotype-announce").start()

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
            min_size=(500, 500),
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
            if wait_for_server(server_thread):
                window.load_url(url)
            elif not runtime.submodule:
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
                api._enter_fullscreen()
            elif want_max:
                api._maximize_window()
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

        def on_closed():
            global _server
            if _server:
                _server.should_exit = True

        _sigint_count = 0

        def sig_handler(sig, frame):
            nonlocal _sigint_count
            _sigint_count += 1
            if _sigint_count > 1:
                print("\n[EXIT] Forcing immediate termination...", flush=True)
                os._exit(1)
            print("\n[EXIT] Interrupt received. Closing cleanly...", flush=True)
            try:
                get_platform_driver().close(window)
            except Exception:
                pass

            def _force_exit():
                time.sleep(1.5)
                os._exit(0)

            threading.Thread(target=_force_exit, daemon=True).start()

        try:
            signal.signal(signal.SIGINT, sig_handler)
            signal.signal(signal.SIGTERM, sig_handler)
        except Exception:
            pass

        window.events.shown += on_shown
        window.events.resized += on_resized
        window.events.moved += on_moved
        window.events.maximized += on_chrome_state
        window.events.restored += on_chrome_state
        window.events.closing += on_closing
        window.events.closed += on_closed

        try:
            # Keep dev tools available on demand (Ctrl+Shift+C / F12) without auto-popping on startup.
            webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
            webview.start(debug=DEBUG, storage_path=str(storage_path), icon=app_icon_path())
        except KeyboardInterrupt:
            print("\n[EXIT] KeyboardInterrupt caught. Closing cleanly...")
            try:
                get_platform_driver().close(window)
            except Exception:
                pass
            try:
                window.events.closed.wait(timeout=3.0)
            except Exception:
                pass
            time.sleep(0.3)

    except Exception as e:
        print(f"[CRITICAL] Failed to create window: {e}")
        sys.exit(1)

    print("\n[EXIT] Shutting down...")
    if _server:
        _server.should_exit = True
    time.sleep(0.2)
    os._exit(0)

if __name__ == "__main__":
    main()

