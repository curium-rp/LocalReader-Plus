import { state } from './state.js';

export function initTimer() {
    console.log("[TIMER] Initializing sleep timer...");

    // Helper function to safely grab elements
    const getEl = (id) => document.getElementById(id);

    // Core Elements
    const btn = getEl("timerSettingsBtn");
    const drawer = getEl("timerSettingsDrawer");
    const closeBtn = getEl("closeTimerDrawerBtn");
    const overlay = getEl("drawerOverlay");

    // Controls
    const hoursInput = getEl("timerHours");
    const minutesInput = getEl("timerMinutes");
    const startBtn = getEl("startTimerBtn");
    const stopBtn = getEl("stopTimerBtn");
    const statusText = getEl("timerStatusText");
    const countdownDisplay = getEl("timerCountdown");

    // Button Display
    const btnIcon = btn?.querySelector("i");
    const btnText = getEl("timerBtnText");

    let statusInterval = null;

    function toggleDrawer(show) {
        if (show) {
            drawer?.classList.add("open");
            overlay?.classList.add("active");
        } else {
            drawer?.classList.remove("open");
            overlay?.classList.remove("active");
        }
    }

    btn?.addEventListener("click", () => {
        toggleDrawer(true);
        fetchStatus();
    });

    closeBtn?.addEventListener("click", () => toggleDrawer(false));
    overlay?.addEventListener("click", () => toggleDrawer(false));

    async function startTimer() {
        const hours = parseInt(hoursInput?.value) || 0;
        const minutes = parseInt(minutesInput?.value) || 0;
        const totalMinutes = (hours * 60) + minutes;

        if (totalMinutes <= 0) {
            alert("Please set a time greater than 1 minute.");
            return;
        }

        try {
            const res = await fetch("/api/timer/set", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ minutes: totalMinutes }),
            });
            const data = await res.json();
            updateUI(data);
        } catch (e) {
            console.error("[TIMER] Failed to set timer", e);
        }
    }

    async function stopTimer() {
        try {
            const res = await fetch("/api/timer/stop", { method: "POST" });
            const data = await res.json();
            updateUI(data);
        } catch (e) {
            console.error("[TIMER] Failed to stop timer", e);
        }
    }

    async function fetchStatus() {
        try {
            const res = await fetch("/api/timer/status");
            if (!res.ok) {
                throw new Error(`HTTP error! status: ${res.status}`);
            }
            const data = await res.json();
            updateUI(data);
        } catch (e) {
            console.error("[TIMER] Failed to fetch status", e);
        }
    }

    function formatTime(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        return h > 0 ? `${h}h ${m}m` : `${m}m`;
    }

    function formatTimeFull(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
    }

    function updateUI(data) {
        if (!data) return;

        if (data.active) {
            // Drawer UI Updates
            stopBtn?.classList.remove("hidden");
            startBtn?.classList.add("hidden");
            
            if (statusText) {
                statusText.textContent = state.translations?.timer?.running || "Timer Running";
                statusText.className = "text-green-400 font-bold text-sm mb-2";
            }
            
            if (countdownDisplay) {
                countdownDisplay.textContent = formatTimeFull(data.remaining_seconds);
            }

            // Button UI Updates
            if (btn) {
                btn.classList.add("active"); 
                btn.style.background = "#27272a"; 
                btn.style.width = "auto";
                btn.style.padding = "0 12px";
                btn.style.borderRadius = "24px";
                btn.style.borderColor = "#3f3f46";
            }
            
            if (btnIcon) {
                btnIcon.style.display = "none";
            }
            
            if (btnText) {
                btnText.style.display = "block";
                btnText.textContent = formatTime(data.remaining_seconds);
                btnText.className = "text-xs font-bold font-mono text-zinc-300";
            }

            // Lock Inputs
            if (hoursInput) hoursInput.disabled = true;
            if (minutesInput) minutesInput.disabled = true;
            
        } else {
            // Drawer UI Updates
            stopBtn?.classList.add("hidden");
            startBtn?.classList.remove("hidden");
            
            if (statusText) {
                statusText.textContent = state.translations?.timer?.inactive || "Timer Inactive";
                statusText.className = "text-zinc-500 font-bold text-sm mb-2";
            }
            
            if (countdownDisplay) {
                countdownDisplay.textContent = "--:--:--";
            }

            // Button UI Updates
            if (btn) {
                btn.classList.remove("active");
                btn.style.background = ""; 
                btn.style.width = "";
                btn.style.padding = "";
                btn.style.borderRadius = "";
                btn.style.borderColor = "";
            }

            if (btnIcon) {
                btnIcon.style.display = "block";
            }
            
            if (btnText) {
                btnText.style.display = "none";
            }

            // Unlock Inputs
            if (hoursInput) hoursInput.disabled = false;
            if (minutesInput) minutesInput.disabled = false;
        }
    }

    // Bind Actions safely
    startBtn?.addEventListener("click", startTimer);
    stopBtn?.addEventListener("click", stopTimer);

    // Initial check
    fetchStatus();

    // Prevent duplicate intervals if initTimer is called multiple times
    if (statusInterval) {
        clearInterval(statusInterval);
    }
    
    statusInterval = setInterval(fetchStatus, 1000);
}