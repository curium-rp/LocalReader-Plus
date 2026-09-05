/**
 * Keyboard shortcuts and hardware media-key bindings.
 *
 * Explicit dependencies (imported, original names):
 *   state              from ./modules/state.js
 *   togglePlayback     from ./modules/tts.js
 *   isHorizontalMode   from ./modules/horizontal.js
 *   flipSpread         from ./modules/horizontal.js
 *
 * Explicit dependencies (received via initShortcuts, original names):
 *   closeSearchMode
 *   handleSearchPopupKeys
 *   closeSidebarMiniPopups
 */
import { state } from "./modules/state.js";
import { togglePlayback } from "./modules/tts.js";
import { isHorizontalMode, flipSpread } from "./modules/horizontal.js";
import { closeAllDrawers } from "./modules/ui.js";

export function initShortcuts({ closeSearchMode, handleSearchPopupKeys, closeSidebarMiniPopups }) {
window.addEventListener("keydown", (e) => {
  if (handleSearchPopupKeys(e)) return;

  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable) return;
  
  if (e.code === "Space") {
    e.preventDefault();
    togglePlayback();
  } else if (e.code === "ArrowLeft") {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      document.getElementById("prevPage")?.click();
    } else if (isHorizontalMode()) {
      flipSpread(-1);
    } else {
      document.getElementById("skipBack")?.click();
    }
  } else if (e.code === "ArrowRight") {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      document.getElementById("nextPage")?.click();
    } else if (isHorizontalMode()) {
      flipSpread(1);
    } else {
      document.getElementById("skipForward")?.click();
    }
  } else if (e.code === "PageUp") {
    e.preventDefault();
    document.getElementById("prevPage")?.click();
  } else if (e.code === "PageDown") {
    e.preventDefault();
    document.getElementById("nextPage")?.click();
  } else if ((e.ctrlKey || e.metaKey) && e.key === "f" && state.currentDoc) {
    e.preventDefault();
    document.getElementById("searchBtn").click();
  } else if (e.key === "Escape") {
    e.preventDefault();
    closeSearchMode();
    const tocModal = document.getElementById("tocModal");
    if (tocModal) tocModal.classList.add("hidden");
    closeAllDrawers();
    
    const imgViewer = document.getElementById("imageViewerModal");
    if (imgViewer) imgViewer.classList.remove("active");

    const exportModal = document.getElementById("exportModal");
    if (exportModal && !exportModal.classList.contains("hidden")) {
      const isComplete = !document.getElementById("exportComplete")?.classList.contains("hidden");
      const isError = !document.getElementById("exportError")?.classList.contains("hidden");
      if (isComplete || isError) {
        exportModal.classList.add("hidden");
        const playBtn = document.getElementById("playBtn");
        if (playBtn) playBtn.disabled = false;
      }
    }
  }
});

// Bind hardware media keys directly to the OS Media Session
if ('mediaSession' in navigator) {
  navigator.mediaSession.setActionHandler('play', () => {
    togglePlayback();
  });
  
  navigator.mediaSession.setActionHandler('pause', () => {
    togglePlayback();
  });
  
  navigator.mediaSession.setActionHandler('previoustrack', () => {
    const skipBackBtn = document.getElementById("skipBack");
    if (skipBackBtn) skipBackBtn.click();
  });
  
  navigator.mediaSession.setActionHandler('nexttrack', () => {
    const skipForwardBtn = document.getElementById("skipForward");
    if (skipForwardBtn) skipForwardBtn.click();
  });
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSidebarMiniPopups();
});
}
