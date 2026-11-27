// Startup release notice: "! v1.4" for 10 seconds, click opens the GitHub
// release. The backend (logic/update_notice.py) compares the latest release with
// APP_VERSION in app/config.py on each startup.
// Its home is the sidebar tray; while the sidebar hides (collapsed, e.g. after
// opening a book) it sits beside the topbar sidebar icon so it is still seen.
import { fetchJSON } from "./api.js";
import { isSidebarDocked, isSidebarPeeking } from "./sidebar.js";

const SHOW_MS = 10000;
const AFTER_HOVER_MS = 2000;
const FADE_MS = 400;

function openRelease(url) {
  const api = window.pywebview?.api;
  if (api?.open_external) api.open_external(url);
  else window.open(url, "_blank", "noopener");
}

export async function showUpdateNotice() {
  const chip = document.getElementById("updateNoticeBtn");
  const tray = chip?.parentElement;
  const sidebar = tray?.closest(".sidebar");
  if (!chip || !tray || !sidebar) return;
  let notice;
  try { ({ notice } = await fetchJSON("/api/system/update-notice")); } catch { return; }
  if (!notice) return;

  const place = () => {
    if (isSidebarDocked() || isSidebarPeeking()) {
      if (chip.parentElement !== tray) tray.prepend(chip);
    } else if (chip.previousElementSibling?.id !== "topbarSidebarToggle") {
      document.getElementById("topbarSidebarToggle")?.after(chip);
    }
  };
  const observer = new MutationObserver(place);
  let timer = 0;
  const hide = () => {
    clearTimeout(timer);
    observer.disconnect();
    chip.classList.add("leaving");
    setTimeout(() => chip.remove(), FADE_MS);
  };
  const hideAfter = ms => { clearTimeout(timer); timer = setTimeout(hide, ms); };

  document.getElementById("updateNoticeVersion").textContent = notice.version;
  chip.title = `New version ${notice.version} is out. Open the release page`;
  chip.addEventListener("click", () => { openRelease(notice.url); hide(); });
  chip.addEventListener("mouseenter", () => clearTimeout(timer));
  chip.addEventListener("mouseleave", () => hideAfter(AFTER_HOVER_MS));
  observer.observe(sidebar, { attributes: true, attributeFilter: ["class"] });
  place();
  chip.classList.remove("hidden");
  hideAfter(SHOW_MS);
}
