#define WIN32_LEAN_AND_MEAN
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#ifndef _WIN32_IE
#define _WIN32_IE 0x0A00
#endif
#include <windows.h>
#include <commctrl.h>
#include <mutex>
#include <unordered_set>

// Windows snap for the frameless pywebview host:
// - WS_CAPTION | WS_THICKFRAME | WS_MAXIMIZEBOX so DWM enables Aero Snap
// - WM_NCCALCSIZE returns 0 so the OS caption is stripped (HTML titlebar owns the top)
// - StartNativeMove sends WM_NCLBUTTONDOWN/HTCAPTION to run the DWM drag loop
// The maximize-button HTMAXBUTTON overlay is intentionally omitted: color-keyed
// layered children cannot receive hits, and a solid child paints a black box
// over WebView2. Caption clicks stay in HTML. Hover flyout is not supported.
//
// Compilation & Tools Reference (Windows x64):
//   MSVC (Recommended):
//     cl.exe /O2 /LD /utf-8 /EHsc /std:c++17 native_snap.cpp /Fe:..\native_snap.dll user32.lib comctl32.lib /link /OPT:REF /OPT:ICF
//     -> Result: 134 KB (dynamic UCRT)
//   MinGW GCC Fallback:
//     g++ -O3 -shared -static -std=c++17 native_snap.cpp -o ..\native_snap.dll -luser32 -lcomctl32
//     -> Result: 656 KB (static runtime)

static const UINT_PTR SNAP_SUBCLASS_ID = 0x534E4150; // 'SNAP'

static std::mutex g_snapMutex;
static std::unordered_set<HWND> g_subclassed;

static LRESULT CALLBACK ParentSubclassProc(
    HWND hwnd,
    UINT msg,
    WPARAM wParam,
    LPARAM lParam,
    UINT_PTR uIdSubclass,
    DWORD_PTR dwRefData
);

static void ApplySnapStyles(HWND hwnd) {
    LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
    LONG_PTR required = WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX;
    if ((style & required) != required) {
        SetWindowLongPtrW(hwnd, GWL_STYLE, style | required);
    }
    SetWindowPos(
        hwnd,
        nullptr,
        0, 0, 0, 0,
        SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED
    );
}

static LPARAM CursorLParam() {
    POINT pt = {};
    GetCursorPos(&pt);
    return MAKELPARAM(pt.x, pt.y);
}

