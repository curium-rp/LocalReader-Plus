// The library sidebar. Docked, it takes layout space; collapsed, it takes none.
// A collapsed sidebar can peek: hovering the topbar toggle floats it over the
// page, so the book's layout (spreads, margins) never moves. Clicking the
// toggle while it peeks docks it. Same behavior as FalcoType's Practice sidebar.
import { closeTypoMenu, applyReaderTypography } from "./typography.js";
import { requestGeometrySync } from "./reader-layout.js";
import { layoutSpreads, getSpreadIndex } from "./horizontal.js";

const PEEK_OPEN_MS = 150;   // hover intent, so passing over the icon does nothing
const PEEK_CLOSE_MS = 300;  // grace for the trip between the icon and the panel
const PEEK_OUT_MS = 240;    // fallback for the slide-out's animationend

// Where the pointer may be while the peek stays open: the panel, its toggle,
// and the boxes the panel opens outside itself.
const PEEK_ZONE = ".sidebar, #topbarSidebarToggle, #typoFloatMenu, #lrShelfStatusMenu, .sidebar-mini-popup, #kokoroDownloadPopup";
// While one of these is open, the peek waits for it instead of closing.
const PEEK_LOCKS = "#typoFloatMenu.open, #lrShelfStatusMenu:not(.hidden), .sidebar-mini-popup:not(.hidden), #kokoroDownloadPopup:not(.hidden)";
// While one of these is up, hover does not peek.
const PEEK_BLOCKERS = '.menubar-item.open, [id$="Modal"], .voice-settings-drawer.open';

let sidebar = null;
let animTimer = null;
let pendingSpreadIndex = null;
let afterToggle = () => {};

const root = () => document.documentElement;
const openWidth = () => getComputedStyle(root()).getPropertyValue("--sidebar-open-width").trim() || "320px";

export function isSidebarDocked() {
  return !!sidebar && !sidebar.classList.contains("collapsed");
}

export function isSidebarPeeking() {
  return !!sidebar?.classList.contains("peek");
}

export function closeSidebarMiniPopups() {
  document.getElementById("languagePopup")?.classList.add("hidden");
  document.getElementById("engineStatusPopup")?.classList.add("hidden");
  document.getElementById("kokoroDownloadPopup")?.classList.add("hidden");
}

export function toggleSidebarMiniPopup(popup) {
  if (!popup) return;
  const willOpen = popup.classList.contains("hidden");
  closeSidebarMiniPopups();
  if (willOpen) popup.classList.remove("hidden");
}

// A class change that must not run the width transition.
function withoutTransition(change) {
  sidebar.style.transition = "none";
  change();
  void sidebar.offsetWidth;
  sidebar.style.transition = "";
}

// The book re-lays out once the docked width settles, keeping its spread.
function finishAnim() {
  if (!sidebar.classList.contains("animating")) return;
  clearTimeout(animTimer);
  sidebar.classList.remove("animating");
  applyReaderTypography();
  const spreadIndex = pendingSpreadIndex;
  pendingSpreadIndex = null;
  void layoutSpreads({ spreadIndex });
}

// Docks (opens in the layout) or collapses the sidebar. A peeking sidebar
// docks in place, without the slide.
export function setSidebarDocked(docked) {
  if (!sidebar) return;
  if (docked && isSidebarPeeking()) { pinPeek(); return; }
  if (docked === isSidebarDocked()) return;
  endPeek(true);
  closeSidebarMiniPopups();
  pendingSpreadIndex = getSpreadIndex();
  sidebar.classList.add("animating");
  clearTimeout(animTimer);
  animTimer = setTimeout(finishAnim, 350);
  sidebar.classList.toggle("collapsed", !docked);
  sidebar.inert = !docked;
  root().style.setProperty("--sidebar-width", docked ? openWidth() : "0px");
  afterToggle();
  applyReaderTypography();
  requestGeometrySync();
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) finishAnim();
  if (!docked) closeTypoMenu();
}

export function toggleSidebar() {
  setSidebarDocked(!isSidebarDocked());
}

// ---- Peek -----------------------------------------------------------------

let openTimer = null;
let closeTimer = null;
let outTimer = null;
// After a click on the toggle, hover does nothing until the pointer leaves it.
let hoverSuppressed = false;

// Any match that is really up: laid out, visible, and taking the pointer
// (some closed modals stay laid out at opacity 0 with no pointer events).
function shown(selector) {
  return [...document.querySelectorAll(selector)].some(node => {
    if (!node.getClientRects().length) return false;
    const style = getComputedStyle(node);
    return style.visibility !== "hidden" && style.pointerEvents !== "none" && Number(style.opacity) > 0;
  });
}

function canPeek() {
  return !!sidebar
    && !isSidebarDocked()
    && !sidebar.classList.contains("animating")
    && !shown(PEEK_BLOCKERS);
}

const peekLocked = () => !!document.querySelector(PEEK_LOCKS);
const inPeekZone = target => !!target?.closest?.(PEEK_ZONE);

function cancelTimers() {
  clearTimeout(openTimer);
  clearTimeout(closeTimer);
  openTimer = closeTimer = null;
}

