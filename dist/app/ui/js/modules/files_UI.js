import { fetchJSON } from "./api.js";
import { showToast, renderIcons } from "./ui.js";
import { selectDocument, loadLibrary, peekShelfStatus } from "./library.js";

// State
let allTreesData = null; // { active_id, paths, trees, total_books, flat_list }
let currentPath = "";
let historyStack = [];
let historyIndex = -1;
let currentViewMode = "details"; // 'details' (list) by default!
let activeFilterQuery = "";
let libraryCache = new Map(); // path -> book object
let expandedFolders = new Set();
let expandedRoots = new Set();
let envWorkingPaths = [];
let envSelectedIndex = -1;
let sortKey = "name";
let sortDir = "asc";

const COL_STORAGE_KEY = "lr-fe-col-widths-v3";
const HANDLE_W = 5;
const COL_MIN = { name: 80, library: 60, type: 48, size: 48, status: 56 };
const COL_DEFAULT = { name: 280, library: 140, type: 88, size: 80, status: 88 };

function loadColWidths() {
  try {
    const parsed = JSON.parse(localStorage.getItem(COL_STORAGE_KEY) || "null");
    if (!parsed || typeof parsed !== "object") return { ...COL_DEFAULT };
    return {
      name: Math.max(COL_MIN.name, Number(parsed.name) || COL_DEFAULT.name),
      library: Math.max(COL_MIN.library, Number(parsed.library) || COL_DEFAULT.library),
      type: Math.max(COL_MIN.type, Number(parsed.type) || COL_DEFAULT.type),
      size: Math.max(COL_MIN.size, Number(parsed.size) || COL_DEFAULT.size),
      status: Math.max(COL_MIN.status, Number(parsed.status) || COL_DEFAULT.status),
    };
  } catch {
    return { ...COL_DEFAULT };
  }
}

async function syncColWidthsFromStorage() {
  try {
    const data = await fetchJSON("/api/explorer/columepx");
    if (data && data.files_ui && typeof data.files_ui === "object") {
      const parsed = data.files_ui;
      colWidths = {
        name: Math.max(COL_MIN.name, Number(parsed.name) || COL_DEFAULT.name),
        library: Math.max(COL_MIN.library, Number(parsed.library) || COL_DEFAULT.library),
        type: Math.max(COL_MIN.type, Number(parsed.type) || COL_DEFAULT.type),
        size: Math.max(COL_MIN.size, Number(parsed.size) || COL_DEFAULT.size),
        status: Math.max(COL_MIN.status, Number(parsed.status) || COL_DEFAULT.status),
      };
      applyDetailsColWidths();
    }
  } catch {
    /* ignore network failure */
  }
}

function saveColWidths() {
  try {
    localStorage.setItem(COL_STORAGE_KEY, JSON.stringify(colWidths));
  } catch {
    /* ignore quota / private mode */
  }
  fetchJSON("/api/explorer/columepx", {
    method: "POST",
    body: JSON.stringify({ view: "files_ui", widths: colWidths }),
  }).catch(() => {});
}

function detailsColTemplate() {
  return `${colWidths.name}px ${HANDLE_W}px ${colWidths.library}px ${HANDLE_W}px ${colWidths.type}px ${HANDLE_W}px ${colWidths.size}px ${HANDLE_W}px ${colWidths.status}px`;
}

function detailsRowMinWidth() {
  return colWidths.name + colWidths.library + colWidths.type + colWidths.size + colWidths.status + HANDLE_W * 4;
}

function applyDetailsColWidths() {
  const template = detailsColTemplate();
  const minW = `${detailsRowMinWidth()}px`;
  document.querySelectorAll(".fe-details-row").forEach((el) => {
    el.style.gridTemplateColumns = template;
    el.style.minWidth = minW;
  });
}

function attachColResize(handle, key) {
  if (!handle || !COL_MIN[key]) return;
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startW = colWidths[key];
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    const onMove = (ev) => {
      colWidths[key] = Math.max(COL_MIN[key], Math.round(startW + (ev.clientX - startX)));
      applyDetailsColWidths();
    };
    const onUp = () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      saveColWidths();
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
  });
}

function colDividerHtml(key) {
  return `<div data-col="${key}" class="fe-col-resize relative h-full cursor-col-resize group" title="Resize column">
    <span class="pointer-events-none absolute inset-y-0 left-1/2 -translate-x-1/2 w-px bg-zinc-500 group-hover:w-0.5 group-hover:bg-blue-400"></span>
  </div>`;
}

function colDividerCell() {
  return `<div class="relative h-full"><span class="absolute inset-y-0 left-1/2 -translate-x-1/2 w-px bg-zinc-800"></span></div>`;
}

let colWidths = loadColWidths();
syncColWidthsFromStorage();

function itemType(item, isFolder) {
  if (isFolder) return "File folder";
  return "EPUB";
}

function getCachedBook(epubPath, fileName) {
  if (epubPath && libraryCache.has(epubPath.toLowerCase())) return libraryCache.get(epubPath.toLowerCase());
  if (fileName && libraryCache.has(fileName.toLowerCase())) return libraryCache.get(fileName.toLowerCase());
  return null;
}

function shelfStatusLabel(item) {
  const book = getCachedBook(item.path, item.name);
  if (!book) return "Add";
  const s = peekShelfStatus(book);
  if (s === "hold" || s === "on hold") return "on hold";
  if (s === "finished") return "finished";
  return "reading";
}

function statusLabelClass(label) {
  if (label === "Add") return "text-blue-400 hover:text-blue-300";
  return "text-emerald-400 hover:text-emerald-300";
}

function itemStatus(item, isFolder) {
  if (isFolder) return "";
  return shelfStatusLabel(item);
}

function sortExplorerItems(items, asFolders) {
  const mul = sortDir === "desc" ? -1 : 1;
  return [...items].sort((a, b) => {
    let av;
    let bv;
    if (sortKey === "size") {
      av = a.size_bytes || 0;
      bv = b.size_bytes || 0;
      if (av !== bv) return (av - bv) * mul;
    } else if (sortKey === "library") {
      av = (a.root_name || "").toLowerCase();
      bv = (b.root_name || "").toLowerCase();
    } else if (sortKey === "type") {
      av = itemType(a, asFolders).toLowerCase();
      bv = itemType(b, asFolders).toLowerCase();
    } else if (sortKey === "status") {
      av = itemStatus(a, asFolders).toLowerCase();
      bv = itemStatus(b, asFolders).toLowerCase();
    } else {
      av = (a.name || a.title || "").toLowerCase();
      bv = (b.name || b.title || "").toLowerCase();
    }
    if (typeof av === "string") {
      const cmp = av.localeCompare(bv, undefined, { numeric: true, sensitivity: "base" });
      if (cmp !== 0) return cmp * mul;
    }
    return (a.name || "").localeCompare(b.name || "", undefined, { numeric: true, sensitivity: "base" });
  });
}

function sortIndicator(key) {
  if (sortKey !== key) return "";
  return sortDir === "asc" ? " ▲" : " ▼";
}

/**
 * Sync the local cache of library books to know which EPUBs are already in the shelf.
 */
async function syncLibraryCache() {
  try {
    const items = await fetchJSON(`/api/library?t=${Date.now()}`);
    libraryCache.clear();
    if (Array.isArray(items)) {
      items.forEach((item) => {
        if (item.source_path) {
          libraryCache.set(item.source_path.toLowerCase(), item);
        }
        if (item.path) {
          libraryCache.set(item.path.toLowerCase(), item);
        }
        if (item.fileName) {
          libraryCache.set(item.fileName.toLowerCase(), item);
        }
      });
    }
  } catch (e) {
    console.warn("[files_UI] Failed to sync library cache:", e);
  }
}

/**
 * Mounts the Explorer modal overlay into the document body if not already present.
 */
