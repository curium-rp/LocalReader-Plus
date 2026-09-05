/**
 * resize.js — Window resize border handles
 *
 * Architecture:
 *   - #windowResizeLayer: pointer-events:none overlay covering the full window
 *   - .resize-handle children: pointer-events:auto hit-targets at each edge/corner
 *
 * Fix summary (capture-leak & stuck-state):
 *   1. setPointerCapture on the handle so events route correctly even if the
 *      pointer leaves the element before the native OS loop takes over.
 *   2. releasePointerCapture + document cursor reset on pointerup / pointercancel
 *      (covers out-of-window release).
 *   3. window "blur" safety reset so Alt+Tab or click-away never leaves a
 *      stuck cursor.
 *   4. Double-click guard: second pointerdown within 300 ms is silently dropped.
 *   5. Duplicate mousedown handler removed — pointerdown supersedes it;
 *      a single passive mousedown preventDefault keeps drag-select off.
 *   6. Global pointerup on `window` (not just the handle) ensures we always
 *      catch the release even if it fires outside the element.
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

// ── state ──────────────────────────────────────────────────────────────────
let _activeHandle   = null;  // The DOM element that captured the pointer
let _activePointerId = null; // pointerId currently captured
let _lastDownTime   = 0;     // epoch ms of last accepted pointerdown (double-click guard)
const DBLCLICK_MS  = 300;    // ignore second pointerdown within this window

// ── helpers ────────────────────────────────────────────────────────────────

/**
 * Reset all JS-side resize state and restore default cursor.
 * Safe to call multiple times (idempotent).
 */
function _resetState() {
  if (_activeHandle && _activePointerId != null) {
    try {
      _activeHandle.releasePointerCapture(_activePointerId);
    } catch {
      /* already released by browser or OS resize loop */
    }
  }
  _activeHandle    = null;
  _activePointerId = null;
  document.body.style.cursor = "";
}

/**
 * Map a resize direction to its CSS cursor value.
 */
function _cursorFor(direction) {
  switch (direction) {
    case "top":
    case "bottom":         return "ns-resize";
    case "left":
    case "right":          return "ew-resize";
    case "topleft":
    case "bottomright":    return "nwse-resize";
    case "topright":
    case "bottomleft":     return "nesw-resize";
    default:               return "default";
  }
}

// ── event handlers ─────────────────────────────────────────────────────────

function _onPointerDown(direction, el, e) {
  // Only primary button (left click)
  if (e.button !== 0) return;

  // Double-click guard: drop second pointerdown within DBLCLICK_MS ms.
  // This prevents double-clicking the top edge from sending two WM_NCLBUTTONDOWN
  // calls and freezing the resize loop.
  const now = Date.now();
  if (now - _lastDownTime < DBLCLICK_MS) {
    e.preventDefault();
    e.stopPropagation();
    return;
  }
  _lastDownTime = now;

  // Bail if maximized or fullscreen (server-side guard exists too)
  if (document.documentElement.dataset.maximized === "true") return;
  if (document.documentElement.dataset.fullscreen === "true") return;

  const api = window.pywebview?.api;
  if (!api?.start_native_resize) return;

  e.preventDefault();
  e.stopPropagation();

  // Capture the pointer on this element so pointerup fires here even if the
  // user releases the mouse outside the browser window.
  _activeHandle    = el;
  _activePointerId = e.pointerId;
  try {
    el.setPointerCapture(e.pointerId);
  } catch {
    /* some environments don't support pointer capture */
  }

  // Reflect cursor on body so it stays correct while OS drag loop runs
  document.body.style.cursor = _cursorFor(direction);

  // Hand off to the native resize loop
  const screenX = Math.round(e.screenX || 0);
  const screenY = Math.round(e.screenY || 0);
  api.start_native_resize(direction, screenX, screenY);
}

function _onPointerUp(e) {
  if (!_activeHandle) return;
  _resetState();
}

function _onPointerCancel(e) {
  // Fires when the browser steals the pointer (e.g. scroll, touch interrupt)
  _resetState();
}

// ── global safety nets ─────────────────────────────────────────────────────

/**
 * window "blur": user Alt+Tabbed, clicked another app, or the OS stole focus.
 * The native resize loop has already ended on the Windows side, but our JS
 * cursor / capture state might not have cleaned up yet.
 */
function _onWindowBlur() {
  if (_activeHandle) {
    _resetState();
  }
}

/**
 * Global pointerup on `window`: catches releases that happened outside the
 * captured element (e.g. if capture was not supported).
 */
function _onWindowPointerUp(e) {
  if (!_activeHandle) return;
  if (_activePointerId == null || e.pointerId === _activePointerId) {
    _resetState();
  }
}

// ── init ───────────────────────────────────────────────────────────────────

export function initResizeBorders() {
  if (document.getElementById("windowResizeLayer")) return;

  const layer = document.createElement("div");
  layer.id = "windowResizeLayer";
  layer.setAttribute("aria-hidden", "true");

  EDGES.forEach(([direction, modifier]) => {
    const edge = document.createElement("div");
    edge.className = `resize-handle resize-handle-${modifier}`;
    edge.dataset.edge = direction;

    // Primary handler: pointerdown with capture
    edge.addEventListener("pointerdown", (e) => _onPointerDown(direction, edge, e));

    // pointerup / pointercancel on the element (fires if capture is active)
    edge.addEventListener("pointerup",     _onPointerUp);
    edge.addEventListener("pointercancel", _onPointerCancel);

    // Prevent browser drag-selection on mousedown; do NOT stopPropagation here
    // so the window-level listeners still fire.
    edge.addEventListener("mousedown", (e) => { e.preventDefault(); });

    layer.appendChild(edge);
  });

  document.body.appendChild(layer);

  // Global safety nets — attached once, not per-handle
  window.addEventListener("pointerup",    _onWindowPointerUp);
  window.addEventListener("pointercancel", _onWindowBlur);
  window.addEventListener("blur",          _onWindowBlur);
}