function startPeek() {
  openTimer = null;
  if (!canPeek() || isSidebarPeeking()) return;
  clearTimeout(outTimer);
  sidebar.classList.remove("peek-out");
  sidebar.classList.add("peek");
  sidebar.inert = false;
}

// Closes the peek: with the slide-out, or at once.
function endPeek(instant = false) {
  cancelTimers();
  if (!sidebar) return;
  if (!isSidebarPeeking()) {
    if (instant && sidebar.classList.contains("peek-out")) finishPeekOut();
    return;
  }
  closeSidebarMiniPopups();
  closeTypoMenu();
  sidebar.inert = true;
  // Focus left inside the panel would sit in an inert, hidden subtree.
  if (sidebar.contains(document.activeElement)) document.activeElement.blur();
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (instant || reduced) {
    withoutTransition(() => sidebar.classList.remove("peek", "peek-out"));
    return;
  }
  sidebar.classList.replace("peek", "peek-out");
  clearTimeout(outTimer);
  outTimer = setTimeout(finishPeekOut, PEEK_OUT_MS);
}

function finishPeekOut() {
  clearTimeout(outTimer);
  if (sidebar.classList.contains("peek-out")) withoutTransition(() => sidebar.classList.remove("peek-out"));
}

// The floating panel becomes the docked sidebar; the book re-lays out once.
function pinPeek() {
  cancelTimers();
  const spreadIndex = getSpreadIndex();
  withoutTransition(() => {
    sidebar.classList.remove("peek", "peek-out", "collapsed");
    root().style.setProperty("--sidebar-width", openWidth());
  });
  sidebar.inert = false;
  afterToggle();
  applyReaderTypography();
  requestGeometrySync();
  void layoutSpreads({ spreadIndex });
}

function scheduleClose() {
  if (closeTimer || !isSidebarPeeking()) return;
  closeTimer = setTimeout(() => {
    closeTimer = null;
    // A box opened from the panel keeps it; the next move outside retries.
    if (!peekLocked()) endPeek();
  }, PEEK_CLOSE_MS);
}

function wirePeek(toggle) {
  toggle.addEventListener("pointerenter", event => {
    if (event.pointerType !== "mouse" || hoverSuppressed || isSidebarPeeking()) return;
    clearTimeout(openTimer);
    if (canPeek()) openTimer = setTimeout(startPeek, PEEK_OPEN_MS);
  });
  toggle.addEventListener("pointerleave", () => {
    hoverSuppressed = false;
    clearTimeout(openTimer);
    openTimer = null;
  });
  document.addEventListener("pointermove", event => {
    if (!isSidebarPeeking() || event.pointerType !== "mouse") return;
    // A press that began inside (a drag, a scrollbar) keeps the panel.
    if (event.buttons) return;
    if (inPeekZone(event.target)) {
      clearTimeout(closeTimer);
      closeTimer = null;
    } else scheduleClose();
  }, { passive: true });
  root().addEventListener("pointerleave", () => scheduleClose());
  window.addEventListener("blur", () => endPeek(true));
  // Esc closes the peek before the reader hears it; keys aimed elsewhere close it too.
  window.addEventListener("keydown", event => {
    if (!isSidebarPeeking()) return;
    if (event.key === "Escape") {
      if (peekLocked()) return;
      event.preventDefault();
      event.stopPropagation();
      endPeek();
      return;
    }
    if (!inPeekZone(event.target)) endPeek();
  }, true);
  sidebar.addEventListener("animationend", event => {
    if (event.target === sidebar && sidebar.classList.contains("peek-out")) finishPeekOut();
  });
}

// The topbar toggle: docks a peeking panel, otherwise toggles.
export function clickSidebarToggle() {
  hoverSuppressed = true;
  if (isSidebarPeeking()) pinPeek();
  else toggleSidebar();
}

// Opening a book (auto mode) collapses a docked sidebar and closes a peek.
export function hideSidebarForBook() {
  if (isSidebarPeeking()) endPeek();
  else setSidebarDocked(false);
}

export function initSidebar({ onToggle } = {}) {
  sidebar = document.querySelector(".sidebar");
  if (!sidebar) return;
  if (onToggle) afterToggle = onToggle;
  root().style.setProperty("--sidebar-open-width", sidebar.classList.contains("collapsed") ? openWidth() : getComputedStyle(sidebar).width);
  sidebar.inert = sidebar.classList.contains("collapsed");
  sidebar.addEventListener("transitionend", event => {
    if (event.target !== sidebar || event.propertyName !== "width") return;
    applyReaderTypography();
    requestGeometrySync();
    finishAnim();
  });
  const collapseBtn = document.getElementById("sidebarCollapseBtn");
  if (collapseBtn) {
    // Inside a peeking panel this closes the peek; docked, it collapses.
    collapseBtn.onclick = () => {
      if (isSidebarPeeking()) endPeek();
      else toggleSidebar();
    };
  }
  const toggle = document.getElementById("topbarSidebarToggle");
  if (toggle) wirePeek(toggle);
}