export function mountFilesUI() {
  if (document.getElementById("filesExplorerModal")) return;

  const overlay = document.createElement("div");
  overlay.id = "filesExplorerModal";
  overlay.className = "fixed top-[32px] right-0 bottom-0 left-0 z-[9999] flex bg-black/80 backdrop-blur-sm hidden";
  overlay.innerHTML = `
    <div id="feExplorerPanel" class="flex flex-col w-full h-full bg-zinc-950 overflow-hidden font-sans text-zinc-200 relative">
      
      <!-- Top Titlebar -->
      <div class="flex items-center justify-between px-4 py-2.5 bg-zinc-900/90 border-b border-zinc-800/80 select-none">
        <div class="flex items-center gap-2.5">
          <i data-lucide="folder-search" class="w-4 h-4 text-blue-400"></i>
          <span class="text-xs font-semibold tracking-wide text-zinc-100">Book Explorer</span>
          <span id="feRootBadge" class="text-[10px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700/50 truncate max-w-xs">All Libraries</span>
          
          <!-- Path Management Button -->
          <button id="fePathManagementBtn" class="flex items-center gap-1.5 px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded text-xs font-medium border border-zinc-700/60 shadow-sm transition-colors ml-1" title="Manage book paths (Reorder, edit, sort)">
            <i data-lucide="sliders" class="w-3.5 h-3.5 text-blue-400"></i>
            <span>Path Management</span>
          </button>
        </div>
        <div class="flex items-center gap-1.5">
          <button id="feCloseBtn" class="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors" title="Close (Esc)">
            <i data-lucide="x" class="w-4 h-4"></i>
          </button>
        </div>
      </div>

      <!-- Command Bar / Navigation Toolbar -->
      <div class="flex flex-wrap items-center gap-2 p-2.5 bg-zinc-900/60 border-b border-zinc-800/60 text-xs">
        <!-- Navigation Arrows -->
        <div class="flex items-center gap-1">
          <button id="feBackBtn" class="p-1.5 rounded hover:bg-zinc-800 disabled:opacity-30 disabled:pointer-events-none text-zinc-300" title="Back (Alt+Left)">
            <i data-lucide="arrow-left" class="w-3.5 h-3.5"></i>
          </button>
          <button id="feForwardBtn" class="p-1.5 rounded hover:bg-zinc-800 disabled:opacity-30 disabled:pointer-events-none text-zinc-300" title="Forward (Alt+Right)">
            <i data-lucide="arrow-right" class="w-3.5 h-3.5"></i>
          </button>
          <button id="feUpBtn" class="p-1.5 rounded hover:bg-zinc-800 disabled:opacity-30 disabled:pointer-events-none text-zinc-300" title="Up to Parent (Alt+Up)">
            <i data-lucide="arrow-up" class="w-3.5 h-3.5"></i>
          </button>
          <button id="feRefreshBtn" class="p-1.5 rounded hover:bg-zinc-800 text-zinc-300 transition-colors" title="Rescan All Paths">
            <i data-lucide="rotate-cw" class="w-3.5 h-3.5"></i>
          </button>
        </div>

        <!-- Breadcrumb Address Bar -->
        <div id="feAddressBar" class="flex-1 min-w-[240px] flex items-center gap-1 px-2.5 py-1 bg-zinc-950 border border-zinc-800 rounded-md overflow-x-auto custom-scrollbar text-xs">
          <span class="text-zinc-500 shrink-0">📁</span>
          <div id="feBreadcrumbList" class="flex items-center gap-1 whitespace-nowrap overflow-hidden"></div>
          <div class="ml-auto flex items-center gap-1 pl-2 shrink-0">
            <button id="feCopyPathBtn" class="p-1 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors" title="Copy Current Path">
              <i data-lucide="copy" class="w-3 h-3"></i>
            </button>
            <button id="feRevealInOSBtn" class="p-1 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors" title="Reveal in File Explorer">
              <i data-lucide="external-link" class="w-3 h-3"></i>
            </button>
          </div>
        </div>

        <!-- Add Folder Buttons -->
        <div class="flex items-center gap-1.5">
          <button id="feAddFolderBtn" class="flex items-center gap-1 px-2.5 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded font-medium text-xs shadow transition-colors" title="Add another book folder to the tree">
            <i data-lucide="folder-plus" class="w-3.5 h-3.5"></i>
            <span>Add Folder</span>
          </button>
          <button id="fePastePathBtn" class="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 border border-zinc-800 transition-colors" title="Paste/Type Path Manually">
            <i data-lucide="edit-3" class="w-3.5 h-3.5"></i>
          </button>
        </div>

        <!-- Global Search Box -->
        <div class="relative w-48 sm:w-56">
          <i data-lucide="search" class="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none"></i>
          <input id="feSearchInput" type="text" placeholder="Search all books..." class="w-full bg-zinc-950 border border-zinc-800 rounded px-8 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-blue-500 transition-colors" />
          <button id="feSearchClearBtn" class="hidden absolute right-2 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300">
            <i data-lucide="x-circle" class="w-3 h-3"></i>
          </button>
        </div>

        <!-- View Switcher (Details List by default) -->
        <div class="flex items-center border border-zinc-800 rounded overflow-hidden">
          <button id="feViewGridBtn" class="p-1.5 hover:bg-zinc-800 text-zinc-400 transition-colors" title="Card Grid View">
            <i data-lucide="layout-grid" class="w-3.5 h-3.5"></i>
          </button>
          <button id="feViewDetailsBtn" class="p-1.5 bg-zinc-800 text-blue-400 transition-colors" title="Details List View">
            <i data-lucide="list" class="w-3.5 h-3.5"></i>
          </button>
        </div>
      </div>

      <!-- Main Body: Split View -->
      <div class="flex-1 min-h-0 flex overflow-hidden">
        
        <!-- Left Pane: Multi-Root Folder Tree -->
        <div id="feLeftPane" class="min-w-[220px] w-[22%] shrink-0 bg-zinc-950/80 border-r border-zinc-800/80 flex flex-col">
          <div class="px-3 py-2 bg-zinc-900/70 border-b border-zinc-800/80 flex items-center justify-between gap-2 select-none shrink-0 whitespace-nowrap">
            <div class="flex items-center gap-1.5 min-w-0 shrink-0">
              <i data-lucide="folders" class="w-3.5 h-3.5 text-blue-400 shrink-0"></i>
              <span class="text-xs font-semibold text-zinc-200 whitespace-nowrap">Library Folders</span>
              <span id="feTotalBooksCount" class="text-[10px] text-zinc-400 bg-zinc-800 px-1.5 py-0.5 rounded-full border border-zinc-700/50 whitespace-nowrap shrink-0 font-mono" title="0 books">0</span>
            </div>
            <div class="flex items-center gap-1 shrink-0">
              <button id="feTreeAddBtn" class="p-1 rounded hover:bg-zinc-800 text-zinc-400 hover:text-blue-400 transition-colors" title="Add Book Folder">
                <i data-lucide="plus" class="w-3.5 h-3.5"></i>
              </button>
              <button id="feTreeRescanAllBtn" class="p-1 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 transition-colors" title="Rescan All Paths">
                <i data-lucide="rotate-cw" class="w-3.5 h-3.5"></i>
              </button>
            </div>
          </div>
          <div id="feTreeView" class="flex-1 overflow-y-auto p-2 custom-scrollbar text-xs space-y-1">
            <!-- Rendered Multi-Root Folder Nodes -->
          </div>
        </div>

        <!-- Right Pane: Files Explorer Content Area -->
        <div class="flex-1 flex flex-col bg-zinc-900/30 overflow-hidden">
          <!-- Active Folder Header Banner -->
          <div id="feFolderBanner" class="px-4 py-2.5 bg-zinc-950/40 border-b border-zinc-800/60 flex items-center justify-between">
            <div class="flex items-center gap-2 min-w-0">
              <span id="feBannerIcon" class="text-lg shrink-0">📁</span>
              <div class="min-w-0">
                <h3 id="feBannerTitle" class="text-xs font-semibold text-zinc-100 truncate">No folder selected</h3>
                <p id="feBannerPath" class="text-[10px] text-zinc-500 truncate font-mono"></p>
              </div>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <span id="feBannerStats" class="text-[11px] text-zinc-400 bg-zinc-900 border border-zinc-800 px-2 py-0.5 rounded">0 items</span>
              <button id="feBannerOpenOSBtn" class="flex items-center gap-1 px-2 py-1 text-[11px] font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded border border-zinc-700/60 transition-colors" title="Open folder in File Explorer">
                <i data-lucide="external-link" class="w-3 h-3 text-zinc-400"></i>
                <span>Open in OS</span>
              </button>
            </div>
          </div>

          <div id="feContentArea" class="flex-1 min-h-0 flex flex-col overflow-hidden">
            <!-- Rendered Cards / Table -->
          </div>
        </div>
      </div>

      <!-- Bottom Status Bar -->
      <div class="flex items-center justify-between px-3 py-1.5 bg-zinc-950 border-t border-zinc-800/80 text-[11px] text-zinc-500 select-none">
        <div id="feStatusLeft">Ready</div>
        <div id="feStatusRight" class="truncate max-w-md font-mono text-[10px]"></div>
      </div>

      <!-- Windows Environment Variables Style Path Management Modal -->
      <div id="feEnvVarsModal" class="fixed top-[32px] right-0 bottom-0 left-0 z-[10000] flex items-center justify-center bg-black/80 backdrop-blur-xs p-4 hidden">
        <div id="feEnvVarsPanel" class="flex flex-col w-full max-w-2xl bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl overflow-hidden font-sans text-zinc-200">
          
          <!-- Titlebar -->
          <div class="flex items-center justify-between px-3.5 py-2.5 bg-zinc-800/90 border-b border-zinc-700/80 select-none">
            <div class="flex items-center gap-2">
              <i data-lucide="sliders" class="w-4 h-4 text-blue-400"></i>
              <span class="text-xs font-semibold tracking-wide text-zinc-100">Edit Book Folder Paths</span>
            </div>
            <button id="feEnvCloseBtn" class="p-1 rounded hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors" title="Close">
              <i data-lucide="x" class="w-3.5 h-3.5"></i>
            </button>
          </div>

          <!-- Body: List on Left, Action Buttons Stack on Right -->
          <div class="p-4 flex flex-col gap-2">
            <div class="text-xs text-zinc-300 font-medium">Book Folder Paths:</div>
            
            <div id="feEnvListRow" class="flex gap-3 h-72">
              <!-- Paths Listbox -->
              <div id="feEnvListBox" class="flex-1 bg-zinc-950 border border-zinc-700 rounded p-1 text-xs divide-y divide-zinc-900/60 overflow-y-auto custom-scrollbar select-none">
                <!-- Rendered Path Items -->
              </div>

              <!-- Right Button Stack (Windows style) -->
              <div class="flex flex-col gap-1.5 w-28 shrink-0 select-none">
                <button id="feEnvNewBtn" class="px-2.5 py-1 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded text-zinc-200 transition-colors">
                  New
                </button>
                <button id="feEnvEditBtn" class="px-2.5 py-1 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded text-zinc-200 transition-colors disabled:opacity-40 disabled:pointer-events-none">
                  Edit
                </button>
                <button id="feEnvBrowseBtn" class="px-2.5 py-1 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded text-zinc-200 transition-colors">
                  Browse...
                </button>
                <button id="feEnvDeleteBtn" class="px-2.5 py-1 text-xs font-medium bg-red-900/30 hover:bg-red-800/50 border border-red-700/50 rounded text-red-300 transition-colors disabled:opacity-40 disabled:pointer-events-none">
                  Delete
                </button>
                <div class="my-1 border-t border-zinc-800"></div>
                <button id="feEnvMoveUpBtn" class="px-2.5 py-1 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded text-zinc-200 transition-colors disabled:opacity-40 disabled:pointer-events-none">
                  Move Up
                </button>
                <button id="feEnvMoveDownBtn" class="px-2.5 py-1 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded text-zinc-200 transition-colors disabled:opacity-40 disabled:pointer-events-none">
                  Move Down
                </button>
                <div class="my-1 border-t border-zinc-800"></div>
                <button id="feEnvSortBtn" class="px-2.5 py-1 text-xs font-medium bg-blue-900/30 hover:bg-blue-800/50 border border-blue-700/50 rounded text-blue-300 transition-colors" title="Auto Sort Paths A-Z">
                  Sort A-Z
                </button>
              </div>
            </div>
          </div>

          <!-- Footer: OK and Cancel buttons -->
          <div class="flex items-center justify-end gap-2 px-4 py-3 bg-zinc-950/60 border-t border-zinc-800">
            <button id="feEnvOkBtn" class="px-5 py-1.5 text-xs font-semibold bg-blue-600 hover:bg-blue-500 text-white rounded shadow transition-colors">
              OK
            </button>
            <button id="feEnvCancelBtn" class="px-4 py-1.5 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded border border-zinc-700 transition-colors">
              Cancel
            </button>
          </div>

        </div>
      </div>

    </div>
  `;

  document.body.appendChild(overlay);

  // Wire Events
  wireExplorerEvents();
  if (typeof renderIcons === "function") renderIcons();
}

