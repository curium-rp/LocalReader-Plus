#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <ole2.h>
#include <oleidl.h>
#include <shellapi.h>
#include <vector>
#include <string>

// Compilation & Tools Reference (Windows x64):
//   MSVC (Recommended):
//     cl.exe /O2 /LD /utf-8 /EHsc /std:c++17 native_shell.cpp /Fe:..\native_shell.dll ole32.lib shell32.lib user32.lib /link /OPT:REF /OPT:ICF
//     -> Result: 119 KB (dynamic UCRT)
//   MinGW GCC Fallback:
//     g++ -O3 -shared -static -std=c++17 native_shell.cpp -o ..\native_shell.dll -lole32 -lshell32 -luuid
//     -> Result: 443 KB (static runtime)
//
// Callback signature passed to Python ctypes
typedef void (*DropCallback)(const wchar_t* filePath);

class NativeDropTarget : public IDropTarget {
private:
    LONG m_refCount;
    DropCallback m_callback;
    bool m_canDrop;

public:
    NativeDropTarget(DropCallback cb)
        : m_refCount(1), m_callback(cb), m_canDrop(false) {}
    virtual ~NativeDropTarget() {}

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (riid == IID_IUnknown || riid == IID_IDropTarget) {
            *ppv = static_cast<IDropTarget*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override {
        return InterlockedIncrement(&m_refCount);
    }

    ULONG STDMETHODCALLTYPE Release() override {
        LONG c = InterlockedDecrement(&m_refCount);
        if (c == 0) {
            delete this;
        }
        return c;
    }

    HRESULT STDMETHODCALLTYPE DragEnter(IDataObject* pDataObj, DWORD grfKeyState, POINTL pt, DWORD* pdwEffect) override {
        (void)grfKeyState;
        (void)pt;
        m_canDrop = false;
        if (!pDataObj || !pdwEffect) return E_INVALIDARG;

        FORMATETC fmt = { CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL };
        if (pDataObj->QueryGetData(&fmt) == S_OK) {
            m_canDrop = true;
            *pdwEffect = DROPEFFECT_COPY;
        } else {
            *pdwEffect = DROPEFFECT_NONE;
        }
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE DragOver(DWORD grfKeyState, POINTL pt, DWORD* pdwEffect) override {
        (void)grfKeyState;
        (void)pt;
        if (!pdwEffect) return E_INVALIDARG;
        *pdwEffect = m_canDrop ? DROPEFFECT_COPY : DROPEFFECT_NONE;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE DragLeave() override {
        m_canDrop = false;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE Drop(IDataObject* pDataObj, DWORD grfKeyState, POINTL pt, DWORD* pdwEffect) override {
        (void)grfKeyState;
        (void)pt;
        if (!pdwEffect) return E_INVALIDARG;
        *pdwEffect = DROPEFFECT_NONE;
        if (!pDataObj) return E_INVALIDARG;

        FORMATETC fmt = { CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL };
        STGMEDIUM medium{};
        if (FAILED(pDataObj->GetData(&fmt, &medium))) return S_OK;

        HDROP hDrop = static_cast<HDROP>(medium.hGlobal);
        if (hDrop) {
            UINT fileCount = DragQueryFileW(hDrop, 0xFFFFFFFF, nullptr, 0);
            for (UINT i = 0; i < fileCount; ++i) {
                UINT len = DragQueryFileW(hDrop, i, nullptr, 0);
                if (len == 0) continue;
                std::vector<wchar_t> pathBuf(len + 1);
                if (DragQueryFileW(hDrop, i, pathBuf.data(), len + 1) == 0) continue;
                if (!m_callback) continue;
                try {
                    m_callback(pathBuf.data());
                } catch (...) {
                }
            }
        }
        ReleaseStgMedium(&medium);
        *pdwEffect = DROPEFFECT_COPY;
        return S_OK;
    }
};

static NativeDropTarget* g_activeTarget = nullptr;
static bool g_oleReady = false;

static bool ensure_ole() {
    if (g_oleReady) return true;
    const HRESULT hr = OleInitialize(nullptr);
    if (FAILED(hr)) return false;
    g_oleReady = true;
    return true;
}

static void release_ole() {
    if (!g_oleReady) return;
    OleUninitialize();
    g_oleReady = false;
}

static HRESULT register_drop_on_hwnd(HWND hwnd, IDropTarget* target) {
    if (!hwnd || !target) return E_INVALIDARG;
    HRESULT hr = RegisterDragDrop(hwnd, target);
    if (hr == DRAGDROP_E_ALREADYREGISTERED) {
        RevokeDragDrop(hwnd);
        hr = RegisterDragDrop(hwnd, target);
    }
    return hr;
}

static BOOL CALLBACK EnumRegisterChildDropTarget(HWND hwndChild, LPARAM lParam) {
    if (hwndChild && lParam) {
        register_drop_on_hwnd(hwndChild, reinterpret_cast<IDropTarget*>(lParam));
    }
    return TRUE;
}

static BOOL CALLBACK EnumRevokeChildDropTarget(HWND hwndChild, LPARAM lParam) {
    (void)lParam;
    if (hwndChild) {
        RevokeDragDrop(hwndChild);
    }
    return TRUE;
}

extern "C" {

__declspec(dllexport) bool RegisterNativeDrop(HWND hwnd, DropCallback cb) {
    if (!hwnd || !cb) return false;
    if (!ensure_ole()) return false;

    if (g_activeTarget) {
        RevokeDragDrop(hwnd);
        EnumChildWindows(hwnd, EnumRevokeChildDropTarget, 0);
        g_activeTarget->Release();
        g_activeTarget = nullptr;
    }

    g_activeTarget = new NativeDropTarget(cb);

    const HRESULT hr = register_drop_on_hwnd(hwnd, g_activeTarget);
    DragAcceptFiles(hwnd, TRUE);
    EnumChildWindows(hwnd, EnumRegisterChildDropTarget, reinterpret_cast<LPARAM>(g_activeTarget));
    return SUCCEEDED(hr);
}

__declspec(dllexport) bool HookChildWindows(HWND hwnd) {
    if (!hwnd || !g_activeTarget) return false;
    EnumChildWindows(hwnd, EnumRegisterChildDropTarget, reinterpret_cast<LPARAM>(g_activeTarget));
    return true;
}

__declspec(dllexport) bool UnregisterNativeDrop(HWND hwnd) {
    if (!hwnd) return false;

    RevokeDragDrop(hwnd);
    EnumChildWindows(hwnd, EnumRevokeChildDropTarget, 0);

    if (g_activeTarget) {
        g_activeTarget->Release();
        g_activeTarget = nullptr;
    }

    release_ole();
    return true;
}

}