static bool IsLeftButtonDown() {
    return (GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0;
}

static bool SysCommand(HWND hwnd, WPARAM cmd) {
    if (!hwnd || !IsWindow(hwnd)) {
        return false;
    }
    SendMessageW(hwnd, WM_SYSCOMMAND, cmd, 0);
    return true;
}

extern "C" {

__declspec(dllexport) bool UnregisterSnapOverlay(HWND parentHwnd);

__declspec(dllexport) HWND GetSnapOverlayHwnd(HWND parentHwnd) {
    (void)parentHwnd;
    return nullptr;
}

__declspec(dllexport) void UpdateSnapOverlayPosition(HWND parentHwnd) {
    (void)parentHwnd;
}

__declspec(dllexport) void SetSnapOverlayVisible(HWND parentHwnd, bool visible) {
    (void)parentHwnd;
    (void)visible;
}

__declspec(dllexport) bool StartNativeMove(HWND hwnd, int screenX, int screenY) {
    (void)screenX;
    (void)screenY;
    if (!hwnd || !IsWindow(hwnd)) {
        return false;
    }
    ReleaseCapture();
    // JS → Python is async. A completed click can already have released the
    // button; starting the DWM drag loop then leaves capture stuck until the
    // next click. Only enter the modal loop while the button is still down.
    if (!IsLeftButtonDown()) {
        return false;
    }
    SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, CursorLParam());
    ReleaseCapture();
    return true;
}

__declspec(dllexport) bool StartNativeResize(HWND hwnd, int hitTestEdge, int screenX, int screenY) {
    (void)screenX;
    (void)screenY;
    if (!hwnd || !IsWindow(hwnd) || hitTestEdge == 0) {
        return false;
    }
    ReleaseCapture();
    if (!IsLeftButtonDown()) {
        return false;
    }
    SendMessageW(hwnd, WM_NCLBUTTONDOWN, (WPARAM)hitTestEdge, CursorLParam());
    ReleaseCapture();
    return true;
}

__declspec(dllexport) bool SnapMaximize(HWND hwnd) {
    return SysCommand(hwnd, SC_MAXIMIZE);
}

__declspec(dllexport) bool SnapRestore(HWND hwnd) {
    return SysCommand(hwnd, SC_RESTORE);
}

__declspec(dllexport) bool SnapMinimize(HWND hwnd) {
    return SysCommand(hwnd, SC_MINIMIZE);
}

__declspec(dllexport) bool SnapApplyGeometry(HWND hwnd, int x, int y, int width, int height) {
    if (!hwnd || !IsWindow(hwnd)) {
        return false;
    }
    return SetWindowPos(
        hwnd,
        nullptr,
        x, y, width, height,
        SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
    ) != 0;
}

__declspec(dllexport) bool SnapGetGeometry(HWND hwnd, int* x, int* y, int* width, int* height) {
    if (!hwnd || !IsWindow(hwnd)) {
        return false;
    }
    RECT rc = {};
    if (!GetWindowRect(hwnd, &rc)) {
        return false;
    }
    if (x) *x = rc.left;
    if (y) *y = rc.top;
    if (width) *width = rc.right - rc.left;
    if (height) *height = rc.bottom - rc.top;
    return true;
}

__declspec(dllexport) bool SnapIsMaximized(HWND hwnd) {
    if (!hwnd || !IsWindow(hwnd)) {
        return false;
    }
    return IsZoomed(hwnd) != 0;
}

__declspec(dllexport) bool SnapIsMinimized(HWND hwnd) {
    if (!hwnd || !IsWindow(hwnd)) {
        return false;
    }
    return IsIconic(hwnd) != 0;
}

__declspec(dllexport) bool RegisterSnapOverlay(HWND parentHwnd) {
    if (!parentHwnd || !IsWindow(parentHwnd)) {
        return false;
    }

    bool alreadyHooked = false;
    {
        std::lock_guard<std::mutex> lock(g_snapMutex);
        alreadyHooked = g_subclassed.find(parentHwnd) != g_subclassed.end();
    }
    if (alreadyHooked) {
        ApplySnapStyles(parentHwnd);
        return true;
    }

    if (!SetWindowSubclass(parentHwnd, ParentSubclassProc, SNAP_SUBCLASS_ID, 0)) {
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(g_snapMutex);
        g_subclassed.insert(parentHwnd);
    }

    ApplySnapStyles(parentHwnd);
    return true;
}

__declspec(dllexport) bool UnregisterSnapOverlay(HWND parentHwnd) {
    if (!parentHwnd) {
        return false;
    }

    bool wasHooked = false;
    {
        std::lock_guard<std::mutex> lock(g_snapMutex);
        wasHooked = g_subclassed.erase(parentHwnd) > 0;
    }

    if (wasHooked) {
        RemoveWindowSubclass(parentHwnd, ParentSubclassProc, SNAP_SUBCLASS_ID);
    }
    return true;
}

} // extern "C"

static LRESULT CALLBACK ParentSubclassProc(
    HWND hwnd,
    UINT msg,
    WPARAM wParam,
    LPARAM lParam,
    UINT_PTR uIdSubclass,
    DWORD_PTR dwRefData
) {
    (void)uIdSubclass;
    (void)dwRefData;

    switch (msg) {
        case WM_NCCALCSIZE: {
            if (wParam && IsZoomed(hwnd)) {
                HMONITOR hMon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
                MONITORINFO mi = { sizeof(MONITORINFO) };
                if (GetMonitorInfoW(hMon, &mi)) {
                    NCCALCSIZE_PARAMS* params = (NCCALCSIZE_PARAMS*)lParam;
                    params->rgrc[0] = mi.rcWork;
                }
            }
            return 0;
        }
        case WM_NCDESTROY: {
            UnregisterSnapOverlay(hwnd);
            break;
        }
    }

    return DefSubclassProc(hwnd, msg, wParam, lParam);
}