/**
 * Sets up button clicks and keyboard handlers for the Explorer modal.
 */
function wireExplorerEvents() {
  document.getElementById("feCloseBtn")?.addEventListener("click", closeFilesUI);
  document.getElementById("feBackBtn")?.addEventListener("click", goBack);
  document.getElementById("feForwardBtn")?.addEventListener("click", goForward);
  document.getElementById("feUpBtn")?.addEventListener("click", goUp);
  document.getElementById("feRefreshBtn")?.addEventListener("click", handleRescanAll);
  document.getElementById("feTreeRescanAllBtn")?.addEventListener("click", handleRescanAll);

  document.getElementById("feAddFolderBtn")?.addEventListener("click", handlePickFolderNative);
  document.getElementById("feTreeAddBtn")?.addEventListener("click", handlePickFolderNative);
  document.getElementById("fePastePathBtn")?.addEventListener("click", handlePromptFolderPath);

  document.getElementById("feCopyPathBtn")?.addEventListener("click", () => {
    if (currentPath) {
      navigator.clipboard.writeText(currentPath).then(() => {
        showToast("Path copied to clipboard", "success", 1200);
      });
    }
  });

  document.getElementById("feRevealInOSBtn")?.addEventListener("click", () => {
    if (currentPath) handleOpenInOS(currentPath);
  });
  document.getElementById("feBannerOpenOSBtn")?.addEventListener("click", () => {
    if (currentPath) handleOpenInOS(currentPath);
  });

  const searchInput = document.getElementById("feSearchInput");
  const searchClear = document.getElementById("feSearchClearBtn");

  searchInput?.addEventListener("input", (e) => {
    activeFilterQuery = e.target.value.trim().toLowerCase();
    searchClear?.classList.toggle("hidden", !activeFilterQuery);
    renderActiveDirectory();
  });

  searchClear?.addEventListener("click", () => {
    if (searchInput) searchInput.value = "";
    activeFilterQuery = "";
    searchClear?.classList.add("hidden");
    renderActiveDirectory();
  });

  document.getElementById("feViewGridBtn")?.addEventListener("click", () => {
    currentViewMode = "grid";
    updateViewButtons();
    renderActiveDirectory();
  });

  document.getElementById("feViewDetailsBtn")?.addEventListener("click", () => {
    currentViewMode = "details";
    updateViewButtons();
    renderActiveDirectory();
  });

  // Path management modal toggle
  document.getElementById("fePathManagementBtn")?.addEventListener("click", openPathManagementModal);
  document.getElementById("feEnvCloseBtn")?.addEventListener("click", closePathManagementModal);
  document.getElementById("feEnvCancelBtn")?.addEventListener("click", closePathManagementModal);

  // Path management actions
  document.getElementById("feEnvNewBtn")?.addEventListener("click", handleEnvNew);
  document.getElementById("feEnvEditBtn")?.addEventListener("click", handleEnvEdit);
  document.getElementById("feEnvBrowseBtn")?.addEventListener("click", handleEnvBrowse);
  document.getElementById("feEnvDeleteBtn")?.addEventListener("click", handleEnvDelete);
  document.getElementById("feEnvMoveUpBtn")?.addEventListener("click", handleEnvMoveUp);
  document.getElementById("feEnvMoveDownBtn")?.addEventListener("click", handleEnvMoveDown);
  document.getElementById("feEnvSortBtn")?.addEventListener("click", handleEnvSortAZ);
  document.getElementById("feEnvOkBtn")?.addEventListener("click", handleEnvOk);

  // Close on Escape or click outside
  const modal = document.getElementById("filesExplorerModal");
  modal?.addEventListener("click", (e) => {
    if (e.target === modal) closeFilesUI();
  });

  window.addEventListener("keydown", (e) => {
    if (!modal || modal.classList.contains("hidden")) return;
    const envModal = document.getElementById("feEnvVarsModal");
    if (envModal && !envModal.classList.contains("hidden")) {
      if (e.key === "Escape") closePathManagementModal();
      return;
    }
    if (e.key === "Escape") {
      closeFilesUI();
    } else if (e.altKey && e.key === "ArrowLeft") {
      goBack();
    } else if (e.altKey && e.key === "ArrowRight") {
      goForward();
    } else if (e.altKey && e.key === "ArrowUp") {
      goUp();
    }
  });

  document.addEventListener("lr-library-change", async () => {
    const explorer = document.getElementById("filesExplorerModal");
    if (!explorer || explorer.classList.contains("hidden")) return;
    await syncLibraryCache();
    renderActiveDirectory();
  });
}

async function openPathManagementModal() {
  const modal = document.getElementById("feEnvVarsModal");
  if (!modal) return;
  modal.classList.remove("hidden");

  try {
    const res = await fetchJSON(`/api/explorer/paths?t=${Date.now()}`);
    envWorkingPaths = (res.paths || []).map((p) => ({ ...p }));
  } catch (err) {
    console.warn("[files_UI] Failed to load master paths:", err);
    envWorkingPaths = [];
  }

  envSelectedIndex = envWorkingPaths.length > 0 ? 0 : -1;
  renderEnvVarsList();
  if (typeof renderIcons === "function") renderIcons();
}

function closePathManagementModal() {
  const modal = document.getElementById("feEnvVarsModal");
  if (modal) modal.classList.add("hidden");
}

function renderEnvVarsList() {
  const listBox = document.getElementById("feEnvListBox");
  if (!listBox) return;
  listBox.innerHTML = "";

  if (envWorkingPaths.length === 0) {
    listBox.innerHTML = `<div class="p-4 text-center text-zinc-500 text-xs italic">No book paths registered.<br/>Click <strong>New</strong> or <strong>Browse...</strong> to add a path.</div>`;
    updateEnvButtons();
    return;
  }

  envWorkingPaths.forEach((item, idx) => {
    const isSelected = idx === envSelectedIndex;

    const row = document.createElement("div");
    row.dataset.index = idx;
    row.className = `flex items-center gap-2 px-2.5 py-1.5 rounded cursor-pointer transition-colors ${
      isSelected
        ? "bg-blue-600/35 text-blue-100 border border-blue-500/60 font-medium"
        : "hover:bg-zinc-800/80 text-zinc-300 border border-transparent"
    }`;

    row.innerHTML = `
      <span class="text-xs shrink-0">📁</span>
      <div class="flex-1 min-w-0 flex items-center justify-between gap-2">
        <span class="truncate font-mono text-[11px]" title="${item.path || ''}">${item.path || '(Empty Path - click Edit)'}</span>
        ${item.total_books ? `<span class="text-[10px] text-zinc-500 bg-zinc-900 px-1 rounded shrink-0">${item.total_books} b.</span>` : ''}
      </div>
    `;

    row.addEventListener("click", () => {
      envSelectedIndex = idx;
      renderEnvVarsList();
    });

    row.addEventListener("dblclick", () => {
      envSelectedIndex = idx;
      handleEnvEdit();
    });

    listBox.appendChild(row);
  });

  updateEnvButtons();
}

function updateEnvButtons() {
  const editBtn = document.getElementById("feEnvEditBtn");
  const deleteBtn = document.getElementById("feEnvDeleteBtn");
  const upBtn = document.getElementById("feEnvMoveUpBtn");
  const downBtn = document.getElementById("feEnvMoveDownBtn");

  const hasSelection = envSelectedIndex >= 0 && envSelectedIndex < envWorkingPaths.length;
  if (editBtn) editBtn.disabled = !hasSelection;
  if (deleteBtn) deleteBtn.disabled = !hasSelection;
  if (upBtn) upBtn.disabled = !hasSelection || envSelectedIndex === 0;
  if (downBtn) downBtn.disabled = !hasSelection || envSelectedIndex === envWorkingPaths.length - 1;
}

