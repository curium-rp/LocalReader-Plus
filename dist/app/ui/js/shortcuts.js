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
import { closeModelManagerModal } from "./modules/downloader.js";

export function initShortcuts({ closeSearchMode, handleSearchPopupKeys, closeSidebarMiniPopups }) {
  // Suppress default webview mouse back (button 3) and forward (button 4) navigation
  const suppressMouseNav = (e) => {
    if (e.button === 3 || e.button === 4) {
      e.preventDefault();
      e.stopPropagation();
    }
  };
  window.addEventListener("pointerdown", suppressMouseNav, true);
  window.addEventListener("pointerup", suppressMouseNav, true);
  window.addEventListener("mousedown", suppressMouseNav, true);
  window.addEventListener("mouseup", suppressMouseNav, true);
  window.addEventListener("click", suppressMouseNav, true);
  window.addEventListener("auxclick", suppressMouseNav, true);

  // Suppress browser Ctrl + mousewheel zoom (preserve native font scaling instead)
  window.addEventListener("wheel", (e) => {
    if (e.ctrlKey) {
      e.preventDefault();
    }
  }, { passive: false });

  window.addEventListener("keydown", (e) => {
    if (handleSearchPopupKeys(e)) return;

    // Suppress browser history back / forward shortcut keys
    if (
      e.key === "BrowserBack" ||
      e.key === "BrowserForward" ||
      (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight"))
    ) {
      e.preventDefault();
      return;
    }

    // Suppress browser default actions:
    // Ctrl+P: Print dialog
    // Ctrl+S: Save webpage as HTML
    // Ctrl+O: Browser open file dialog
    // Ctrl+U: View HTML source
    // Ctrl+G: Browser find next match
    // Ctrl+T: New browser tab
    // Ctrl+N: New browser window
    // Ctrl+D: Add browser bookmark
    // Ctrl+J: Browser downloads panel
    const blockedCtrlKeys = ["p", "s", "o", "u", "g", "t", "n", "d", "j"];
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && blockedCtrlKeys.includes(e.key.toLowerCase())) {
      e.preventDefault();
      return;
    }

    // Suppress browser viewport zoom (Ctrl +, Ctrl -, Ctrl 0) - app uses native typography controls
    if ((e.ctrlKey || e.metaKey) && ["+", "-", "=", "_", "0"].includes(e.key)) {
      e.preventDefault();
      return;
    }

    // Suppress unwanted browser/system function keys:
    // F1: Windows / Edge help webpage
    // F3: Browser search next
    // F7: "Turn on Caret Browsing?" modal
    if (["F1", "F3", "F7"].includes(e.key)) {
      e.preventDefault();
      return;
    }

    // Suppress Shift+F3 (Browser search prev) and Alt+Home (Browser homepage)
    if ((e.shiftKey && e.key === "F3") || (e.altKey && e.key === "Home")) {
      e.preventDefault();
      return;
    }

    // Ctrl+Shift+F: Toggle Book Explorer
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "f") {
      e.preventDefault();
      const feModal = document.getElementById("filesExplorerModal");
      if (feModal && !feModal.classList.contains("hidden")) {
        if (typeof window.closeFilesUI === "function") window.closeFilesUI();
      } else {
        if (typeof window.openFilesExplorer === "function") window.openFilesExplorer();
      }
      return;
    }

    // Ctrl+Shift+H: Toggle History
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "h") {
      e.preventDefault();
      const histModal = document.getElementById("historyModal");
      if (histModal && !histModal.classList.contains("hidden")) {
        if (typeof window.closeHistoryUI === "function") window.closeHistoryUI();
      } else {
        if (typeof window.openHistoryUI === "function") window.openHistoryUI();
      }
      return;
    }

    // Ctrl+F / Cmd+F routing: Book Explorer -> History -> Reader Search
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "f") {
      const feModal = document.getElementById("filesExplorerModal");
      if (feModal && !feModal.classList.contains("hidden")) {
        e.preventDefault();
        const feInput = document.getElementById("feSearchInput");
        if (feInput) {
          feInput.focus();
          feInput.select();
        }
        return;
      }

      const histModal = document.getElementById("historyModal");
      if (histModal && !histModal.classList.contains("hidden")) {
        e.preventDefault();
        const histInput = document.getElementById("historySearchInput");
        if (histInput) {
          histInput.focus();
          histInput.select();
        }
        return;
      }

      if (state.currentDoc) {
        e.preventDefault();
        document.getElementById("searchBtn")?.click();
        return;
      }
    }

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
  if (e.key === "Escape") {
    closeSidebarMiniPopups();
    closeModelManagerModal();
  }
});
}
