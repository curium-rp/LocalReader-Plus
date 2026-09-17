/**
 * resize.js — Window resize border handles
 *
 * Architecture:
 *   - #windowResizeLayer: pointer-events:none overlay covering the full window
 *   - .resize-handle children: pointer-events:auto hit-targets at each edge/corner
 *
 * Native resize is handed to Win32 (WM_NCLBUTTONDOWN) / GTK / Cocoa immediately.
 * The OS modal sizing loop consumes mouse-up, so Chromium often never sees
 * pointerup. Do not setPointerCapture or override document.body.style.cursor —
 * those leak across the swallowed release and leave a stuck resize cursor
 * until the next click. Hover cursors come from CSS; the drag cursor comes
 * from the OS.
 */

const EDGES = [
  ["left",        "left"],
  ["right",       "right"],
  ["top",         "top"],
  ["bottom",      "bottom"],
  ["topleft",     "top-left"],
  ["topright",    "top-right"],
  ["bottomleft",  "bottom-left"],
  ["bottomright", "bottom-right"],
];

let _lastDownTime = 0;
const DBLCLICK_MS = 300;

/**
 * Clear any leftover JS cursor / capture from an older resize path.
 * Safe to call multiple times (idempotent).
 */
function _resetState() {
  try {
    if (document.body.style.cursor) {
      document.body.style.cursor = "";
    }
  } catch {
    /* document may be tearing down */
  }
}

function _onPointerDown(direction, e) {
  if (e.button !== 0) return;

  // Drop a second pointerdown within DBLCLICK_MS so a double-click on the
  // top edge cannot queue two native sizing loops.
  const now = Date.now();
  if (now - _lastDownTime < DBLCLICK_MS) {
    e.preventDefault();
    e.stopPropagation();
    return;
  }
  _lastDownTime = now;

  if (document.documentElement.dataset.maximized === "true") return;
  if (document.documentElement.dataset.fullscreen === "true") return;

  const api = window.pywebview?.api;
  if (!api?.start_native_resize) return;

  e.preventDefault();
  e.stopPropagation();

  _resetState();

  const screenX = Math.round(e.screenX || 0);
  const screenY = Math.round(e.screenY || 0);
  try {
    const result = api.start_native_resize(direction, screenX, screenY);
    if (result && typeof result.then === "function") {
      result.finally(() => _resetState());
    }
  } catch {
    _resetState();
  }
}

/**
 * After the OS modal loop exits, Chromium may still think a button is down
 * until the next click. Any move with no buttons held means the resize is
 * over — wipe leftover cursor overrides immediately.
 */
function _onWindowPointerMove(e) {
  if (e.buttons !== 0) return;
  if (!document.body.style.cursor) return;
  _resetState();
}

export function initResizeBorders() {
  if (document.getElementById("windowResizeLayer")) return;

  const layer = document.createElement("div");
  layer.id = "windowResizeLayer";
  layer.setAttribute("aria-hidden", "true");

  EDGES.forEach(([direction, modifier]) => {
    const edge = document.createElement("div");
    edge.className = `resize-handle resize-handle-${modifier}`;
    edge.dataset.edge = direction;

    edge.addEventListener("pointerdown", (e) => _onPointerDown(direction, e));
    // Keep default drag-select off; do not stopPropagation.
    edge.addEventListener("mousedown", (e) => { e.preventDefault(); });

    layer.appendChild(edge);
  });

  document.body.appendChild(layer);

  window.addEventListener("pointermove", _onWindowPointerMove);
  window.addEventListener("pointerup", _resetState);
  window.addEventListener("pointercancel", _resetState);
  window.addEventListener("blur", _resetState);

  const stateObserver = new MutationObserver(() => {
    if (
      document.documentElement.dataset.fullscreen === "true" ||
      document.documentElement.dataset.maximized === "true"
    ) {
      _resetState();
    }
  });
  stateObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-fullscreen", "data-maximized"],
  });
}