function handleEnvNew() {
  envWorkingPaths.push({
    id: null,
    file: "",
    path: "",
    name: "New Folder",
    scanned_at: 0,
    total_books: 0,
  });
  envSelectedIndex = envWorkingPaths.length - 1;
  renderEnvVarsList();
  handleEnvEdit();
}

function handleEnvEdit() {
  if (envSelectedIndex < 0 || envSelectedIndex >= envWorkingPaths.length) return;
  const listBox = document.getElementById("feEnvListBox");
  const row = listBox?.children[envSelectedIndex];
  if (!row) return;

  const currentVal = envWorkingPaths[envSelectedIndex].path || "";
  row.innerHTML = `
    <span class="text-xs shrink-0">📁</span>
    <input type="text" class="flex-1 bg-zinc-900 border border-blue-500 rounded px-2 py-0.5 text-xs text-white font-mono focus:outline-none" value="${currentVal.replace(/"/g, '&quot;')}" placeholder="e.g. D:\\Books\\LightNovels" />
  `;

  const input = row.querySelector("input");
  if (input) {
    input.focus();
    input.select();

    const commitChange = () => {
      const newVal = input.value.trim();
      envWorkingPaths[envSelectedIndex].path = newVal;
      const parts = newVal.replace(/\\/g, "/").split("/").filter(Boolean);
      envWorkingPaths[envSelectedIndex].name = parts[parts.length - 1] || "Folder";
      renderEnvVarsList();
    };

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        commitChange();
      } else if (e.key === "Escape") {
        renderEnvVarsList();
      }
    });
    input.addEventListener("blur", commitChange, { once: true });
  }
}

async function handleEnvBrowse() {
  try {
    showToast("Opening folder browser...", "info", 1500);
    const res = await fetchJSON("/api/explorer/pick-folder", { method: "POST" });
    if (res && res.path) {
      const selectedPath = res.path;
      if (envSelectedIndex >= 0 && envSelectedIndex < envWorkingPaths.length && !envWorkingPaths[envSelectedIndex].path) {
        envWorkingPaths[envSelectedIndex].path = selectedPath;
        const parts = selectedPath.replace(/\\/g, "/").split("/").filter(Boolean);
        envWorkingPaths[envSelectedIndex].name = parts[parts.length - 1] || "Folder";
      } else {
        const parts = selectedPath.replace(/\\/g, "/").split("/").filter(Boolean);
        envWorkingPaths.push({
          id: null,
          file: "",
          path: selectedPath,
          name: parts[parts.length - 1] || "Folder",
          scanned_at: 0,
          total_books: 0,
        });
        envSelectedIndex = envWorkingPaths.length - 1;
      }
      renderEnvVarsList();
    }
  } catch (err) {
    console.error("[files_UI] Browse folder error:", err);
    showToast("Browse failed: " + err.message, "error");
  }
}

function handleEnvDelete() {
  if (envSelectedIndex < 0 || envSelectedIndex >= envWorkingPaths.length) return;
  envWorkingPaths.splice(envSelectedIndex, 1);
  if (envSelectedIndex >= envWorkingPaths.length) {
    envSelectedIndex = envWorkingPaths.length - 1;
  }
  renderEnvVarsList();
}

function handleEnvMoveUp() {
  if (envSelectedIndex <= 0 || envSelectedIndex >= envWorkingPaths.length) return;
  const temp = envWorkingPaths[envSelectedIndex];
  envWorkingPaths[envSelectedIndex] = envWorkingPaths[envSelectedIndex - 1];
  envWorkingPaths[envSelectedIndex - 1] = temp;
  envSelectedIndex--;
  renderEnvVarsList();
}

function handleEnvMoveDown() {
  if (envSelectedIndex < 0 || envSelectedIndex >= envWorkingPaths.length - 1) return;
  const temp = envWorkingPaths[envSelectedIndex];
  envWorkingPaths[envSelectedIndex] = envWorkingPaths[envSelectedIndex + 1];
  envWorkingPaths[envSelectedIndex + 1] = temp;
  envSelectedIndex++;
  renderEnvVarsList();
}

function handleEnvSortAZ() {
  envWorkingPaths.sort((a, b) => {
    const nameA = (a.name || a.path || "").toLowerCase();
    const nameB = (b.name || b.path || "").toLowerCase();
    return nameA.localeCompare(nameB);
  });
  renderEnvVarsList();
  showToast("Sorted paths A-Z", "info", 1200);
}

async function handleEnvOk() {
  try {
    showToast("Saving path settings...", "info");
    const validPaths = envWorkingPaths.filter((p) => p.path && p.path.trim());

    // Scan any newly added paths that do not have an ID yet
    for (const p of validPaths) {
      if (!p.id) {
        try {
          const scanRes = await fetchJSON("/api/explorer/scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: p.path }),
          });
          if (scanRes && scanRes.id) {
            p.id = scanRes.id;
            p.file = `tree_${scanRes.id}.json`;
            p.total_books = scanRes.data?.total_books || 0;
            p.name = scanRes.data?.root_name || p.name;
          }
        } catch (e) {
          console.warn("[files_UI] Auto-scan for path failed:", p.path, e);
        }
      }
    }

    // Persist reordered/updated master paths (all trees stay visible; no active-path switch)
    await fetchJSON("/api/explorer/paths/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: validPaths }),
    });

    closePathManagementModal();
    await reloadTree(true);
    showToast("Paths updated successfully", "success");
  } catch (err) {
    console.error("[files_UI] Failed to save paths:", err);
    showToast("Save paths failed: " + err.message, "error");
  }
}

function updateViewButtons() {
  const gridBtn = document.getElementById("feViewGridBtn");
  const detailsBtn = document.getElementById("feViewDetailsBtn");
  if (!gridBtn || !detailsBtn) return;

  if (currentViewMode === "grid") {
    gridBtn.className = "p-1.5 bg-zinc-800 text-blue-400 transition-colors";
    detailsBtn.className = "p-1.5 hover:bg-zinc-800 text-zinc-400 transition-colors";
  } else {
    gridBtn.className = "p-1.5 hover:bg-zinc-800 text-zinc-400 transition-colors";
    detailsBtn.className = "p-1.5 bg-zinc-800 text-blue-400 transition-colors";
  }
}


/**
 * Triggers the native Windows folder picker via files_api.py.
 */
async function handlePickFolderNative() {
  try {
    showToast("Opening folder browser...", "info", 2000);
    const res = await fetchJSON("/api/explorer/pick-folder", { method: "POST" });
    if (res && res.path) {
      await scanAndLoadFolder(res.path);
    }
  } catch (err) {
    console.error("[files_UI] Native folder picker error:", err);
    showToast("Folder dialog error: " + err.message, "error");
  }
}

/**
 * Fallback prompt to manually paste or type a folder path.
 */
async function handlePromptFolderPath() {
  const input = window.prompt("Enter or paste book folder path:", currentPath || "");
  if (input && input.trim()) {
    await scanAndLoadFolder(input.trim());
  }
}

/**
 * Scans a given folder path via files_api.py, registers it, and renders multi-root tree.
 */
async function scanAndLoadFolder(folderPath, pathId = null) {
  try {
    showToast("Scanning folder for EPUBs...", "info");
    const payload = { path: folderPath };
    if (pathId) payload.id = String(pathId);

    const res = await fetchJSON("/api/explorer/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res) {
      if (res.all_trees) {
        allTreesData = res.all_trees;
      } else {
        await reloadTree(false);
      }

      currentPath = folderPath;
      historyStack = [currentPath];
      historyIndex = 0;
      if (res.id) expandedRoots.add(String(res.id));

      await syncLibraryCache();
      renderAll();
      showToast(`Scanned ${res.data?.total_books ?? 0} books in ${res.data?.root_name || 'folder'}`, "success");
    }
  } catch (err) {
    console.error("[files_UI] Folder scan error:", err);
    showToast("Scan failed: " + err.message, "error");
  }
}

/**
 * Rescans a single root path.
 */
async function handleRescanRoot(pathId, folderPath) {
  if (!folderPath) return;
  await scanAndLoadFolder(folderPath, pathId);
}

/**
 * Unregisters a root path from the library.
 */
async function handleDeleteRoot(pathId, rootName) {
  const confirmMsg = `Remove "${rootName}" from Book Explorer?\n\n(No files on your disk will be deleted)`;
  if (!window.confirm(confirmMsg)) return;

  try {
    showToast(`Removing ${rootName}...`, "info", 1000);
    const res = await fetchJSON(`/api/explorer/path/${pathId}`, { method: "DELETE" });
    if (res && res.all_trees) {
      allTreesData = res.all_trees;
      expandedRoots.delete(String(pathId));

      if (currentPath && !findNodeAcrossTrees(currentPath)) {
        currentPath = allTreesData.trees[0]?.path || "";
        historyStack = currentPath ? [currentPath] : [];
        historyIndex = currentPath ? 0 : -1;
      }

      await syncLibraryCache();
      renderAll();
      showToast(`Removed "${rootName}" from list`, "info");
    }
  } catch (err) {
    console.error("[files_UI] Delete path failed:", err);
    showToast("Failed to remove path: " + err.message, "error");
  }
}

