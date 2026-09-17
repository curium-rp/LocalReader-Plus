import { fetchJSON } from "./api.js";
import { renderIcons } from "./ui.js";
import {
  selectDocument,
  peekShelfStatus,
  openShelfStatusMenu,
  resolveLibraryProgress,
} from "./library.js";

// State
let historyItems = [];
let activeTab = "all";
let searchQuery = "";
let sortKey = "lastOpened";
let sortDir = "desc";

// Column resize state matching files_UI.js
const COL_STORAGE_KEY = "lr-history-col-widths-v2";
const HANDLE_W = 5;
const COL_MIN = { name: 100, progress: 60, pages: 70, lastOpened: 90, status: 70 };
const COL_DEFAULT = { name: 260, progress: 75, pages: 100, lastOpened: 130, status: 88 };

function loadColWidths() {
  try {
    const parsed = JSON.parse(localStorage.getItem(COL_STORAGE_KEY) || "null");
    if (!parsed || typeof parsed !== "object") return { ...COL_DEFAULT };
    return {
      name: Math.max(COL_MIN.name, Number(parsed.name) || COL_DEFAULT.name),
      progress: Math.max(COL_MIN.progress, Number(parsed.progress) || COL_DEFAULT.progress),
      pages: Math.max(COL_MIN.pages, Number(parsed.pages) || COL_DEFAULT.pages),
      lastOpened: Math.max(COL_MIN.lastOpened, Number(parsed.lastOpened) || COL_DEFAULT.lastOpened),
      status: Math.max(COL_MIN.status, Number(parsed.status) || COL_DEFAULT.status),
    };
  } catch {
    return { ...COL_DEFAULT };
  }
}

let colWidths = loadColWidths();

