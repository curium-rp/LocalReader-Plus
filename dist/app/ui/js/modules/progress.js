import { state } from "./state.js";

const CHARS_PER_PAGE = 1024;

const index = {
  charCounts: [],
  cumulative: [0],
  totalChars: 0,
};

let scrollRaf = 0;

function stripTags(html) {
  return String(html || "")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function getBookType() {
  const stored = String(state.currentDoc?.bookType || "").toLowerCase();
  if (stored === "pdf" || stored === "epub") return stored;
  const pages = state.currentPages || [];
  if (pages[0] && String(pages[0]).includes("pdf-page")) return "pdf";
  const name = String(state.currentDoc?.fileName || "").toLowerCase();
  if (name.endsWith(".pdf")) return "pdf";
  if (name.endsWith(".epub")) return "epub";
  return pages.length ? "epub" : "pdf";
}

export function indexDocument(pages) {
  const list = Array.isArray(pages) ? pages : [];
  index.charCounts = list.map((page) => stripTags(page).length);
  index.cumulative = [0];
  let running = 0;
  for (const count of index.charCounts) {
    running += count;
    index.cumulative.push(running);
  }
  index.totalChars = running;
  if (state.currentDoc && !state.currentDoc.bookType) {
    state.currentDoc.bookType = getBookType();
  }
  updateProgressDisplay();
}

function intraPageChars(pageIndex) {
  const pageChars = index.charCounts[pageIndex] || 0;
  if (pageChars <= 0) return 0;

  const els = state.sentenceElements || [];
  const active = document.querySelector("#textContent .active-sentence");
  if (active && els.length) {
    const activeIndex = els.indexOf(active);
    if (activeIndex >= 0) {
      let before = 0;
      let total = 0;
      for (let i = 0; i < els.length; i++) {
        const len = (els[i].textContent || "").length;
        total += len;
        if (i < activeIndex) before += len;
      }
      if (total > 0) return (before / total) * pageChars;
    }
  }

  const scroller = document.querySelector(".content-area");
  if (!scroller) return 0;
  const max = scroller.scrollHeight - scroller.clientHeight;
  if (max <= 0) return 0;
  return Math.min(1, Math.max(0, scroller.scrollTop / max)) * pageChars;
}

function intraPageRatio() {
  const scroller = document.querySelector(".content-area");
  if (!scroller) return 0;
  const max = scroller.scrollHeight - scroller.clientHeight;
  if (max <= 0) return 0;
  return Math.min(1, Math.max(0, scroller.scrollTop / max));
}

export function getProgressMetrics() {
  const pages = state.currentPages || [];
  const viewIndex = Math.max(0, Math.min(state.viewPageIndex || 0, Math.max(0, pages.length - 1)));
  const bookType = getBookType();

  if (bookType === "pdf") {
    const total = Math.max(1, pages.length);
    const current = pages.length ? viewIndex + 1 : 1;
    const ratio = (viewIndex + intraPageRatio()) / total;
    return {
      bookType,
      currentPage: current,
      totalPages: total,
      percent: total ? (current / total) * 100 : 0,
      hairline: Math.min(100, Math.max(0, ratio * 100)),
    };
  }

  const globalOffset = (index.cumulative[viewIndex] || 0) + intraPageChars(viewIndex);
  const totalChars = Math.max(1, index.totalChars);
  const clamped = Math.min(totalChars, Math.max(0, globalOffset));
  const totalPages = Math.max(1, Math.ceil(index.totalChars / CHARS_PER_PAGE) || 1);
  const currentPage = index.totalChars > 0 ? Math.min(totalPages, Math.floor(clamped / CHARS_PER_PAGE) + 1) : 1;
  const percent = index.totalChars > 0 ? (clamped / totalChars) * 100 : 0;
  return {
    bookType,
    currentPage,
    totalPages,
    percent,
    hairline: percent,
  };
}

export function updateProgressDisplay() {
  const metrics = getProgressMetrics();
  const fill = document.getElementById("topbarProgressFill");
  if (fill) fill.style.width = `${metrics.hairline.toFixed(2)}%`;

  const percentBtn = document.getElementById("progressPercentBtn");
  if (percentBtn) percentBtn.textContent = `${Math.round(metrics.percent)}%`;

  const pageInput = document.getElementById("pageInput");
  const pageTotal = document.getElementById("pageTotal");
  if (pageTotal) pageTotal.textContent = String(metrics.totalPages);

  if (pageInput) {
    pageInput.disabled = metrics.totalPages <= 1;
    pageInput.style.backgroundColor = "transparent";
    pageInput.style.border = "none";
    pageInput.style.outline = "none";
    pageInput.style.color = "#e4e4e7";

    if (document.activeElement !== pageInput) {
      pageInput.value = String(metrics.currentPage);
      pageInput.min = "1";
      pageInput.max = String(metrics.totalPages);
      pageInput.style.width = `${Math.max(2, String(metrics.currentPage).length + 1)}ch`;
    }
  }

  writeProgressToDoc();
}

function writeProgressToDoc() {
  if (!state.currentDoc) return;
  const metrics = getProgressMetrics();
  state.currentDoc.current_page = metrics.currentPage;
  state.currentDoc.total_pages = metrics.totalPages;
  state.currentDoc.progress_percent = Math.round(metrics.percent);
  document.dispatchEvent(new CustomEvent("lr-progress-updated"));
}

export function jumpToDisplayedPage(displayedPage) {
  const pages = state.currentPages || [];
  if (!pages.length) return false;
  const metrics = getProgressMetrics();
  const pageNum = Math.max(1, Math.min(metrics.totalPages, displayedPage));

  if (getBookType() === "pdf") {
    state.viewPageIndex = pageNum - 1;
    return true;
  }

  const targetChar = (pageNum - 1) * CHARS_PER_PAGE;
  let block = pages.length - 1;
  for (let i = 0; i < pages.length; i++) {
    const end = index.cumulative[i + 1] || 0;
    if (targetChar < end || i === pages.length - 1) {
      block = i;
      break;
    }
  }
  state.viewPageIndex = block;
  return true;
}

function scheduleScrollUpdate() {
  if (scrollRaf) return;
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = 0;
    updateProgressDisplay();
  });
}

export function initProgress() {
  const scroller = document.querySelector(".content-area");
  scroller?.addEventListener("scroll", scheduleScrollUpdate, { passive: true });
  window.addEventListener("jump-to-sentence", scheduleScrollUpdate);
  document.addEventListener("lr-progress-tick", updateProgressDisplay);
}