/**
 * Rescans all registered paths.
 */
async function handleRescanAll() {
  try {
    showToast("Rescanning all registered paths...", "info");
    const res = await fetchJSON("/api/explorer/rescan-all", { method: "POST" });
    if (res && res.data) {
      allTreesData = res.data;
      await syncLibraryCache();
      renderAll();
      showToast(`Refreshed all paths (${allTreesData.total_books || 0} total books)`, "success");
    }
  } catch (err) {
    console.error("[files_UI] Rescan all failed:", err);
    showToast("Rescan all failed: " + err.message, "error");
  }
}

/**
 * Opens a folder or file in the native operating system file explorer.
 */
async function handleOpenInOS(targetPath) {
  if (!targetPath) return;
  try {
    const res = await fetchJSON("/api/explorer/open-in-os", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: targetPath }),
    });
    if (res && res.status === "ok") {
      showToast("Opened in File Explorer", "info", 1200);
    }
  } catch (err) {
    console.warn("[files_UI] Open in OS failed:", err);
    showToast("Could not open in OS explorer: " + err.message, "error");
  }
}

/**
 * Loads all registered trees from the backend.
 */
export async function reloadTree(notify = false) {
  try {
    const res = await fetchJSON(`/api/explorer/trees?t=${Date.now()}`);
    if (res && res.data) {
      allTreesData = res.data;

      // Initialize expanded state for roots based on master collapsed property
      if (Array.isArray(allTreesData.paths)) {
        allTreesData.paths.forEach((p) => {
          const pid = String(p.id);
          if (!p.collapsed) {
            expandedRoots.add(pid);
          } else {
            expandedRoots.delete(pid);
          }
        });
      }

      // Default currentPath to first available tree root if empty or invalid
      if (!currentPath || !findNodeAcrossTrees(currentPath)) {
        const firstTree = allTreesData.trees?.find((t) => t.path);
        currentPath = firstTree ? firstTree.path : "";
        historyStack = currentPath ? [currentPath] : [];
        historyIndex = currentPath ? 0 : -1;
      }

      await syncLibraryCache();
      renderAll();
      if (notify) showToast("Trees refreshed", "info");
    } else {
      allTreesData = null;
      renderAll();
    }
  } catch (err) {
    console.error("[files_UI] Failed to load trees:", err);
  }
}

/**
 * Checks whether the explorer modal is currently open.
 */
export function isFilesUIOpen() {
  const modal = document.getElementById("filesExplorerModal");
  return Boolean(modal && !modal.classList.contains("hidden"));
}

/**
 * Displays the explorer modal.
 */
export async function openFilesUI() {
  if (typeof window !== "undefined" && typeof window.closeHistoryUI === "function") {
    window.closeHistoryUI();
  } else {
    const hist = document.getElementById("historyModal");
    if (hist) hist.classList.add("hidden");
  }

  mountFilesUI();
  const modal = document.getElementById("filesExplorerModal");
  if (modal) modal.classList.remove("hidden");
  await Promise.all([syncColWidthsFromStorage(), reloadTree()]);
}

/**
 * Closes the explorer modal.
 */
export function closeFilesUI() {
  const modal = document.getElementById("filesExplorerModal");
  if (modal) modal.classList.add("hidden");
}

// Make accessible globally
if (typeof window !== "undefined") {
  window.openFilesExplorer = openFilesUI;
  window.closeFilesUI = closeFilesUI;
  window.isFilesUIOpen = isFilesUIOpen;
}

/**
 * Main render coordinator.
 */
function renderAll() {
  updateHeaderBadges();
  renderAddressBreadcrumbs();
  renderFolderTree();
  renderActiveDirectory();
  updateNavButtons();
  if (typeof renderIcons === "function") renderIcons();
}

function updateHeaderBadges() {
  const badge = document.getElementById("feRootBadge");
  const countBadge = document.getElementById("feTotalBooksCount");
  
  if (allTreesData && Array.isArray(allTreesData.trees)) {
    const rootCount = allTreesData.trees.length;
    if (badge) {
      badge.textContent = `${rootCount} Librar${rootCount === 1 ? 'y' : 'ies'}`;
      badge.title = `${rootCount} registered folders`;
    }
    if (countBadge) {
      const total = allTreesData.total_books || 0;
      countBadge.textContent = total;
      countBadge.title = `${total} book${total === 1 ? '' : 's'} across all libraries`;
    }
  } else {
    if (badge) badge.textContent = "No folder loaded";
    if (countBadge) {
      countBadge.textContent = "0";
      countBadge.title = "0 books";
    }
  }
}

function updateNavButtons() {
  const backBtn = document.getElementById("feBackBtn");
  const fwdBtn = document.getElementById("feForwardBtn");
  const upBtn = document.getElementById("feUpBtn");

  if (backBtn) backBtn.disabled = historyIndex <= 0;
  if (fwdBtn) fwdBtn.disabled = historyIndex >= historyStack.length - 1;

  if (upBtn) {
    const info = findNodeAcrossTrees(currentPath);
    const isAtRoot = !info || !info.root || !info.root.tree || info.root.tree.path === currentPath;
    upBtn.disabled = isAtRoot;
  }
}

function navigateTo(path, recordHistory = true) {
  if (!path) return;
  if (recordHistory) {
    if (historyIndex < historyStack.length - 1) {
      historyStack = historyStack.slice(0, historyIndex + 1);
    }
    historyStack.push(path);
    historyIndex = historyStack.length - 1;
  }
  currentPath = path;

  // Auto-expand path ancestors in tree
  const info = findNodeAcrossTrees(path);
  if (info && info.root) {
    expandedRoots.add(String(info.root.id));
    if (info.root.tree) {
      let curr = info.node;
      while (curr) {
        expandedFolders.add(curr.path);
        const parentP = getParentPath(info.root.tree, curr.path);
        curr = parentP ? findNodeByPath(info.root.tree, parentP) : null;
      }
    }
  }

  renderAll();
}

function goBack() {
  if (historyIndex > 0) {
    historyIndex--;
    currentPath = historyStack[historyIndex];
    renderAll();
  }
}

function goForward() {
  if (historyIndex < historyStack.length - 1) {
    historyIndex++;
    currentPath = historyStack[historyIndex];
    renderAll();
  }
}

function goUp() {
  if (!currentPath) return;
  const info = findNodeAcrossTrees(currentPath);
  if (!info || !info.root || !info.root.tree) return;
  if (info.root.tree.path === currentPath) return;

  const parentPath = getParentPath(info.root.tree, currentPath);
  if (parentPath) {
    navigateTo(parentPath);
  }
}

function getParentPath(node, targetPath, parent = null) {
  if (!node) return null;
  if (node.path === targetPath) return parent ? parent.path : null;
  for (const sub of node.subfolders || []) {
    const found = getParentPath(sub, targetPath, node);
    if (found) return found;
  }
  return null;
}

function findNodeByPath(node, targetPath) {
  if (!node) return null;
  if (node.path === targetPath) return node;
  for (const sub of node.subfolders || []) {
    const res = findNodeByPath(sub, targetPath);
    if (res) return res;
  }
  return null;
}

/**
 * Searches across all loaded trees to find the node and its owning root.
 */
function findNodeAcrossTrees(targetPath) {
  if (!allTreesData || !Array.isArray(allTreesData.trees) || !targetPath) return null;
  for (const t of allTreesData.trees) {
    if (t.tree) {
      const found = findNodeByPath(t.tree, targetPath);
      if (found) return { node: found, root: t };
    } else if (t.path === targetPath) {
      return { node: { name: t.name, path: t.path, is_dir: true, subfolders: [], files: [], book_count: t.total_books || 0 }, root: t };
    }
  }
  return null;
}

/**
 * Breadcrumb Address Bar
 */
function renderAddressBreadcrumbs() {
  const container = document.getElementById("feBreadcrumbList");
  if (!container) return;
  container.innerHTML = "";

  if (!currentPath) {
    container.innerHTML = `<span class="text-zinc-500 italic text-xs">No folder selected</span>`;
    return;
  }

  const info = findNodeAcrossTrees(currentPath);
  if (!info || !info.root || !info.root.tree) {
    const btn = document.createElement("button");
    btn.className = "px-1.5 py-0.5 rounded hover:bg-zinc-800 text-zinc-300 font-mono text-[11px] truncate max-w-[320px]";
    btn.textContent = currentPath;
    btn.title = currentPath;
    container.appendChild(btn);
    return;
  }

  const pathAncestors = [];
  let curr = info.node;
  while (curr) {
    pathAncestors.unshift(curr);
    const parentPath = getParentPath(info.root.tree, curr.path);
    curr = parentPath ? findNodeByPath(info.root.tree, parentPath) : null;
  }

  pathAncestors.forEach((seg, idx) => {
    const btn = document.createElement("button");
    btn.className = `px-1.5 py-0.5 rounded hover:bg-zinc-800 transition-colors truncate max-w-[140px] ${
      idx === 0 ? "font-semibold text-blue-400 hover:text-blue-300" : "text-zinc-300 hover:text-white"
    }`;
    btn.textContent = seg.name || "Root";
    btn.title = seg.path;
    btn.addEventListener("click", () => navigateTo(seg.path));
    container.appendChild(btn);

    if (idx < pathAncestors.length - 1) {
      const sep = document.createElement("span");
      sep.className = "text-zinc-600 text-[10px]";
      sep.textContent = "›";
      container.appendChild(sep);
    }
  });
}

