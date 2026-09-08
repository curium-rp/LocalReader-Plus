import { twoPageFits } from "./horizontal.js";

const M_SAFE = 16;
const W_MIN = 320;
const W_DRAWER = 320;
const W_RAIL = 0;

let resizeTimer = null;
let hooksBound = false;
let renderSource = null;

export function bindRenderState(getter) {
  renderSource = typeof getter === "function" ? getter : null;
}

function clampPct(value, fallback = 8) {
  const n = parseInt(value, 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(0, Math.min(35, n));
}

export function syncReaderGeometry() {
  const textContainer = document.querySelector(".chapter-text") || document.getElementById("textContent");
  if (!textContainer) return;

  const render = renderSource ? renderSource() : null;
  if (render?.two_page_landscape && twoPageFits()) return;

  const sidebar = document.querySelector(".sidebar");
  const isCollapsed = !sidebar || sidebar.classList.contains("collapsed");
  const W_win = window.innerWidth;
  const W_avail = isCollapsed ? W_win : Math.max(0, W_win - W_DRAWER);

  const pct_L = isCollapsed
    ? clampPct(render?.margin_left, 8)
    : clampPct(render?.margin_left_open, 5);
  const pct_R = isCollapsed
    ? clampPct(render?.margin_right, 8)
    : clampPct(render?.margin_right_open, 5);

  let M_active_L = 0;
  let M_active_R = 0;
  let W_content = W_avail;

  if (pct_L === 0 && pct_R === 0) {
    M_active_L = 0;
    M_active_R = 0;
    W_content = W_avail;
  } else {
    let M_L = Math.round((pct_L / 100) * W_avail);
    let M_R = Math.round((pct_R / 100) * W_avail);
    const maxMargin = Math.max(0, W_avail - (W_win < W_MIN ? 1 : W_MIN));
    if (M_L + M_R > maxMargin && (M_L + M_R) > 0) {
      const scale = maxMargin / (M_L + M_R);
      M_L = Math.floor(M_L * scale);
      M_R = Math.floor(M_R * scale);
    }
    M_active_L = M_L;
    M_active_R = M_R;
    W_content = Math.max(0, W_avail - M_active_L - M_active_R);
  }

  if (W_content + M_active_L + M_active_R > W_avail) {
    W_content = Math.max(0, W_avail - M_active_L - M_active_R);
  }

  const root = document.documentElement;
  root.style.setProperty("--reader-content-width", `${Math.max(0, Math.round(W_content))}px`);
  root.style.setProperty("--reader-lr-margin", `${M_active_L}px`);
  root.style.setProperty("--reader-margin-left", `${M_active_L}px`);
  root.style.setProperty("--reader-margin-right", `${M_active_R}px`);
}

export function requestGeometrySync() {
  if (resizeTimer) cancelAnimationFrame(resizeTimer);
  resizeTimer = requestAnimationFrame(() => {
    resizeTimer = null;
    syncReaderGeometry();
  });
}

export function initLayoutHooks() {
  if (hooksBound) return;
  hooksBound = true;
  window.addEventListener("resize", requestGeometrySync);
  syncReaderGeometry();
}
