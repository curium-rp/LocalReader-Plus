import { state } from './state.js';
import { toggleSettingsDrawer, showToast, renderIcons } from './ui.js';
import { stopPlayback, saveProgress } from './tts.js';

let timerInterval = null;
let timerEndTime = null;
let timerTotalSec = 0;
let timerAction = 'stop'; // 'stop' | 'close'

export function initTimer() {
    console.log("[TIMER] Initializing sleep timer...");

    const getEl = (id) => document.getElementById(id);

    // Core Elements
    const btn = getEl("timerSettingsBtn");
    const closeBtn = getEl("closeTimerDrawerBtn");

    // Controls
    const hoursInput = getEl("timerHours");
    const minutesInput = getEl("timerMinutes");
    const startBtn = getEl("startTimerBtn");
    const stopBtn = getEl("stopTimerBtn");
    const statusText = getEl("timerStatusText");
    const countdownDisplay = getEl("timerCountdown");
    const actionStopBtn = getEl("timerActionStop");
    const actionCloseBtn = getEl("timerActionClose");

    // Button Display
    const btnIcon = btn?.querySelector("i");
    const btnText = getEl("timerBtnText");

    btn?.addEventListener("click", () => {
        toggleSettingsDrawer("timerSettingsDrawer", true);
    });

    closeBtn?.addEventListener("click", () => toggleSettingsDrawer("timerSettingsDrawer", false));

    // Action toggle handling
    function setTimerAction(action) {
        timerAction = action;
        if (action === 'stop') {
            actionStopBtn?.classList.add("bg-blue-600", "text-white", "shadow");
            actionStopBtn?.classList.remove("text-zinc-400");
            actionCloseBtn?.classList.remove("bg-blue-600", "text-white", "shadow");
            actionCloseBtn?.classList.add("text-zinc-400");
        } else {
            actionCloseBtn?.classList.add("bg-blue-600", "text-white", "shadow");
            actionCloseBtn?.classList.remove("text-zinc-400");
            actionStopBtn?.classList.remove("bg-blue-600", "text-white", "shadow");
            actionStopBtn?.classList.add("text-zinc-400");
        }
    }

    actionStopBtn?.addEventListener("click", () => setTimerAction('stop'));
    actionCloseBtn?.addEventListener("click", () => setTimerAction('close'));

    function formatTime(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        return h > 0 ? `${h}h ${m}m` : `${m}m`;
    }

    function formatButtonTime(seconds) {
        const h = Math.floor(seconds / 3600);
        if (h > 0) return `${h}h`;
        const m = Math.ceil(seconds / 60);
        return `${Math.max(1, m)}m`;
    }

    function formatTimeFull(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
    }

    function updateUI(active, remainingSeconds = 0) {
        if (active) {
            stopBtn?.classList.remove("hidden");
            startBtn?.classList.add("hidden");

            if (statusText) {
                const actionLabel = timerAction === 'close' ? " · Close App" : " · Stop Reading";
                statusText.textContent = (state.translations?.timer?.running || "Timer Running") + actionLabel;
                statusText.className = "text-green-400 font-bold text-sm mb-2";
            }

            if (countdownDisplay) {
                countdownDisplay.textContent = formatTimeFull(remainingSeconds);
            }

            // Keep the exact same circular button shape (no stretching/resizing)
            if (btn) {
                btn.classList.add("active");
            }

            // Replace the timer symbol with the short countdown text (e.g. 1h or 30m)
            if (btnIcon) btnIcon.style.display = "none";

            if (btnText) {
                btnText.style.display = "inline-flex";
                btnText.style.alignItems = "center";
                btnText.style.justifyContent = "center";
                btnText.textContent = formatButtonTime(remainingSeconds);
                btnText.className = "text-[11px] font-bold font-mono text-zinc-100 leading-none select-none";
            }

            if (hoursInput) hoursInput.disabled = true;
            if (minutesInput) minutesInput.disabled = true;
            if (actionStopBtn) actionStopBtn.disabled = true;
            if (actionCloseBtn) actionCloseBtn.disabled = true;
        } else {
            stopBtn?.classList.add("hidden");
            startBtn?.classList.remove("hidden");

            if (statusText) {
                statusText.textContent = state.translations?.timer?.inactive || "Timer Inactive";
                statusText.className = "text-zinc-500 font-bold text-sm mb-2";
            }

            if (countdownDisplay) {
                countdownDisplay.textContent = "--:--:--";
            }

            // Restore idle circular state
            if (btn) {
                btn.classList.remove("active");
            }

            // Restore the timer symbol and hide the text
            if (btnIcon) btnIcon.style.display = "block";
            if (btnText) {
                btnText.style.display = "none";
                btnText.textContent = "";
            }

            if (hoursInput) hoursInput.disabled = false;
            if (minutesInput) minutesInput.disabled = false;
            if (actionStopBtn) actionStopBtn.disabled = false;
            if (actionCloseBtn) actionCloseBtn.disabled = false;
        }
    }

    async function onTimerExpired() {
        if (timerInterval) {
            clearInterval(timerInterval);
            timerInterval = null;
        }
        timerEndTime = null;
        updateUI(false);

        // Smoothly stop playback and save reading progress bookmark
        try {
            stopPlayback();
            saveProgress();
        } catch (e) {
            console.error("[TIMER] Error during playback stop/save", e);
        }

        if (timerAction === 'close') {
            showToast("Sleep timer finished: Closing app...");
            setTimeout(() => {
                try {
                    if (window.pywebview?.api?.close) {
                        window.pywebview.api.close();
                    } else {
                        window.close();
                    }
                } catch (err) {
                    console.error("[TIMER] Window close failed:", err);
                }
            }, 800);
        } else {
            showToast("Sleep timer finished: Playback paused.");
        }
    }

    function startTimer() {
        const hours = parseInt(hoursInput?.value) || 0;
        const minutes = parseInt(minutesInput?.value) || 0;
        const totalMinutes = (hours * 60) + minutes;

        if (totalMinutes <= 0) {
            alert("Please set a time greater than 1 minute.");
            return;
        }

        timerTotalSec = totalMinutes * 60;
        timerEndTime = Date.now() + (timerTotalSec * 1000);

        if (timerInterval) clearInterval(timerInterval);

        updateUI(true, timerTotalSec);

        timerInterval = setInterval(() => {
            const now = Date.now();
            const remaining = Math.max(0, Math.ceil((timerEndTime - now) / 1000));

            if (remaining <= 0) {
                onTimerExpired();
            } else {
                updateUI(true, remaining);
            }
        }, 1000);

        showToast(`Sleep timer started: ${formatTime(timerTotalSec)}`);
    }

    function stopTimer() {
        if (timerInterval) {
            clearInterval(timerInterval);
            timerInterval = null;
        }
        timerEndTime = null;
        updateUI(false);
        showToast("Sleep timer stopped.");
    }

    startBtn?.addEventListener("click", startTimer);
    stopBtn?.addEventListener("click", stopTimer);

    renderIcons();
}