/**
 * Renders the Multi-Root Left Folder Tree.
 * Every registered main path is listed as a top-level library root!
 */
function renderFolderTree() {
  const container = document.getElementById("feTreeView");
  if (!container) return;

  if (!allTreesData || !Array.isArray(allTreesData.trees) || allTreesData.trees.length === 0) {
    container.innerHTML = `
      <div class="flex flex-col items-center justify-center p-5 text-center text-zinc-500 text-xs mt-4">
        <span class="text-2xl mb-2 opacity-50">📂</span>
        <span class="font-medium text-zinc-300 mb-1">No book paths added</span>
        <p class="text-[11px] text-zinc-500 mb-3 max-w-[160px]">Add your EPUB folders to build the virtual tree.</p>
        <button id="feTreeAddEmptyBtn" class="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded text-xs font-medium shadow transition-colors">
          <i data-lucide="folder-plus" class="w-3.5 h-3.5"></i>
          <span>Add Folder</span>
        </button>
      </div>
    `;
    container.querySelector("#feTreeAddEmptyBtn")?.addEventListener("click", handlePickFolderNative);
    if (typeof renderIcons === "function") renderIcons();
    return;
  }

  container.innerHTML = "";

  allTreesData.trees.forEach((rootTree) => {
    const rootEl = createRootTreeSectionElement(rootTree);
    container.appendChild(rootEl);
  });
}

/**
 * Creates a top-level Root Library Folder item with expand/collapse and hover controls.
 */
function createRootTreeSectionElement(rootTree) {
  const wrap = document.createElement("div");
  wrap.className = "flex flex-col mb-1 select-none";

  const rootId = String(rootTree.id);
  const isRootExpanded = expandedRoots.has(rootId);
  const isRootActive = currentPath === rootTree.path;
  const hasTree = rootTree.tree != null;
  const hasSubfolders = hasTree && rootTree.tree.subfolders && rootTree.tree.subfolders.length > 0;

  // Root Row Header
  const rootRow = document.createElement("div");
  rootRow.className = `group flex items-center gap-1.5 px-2 py-1.5 rounded-lg cursor-pointer transition-all ${
    isRootActive
      ? "bg-blue-600/25 text-blue-300 font-semibold border border-blue-500/30"
      : "hover:bg-zinc-900/90 text-zinc-300 hover:text-white border border-transparent"
  }`;

  // Expand / Collapse Chevron
  const caret = document.createElement("button");
  caret.className = "w-4 h-4 flex items-center justify-center text-[10px] text-zinc-400 hover:text-white rounded hover:bg-zinc-800 shrink-0";
  caret.textContent = isRootExpanded ? "▾" : "▸";
  caret.title = isRootExpanded ? "Collapse library" : "Expand library";

  caret.addEventListener("click", async (e) => {
    e.stopPropagation();
    const willExpand = !expandedRoots.has(rootId);
    if (willExpand) {
      expandedRoots.add(rootId);
    } else {
      expandedRoots.delete(rootId);
    }
    renderFolderTree();

    try {
      await fetchJSON("/api/explorer/paths/collapse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: rootId, collapsed: !willExpand }),
      });
    } catch (err) {
      console.warn("[files_UI] Failed to persist collapse state:", err);
    }
  });

  rootRow.appendChild(caret);

  // Library Icon
  const icon = document.createElement("span");
  icon.className = "text-sm shrink-0";
  icon.textContent = "📚";
  rootRow.appendChild(icon);

  // Root Name
  const name = document.createElement("span");
  name.className = "truncate flex-1 text-xs font-medium";
  name.textContent = rootTree.name || "Library";
  name.title = rootTree.path;
  rootRow.appendChild(name);

  // Book count badge
  if (rootTree.total_books != null) {
    const badge = document.createElement("span");
    badge.className = "text-[10px] text-zinc-400 bg-zinc-900/90 border border-zinc-800 px-1.5 py-0.2 rounded font-mono shrink-0";
    badge.textContent = rootTree.total_books;
    rootRow.appendChild(badge);
  }

  // Hover Action Buttons on Root Item (Rescan, Remove)
  const actions = document.createElement("div");
  actions.className = "hidden group-hover:flex items-center gap-0.5 shrink-0 ml-1";

  const rescanBtn = document.createElement("button");
  rescanBtn.className = "p-1 rounded hover:bg-zinc-800 text-zinc-400 hover:text-blue-400 transition-colors";
  rescanBtn.title = "Rescan this folder";
  rescanBtn.innerHTML = `<i data-lucide="rotate-cw" class="w-3 h-3"></i>`;
  rescanBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    handleRescanRoot(rootId, rootTree.path);
  });
  actions.appendChild(rescanBtn);

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "p-1 rounded hover:bg-zinc-800 text-zinc-400 hover:text-red-400 transition-colors";
  deleteBtn.title = "Remove from Book Explorer";
  deleteBtn.innerHTML = `<i data-lucide="trash-2" class="w-3 h-3"></i>`;
  deleteBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    handleDeleteRoot(rootId, rootTree.name || rootTree.path);
  });
  actions.appendChild(deleteBtn);

  rootRow.appendChild(actions);

  rootRow.addEventListener("click", () => {
    if (rootTree.path) {
      navigateTo(rootTree.path);
    }
  });

  rootRow.addEventListener("dblclick", () => {
    caret.click();
  });

  wrap.appendChild(rootRow);

  // Subfolders Children Container
  if (isRootExpanded && hasTree) {
    const childrenCont = document.createElement("div");
    childrenCont.className = "flex flex-col ml-3 pl-1.5 border-l border-zinc-800/80 mt-0.5 space-y-0.5";

    if (hasSubfolders) {
      rootTree.tree.subfolders.forEach((sub) => {
        childrenCont.appendChild(createTreeNodeElement(sub, 1, rootId));
      });
    } else if ((rootTree.tree.files && rootTree.tree.files.length > 0) || rootTree.total_books > 0) {
      const emptyNote = document.createElement("div");
      emptyNote.className = "text-[10px] text-zinc-500 italic py-1 px-2";
      emptyNote.textContent = `${rootTree.total_books || 0} books in root`;
      childrenCont.appendChild(emptyNote);
    } else {
      const emptyNote = document.createElement("div");
      emptyNote.className = "text-[10px] text-zinc-500 italic py-1 px-2";
      emptyNote.textContent = "No subfolders or books";
      childrenCont.appendChild(emptyNote);
    }

    wrap.appendChild(childrenCont);
  }

  return wrap;
}

/**
 * Creates hierarchical subfolder node element with interactive expand/collapse carets.
 */
function createTreeNodeElement(node, depth = 1, rootId = "") {
  const wrap = document.createElement("div");
  wrap.className = "flex flex-col select-none";

  const isActive = node.path === currentPath;
  const hasSub = node.subfolders && node.subfolders.length > 0;
  const isExpanded = expandedFolders.has(node.path);

  const row = document.createElement("div");
  row.className = `flex items-center gap-1.5 px-2 py-1 rounded cursor-pointer transition-colors ${
    isActive
      ? "bg-blue-600/25 text-blue-300 font-semibold"
      : "hover:bg-zinc-900/80 text-zinc-400 hover:text-zinc-200"
  }`;
  row.style.paddingLeft = `${Math.max(4, (depth - 1) * 12 + 4)}px`;

  // Caret toggle
  const caret = document.createElement("span");
  caret.className = `w-3.5 h-3.5 flex items-center justify-center text-[9px] text-zinc-500 rounded shrink-0 ${
    hasSub ? "hover:bg-zinc-800 hover:text-zinc-200 cursor-pointer" : ""
  }`;
  caret.textContent = hasSub ? (isExpanded ? "▾" : "▸") : "•";

  if (hasSub) {
    caret.addEventListener("click", (e) => {
      e.stopPropagation();
      if (expandedFolders.has(node.path)) {
        expandedFolders.delete(node.path);
      } else {
        expandedFolders.add(node.path);
      }
      renderFolderTree();
    });
  }
  row.appendChild(caret);

  // Folder icon
  const icon = document.createElement("span");
  icon.className = "text-xs shrink-0";
  icon.textContent = isActive ? "📂" : (isExpanded ? "📂" : "📁");
  row.appendChild(icon);

  // Name
  const name = document.createElement("span");
  name.className = "truncate flex-1 text-xs";
  name.textContent = node.name || "Folder";
  name.title = node.path;
  row.appendChild(name);

  // Book count badge
  if (node.book_count > 0) {
    const badge = document.createElement("span");
    badge.className = "text-[10px] text-zinc-500 bg-zinc-900 px-1 rounded font-mono shrink-0";
    badge.textContent = node.book_count;
    row.appendChild(badge);
  }

  row.addEventListener("click", () => navigateTo(node.path));

  if (hasSub) {
    row.addEventListener("dblclick", () => {
      caret.click();
    });
  }

  wrap.appendChild(row);

  if (hasSub && isExpanded) {
    const childrenCont = document.createElement("div");
    childrenCont.className = "flex flex-col space-y-0.5";
    node.subfolders.forEach((sub) => {
      childrenCont.appendChild(createTreeNodeElement(sub, depth + 1, rootId));
    });
    wrap.appendChild(childrenCont);
  }

  return wrap;
}

/**
 * Right Pane: Files Explorer View (Folders & Books in currentPath)
 */
