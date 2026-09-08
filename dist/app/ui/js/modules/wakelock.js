import { state } from "./state.js";
import { fetchJSON } from "./api.js";

let wakeLockSentinel = null;
let isRequesting = false;

const VALID_MODES = ["auto", "on", "off"];

function applyWakeLockUi() {
  document.querySelectorAll("[data-wake-lock-mode]").forEach((btn) => {
    const on = btn.dataset.wakeLockMode === state.wakeLockMode;
    btn.classList.toggle("text-zinc-200", on);
    btn.classList.toggle("bg-zinc-700", on);
    btn.classList.toggle("text-zinc-500", !on);
  });
}

export async function setWakeLockMode(mode) {
  if (!VALID_MODES.includes(mode)) return;
  state.wakeLockMode = mode;
  applyWakeLockUi();
  await updateWakeLock();

  try {
    await fetchJSON("/api/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wake_lock_mode: mode }),
    });
  } catch (err) {
    console.warn("[WakeLock] Failed to persist state:", err);
  }
}

export async function updateWakeLock() {
  if (!("wakeLock" in navigator)) return;

  const isVisible = document.visibilityState === "visible";
  const shouldLock =
    isVisible &&
    (state.wakeLockMode === "on" ||
      (state.wakeLockMode === "auto" && Boolean(state.isPlaying)));

  if (shouldLock) {
    if (!wakeLockSentinel && !isRequesting) {
      try {
        isRequesting = true;
        wakeLockSentinel = await navigator.wakeLock.request("screen");
        wakeLockSentinel.addEventListener("release", () => {
          wakeLockSentinel = null;
        });
      } catch (err) {
        // Can fail if low battery or permission denied
        console.warn("[WakeLock] Request failed:", err.name, err.message);
        wakeLockSentinel = null;
      } finally {
        isRequesting = false;
      }
    }
  } else {
    if (wakeLockSentinel) {
      try {
        await wakeLockSentinel.release();
      } catch (err) {
        console.warn("[WakeLock] Release error:", err);
      } finally {
        wakeLockSentinel = null;
      }
    }
  }
}

export async function initWakeLock() {
  try {
    const data = await fetchJSON("/api/state");
    if (data?.wake_lock_mode && VALID_MODES.includes(data.wake_lock_mode)) {
      state.wakeLockMode = data.wake_lock_mode;
    }
  } catch (err) {
    console.warn("[WakeLock] Could not load state, using default:", err);
  }

  applyWakeLockUi();

  document.querySelectorAll("[data-wake-lock-mode]").forEach((btn) => {
    btn.onclick = () => setWakeLockMode(btn.dataset.wakeLockMode);
  });

  document.addEventListener("visibilitychange", () => {
    updateWakeLock();
  });

  window.addEventListener("focus", () => {
    updateWakeLock();
  });

  window.addEventListener("blur", () => {
    updateWakeLock();
  });

  // Evaluate initial state
  await updateWakeLock();
}