async function syncColWidthsFromStorage() {
  try {
    const data = await fetchJSON("/api/explorer/columepx");
    if (data && data.history && typeof data.history === "object") {
      const parsed = data.history;
      colWidths = {
        name: Math.max(COL_MIN.name, Number(parsed.name) || COL_DEFAULT.name),
        progress: Math.max(COL_MIN.progress, Number(parsed.progress) || COL_DEFAULT.progress),
        pages: Math.max(COL_MIN.pages, Number(parsed.pages) || COL_DEFAULT.pages),
        lastOpened: Math.max(COL_MIN.lastOpened, Number(parsed.lastOpened) || COL_DEFAULT.lastOpened),
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
    body: JSON.stringify({ view: "history", widths: colWidths }),
  }).catch(() => {});
}

syncColWidthsFromStorage();

function detailsColTemplate() {
  return `${colWidths.name}px ${HANDLE_W}px ${colWidths.progress}px ${HANDLE_W}px ${colWidths.pages}px ${HANDLE_W}px ${colWidths.lastOpened}px ${HANDLE_W}px ${colWidths.status}px`;
}

function detailsRowMinWidth() {
  return (
    colWidths.name +
    colWidths.progress +
    colWidths.pages +
    colWidths.lastOpened +
    colWidths.status +
    HANDLE_W * 4
  );
}

function applyDetailsColWidths() {
  const template = detailsColTemplate();
  const minW = `${detailsRowMinWidth()}px`;
  document.querySelectorAll(".history-details-row").forEach((el) => {
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
  return `<div data-col="${key}" class="history-col-resize relative h-full cursor-col-resize group" title="Resize column">
    <span class="pointer-events-none absolute inset-y-0 left-1/2 -translate-x-1/2 w-px bg-zinc-500 group-hover:w-0.5 group-hover:bg-blue-400"></span>
  </div>`;
}

function colDividerCell() {
  return `<div class="relative h-full"><span class="absolute inset-y-0 left-1/2 -translate-x-1/2 w-px bg-zinc-800"></span></div>`;
}

function shelfStatusLabel(item) {
  const s = peekShelfStatus(item);
  if (s === "hold" || s === "on hold") return "on hold";
  if (s === "finished") return "finished";
  return "reading";
}

function statusLabelClass(label) {
  if (label === "on hold" || label === "hold") return "text-amber-400 hover:text-amber-300";
  if (label === "finished") return "text-zinc-400 hover:text-zinc-300";
  return "text-emerald-400 hover:text-emerald-300";
}

function sortIndicator(key) {
  if (sortKey !== key) return "";
  return sortDir === "asc" ? " ▲" : " ▼";
}

function updateSortIndicators() {
  const header = document.getElementById("historyHeaderRow");
  if (!header) return;
  header.querySelectorAll(".history-sort-btn").forEach((btn) => {
    const key = btn.getAttribute("data-sort");
    let label = "Name";
    if (key === "progress") label = "Progress";
    else if (key === "pages") label = "Pages";
    else if (key === "lastOpened") label = "Last opened";
    else if (key === "status") label = "Status";
    btn.textContent = `${label}${sortIndicator(key)}`;
  });
}

function escapeHtml(str) {
  return String(str || "").replace(/[&<>'"]/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[ch] || ch));
}

function formatLastOpened(ms) {
  const ts = Number(ms) || 0;
  if (ts <= 0) return "Never";
  const diff = Date.now() - ts;
  const min = 60 * 1000;
  const hour = 60 * min;
  const day = 24 * hour;
  if (diff < 0) return new Date(ts).toLocaleString();
  if (diff < hour) return `${Math.max(1, Math.round(diff / min))} min ago`;
  if (diff < day) return `${Math.max(1, Math.round(diff / hour))} hr ago`;
  if (diff < 7 * day) return `${Math.max(1, Math.round(diff / day))} d ago`;
  return new Date(ts).toLocaleString();
}

function matchesTab(item, tab) {
  const status = peekShelfStatus(item);
  if (tab === "all") return true;
  if (tab === "reading") return status === "reading";
  if (tab === "hold") return status === "hold";
  if (tab === "finished") return status === "finished";
  return true;
}

function visibleHistoryItems() {
  const q = searchQuery.trim().toLowerCase();
  const items = historyItems
    .filter((item) => matchesTab(item, activeTab))
    .filter((item) => {
      if (!q) return true;
      const name = String(item.fileName || item.title || "").toLowerCase();
      return name.includes(q);
    });

  const mul = sortDir === "desc" ? -1 : 1;
  return items.sort((a, b) => {
    if (sortKey === "name") {
      const an = String(a.fileName || a.title || "").toLowerCase();
      const bn = String(b.fileName || b.title || "").toLowerCase();
      const cmp = an.localeCompare(bn, undefined, { numeric: true, sensitivity: "base" });
      if (cmp !== 0) return cmp * mul;
    } else if (sortKey === "progress") {
      const pa = resolveLibraryProgress(a).percent || 0;
      const pb = resolveLibraryProgress(b).percent || 0;
      if (pa !== pb) return (pa - pb) * mul;
    } else if (sortKey === "pages") {
      const pa = resolveLibraryProgress(a).total || 0;
      const pb = resolveLibraryProgress(b).total || 0;
      if (pa !== pb) return (pa - pb) * mul;
    } else if (sortKey === "status") {
      const sa = shelfStatusLabel(a);
      const sb = shelfStatusLabel(b);
      const cmp = sa.localeCompare(sb, undefined, { sensitivity: "base" });
      if (cmp !== 0) return cmp * mul;
    } else {
      const at = Number(a.lastAccessed) || 0;
      const bt = Number(b.lastAccessed) || 0;
      if (at !== bt) return (at - bt) * mul;
    }
    const an = String(a.fileName || a.title || "").toLowerCase();
    const bn = String(b.fileName || b.title || "").toLowerCase();
    return an.localeCompare(bn, undefined, { numeric: true, sensitivity: "base" });
  });
}

function historyModal() {
  return document.getElementById("historyModal");
}

export function isHistoryOpen() {
  const modal = historyModal();
  return Boolean(modal && !modal.classList.contains("hidden"));
}

export function mountHistoryUI() {
  if (historyModal()) return;

  const overlay = document.createElement("div");
  overlay.id = "historyModal";
  overlay.className = "fixed top-[32px] right-0 bottom-0 left-0 z-[9999] flex bg-black/80 backdrop-blur-sm hidden";
  overlay.innerHTML = `
    <div id="historyPanel" class="flex flex-col w-full h-full bg-zinc-950 overflow-hidden font-sans text-zinc-200 relative">
      <div class="flex items-center justify-between px-4 py-2.5 bg-zinc-900/90 border-b border-zinc-800/80 select-none">
        <div class="flex items-center gap-2.5">
          <i data-lucide="history" class="w-4 h-4 text-blue-400"></i>
          <span class="text-xs font-semibold tracking-wide text-zinc-100">History</span>
        </div>
        <button id="historyCloseBtn" class="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors" title="Close (Esc)">
          <i data-lucide="x" class="w-4 h-4"></i>
        </button>
      </div>

      <div class="flex items-center gap-3 px-4 py-2 bg-zinc-900/70 border-b border-zinc-800/80">
        <div id="historyTabs" class="flex items-center gap-1">
          <button type="button" data-history-tab="all" class="history-tab px-2.5 py-1 rounded text-xs font-medium border border-zinc-700 bg-zinc-800 text-zinc-100">All</button>
          <button type="button" data-history-tab="reading" class="history-tab px-2.5 py-1 rounded text-xs font-medium border border-transparent text-zinc-400 hover:text-zinc-200">Reading</button>
          <button type="button" data-history-tab="hold" class="history-tab px-2.5 py-1 rounded text-xs font-medium border border-transparent text-zinc-400 hover:text-zinc-200">On hold</button>
          <button type="button" data-history-tab="finished" class="history-tab px-2.5 py-1 rounded text-xs font-medium border border-transparent text-zinc-400 hover:text-zinc-200">Finished</button>
        </div>
        <div class="relative flex-1 max-w-sm ml-auto">
          <i data-lucide="search" class="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none"></i>
          <input id="historySearchInput" type="text" placeholder="Search history..." class="w-full bg-zinc-950 border border-zinc-800 rounded px-8 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-blue-500 transition-colors" />
        </div>
      </div>

      <div class="flex-1 min-h-0 overflow-auto custom-scrollbar text-xs">
        <div id="historyHeaderRow" class="history-details-row grid items-stretch bg-zinc-950 font-semibold text-zinc-400 border-b border-zinc-800 text-[11px] sticky top-0 z-10 select-none" style="grid-template-columns: ${detailsColTemplate()}; min-width: ${detailsRowMinWidth()}px;">
          <button type="button" data-sort="name" class="history-sort-btn flex items-center min-w-0 px-3 py-1.5 text-left hover:text-zinc-200 truncate">Name${sortIndicator("name")}</button>
          ${colDividerHtml("name")}
          <button type="button" data-sort="progress" class="history-sort-btn flex items-center min-w-0 px-2 py-1.5 text-left hover:text-zinc-200 truncate">Progress${sortIndicator("progress")}</button>
          ${colDividerHtml("progress")}
          <button type="button" data-sort="pages" class="history-sort-btn flex items-center min-w-0 px-2 py-1.5 text-left hover:text-zinc-200 truncate">Pages${sortIndicator("pages")}</button>
          ${colDividerHtml("pages")}
          <button type="button" data-sort="lastOpened" class="history-sort-btn flex items-center min-w-0 px-2 py-1.5 text-left hover:text-zinc-200 truncate">Last opened${sortIndicator("lastOpened")}</button>
          ${colDividerHtml("lastOpened")}
          <button type="button" data-sort="status" class="history-sort-btn flex items-center min-w-0 px-2 py-1.5 text-left hover:text-zinc-200 truncate">Status${sortIndicator("status")}</button>
        </div>
        <div id="historyList" class="divide-y divide-zinc-900 bg-zinc-950/40"></div>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  const header = overlay.querySelector("#historyHeaderRow");
  if (header) {
    header.querySelectorAll(".history-col-resize").forEach((handle) => {
      attachColResize(handle, handle.getAttribute("data-col"));
    });

    header.querySelectorAll(".history-sort-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-sort");
        if (!key) return;
        if (sortKey === key) {
          sortDir = sortDir === "asc" ? "desc" : "asc";
        } else {
          sortKey = key;
          sortDir = key === "lastOpened" ? "desc" : "asc";
        }
        updateSortIndicators();
        renderHistoryList();
      });
    });
  }

  document.getElementById("historyCloseBtn")?.addEventListener("click", closeHistoryUI);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeHistoryUI();
  });
  document.getElementById("historyTabs")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-history-tab]");
    if (!btn) return;
    activeTab = btn.getAttribute("data-history-tab") || "all";
    paintHistoryTabs();
    renderHistoryList();
  });
  document.getElementById("historySearchInput")?.addEventListener("input", (e) => {
    searchQuery = e.target.value || "";
    renderHistoryList();
  });

  window.addEventListener("keydown", (e) => {
    if (!isHistoryOpen()) return;
    if (e.key === "Escape") closeHistoryUI();
  });

  document.addEventListener("lr-library-change", () => {
    if (isHistoryOpen()) refreshHistory();
  });

  renderIcons();
}

function paintHistoryTabs() {
  document.querySelectorAll("[data-history-tab]").forEach((btn) => {
    const on = btn.getAttribute("data-history-tab") === activeTab;
    btn.classList.toggle("bg-zinc-800", on);
    btn.classList.toggle("border-zinc-700", on);
    btn.classList.toggle("text-zinc-100", on);
    btn.classList.toggle("border-transparent", !on);
    btn.classList.toggle("text-zinc-400", !on);
  });
}

async function refreshHistory() {
  try {
    const items = await fetchJSON(`/api/library?t=${Date.now()}`);
    historyItems = Array.isArray(items) ? items : [];
  } catch (err) {
    console.error("[history] Failed to load library:", err);
    historyItems = [];
  }
  renderHistoryList();
}

function renderHistoryList() {
  const list = document.getElementById("historyList");
  if (!list) return;
  const rows = visibleHistoryItems();
  list.innerHTML = "";
  if (rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "p-8 text-center text-zinc-500 text-xs";
    empty.textContent = searchQuery.trim() ? "No matching books." : "No books in this list.";
    list.appendChild(empty);
    renderIcons();
    return;
  }

  const template = detailsColTemplate();
  const minW = `${detailsRowMinWidth()}px`;

  rows.forEach((item) => {
    const { current, total, percent } = resolveLibraryProgress(item);
    const name = escapeHtml(item.fileName || item.title || "Untitled");
    const statusLabel = shelfStatusLabel(item);
    const row = document.createElement("div");
    row.className = "history-details-row grid items-stretch hover:bg-zinc-900/80 transition-colors select-none cursor-pointer";
    row.style.gridTemplateColumns = template;
    row.style.minWidth = minW;
    row.innerHTML = `
      <div class="flex items-center gap-2 min-w-0 px-3 py-1.5" title="${name}">
        <span class="text-sm shrink-0">📘</span>
        <span class="truncate font-medium text-zinc-200">${name}</span>
      </div>
      ${colDividerCell()}
      <div class="flex items-center px-2 text-zinc-400 truncate">${percent}%</div>
      ${colDividerCell()}
      <div class="flex items-center px-2 text-zinc-400 truncate">${current} / ${total}</div>
      ${colDividerCell()}
      <div class="flex items-center px-2 text-zinc-500 truncate">${escapeHtml(formatLastOpened(item.lastAccessed))}</div>
      ${colDividerCell()}
      <div class="flex items-center px-2">
        <button type="button" class="history-status-btn text-[10px] truncate ${statusLabelClass(statusLabel)}" title="Change shelf status">${statusLabel}</button>
      </div>
    `;
    const openItem = async () => {
      closeHistoryUI();
      if (typeof window !== "undefined" && typeof window.closeFilesUI === "function") {
        window.closeFilesUI();
      }
      await selectDocument(item);
    };
    row.addEventListener("click", openItem);
    row.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      openShelfStatusMenu(e, item);
    });
    const statusBtn = row.querySelector(".history-status-btn");
    if (statusBtn) {
      statusBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openShelfStatusMenu(e, item);
      });
    }
    list.appendChild(row);
  });
  renderIcons();
  requestAnimationFrame(() => applyDetailsColWidths());
}

export async function openHistoryUI() {
  if (typeof window !== "undefined" && typeof window.closeFilesUI === "function") {
    window.closeFilesUI();
  } else {
    const filesModal = document.getElementById("filesExplorerModal");
    if (filesModal) filesModal.classList.add("hidden");
  }

  mountHistoryUI();
  const modal = historyModal();
  if (modal) modal.classList.remove("hidden");
  const search = document.getElementById("historySearchInput");
  if (search) {
    search.value = searchQuery;
    search.focus();
  }
  paintHistoryTabs();
  applyDetailsColWidths();
  await Promise.all([syncColWidthsFromStorage(), refreshHistory()]);
}

export function closeHistoryUI() {
  const modal = historyModal();
  if (modal) modal.classList.add("hidden");
}

if (typeof window !== "undefined") {
  window.openHistoryUI = openHistoryUI;
  window.closeHistoryUI = closeHistoryUI;
  window.isHistoryOpen = isHistoryOpen;
}