function renderActiveDirectory() {
  const container = document.getElementById("feContentArea");
  const statusLeft = document.getElementById("feStatusLeft");
  const statusRight = document.getElementById("feStatusRight");
  const bannerTitle = document.getElementById("feBannerTitle");
  const bannerPath = document.getElementById("feBannerPath");
  const bannerStats = document.getElementById("feBannerStats");
  const bannerIcon = document.getElementById("feBannerIcon");
  if (!container) return;

  if (typeof renderIcons === "function") {
    queueMicrotask(() => renderIcons());
  }

  // Check if search query is active
  if (activeFilterQuery) {
    renderSearchResults(activeFilterQuery);
    return;
  }

  if (!allTreesData || !Array.isArray(allTreesData.trees) || allTreesData.trees.length === 0) {
    renderEmptyState();
    return;
  }

  const info = findNodeAcrossTrees(currentPath);
  if (!info) {
    container.innerHTML = `
      <div class="flex-1 flex flex-col items-center justify-center text-center text-zinc-500 text-xs">
        <i data-lucide="folder-x" class="w-8 h-8 mb-2 opacity-40"></i>
        <span>Select a library path from the sidebar to preview contents.</span>
      </div>
    `;
    if (bannerTitle) bannerTitle.textContent = "No folder selected";
    if (bannerPath) bannerPath.textContent = "";
    if (bannerStats) bannerStats.textContent = "0 items";
    return;
  }

  const node = info.node;
  const isRoot = info.root && info.root.path === node.path;

  // Update Top Banner of Content Area
  if (bannerTitle) bannerTitle.textContent = node.name || (isRoot ? info.root.name : "Folder");
  if (bannerPath) {
    bannerPath.textContent = node.path || "";
    bannerPath.title = node.path || "";
  }
  if (bannerIcon) bannerIcon.textContent = isRoot ? "📚" : "📁";
  if (statusRight) statusRight.textContent = node.path || "";

  container.innerHTML = "";

  const subfolders = sortExplorerItems(node.subfolders || [], true);
  const files = sortExplorerItems(node.files || [], false);
  const hasSubfolders = subfolders.length > 0;
  const hasFiles = files.length > 0;

  const statsString = `${files.length} books, ${subfolders.length} folders`;
  if (bannerStats) bannerStats.textContent = statsString;
  if (statusLeft) statusLeft.textContent = statsString;

  if (!hasSubfolders && !hasFiles) {
    container.innerHTML = `
      <div class="flex-1 flex flex-col items-center justify-center text-center text-zinc-500 text-xs">
        <i data-lucide="folder-open" class="w-8 h-8 mb-2 opacity-40"></i>
        <span>This folder contains no .epub files or subfolders.</span>
      </div>
    `;
    return;
  }

  if (currentViewMode === "details") {
    container.appendChild(createExplorerDetailsList(subfolders, files));
    return;
  }

  const scroller = document.createElement("div");
  scroller.className = "flex-1 overflow-y-auto p-4 custom-scrollbar";

  if (hasSubfolders) {
    const subTitle = document.createElement("div");
    subTitle.className = "text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-2 flex items-center gap-1.5";
    subTitle.innerHTML = `<i data-lucide="folder" class="w-3.5 h-3.5 text-blue-400"></i> Folders (${subfolders.length})`;
    scroller.appendChild(subTitle);

    const subGrid = document.createElement("div");
    subGrid.className = "grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-2.5 mb-5";
    subfolders.forEach((sub) => {
      subGrid.appendChild(createFolderTile(sub));
    });
    scroller.appendChild(subGrid);
  }

  if (hasFiles) {
    const booksTitle = document.createElement("div");
    booksTitle.className = "text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-2 flex items-center gap-1.5";
    booksTitle.innerHTML = `<i data-lucide="book-open" class="w-3.5 h-3.5 text-blue-400"></i> Books (${files.length})`;
    scroller.appendChild(booksTitle);

    const bookGrid = document.createElement("div");
    bookGrid.className = "grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3";
    files.forEach((file) => {
      bookGrid.appendChild(createBookCard(file));
    });
    scroller.appendChild(bookGrid);
  }

  container.appendChild(scroller);
}

/**
 * Creates clickable tile for a subfolder (Grid View).
 */
function createFolderTile(folderNode) {
  const el = document.createElement("div");
  el.className = "flex items-center gap-2.5 p-2.5 bg-zinc-950/80 hover:bg-zinc-800/80 border border-zinc-800/80 rounded-lg cursor-pointer transition-all hover:border-zinc-700";
  el.innerHTML = `
    <span class="text-xl">📁</span>
    <div class="flex-1 min-w-0">
      <div class="text-xs font-medium text-zinc-200 truncate" title="${folderNode.name}">${folderNode.name}</div>
      <div class="text-[10px] text-zinc-500">${folderNode.book_count || 0} books</div>
    </div>
  `;
  el.addEventListener("click", () => navigateTo(folderNode.path));
  return el;
}

/**
 * Creates card for an EPUB book in Grid View.
 */
function createBookCard(file) {
  const card = document.createElement("div");
  card.className = "group relative flex flex-col bg-zinc-950 border border-zinc-800 rounded-lg p-3 hover:border-blue-500/50 hover:bg-zinc-900/60 transition-all select-none shadow-sm";

  const statusLabel = itemStatus(file, false);
  const inShelf = statusLabel !== "Add";

  card.innerHTML = `
    <!-- Card Header / Cover Mockup -->
    <div class="flex items-center gap-2.5 mb-2.5">
      <div class="w-10 h-14 rounded bg-gradient-to-br from-blue-900/40 to-purple-900/40 border border-blue-500/20 flex flex-col items-center justify-center text-blue-400 shrink-0 group-hover:scale-105 transition-transform">
        <i data-lucide="book" class="w-5 h-5 opacity-80"></i>
        <span class="text-[8px] font-bold mt-1 text-blue-300/80">EPUB</span>
      </div>
      <div class="flex-1 min-w-0">
        <h4 class="text-xs font-semibold text-zinc-100 group-hover:text-blue-400 line-clamp-2 transition-colors leading-tight">
          ${file.title || file.name}
        </h4>
        <div class="flex items-center gap-1.5 mt-1 text-[10px] text-zinc-500">
          <span>${file.size_formatted}</span>
          ${file.root_name ? `<span class="truncate max-w-[90px] text-blue-400/80 bg-blue-950/40 px-1 rounded">• ${file.root_name}</span>` : ''}
        </div>
      </div>
    </div>

    <!-- Card Action Footer -->
    <div class="mt-auto pt-2 border-t border-zinc-900 flex items-center justify-between gap-1">
      ${
        inShelf
          ? `
        <span class="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded bg-zinc-800/80 border border-zinc-700/60 ${statusLabelClass(statusLabel)}" title="${statusLabel}">
          <i data-lucide="check" class="w-3 h-3"></i> In library
        </span>
        <button class="fe-open-btn px-2 py-1 text-[10px] font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded transition-colors">
          Open
        </button>
      `
          : `
        <button class="fe-add-btn w-full flex items-center justify-center gap-1 px-2.5 py-1 text-[11px] font-medium bg-blue-600/20 hover:bg-blue-600 text-blue-400 hover:text-white border border-blue-500/30 hover:border-transparent rounded transition-all">
          <i data-lucide="plus" class="w-3 h-3"></i> Add to Shelf
        </button>
      `
      }
    </div>
  `;

  const addBtn = card.querySelector(".fe-add-btn");
  addBtn?.addEventListener("click", async (e) => {
    e.stopPropagation();
    await handleAddBookToShelf(file.path, card);
  });

  const openBtn = card.querySelector(".fe-open-btn");
  openBtn?.addEventListener("click", async (e) => {
    e.stopPropagation();
    await handleOpenBookDirect(file.path);
  });

  card.addEventListener("dblclick", async () => {
    await handleOpenBookDirect(file.path);
  });

  return card;
}

/**
 * Unified Windows-style details list: Name | Library | Type | Size | Status.
 * All columns are px-sized; dragging one pushes the rest (can scroll off-screen).
 */
function createExplorerDetailsList(folders, files) {
  const wrap = document.createElement("div");
  wrap.className = "flex-1 min-h-0 overflow-auto custom-scrollbar text-xs";

  const header = document.createElement("div");
  header.className = "fe-details-row grid items-stretch bg-zinc-950 font-semibold text-zinc-400 border-b border-zinc-800 text-[11px] sticky top-0 z-10 select-none";
  header.style.gridTemplateColumns = detailsColTemplate();
  header.style.minWidth = `${detailsRowMinWidth()}px`;
  header.innerHTML = `
    <button type="button" data-sort="name" class="fe-sort-h flex items-center min-w-0 px-2 py-1.5 text-left hover:text-zinc-200 truncate">Name${sortIndicator("name")}</button>
    ${colDividerHtml("name")}
    <button type="button" data-sort="library" class="fe-sort-h flex items-center min-w-0 px-2 text-left hover:text-zinc-200 truncate">Library${sortIndicator("library")}</button>
    ${colDividerHtml("library")}
    <button type="button" data-sort="type" class="fe-sort-h flex items-center min-w-0 px-2 text-left hover:text-zinc-200 truncate">Type${sortIndicator("type")}</button>
    ${colDividerHtml("type")}
    <button type="button" data-sort="size" class="fe-sort-h flex items-center min-w-0 px-2 text-left hover:text-zinc-200 truncate">Size${sortIndicator("size")}</button>
    ${colDividerHtml("size")}
    <button type="button" data-sort="status" class="fe-sort-h flex items-center min-w-0 px-2 text-left hover:text-zinc-200 truncate">Status${sortIndicator("status")}</button>
  `;

  header.querySelectorAll(".fe-col-resize").forEach((handle) => {
    attachColResize(handle, handle.getAttribute("data-col"));
  });

  header.querySelectorAll(".fe-sort-h").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.getAttribute("data-sort");
      if (!key) return;
      if (sortKey === key) {
        sortDir = sortDir === "asc" ? "desc" : "asc";
      } else {
        sortKey = key;
        sortDir = "asc";
      }
      renderActiveDirectory();
    });
  });

  const scroller = document.createElement("div");
  scroller.className = "divide-y divide-zinc-900 bg-zinc-950/40";

  folders.forEach((folder) => {
    scroller.appendChild(createFolderDetailRow(folder));
  });
  files.forEach((file) => {
    scroller.appendChild(createBookDetailRow(file));
  });

  wrap.appendChild(header);
  wrap.appendChild(scroller);
  requestAnimationFrame(() => applyDetailsColWidths());
  return wrap;
}

function createFolderDetailRow(folder) {
  const row = document.createElement("div");
  row.className = "fe-details-row grid items-stretch hover:bg-zinc-900/80 transition-colors select-none cursor-pointer";
  row.style.gridTemplateColumns = detailsColTemplate();
  row.style.minWidth = `${detailsRowMinWidth()}px`;
  const lib = folder.root_name || "";
  row.innerHTML = `
    <div class="flex items-center gap-2 min-w-0 px-2 py-1.5" title="${folder.name}">
      <span class="text-sm shrink-0">📁</span>
      <span class="truncate font-medium text-zinc-200">${folder.name}</span>
    </div>
    ${colDividerCell()}
    <div class="flex items-center px-2 text-zinc-500 text-[10px] truncate" title="${lib}">${lib}</div>
    ${colDividerCell()}
    <div class="flex items-center px-2 text-zinc-500 text-[10px] truncate">${itemType(folder, true)}</div>
    ${colDividerCell()}
    <div class="flex items-center px-2 text-zinc-500"></div>
    ${colDividerCell()}
    <div class="flex items-center px-2 text-zinc-500 text-[10px] truncate"></div>
  `;
  row.addEventListener("click", () => navigateTo(folder.path));
  row.addEventListener("dblclick", () => navigateTo(folder.path));
  return row;
}

function createBookDetailRow(file) {
  const statusLabel = itemStatus(file, false);
  const inShelf = statusLabel !== "Add";
  const row = document.createElement("div");
  row.className = "fe-details-row grid items-stretch hover:bg-zinc-900/80 transition-colors select-none";
  row.style.gridTemplateColumns = detailsColTemplate();
  row.style.minWidth = `${detailsRowMinWidth()}px`;
  const lib = file.root_name || "";
  row.innerHTML = `
    <div class="flex items-center gap-2 min-w-0 px-2 py-1.5">
      <span class="text-sm shrink-0">📘</span>
      <span class="truncate font-medium text-zinc-200">${file.name}</span>
    </div>
    ${colDividerCell()}
    <div class="flex items-center px-2 text-zinc-500 text-[10px] truncate" title="${lib}">${lib}</div>
    ${colDividerCell()}
    <div class="flex items-center px-2 text-zinc-400 text-[10px] truncate">${itemType(file, false)}</div>
    ${colDividerCell()}
    <div class="flex items-center px-2 text-zinc-400 text-[10px] truncate">${file.size_formatted || ""}</div>
    ${colDividerCell()}
    <div class="flex items-center px-2">
      ${
        inShelf
          ? `<button type="button" class="fe-open-btn text-[10px] truncate ${statusLabelClass(statusLabel)}" title="${statusLabel}">In library</button>`
          : `<button type="button" class="fe-add-btn text-[10px] truncate ${statusLabelClass(statusLabel)}">${statusLabel}</button>`
      }
    </div>
  `;
  row.querySelector(".fe-add-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    handleAddBookToShelf(file.path, row);
  });
  row.querySelector(".fe-open-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    handleOpenBookDirect(file.path);
  });
  row.addEventListener("dblclick", () => handleOpenBookDirect(file.path));
  return row;
}

/**
 * Searches across all books in all registered paths for instant live filtering.
 */
function renderSearchResults(query) {
  const container = document.getElementById("feContentArea");
  const statusLeft = document.getElementById("feStatusLeft");
  const bannerTitle = document.getElementById("feBannerTitle");
  const bannerPath = document.getElementById("feBannerPath");
  const bannerStats = document.getElementById("feBannerStats");
  if (!container || !allTreesData || !allTreesData.flat_list) return;

  const matches = sortExplorerItems(
    allTreesData.flat_list.filter(
      (b) => b.name.toLowerCase().includes(query) || (b.title && b.title.toLowerCase().includes(query))
    ),
    false
  );

  if (bannerTitle) bannerTitle.textContent = `Search: "${query}"`;
  if (bannerPath) bannerPath.textContent = `Searching across all library paths`;
  if (bannerStats) bannerStats.textContent = `${matches.length} matches`;

  container.innerHTML = "";

  if (matches.length === 0) {
    container.innerHTML = `
      <div class="flex-1 flex flex-col items-center justify-center p-8 text-center text-zinc-500 text-xs">
        No books matched "${query}".
      </div>
    `;
    if (statusLeft) statusLeft.textContent = "0 matches";
    return;
  }

  if (currentViewMode === "grid") {
    const scroller = document.createElement("div");
    scroller.className = "flex-1 overflow-y-auto p-4 custom-scrollbar";
    const label = document.createElement("div");
    label.className = "text-xs font-semibold text-zinc-400 mb-3 flex items-center gap-1.5";
    label.innerHTML = `<i data-lucide="search" class="w-3.5 h-3.5 text-blue-400"></i><span>Search Results across all libraries (${matches.length})</span>`;
    scroller.appendChild(label);
    const bookGrid = document.createElement("div");
    bookGrid.className = "grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3";
    matches.forEach((file) => {
      bookGrid.appendChild(createBookCard(file));
    });
    scroller.appendChild(bookGrid);
    container.appendChild(scroller);
  } else {
    container.appendChild(createExplorerDetailsList([], matches));
  }

  if (statusLeft) statusLeft.textContent = `${matches.length} matching books`;
}

function renderEmptyState() {
  const container = document.getElementById("feContentArea");
  const bannerTitle = document.getElementById("feBannerTitle");
  const bannerPath = document.getElementById("feBannerPath");
  const bannerStats = document.getElementById("feBannerStats");
  if (!container) return;

  if (bannerTitle) bannerTitle.textContent = "No Library Added";
  if (bannerPath) bannerPath.textContent = "Add a folder to begin";
  if (bannerStats) bannerStats.textContent = "0 items";

  container.innerHTML = `
    <div class="flex-1 flex flex-col items-center justify-center text-center p-6">
      <div class="w-12 h-12 rounded-full bg-blue-600/10 border border-blue-500/20 flex items-center justify-center text-blue-400 mb-3">
        <i data-lucide="folder-plus" class="w-6 h-6"></i>
      </div>
      <h3 class="text-sm font-semibold text-zinc-200 mb-1">No Book Folders Registered</h3>
      <p class="text-xs text-zinc-400 max-w-sm mb-4">
        Choose a folder containing your EPUB files. The Book Explorer will index all folders and display them together in your sidebar tree.
      </p>
      <button id="feEmptyAddBtn" class="flex items-center justify-center gap-1.5 px-3.5 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium rounded-lg shadow-lg transition-all">
        <i data-lucide="folder-open" class="w-4 h-4"></i>
        <span>Select Book Folder</span>
      </button>
    </div>
  `;

  container.querySelector("#feEmptyAddBtn")?.addEventListener("click", handlePickFolderNative);
}

function isBookInShelf(epubPath, fileName) {
  return Boolean(getCachedBook(epubPath, fileName));
}

/**
 * 1-Click adds book to shelf.
 */
async function handleAddBookToShelf(epubPath, cardElement) {
  try {
    showToast("Adding book to shelf...", "info", 1500);
    const res = await fetchJSON("/api/explorer/add-to-shelf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ epub_path: epubPath }),
    });

    if (res && res.book) {
      libraryCache.set(epubPath.toLowerCase(), res.book);
      showToast("Added to library shelf!", "success");

      // Signal library.js to refresh active shelf
      await loadLibrary();
      document.dispatchEvent(new CustomEvent("lr-library-change"));

      // Refresh card badge
      renderActiveDirectory();
    }
  } catch (err) {
    console.error("[files_UI] Add to shelf failed:", err);
    showToast("Failed to add book: " + err.message, "error");
  }
}

/**
 * Double click or Open button: adds to shelf and selects document to start reading immediately.
 */
async function handleOpenBookDirect(epubPath) {
  try {
    let book = libraryCache.get(epubPath.toLowerCase());
    if (!book) {
      showToast("Binding book to shelf...", "info", 1000);
      const res = await fetchJSON("/api/explorer/add-to-shelf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ epub_path: epubPath }),
      });
      if (res && res.book) {
        book = res.book;
        libraryCache.set(epubPath.toLowerCase(), book);
        await loadLibrary();
        document.dispatchEvent(new CustomEvent("lr-library-change"));
      }
    }

    if (book) {
      closeFilesUI();
      if (typeof window !== "undefined" && typeof window.closeHistoryUI === "function") {
        window.closeHistoryUI();
      }
      await selectDocument(book);
    }
  } catch (err) {
    console.error("[files_UI] Open book failed:", err);
    showToast("Failed to open book: " + err.message, "error");
  }
}
