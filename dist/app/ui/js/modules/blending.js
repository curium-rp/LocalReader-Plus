import { fetchJSON } from "./api.js";
import { renderIcons, toggleSettingsDrawer } from "./ui.js";
import { state } from "./state.js";

// ── Constants ─────────────────────────────────────────────────────────────────

const MAX_SLOTS               = 10;
const WEIGHT_MAX              = 2.50;
const WEIGHT_STEP_BTN         = 0.10;
const WEIGHT_STEP_HOLD        = 0.05;
const WEIGHT_STEP_WHEEL       = 0.01;
const WEIGHT_MIN              = 0.00;
const HOLD_INITIAL_DELAY_MS   = 300;
const HOLD_REPEAT_INTERVAL_MS = 120;
const API_BLEND_LOAD   = "/api/blending/load";
const API_BLEND_SAVE   = "/api/blending/save";
const API_BLEND_PROFILES = "/api/blending/profiles";
const API_BLEND_APPLY  = "/api/blending/apply";

// ── Module state ──────────────────────────────────────────────────────────────

let blendEnabled   = false;
let activeProfile  = "preset_bella_sarah"; // active profile key, defaults to first preset
let availableVoices = []; // flat list of {id, name, lang}
let profileList    = []; // ["preset_bella_sarah", "preset_heart_bella", ...]
let profileLabels  = {}; // {key: displayLabel}
let profilePresets = {}; // {key: is_preset}
let profileSlotsMap = {}; // {key: slots}
let selectedMgrKey = "preset_bella_sarah";

const defaultSlots = () =>
  Array.from({ length: MAX_SLOTS }, (_, i) => ({
    active: i === 0,
    voice: "",
    weight: i === 0 ? 1.00 : 0.00,
  }));

let slots = defaultSlots();

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n) {
  return parseFloat(n).toFixed(2);
}

function clampWeight(v) {
  return Math.max(WEIGHT_MIN, Math.round(v * 100) / 100);
}

function totalWeight() {
  return slots
    .filter((s) => s.active)
    .reduce((sum, s) => sum + s.weight, 0);
}

function getModal() {
  return document.getElementById("blendingModal");
}

export const VOICE_BENCHMARKS = {
  // American English
  af_heart:      { grade: "A",  dur: "HH" },
  af_alloy:      { grade: "C",  dur: "MM" },
  af_aoede:      { grade: "C+", dur: "H" },
  af_bella:      { grade: "A-", dur: "HH" },
  af_jessica:    { grade: "D",  dur: "MM" },
  af_kore:       { grade: "C+", dur: "H" },
  af_nicole:     { grade: "B-", dur: "HH" },
  af_nova:       { grade: "C",  dur: "MM" },
  af_river:      { grade: "D",  dur: "MM" },
  af_sarah:      { grade: "C+", dur: "H" },
  af_sky:        { grade: "C-", dur: "M" },
  am_adam:       { grade: "F+", dur: "H" },
  am_echo:       { grade: "D",  dur: "MM" },
  am_eric:       { grade: "D",  dur: "MM" },
  am_fenrir:     { grade: "C+", dur: "H" },
  am_liam:       { grade: "D",  dur: "MM" },
  am_michael:    { grade: "C+", dur: "H" },
  am_onyx:       { grade: "D",  dur: "MM" },
  am_puck:       { grade: "C+", dur: "H" },
  am_santa:      { grade: "D-", dur: "M" },

  // British English
  bf_alice:      { grade: "D",  dur: "MM" },
  bf_emma:       { grade: "B-", dur: "HH" },
  bf_isabella:   { grade: "C",  dur: "MM" },
  bf_lily:       { grade: "D",  dur: "MM" },
  bm_daniel:     { grade: "D",  dur: "MM" },
  bm_fable:      { grade: "C",  dur: "MM" },
  bm_george:     { grade: "C",  dur: "MM" },
  bm_lewis:      { grade: "D+", dur: "H" },

  // Japanese
  jf_alpha:      { grade: "C+", dur: "H" },
  jf_gongitsune: { grade: "C",  dur: "MM" },
  jf_nezumi:     { grade: "C-", dur: "M" },
  jf_tebukuro:   { grade: "C",  dur: "MM" },
  jm_kumo:       { grade: "C-", dur: "M" },

  // Mandarin Chinese
  zf_xiaobei:    { grade: "D",  dur: "MM" },
  zf_xiaoni:     { grade: "D",  dur: "MM" },
  zf_xiaoxiao:   { grade: "D",  dur: "MM" },
  zf_xiaoyi:     { grade: "D",  dur: "MM" },
  zm_yunjian:    { grade: "D",  dur: "MM" },
  zm_yunxi:      { grade: "D",  dur: "MM" },
  zm_yunxia:     { grade: "D",  dur: "MM" },
  zm_yunyang:    { grade: "D",  dur: "MM" },

  // French
  ff_siwis:      { grade: "B-", dur: "<11h" },

  // Spanish
  ef_dora:       { grade: "D",  dur: "MM" },
  em_alex:       { grade: "D",  dur: "MM" },
  em_santa:      { grade: "D-", dur: "M" },

  // Italian
  if_sara:       { grade: "C",  dur: "MM" },
  im_nicola:     { grade: "C",  dur: "MM" },

  // Brazilian Portuguese
  pf_dora:       { grade: "D",  dur: "MM" },
  pm_alex:       { grade: "D",  dur: "MM" },
  pm_santa:      { grade: "D-", dur: "M" },

  // Hindi
  hf_alpha:      { grade: "C",  dur: "MM" },
  hf_beta:       { grade: "C",  dur: "MM" },
  hm_omega:      { grade: "C",  dur: "MM" },
  hm_psi:        { grade: "C",  dur: "MM" },

  // Fallbacks
  santa:         { grade: "D-", dur: "M" },
};

function getVoiceLabel(voice) {
  let baseName = voice.name;
  const attrs = state.translations?.voice_attributes || {};

  const getAttrs = (vid) => {
    if (vid.startsWith("af_")) return [attrs.american || "American", attrs.female || "Female"];
    if (vid.startsWith("am_")) return [attrs.american || "American", attrs.male || "Male"];
    if (vid.startsWith("bf_")) return [attrs.british || "British", attrs.female || "Female"];
    if (vid.startsWith("bm_")) return [attrs.british || "British", attrs.male || "Male"];
    if (vid.startsWith("ff_")) return [attrs.french || "French", attrs.female || "Female"];
    if (vid.startsWith("jf_")) return [attrs.japanese || "Japanese", attrs.female || "Female"];
    if (vid.startsWith("jm_")) return [attrs.japanese || "Japanese", attrs.male || "Male"];
    if (vid.startsWith("ef_")) return [attrs.spanish || "Spanish", attrs.female || "Female"];
    if (vid.startsWith("em_")) return [attrs.spanish || "Spanish", attrs.male || "Male"];
    if (vid.startsWith("zf_")) return [attrs.chinese || "Chinese", attrs.female || "Female"];
    if (vid.startsWith("zm_")) return [attrs.chinese || "Chinese", attrs.male || "Male"];
    if (vid.startsWith("if_")) return [attrs.italian || "Italian", attrs.female || "Female"];
    if (vid.startsWith("im_")) return [attrs.italian || "Italian", attrs.male || "Male"];
    if (vid.startsWith("pf_")) return [attrs.portuguese || "Portuguese", attrs.female || "Female"];
    if (vid.startsWith("pm_")) return [attrs.portuguese || "Portuguese", attrs.male || "Male"];
    if (vid === "santa") return [attrs.spanish || "Spanish", attrs.male || "Male"];
    return [];
  };

  const [region, gender] = getAttrs(voice.id);
  if (region && gender) {
    baseName = `${voice.name} (${region} ${gender})`;
  } else {
    baseName = state.translations?.voices?.[voice.id] || voice.name;
  }

  const bm = VOICE_BENCHMARKS[voice.id];
  if (bm && bm.grade && bm.dur) {
    return `[${bm.grade}] [${bm.dur}] ${baseName}`;
  } else if (bm && bm.grade) {
    return `[${bm.grade}] ${baseName}`;
  }
  return baseName;
}

// ── API calls ─────────────────────────────────────────────────────────────────

let cachedLangGroups = null;
let cachedSortedLangs = null;

function rebuildVoiceGroupsCache() {
  const groups = {};
  availableVoices.forEach((v) => {
    if (!groups[v.lang]) {
      groups[v.lang] = { label: v.categoryLabel || v.lang, voices: [] };
    }
    groups[v.lang].voices.push(v);
  });
  cachedSortedLangs = Object.keys(groups).sort((a, b) => {
    if (a.startsWith("en") && !b.startsWith("en")) return -1;
    if (!a.startsWith("en") && b.startsWith("en")) return 1;
    return a.localeCompare(b);
  });
  cachedLangGroups = groups;
}

let fetchVoicesPromise = null;
async function fetchVoices() {
  if (fetchVoicesPromise) return fetchVoicesPromise;
  fetchVoicesPromise = (async () => {
    try {
      const data = await fetchJSON("/api/voices/available");
      availableVoices = [];
      const categories = data.categories || {};
      const sortedKeys = Object.keys(categories).sort((a, b) => {
        if (a.startsWith("en") && !b.startsWith("en")) return -1;
        if (!a.startsWith("en") && b.startsWith("en")) return 1;
        return a.localeCompare(b);
      });
      sortedKeys.forEach((lang) => {
        const category = categories[lang];
        const categoryLabel = category.label || lang;
        (category.voices || []).forEach((v) => {
          const cleanId = v.id.toLowerCase().split("_").pop();
          if (["alpha", "beta", "omega", "psi"].includes(cleanId)) return;
          availableVoices.push({
            id: v.id,
            name: v.name,
            lang,
            categoryLabel,
            label: getVoiceLabel(v),
          });
        });
      });
      rebuildVoiceGroupsCache();
      if (slots[0].voice === "" && availableVoices.length > 0) {
        const heart = availableVoices.find((v) => v.id === "af_heart");
        slots[0].voice = heart ? heart.id : availableVoices[0].id;
      }
    } catch (e) {
      console.error("[Blending] Failed to load voices:", e);
    } finally {
      fetchVoicesPromise = null;
    }
  })();
  return fetchVoicesPromise;
}

let fetchProfilesPromise = null;
async function fetchProfiles() {
  if (fetchProfilesPromise) return fetchProfilesPromise;
  fetchProfilesPromise = (async () => {
    try {
      const data = await fetchJSON(API_BLEND_PROFILES);
      const raw = data.profiles || [];
      profileList = raw.map((p) => (typeof p === "string" ? p : p.key)).filter((k) => k !== "bleeding");
      profileLabels = {};
      profilePresets = {};
      profileSlotsMap = {};
      raw.forEach((p) => {
        if (typeof p === "object" && p.key !== "bleeding") {
          profileLabels[p.key] = p.label || p.key;
          profilePresets[p.key] = !!p.is_preset;
          profileSlotsMap[p.key] = p.slots || [];
        }
      });
      if (profileList.length === 0) profileList = ["preset_bella_sarah"];
    } catch {
      profileList = ["preset_bella_sarah"];
    } finally {
      fetchProfilesPromise = null;
    }
  })();
  return fetchProfilesPromise;
}

async function loadProfile(name) {
  try {
    const data = await fetchJSON(`${API_BLEND_LOAD}?profile=${encodeURIComponent(name)}`);
    if (data.slots && Array.isArray(data.slots)) {
      slots = data.slots.slice(0, MAX_SLOTS);
      while (slots.length < MAX_SLOTS)
        slots.push({ active: false, voice: "", weight: 0.00 });
    }
    blendEnabled = !!data.enabled;
    if (name !== "bleeding") {
      activeProfile = name;
    }
  } catch {
    // no profile yet, keep defaults
  }
}

async function saveProfile(name) {
  try {
    await fetchJSON(API_BLEND_SAVE, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: name, enabled: blendEnabled, slots }),
    });
  } catch (e) {
    console.error("[Blending] Save failed:", e);
  }
}

async function applyBlend() {
  const active = slots.filter((s) => s.active && s.voice);
  const expression = active.map((s) => `${s.voice}(${fmt(s.weight)})`).join("+");

  // Sync to shared frontend state
  state.blendEnabled = blendEnabled;
  state.blendExpression = blendEnabled ? expression : "";

  // Invalidate audio buffer cache so subsequent sentences synthesize with the new voice
  if (state.audioBufferCache && typeof state.audioBufferCache.clear === "function") {
    state.audioBufferCache.clear();
  }

  try {
    await fetchJSON(API_BLEND_APPLY, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expression, enabled: blendEnabled }),
    });
  } catch (e) {
    console.error("[Blending] Apply failed:", e);
  }
}

// ── DOM renderers ─────────────────────────────────────────────────────────────

function buildVoiceOptions(currentSlotIndex = null) {
  if (!cachedLangGroups || !cachedSortedLangs) {
    rebuildVoiceGroupsCache();
  }
  // Collect voices currently chosen by other slots so they are excluded
  const usedByOthers = new Set();
  if (currentSlotIndex !== null) {
    for (let idx = 0; idx < slots.length; idx++) {
      if (idx !== currentSlotIndex && slots[idx].voice) {
        usedByOthers.add(slots[idx].voice);
      }
    }
  }

  let html = "";
  for (let l = 0; l < cachedSortedLangs.length; l++) {
    const lang = cachedSortedLangs[l];
    const grp = cachedLangGroups[lang];
    if (!grp) continue;
    let opts = "";
    for (let v = 0; v < grp.voices.length; v++) {
      const voice = grp.voices[v];
      if (!usedByOthers.has(voice.id)) {
        opts += `<option value="${voice.id}">${voice.label || voice.name}</option>`;
      }
    }
    if (opts) {
      html += `<optgroup label="${grp.label}">${opts}</optgroup>`;
    }
  }
  return html;
}

function buildProfileOptions() {
  const presets = profileList.filter((p) => profilePresets[p] || p.startsWith("preset_"));
  const custom = profileList.filter((p) => p !== "bleeding" && !profilePresets[p] && !p.startsWith("preset_"));

  let html = "";

  if (presets.length > 0) {
    const presetOpts = presets
      .map((p) => `<option value="${p}"${p === activeProfile ? " selected" : ""}>★ ${profileLabels[p] || p}</option>`)
      .join("");
    html += `<optgroup label="Kokoro Presets">${presetOpts}</optgroup>`;
  }

  if (custom.length > 0) {
    const customOpts = custom
      .map((p) => {
        const label = profileLabels[p] && profileLabels[p] !== p ? profileLabels[p] : p.replace(/^profiles_/, "Profile ");
        return `<option value="${p}"${p === activeProfile ? " selected" : ""}>${label}</option>`;
      })
      .join("");
    html += `<optgroup label="Custom Profiles">${customOpts}</optgroup>`;
  }

  return html;
}

function updateAllVoiceSelectOptions() {
  const container = document.getElementById("blendSlotsGrid");
  if (!container) return;

  slots.forEach((slot, i) => {
    const sel = container.querySelector(`select[data-slot="${i}"]`);
    if (!sel) return;
    const currentVal = slot.voice;
    sel.innerHTML = `<option value="" disabled${!currentVal ? " selected" : ""}>— voice —</option>` + buildVoiceOptions(i);
    if (currentVal) {
      sel.value = currentVal;
    }
  });
}

function renderSlots() {
  const container = document.getElementById("blendSlotsGrid");
  if (!container) return;

  container.innerHTML = slots
    .map(
      (slot, i) => {
        const voiceOpts = buildVoiceOptions(i);
        return `
    <div class="blend-slot flex items-center gap-2 px-3 py-2 bg-zinc-900/60 rounded-lg border border-zinc-800/70" data-slot="${i}">
      <!-- Checkbox -->
      <input
        type="checkbox"
        class="blend-slot-active shrink-0 w-3.5 h-3.5 accent-blue-500 cursor-pointer"
        data-slot="${i}"
        ${slot.active ? "checked" : ""}
        title="Enable slot ${i + 1}"
      />
      <!-- Voice select -->
      <select
        class="blend-slot-voice flex-1 min-w-0 bg-zinc-900 text-xs font-medium border border-zinc-800 rounded-md px-2 py-1.5 outline-none text-zinc-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20"
        data-slot="${i}"
      >
        <option value="" disabled${!slot.voice ? " selected" : ""}>— voice —</option>
        ${voiceOpts}
      </select>
      <!-- Weight display + steppers -->
      <div class="blend-slot-weight-wrap flex items-center gap-0.5 shrink-0">
        <button
          type="button"
          class="blend-slot-weight-val flex items-center justify-center px-1.5 h-6 bg-zinc-800/80 hover:bg-zinc-800 border border-zinc-700/60 hover:border-blue-500/60 rounded cursor-pointer transition-colors text-xs font-mono text-blue-400 focus:outline-none select-none"
          data-slot="${i}"
          title="Scroll to fine-adjust (±0.01)"
        >${fmt(slot.weight)}</button>
        <div class="flex flex-col border border-zinc-700/70 bg-zinc-800/90 rounded overflow-hidden select-none shadow-sm">
          <button
            type="button"
            class="blend-slot-up w-4 h-[11px] flex items-center justify-center text-zinc-400 hover:text-blue-300 hover:bg-zinc-700 active:bg-blue-600/40 transition-colors focus:outline-none"
            data-slot="${i}"
            aria-label="Increase weight"
          >
            <svg class="w-2.5 h-2.5 pointer-events-none" viewBox="0 0 16 16" fill="currentColor"><path d="M8 4.5l5 6H3l5-6z"/></svg>
          </button>
          <div class="w-full h-[1px] bg-zinc-700/60"></div>
          <button
            type="button"
            class="blend-slot-down w-4 h-[11px] flex items-center justify-center text-zinc-400 hover:text-blue-300 hover:bg-zinc-700 active:bg-blue-600/40 transition-colors focus:outline-none"
            data-slot="${i}"
            aria-label="Decrease weight"
          >
            <svg class="w-2.5 h-2.5 pointer-events-none" viewBox="0 0 16 16" fill="currentColor"><path d="M8 11.5l-5-6h10l-5 6z"/></svg>
          </button>
        </div>
      </div>
    </div>
  `;
      }
    )
    .join("");

  // Set selected voice
  slots.forEach((slot, i) => {
    const sel = container.querySelector(`select[data-slot="${i}"]`);
    if (sel && slot.voice) sel.value = slot.voice;
  });

  // Attach slot events
  attachSlotEvents(container);
  updateTotalDisplay();
}

function updateTotalDisplay() {
  const total = totalWeight();
  const el = document.getElementById("blendTotalVal");
  const warn = document.getElementById("blendTotalWarn");
  if (!el) return;
  el.textContent = fmt(total);
  const over = total > WEIGHT_MAX + 0.001;
  el.classList.toggle("text-amber-400", over);
  el.classList.toggle("text-blue-400", !over);
  if (warn) warn.classList.toggle("hidden", !over);
}

function updateToggleUI() {
  const btns = [
    document.getElementById("blendMasterToggleBtn"),
    document.getElementById("drawerBlendMasterToggleBtn"),
  ].filter(Boolean);

  const thumbs = [
    document.getElementById("blendToggleThumb"),
    document.getElementById("drawerBlendToggleThumb"),
  ].filter(Boolean);

  const labels = [
    document.getElementById("blendToggleLabel"),
    document.getElementById("drawerBlendToggleLabel"),
  ].filter(Boolean);

  btns.forEach((btn) => {
    btn.setAttribute("aria-checked", blendEnabled ? "true" : "false");
    if (blendEnabled) {
      btn.classList.remove("bg-zinc-700");
      btn.classList.add("bg-blue-600");
    } else {
      btn.classList.remove("bg-blue-600");
      btn.classList.add("bg-zinc-700");
    }
  });

  thumbs.forEach((thumb) => {
    thumb.style.transform = blendEnabled ? "translateX(16px)" : "translateX(0)";
  });

  labels.forEach((label) => {
    label.textContent = blendEnabled ? "Blending on" : "Blending off";
    label.classList.toggle("text-blue-400", blendEnabled);
    label.classList.toggle("text-zinc-400", !blendEnabled);
  });
}

function updateSlotWeight(i, newVal) {
  slots[i].weight = clampWeight(newVal);
  const valBtn = document.querySelector(`.blend-slot-weight-val[data-slot="${i}"]`);
  if (valBtn) valBtn.textContent = fmt(slots[i].weight);
  updateTotalDisplay();
}

// ── Click & Hold repeater helper ─────────────────────────────────────────────

function attachHoldRepeat(btn, onTapStep, onHoldStep) {
  let holdTimer = null;
  let repeatInterval = null;

  const onGlobalMouseUp = () => {
    stop();
  };

  const stop = () => {
    if (holdTimer) {
      clearTimeout(holdTimer);
      holdTimer = null;
    }
    if (repeatInterval) {
      clearInterval(repeatInterval);
      repeatInterval = null;
    }
    window.removeEventListener("mouseup", onGlobalMouseUp);
  };

  btn.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    stop();
    onTapStep();

    window.addEventListener("mouseup", onGlobalMouseUp);

    holdTimer = setTimeout(() => {
      repeatInterval = setInterval(() => {
        onHoldStep();
      }, HOLD_REPEAT_INTERVAL_MS);
    }, HOLD_INITIAL_DELAY_MS);
  });

  btn.addEventListener("mouseup", stop);
  btn.addEventListener("mouseleave", stop);

  btn.addEventListener("touchstart", (e) => {
    e.preventDefault();
    stop();
    onTapStep();
    holdTimer = setTimeout(() => {
      repeatInterval = setInterval(() => {
        onHoldStep();
      }, HOLD_REPEAT_INTERVAL_MS);
    }, HOLD_INITIAL_DELAY_MS);
  }, { passive: false });

  btn.addEventListener("touchend", stop);
  btn.addEventListener("touchcancel", stop);
}

// ── Slot event wiring ─────────────────────────────────────────────────────────

function attachSlotEvents(container) {
  // Checkbox toggles
  container.querySelectorAll(".blend-slot-active").forEach((cb) => {
    cb.addEventListener("change", () => {
      const i = parseInt(cb.dataset.slot, 10);
      slots[i].active = cb.checked;
      updateTotalDisplay();
    });
  });

  // Voice selects
  container.querySelectorAll(".blend-slot-voice").forEach((sel) => {
    sel.addEventListener("change", () => {
      const i = parseInt(sel.dataset.slot, 10);
      slots[i].voice = sel.value;
      updateAllVoiceSelectOptions();
    });
  });

  // Stepper up (+0.10 on tap, +0.05 on hold) with click & hold repeat
  container.querySelectorAll(".blend-slot-up").forEach((btn) => {
    const i = parseInt(btn.dataset.slot, 10);
    attachHoldRepeat(
      btn,
      () => updateSlotWeight(i, slots[i].weight + WEIGHT_STEP_BTN),
      () => updateSlotWeight(i, slots[i].weight + WEIGHT_STEP_HOLD)
    );
  });

  // Stepper down (-0.10 on tap, -0.05 on hold) with click & hold repeat
  container.querySelectorAll(".blend-slot-down").forEach((btn) => {
    const i = parseInt(btn.dataset.slot, 10);
    attachHoldRepeat(
      btn,
      () => updateSlotWeight(i, slots[i].weight - WEIGHT_STEP_BTN),
      () => updateSlotWeight(i, slots[i].weight - WEIGHT_STEP_HOLD)
    );
  });

  // Mouse wheel ±0.01
  container.querySelectorAll(".blend-slot-weight-val").forEach((valBtn) => {
    valBtn.addEventListener("wheel", (e) => {
      e.preventDefault();
      const i = parseInt(valBtn.dataset.slot, 10);
      const delta = e.deltaY < 0 ? WEIGHT_STEP_WHEEL : -WEIGHT_STEP_WHEEL;
      updateSlotWeight(i, slots[i].weight + delta);
    }, { passive: false });
  });
}

async function refreshProfileDropdown(fetchNew = false) {
  if (fetchNew || profileList.length === 0) {
    await fetchProfiles();
  }
  const sel = document.getElementById("blendProfileSelect");
  if (sel) sel.innerHTML = buildProfileOptions();
}

// ── Modal mount ───────────────────────────────────────────────────────────────

function buildModal() {
  const modal = document.createElement("div");
  modal.id = "blendingModal";
  modal.className =
    "fixed top-[32px] right-0 bottom-0 left-0 z-[9998] hidden flex bg-black/70 backdrop-blur-sm";

  modal.innerHTML = `
    <div id="blendingPanel"
      class="flex flex-col bg-zinc-950 border-l border-zinc-800/80 shadow-2xl overflow-hidden font-sans text-zinc-200 ml-auto"
      style="width:520px; max-width:100vw;"
    >
      <!-- Titlebar -->
      <div class="flex items-center justify-between px-4 py-2.5 bg-zinc-900/90 border-b border-zinc-800/80 select-none shrink-0">
        <div class="flex items-center gap-2.5">
          <i data-lucide="audio-lines" class="w-4 h-4 text-blue-400 shrink-0"></i>
          <span class="text-xs font-semibold tracking-wide text-zinc-100">Blending Voice</span>
          <!-- Manage Profiles button – styled after files_UI.js:242 -->
          <button id="blendManageProfilesBtn"
            class="flex items-center gap-1.5 px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded text-xs font-medium border border-zinc-700/60 shadow-sm transition-colors ml-1"
            title="Manage voice profiles"
          >
            <i data-lucide="sliders" class="w-3.5 h-3.5 text-blue-400 shrink-0"></i>
            <span>Manage Profiles</span>
          </button>
        </div>
        <div class="flex items-center gap-3">
          <!-- Total weight indicator -->
          <div class="flex items-center gap-1 text-[10px] font-mono text-zinc-400">
            <span>Total:</span>
            <span id="blendTotalVal" class="text-blue-400 font-semibold">0.00</span>
            <span class="text-zinc-600">/ ${fmt(WEIGHT_MAX)}</span>
            <span id="blendTotalWarn" class="hidden px-1 py-0.5 rounded bg-amber-500/20 text-amber-400 text-[9px] font-bold">OVER</span>
            <button id="blendInfoTipsBtn"
              type="button"
              class="flex items-center justify-center w-3.5 h-3.5 rounded-full bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-400 hover:text-blue-400 text-[9px] font-bold transition-colors cursor-pointer ml-0.5"
              title="Mixing Tips & Golden Rules"
            >?</button>
          </div>
          <button id="blendCloseBtn"
            class="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors"
            title="Close (Esc)"
          >
            <i data-lucide="x" class="w-4 h-4"></i>
          </button>
        </div>
      </div>

      <!-- Sub-bar: toggle + profile selector -->
      <div class="flex items-center gap-4 px-4 py-2 bg-zinc-900/60 border-b border-zinc-800/60 shrink-0">
        <!-- Master toggle -->
        <div class="flex items-center gap-2 select-none">
          <div id="blendToggleContainer" class="flex items-center gap-2 cursor-pointer group" title="Toggle voice blending on/off">
            <button
              type="button"
              id="blendMasterToggleBtn"
              class="relative w-8 h-4 rounded-full transition-colors duration-200 bg-zinc-700 cursor-pointer focus:outline-none"
              role="switch"
              aria-checked="false"
            >
              <div id="blendToggleThumb"
                class="absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white shadow transition-transform duration-200 pointer-events-none"
              ></div>
            </button>
            <span id="blendToggleLabel" class="text-xs text-zinc-400 group-hover:text-zinc-200 transition-colors">Blending off</span>
          </div>
          <button id="blendInfoGradeBtn"
            type="button"
            class="flex items-center justify-center w-4 h-4 rounded-full bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-400 hover:text-blue-400 text-[10px] font-bold transition-colors cursor-pointer"
            title="Quality Grades & Training Duration Meaning"
          >?</button>

          <!-- Divider -->
          <div class="w-px h-3.5 bg-zinc-800 mx-0.5"></div>

          <!-- Controls button to return to speed/pause settings drawer -->
          <button
            id="blendControlsBtn"
            type="button"
            class="px-2.5 py-1 bg-zinc-800/90 hover:bg-zinc-700 text-zinc-200 hover:text-white rounded text-xs font-semibold border border-zinc-700/60 shadow-xs transition-colors cursor-pointer"
          >Controls</button>
        </div>

        <!-- Profile selector -->
        <div class="flex items-center gap-1.5 ml-auto">
          <i data-lucide="bookmark" class="w-3.5 h-3.5 text-zinc-500 shrink-0"></i>
          <select id="blendProfileSelect"
            class="bg-zinc-900 text-xs font-medium border border-zinc-800 rounded px-2 py-1 outline-none text-zinc-300 focus:border-blue-500 max-w-[170px]"
          >
          </select>
          <button id="blendProfileLoadBtn"
            class="px-2 py-1 text-[10px] font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded border border-zinc-700/60 transition-colors"
            title="Load selected profile"
          >Load</button>
        </div>
      </div>

      <!-- Slot grid -->
      <div class="flex-1 overflow-y-auto custom-scrollbar px-4 py-3">
        <div id="blendSlotsGrid" class="flex flex-col gap-2">
          <!-- slots rendered by renderSlots() -->
        </div>
      </div>

      <!-- Footer actions -->
      <div class="flex items-center gap-2 px-4 py-3 border-t border-zinc-800/70 bg-zinc-900/60 shrink-0">
        <button id="blendNormalizeBtn"
          class="px-3 py-1.5 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded border border-zinc-700/60 transition-colors"
          title="Scale active weights so they sum to 1.00"
        >Normalize</button>
        <div class="ml-auto flex items-center gap-2">
          <button id="blendSaveBtn"
            class="px-3 py-1.5 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded border border-zinc-700/60 transition-colors"
          >Save</button>
          <button id="blendApplyBtn"
            class="px-4 py-1.5 text-xs font-semibold bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors shadow"
          >Apply</button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(modal);
  return modal;
}

// ── Manage Profiles modal (Windows Environment Variables Style max-w-2xl) ─────

function openManageProfiles() {
  let mgr = document.getElementById("blendProfileMgr");
  if (!mgr) {
    mgr = document.createElement("div");
    mgr.id = "blendProfileMgr";
    mgr.className =
      "fixed top-[32px] right-0 bottom-0 left-0 z-[10000] flex items-center justify-center bg-black/80 backdrop-blur-xs p-4 hidden";
    mgr.innerHTML = `
      <div id="blendProfileMgrPanel" class="flex flex-col w-full max-w-2xl bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl overflow-hidden font-sans text-zinc-200">
        
        <!-- Titlebar -->
        <div class="flex items-center justify-between px-3.5 py-2.5 bg-zinc-800/90 border-b border-zinc-700/80 select-none">
          <div class="flex items-center gap-2">
            <i data-lucide="sliders" class="w-4 h-4 text-blue-400"></i>
            <span class="text-xs font-semibold tracking-wide text-zinc-100">Manage Voice Profiles & Presets</span>
          </div>
          <button id="blendMgrClose" class="p-1 rounded hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors" title="Close (Esc)">
            <i data-lucide="x" class="w-3.5 h-3.5"></i>
          </button>
        </div>

        <!-- Body: List on Left, Action Buttons Stack on Right -->
        <div class="p-4 flex flex-col gap-2.5">
          <div class="flex items-center justify-between text-xs text-zinc-300 font-medium select-none">
            <span>Voice Profiles & Built-in Presets:</span>
            <span id="blendMgrStats" class="text-[11px] text-zinc-500 font-normal"></span>
          </div>
          
          <div class="flex gap-3 h-80">
            <!-- Profiles Listbox -->
            <div id="blendMgrList" class="flex-1 bg-zinc-950 border border-zinc-700 rounded p-1.5 text-xs divide-y divide-zinc-900/60 overflow-y-auto custom-scrollbar select-none">
              <!-- Rendered Items -->
            </div>

            <!-- Right Button Stack -->
            <div class="flex flex-col gap-1.5 w-32 shrink-0 select-none">
              <button id="blendMgrApplyNowBtn" class="px-2.5 py-1.5 text-xs font-semibold bg-blue-600 hover:bg-blue-500 text-white rounded shadow transition-colors flex items-center justify-center gap-1.5" title="Instantly load and activate this profile">
                <i data-lucide="zap" class="w-3.5 h-3.5"></i>
                <span>Apply Now</span>
              </button>
              <button id="blendMgrLoadBtn" class="px-2.5 py-1.5 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded text-zinc-200 transition-colors flex items-center justify-center gap-1.5" title="Load into blending drawer slots to customize">
                <i data-lucide="arrow-down-to-dot" class="w-3.5 h-3.5 text-blue-400"></i>
                <span>Load</span>
              </button>
              <div class="my-1 border-t border-zinc-800"></div>
              <button id="blendMgrSaveAsToggleBtn" class="px-2.5 py-1.5 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded text-zinc-200 transition-colors flex items-center justify-center gap-1.5" title="Save current slots as a new profile">
                <i data-lucide="plus" class="w-3.5 h-3.5 text-emerald-400"></i>
                <span>Save As...</span>
              </button>
              <button id="blendMgrDeleteBtn" class="px-2.5 py-1.5 text-xs font-medium bg-red-900/30 hover:bg-red-800/50 border border-red-700/50 rounded text-red-300 transition-colors disabled:opacity-30 disabled:pointer-events-none flex items-center justify-center gap-1.5" title="Delete custom profile">
                <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                <span>Delete</span>
              </button>
            </div>
          </div>

          <!-- Save As Form Row -->
          <div id="blendMgrSaveAsRow" class="hidden flex items-center gap-2 p-2 bg-zinc-950/80 border border-zinc-800 rounded select-none">
            <span class="text-xs text-zinc-400 shrink-0">New Profile Name:</span>
            <input id="blendMgrNewName" type="text" placeholder="e.g. My Favorite Blend"
              class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2.5 py-1 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-blue-500"
            />
            <button id="blendMgrConfirmSaveBtn" class="px-3 py-1 text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white rounded transition-colors">
              Save
            </button>
            <button id="blendMgrCancelSaveBtn" class="px-2 py-1 text-xs text-zinc-400 hover:text-white transition-colors">
              Cancel
            </button>
          </div>
        </div>

        <!-- Footer -->
        <div class="flex items-center justify-between px-4 py-2.5 bg-zinc-950/60 border-t border-zinc-800 text-[11px] text-zinc-500 select-none">
          <div id="blendMgrFooterStatus">Tip: Presets are built-in and protected. Use 'Save As...' to make customized copies.</div>
          <button id="blendMgrDoneBtn" class="px-4 py-1 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded border border-zinc-700 transition-colors">
            Close
          </button>
        </div>

      </div>
    `;
    document.body.appendChild(mgr);
    renderIcons();

    // Wire Modal Level Events
    const closeMgr = () => mgr.classList.add("hidden");
    mgr.querySelector("#blendMgrClose").onclick = closeMgr;
    mgr.querySelector("#blendMgrDoneBtn").onclick = closeMgr;
    mgr.onclick = (e) => { if (e.target === mgr) closeMgr(); };

    // Apply Now
    mgr.querySelector("#blendMgrApplyNowBtn").onclick = async () => {
      if (!selectedMgrKey) return;
      const applyBtn = mgr.querySelector("#blendMgrApplyNowBtn");
      await loadProfile(selectedMgrKey);
      blendEnabled = true;
      updateToggleUI();
      renderSlots();
      await applyBlend();
      await refreshProfileDropdown();
      renderMgrList(mgr);

      const origText = applyBtn.innerHTML;
      applyBtn.innerHTML = `<i data-lucide="check" class="w-3.5 h-3.5"></i><span>Applied!</span>`;
      applyBtn.classList.add("bg-emerald-600");
      applyBtn.classList.remove("bg-blue-600");
      renderIcons();
      setTimeout(() => {
        applyBtn.innerHTML = origText;
        applyBtn.classList.remove("bg-emerald-600");
        applyBtn.classList.add("bg-blue-600");
        renderIcons();
      }, 1200);
    };

    // Load
    mgr.querySelector("#blendMgrLoadBtn").onclick = async () => {
      if (!selectedMgrKey) return;
      const loadBtn = mgr.querySelector("#blendMgrLoadBtn");
      await loadProfile(selectedMgrKey);
      renderSlots();
      updateToggleUI();
      await refreshProfileDropdown();
      renderMgrList(mgr);

      const origText = loadBtn.innerHTML;
      loadBtn.innerHTML = `<i data-lucide="check" class="w-3.5 h-3.5 text-emerald-400"></i><span>Loaded!</span>`;
      renderIcons();
      setTimeout(() => {
        loadBtn.innerHTML = origText;
        renderIcons();
      }, 1200);
    };

    // Save As Toggle
    mgr.querySelector("#blendMgrSaveAsToggleBtn").onclick = () => {
      const row = mgr.querySelector("#blendMgrSaveAsRow");
      const inp = mgr.querySelector("#blendMgrNewName");
      row.classList.toggle("hidden");
      if (!row.classList.contains("hidden")) {
        inp.focus();
      }
    };

    // Save As Confirm
    mgr.querySelector("#blendMgrConfirmSaveBtn").onclick = async () => {
      const inp = mgr.querySelector("#blendMgrNewName");
      const label = inp.value.trim();
      if (!label) return;

      const existingNums = profileList
        .filter((p) => p.startsWith("profiles_"))
        .map((p) => parseInt(p.replace("profiles_", ""), 10))
        .filter((n) => !isNaN(n));
      const nextNum = existingNums.length > 0 ? Math.max(...existingNums) + 1 : 1;
      const key = `profiles_${nextNum}`;

      await fetchJSON(API_BLEND_SAVE, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: key, label, enabled: blendEnabled, slots }),
      });

      await fetchProfiles();
      selectedMgrKey = key;
      await refreshProfileDropdown();
      inp.value = "";
      mgr.querySelector("#blendMgrSaveAsRow").classList.add("hidden");
      renderMgrList(mgr);
    };

    // Save As Cancel
    mgr.querySelector("#blendMgrCancelSaveBtn").onclick = () => {
      mgr.querySelector("#blendMgrSaveAsRow").classList.add("hidden");
    };

    // Delete
    mgr.querySelector("#blendMgrDeleteBtn").onclick = async () => {
      if (!selectedMgrKey || selectedMgrKey === "bleeding" || profilePresets[selectedMgrKey] || selectedMgrKey.startsWith("preset_")) {
        return;
      }
      try {
        await fetchJSON(`${API_BLEND_PROFILES}?profile=${encodeURIComponent(selectedMgrKey)}`, {
          method: "DELETE",
        });
      } catch {
        // best effort
      }
      await fetchProfiles();
      if (activeProfile === selectedMgrKey) activeProfile = profileList[0] || "preset_bella_sarah";
      selectedMgrKey = profileList[0] || "preset_bella_sarah";
      await refreshProfileDropdown();
      renderMgrList(mgr);
    };
  }

  // Pre-select activeProfile if valid
  if (profileList.includes(activeProfile) && activeProfile !== "bleeding") {
    selectedMgrKey = activeProfile;
  } else if (!selectedMgrKey || !profileList.includes(selectedMgrKey) || selectedMgrKey === "bleeding") {
    selectedMgrKey = profileList[0] || "preset_bella_sarah";
  }

  renderMgrList(mgr);
  mgr.classList.remove("hidden");
}

function renderMgrList(mgr) {
  const list = mgr.querySelector("#blendMgrList");
  if (!list) return;

  const validProfiles = profileList.filter((p) => p !== "bleeding");
  const stats = mgr.querySelector("#blendMgrStats");
  const presetsCount = validProfiles.filter((p) => profilePresets[p] || p.startsWith("preset_")).length;
  const customCount = validProfiles.filter((p) => !profilePresets[p] && !p.startsWith("preset_")).length;
  if (stats) {
    stats.textContent = `${presetsCount} Presets • ${customCount} Custom`;
  }

  // Generate formula summary from slots
  const getFormula = (key) => {
    const sList = profileSlotsMap[key] || [];
    const active = sList.filter((s) => s.active && s.voice);
    if (active.length === 0) return "";
    return active.map((s) => `${s.voice} (${Math.round(s.weight * 100)}%)`).join(" + ");
  };

  list.innerHTML = validProfiles
    .map((p) => {
      const isPreset = !!profilePresets[p] || p.startsWith("preset_");
      const isSelected = p === selectedMgrKey;

      const label = profileLabels[p] && profileLabels[p] !== p
        ? profileLabels[p]
        : p.replace(/^profiles_/, "Profile ");

      const badgeHtml = isPreset
        ? `<span class="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-amber-950/80 text-amber-300 border border-amber-700/60 shrink-0">Preset</span>`
        : `<span class="px-1.5 py-0.5 rounded text-[9px] font-medium bg-zinc-800 text-zinc-400 border border-zinc-700/50 shrink-0">Custom</span>`;

      const formula = getFormula(p);

      return `
      <div class="blend-mgr-item flex flex-col gap-1 p-2 rounded cursor-pointer transition-all ${
        isSelected
          ? "bg-blue-600/20 border border-blue-500/50 text-white"
          : "hover:bg-zinc-900/80 border border-transparent text-zinc-300"
      }" data-key="${p}">
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 min-w-0">
            <i data-lucide="${isPreset ? "sparkles" : "bookmark"}" class="w-3.5 h-3.5 ${
        isPreset ? "text-amber-400" : "text-blue-400"
      } shrink-0"></i>
            <span class="font-semibold text-xs truncate">${label}</span>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            ${badgeHtml}
          </div>
        </div>
        ${
          formula
            ? `<div class="text-[10px] text-zinc-400 font-mono truncate pl-5.5">${formula}</div>`
            : ""
        }
      </div>`;
    })
    .join("");

  renderIcons();

  // Update Delete button state according to selected item
  const delBtn = mgr.querySelector("#blendMgrDeleteBtn");
  if (delBtn) {
    const isProtected =
      !selectedMgrKey ||
      profilePresets[selectedMgrKey] ||
      selectedMgrKey.startsWith("preset_");
    delBtn.disabled = isProtected;
    delBtn.title = isProtected ? "Built-in presets cannot be deleted" : "Delete custom profile";
  }

  // Click row to select
  list.querySelectorAll(".blend-mgr-item").forEach((el) => {
    el.addEventListener("click", () => {
      selectedMgrKey = el.dataset.key;
      renderMgrList(mgr);
    });
    el.addEventListener("dblclick", async () => {
      selectedMgrKey = el.dataset.key;
      const applyBtn = mgr.querySelector("#blendMgrApplyNowBtn");
      if (applyBtn) applyBtn.click();
    });
  });
}

// ── Toggle track visual sync ──────────────────────────────────────────────────

function syncToggleVisual() {
  updateToggleUI();
}

// ── Open / close ──────────────────────────────────────────────────────────────

export async function refreshBlendingVoices() {
  await fetchVoices();
  const availIds = new Set(availableVoices.map((v) => v.id));
  slots.forEach((s, idx) => {
    if (s.voice && !availIds.has(s.voice)) {
      s.voice = "";
      if (idx !== 0) s.active = false;
    }
  });
  if (slots[0].voice === "" && availableVoices.length > 0) {
    slots[0].voice = availableVoices[0].id;
  }
  const modal = getModal();
  if (modal && !modal.classList.contains("hidden")) {
    renderSlots();
    updateTotalDisplay();
  }
}

async function openModal() {
  let modal = getModal();
  const isFirstMount = !modal;
  if (!modal) {
    modal = buildModal();
    attachModalEvents(modal);
    renderIcons();
  }

  // Ensure prerequisite data is present (if startup pre-fetch hasn't finished yet)
  if (availableVoices.length === 0 || profileList.length === 0) {
    await Promise.all([
      availableVoices.length === 0 ? fetchVoices() : Promise.resolve(),
      profileList.length === 0 ? fetchProfiles() : Promise.resolve(),
    ]);
  }

  if (isFirstMount) {
    if (!profileList.includes(activeProfile) || activeProfile === "bleeding") {
      activeProfile = profileList[0] || "preset_bella_sarah";
    }
  }

  // Show modal immediately - zero visual lag!
  modal.classList.remove("hidden");
  modal.style.display = "flex";

  renderSlots();
  updateToggleUI();
  syncToggleVisual();
  refreshProfileDropdown(false);
}

function closeModal() {
  const modal = getModal();
  if (modal) {
    modal.classList.add("hidden");
    modal.style.display = "";
  }
}

// ── Info Modals (Quality Grades & Mixing Tips) ────────────────────────────────

function openGradeInfoModal() {
  let modal = document.getElementById("blendGradeInfoModal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "blendGradeInfoModal";
    modal.className = "fixed top-[32px] right-0 bottom-0 left-0 z-[10001] flex items-center justify-center bg-black/80 backdrop-blur-xs p-4 hidden select-none";
    modal.innerHTML = `
      <div class="flex flex-col w-full max-w-lg bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl overflow-hidden font-sans text-zinc-200">
        <!-- Titlebar -->
        <div class="flex items-center justify-between px-3.5 py-2.5 bg-zinc-800/90 border-b border-zinc-700/80">
          <div class="flex items-center gap-2">
            <i data-lucide="award" class="w-4 h-4 text-amber-400"></i>
            <span class="text-xs font-semibold tracking-wide text-zinc-100">Quality Grades & Training Durations</span>
          </div>
          <button class="blend-info-close p-1 rounded hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors">
            <i data-lucide="x" class="w-3.5 h-3.5"></i>
          </button>
        </div>

        <!-- Body -->
        <div class="p-4 flex flex-col gap-3 text-xs overflow-y-auto max-h-[75vh] custom-scrollbar">
          <div>
            <h4 class="font-semibold text-blue-400 mb-1.5 flex items-center gap-1.5">
              <span>Overall Quality Grades</span>
              <span class="text-[10px] text-zinc-400 font-normal">(Clarity & text alignment)</span>
            </h4>
            <div class="grid grid-cols-1 gap-1.5 bg-zinc-950/70 p-2.5 rounded border border-zinc-800">
              <div class="flex items-start gap-2">
                <span class="font-bold text-amber-400 font-mono w-10 shrink-0">[A]</span>
                <span class="text-zinc-300"><strong class="text-white">Gold Standard</strong> (af_heart). Flawless phonetics, zero digital noise, natural pitch baseline.</span>
              </div>
              <div class="flex items-start gap-2">
                <span class="font-bold text-emerald-400 font-mono w-10 shrink-0">[A-]</span>
                <span class="text-zinc-300"><strong class="text-white">High Dynamic Range</strong> (af_bella). Crisp consonants and expressive attack.</span>
              </div>
              <div class="flex items-start gap-2">
                <span class="font-bold text-cyan-400 font-mono w-10 shrink-0">[B-]</span>
                <span class="text-zinc-300"><strong class="text-white">Studio Tier</strong> (af_nicole, bf_emma, ff_siwis). Warm, intimate, high consistency.</span>
              </div>
              <div class="flex items-start gap-2">
                <span class="font-bold text-blue-400 font-mono w-10 shrink-0">[C+]</span>
                <span class="text-zinc-300"><strong class="text-white">Solid Baseline</strong> (am_fenrir, am_michael, af_sarah, af_kore). Rich baritones and character clarity.</span>
              </div>
              <div class="flex items-start gap-2">
                <span class="font-bold text-zinc-400 font-mono w-10 shrink-0">[C/C-]</span>
                <span class="text-zinc-300"><strong class="text-white">Moderate Quality</strong>. Great for secondary accents (20–40% weight).</span>
              </div>
              <div class="flex items-start gap-2">
                <span class="font-bold text-red-400 font-mono w-10 shrink-0">[D/F]</span>
                <span class="text-zinc-300"><strong class="text-white">Thin Training / Misaligned</strong>. Prone to hallucinations alone; best kept below 15%.</span>
              </div>
            </div>
          </div>

          <div>
            <h4 class="font-semibold text-purple-400 mb-1.5 flex items-center gap-1.5">
              <span>Training Duration Codes</span>
              <span class="text-[10px] text-zinc-400 font-normal">(Audio seen during training)</span>
            </h4>
            <div class="grid grid-cols-2 gap-2 bg-zinc-950/70 p-2.5 rounded border border-zinc-800 font-mono text-[11px]">
              <div class="flex items-center gap-2">
                <span class="text-amber-400 font-bold">[HH]</span>
                <span class="text-zinc-300 font-sans">10 – 100 hours</span>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-blue-400 font-bold">[H]</span>
                <span class="text-zinc-300 font-sans">1 – 10 hours</span>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-zinc-400 font-bold">[MM]</span>
                <span class="text-zinc-300 font-sans">10 – 100 minutes</span>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-red-400 font-bold">[M]</span>
                <span class="text-zinc-300 font-sans">1 – 10 minutes</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div class="flex items-center justify-end px-4 py-2 bg-zinc-950/60 border-t border-zinc-800">
          <button class="blend-info-close px-4 py-1 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded border border-zinc-700 transition-colors">
            Got it
          </button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    renderIcons();

    const closeInfo = () => modal.classList.add("hidden");
    modal.querySelectorAll(".blend-info-close").forEach((b) => (b.onclick = closeInfo));
    modal.onclick = (e) => { if (e.target === modal) closeInfo(); };
  }
  modal.classList.remove("hidden");
}

function openTipsInfoModal() {
  let modal = document.getElementById("blendTipsInfoModal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "blendTipsInfoModal";
    modal.className = "fixed top-[32px] right-0 bottom-0 left-0 z-[10001] flex items-center justify-center bg-black/80 backdrop-blur-xs p-4 hidden select-none";
    modal.innerHTML = `
      <div class="flex flex-col w-full max-w-lg bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl overflow-hidden font-sans text-zinc-200">
        <!-- Titlebar -->
        <div class="flex items-center justify-between px-3.5 py-2.5 bg-zinc-800/90 border-b border-zinc-700/80">
          <div class="flex items-center gap-2">
            <i data-lucide="sparkles" class="w-4 h-4 text-blue-400"></i>
            <span class="text-xs font-semibold tracking-wide text-zinc-100">Voice Blending Tips & Golden Rules</span>
          </div>
          <button class="blend-info-close p-1 rounded hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors">
            <i data-lucide="x" class="w-3.5 h-3.5"></i>
          </button>
        </div>

        <!-- Body -->
        <div class="p-4 flex flex-col gap-3 text-xs overflow-y-auto max-h-[75vh] custom-scrollbar">
          <div class="p-2.5 bg-zinc-950/70 rounded border border-zinc-800 flex flex-col gap-1.5">
            <div class="font-semibold text-blue-400 flex items-center gap-1.5">
              <span>🎯 Target Total Weight: 1.00</span>
            </div>
            <p class="text-zinc-300 leading-relaxed">
              Weights summing close to <strong>1.00</strong> produce optimal audio dynamics. Exceeding 1.50–2.00 may trigger digital clipping or loudness distortion. Click <strong>Normalize</strong> anytime to scale active slots to 1.00!
            </p>
          </div>

          <div class="p-2.5 bg-zinc-950/70 rounded border border-zinc-800 flex flex-col gap-1.5">
            <div class="font-semibold text-emerald-400 flex items-center gap-1.5">
              <span>🏛️ Use a Tier A/B Anchor (50% – 70%)</span>
            </div>
            <p class="text-zinc-300 leading-relaxed">
              Always anchor your blend with a high-grade model (e.g. <code>af_heart</code> or <code>af_bella</code>). Then blend in secondary voices (20% – 30%) to color the timbre with warmth or accents without losing pronunciation stability.
            </p>
          </div>

          <div class="p-2.5 bg-zinc-950/70 rounded border border-zinc-800 flex flex-col gap-1.5">
            <div class="font-semibold text-amber-400 flex items-center gap-1.5">
              <span>⚖️ Cross-Gender 70/30 Rule</span>
            </div>
            <p class="text-zinc-300 leading-relaxed">
              When mixing male and female voices, a <strong>70/30</strong> or <strong>80/20</strong> ratio avoids pitch contour (F0) flutter while adding rich low-end chest resonance.
            </p>
          </div>

          <div class="p-2.5 bg-zinc-950/70 rounded border border-zinc-800 flex flex-col gap-1.5">
            <div class="font-semibold text-purple-400 flex items-center gap-1.5">
              <span>⚡ Fast Stepper Adjustments</span>
            </div>
            <p class="text-zinc-300 leading-relaxed">
              • <strong>Click</strong> stepper arrows: steps by ±0.10<br/>
              • <strong>Hold</strong> stepper arrows: smoothly steps by ±0.05 every 120ms<br/>
              • <strong>Mouse wheel</strong> over number: fine micro-tunes by ±0.01
            </p>
          </div>
        </div>

        <!-- Footer -->
        <div class="flex items-center justify-end px-4 py-2 bg-zinc-950/60 border-t border-zinc-800">
          <button class="blend-info-close px-4 py-1 text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded border border-zinc-700 transition-colors">
            Got it
          </button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    renderIcons();

    const closeTips = () => modal.classList.add("hidden");
    modal.querySelectorAll(".blend-info-close").forEach((b) => (b.onclick = closeTips));
    modal.onclick = (e) => { if (e.target === modal) closeTips(); };
  }
  modal.classList.remove("hidden");
}

// ── Save popup ────────────────────────────────────────────────────────────────

function showSavePopup(modal) {
  // Remove any existing popup first
  document.getElementById("blendSavePopupOverlay")?.remove();

  // Overlay covers the whole viewport and centers the card
  const overlay = document.createElement("div");
  overlay.id = "blendSavePopupOverlay";
  overlay.className = "fixed inset-0 z-[10001] flex items-center justify-center bg-black/40";

  const popup = document.createElement("div");
  popup.className =
    "flex flex-col gap-2 p-4 bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-72";

  popup.innerHTML = `
    <div class="flex items-center justify-between mb-1">
      <span class="text-xs font-semibold text-zinc-100">Save as profile</span>
      <button id="blendSavePopupClose" class="p-1 rounded hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors">
        <i data-lucide="x" class="w-3.5 h-3.5"></i>
      </button>
    </div>
    <input
      id="blendSavePopupName"
      type="text"
      placeholder="Profile name…"
      class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-blue-500 w-full"
      autocomplete="off"
    />
    <button id="blendSavePopupConfirm"
      class="w-full py-1.5 text-xs font-semibold bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors"
    >Save</button>
  `;

  overlay.appendChild(popup);
  document.body.appendChild(overlay);
  renderIcons();

  const inp = popup.querySelector("#blendSavePopupName");
  inp.focus();

  const dismiss = () => overlay.remove();

  popup.querySelector("#blendSavePopupClose").onclick = dismiss;

  // Clicking the dim backdrop dismisses
  overlay.addEventListener("mousedown", (e) => {
    if (e.target === overlay) dismiss();
  });

  // Confirm handler
  const doSave = async () => {
    const label = inp.value.trim();
    if (!label) { inp.focus(); return; }
    // Auto-assign next profiles_N
    const existingNums = profileList
      .filter((p) => p.startsWith("profiles_"))
      .map((p) => parseInt(p.replace("profiles_", ""), 10))
      .filter((n) => !isNaN(n));
    const nextNum = existingNums.length > 0 ? Math.max(...existingNums) + 1 : 1;
    const key = `profiles_${nextNum}`;
    await fetchJSON(API_BLEND_SAVE, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: key, label, enabled: blendEnabled, slots }),
    });
    if (!profileList.includes(key)) profileList.push(key);
    profileLabels[key] = label;
    await refreshProfileDropdown();
    dismiss();
  };

  popup.querySelector("#blendSavePopupConfirm").onclick = doSave;
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") doSave(); });
}

function attachModalEvents(modal) {
  // Close on overlay click (outside panel)
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  // Close button
  modal.querySelector("#blendCloseBtn")?.addEventListener("click", closeModal);

  // Esc key
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && getModal() && !getModal().classList.contains("hidden")) {
      closeModal();
    }
  });

  // Master toggle
  const toggleContainer = modal.querySelector("#blendToggleContainer");
  toggleContainer?.addEventListener("click", async () => {
    blendEnabled = !blendEnabled;
    updateToggleUI();
    await applyBlend();
  });

  // Info buttons
  modal.querySelector("#blendInfoGradeBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    openGradeInfoModal();
  });
  modal.querySelector("#blendInfoTipsBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    openTipsInfoModal();
  });

  // Controls button -> returns to Voice Settings drawer
  modal.querySelector("#blendControlsBtn")?.addEventListener("click", () => {
    closeModal();
    toggleSettingsDrawer("voiceSettingsDrawer", true);
  });

  // Manage Profiles
  modal.querySelector("#blendManageProfilesBtn")?.addEventListener("click", openManageProfiles);

  // Load profile
  modal.querySelector("#blendProfileLoadBtn")?.addEventListener("click", async () => {
    const sel = modal.querySelector("#blendProfileSelect");
    if (!sel) return;
    await loadProfile(sel.value);
    renderSlots();
    updateToggleUI();
    syncToggleVisual();
  });

  // Normalize
  modal.querySelector("#blendNormalizeBtn")?.addEventListener("click", () => {
    const total = totalWeight();
    if (total === 0) return;
    slots.forEach((s) => {
      if (s.active) s.weight = clampWeight(s.weight / total);
    });
    renderSlots();
  });

  // Save — show inline name popup
  modal.querySelector("#blendSaveBtn")?.addEventListener("click", () => {
    showSavePopup(modal);
  });

  // Apply
  modal.querySelector("#blendApplyBtn")?.addEventListener("click", async () => {
    if (!blendEnabled) {
      blendEnabled = true;
      updateToggleUI();
    }
    const hasActiveSlot = slots.some((s) => s.active && s.voice);
    if (!hasActiveSlot && slots[0] && slots[0].voice) {
      slots[0].active = true;
      renderSlots();
    }
    const btn = modal.querySelector("#blendApplyBtn");
    if (btn) {
      const origText = btn.textContent;
      btn.textContent = "Applied!";
      btn.classList.remove("bg-blue-600", "hover:bg-blue-500");
      btn.classList.add("bg-emerald-600", "hover:bg-emerald-500");
      setTimeout(() => {
        btn.textContent = origText;
        btn.classList.remove("bg-emerald-600", "hover:bg-emerald-500");
        btn.classList.add("bg-blue-600", "hover:bg-blue-500");
      }, 1200);
    }
    await saveProfile("bleeding");
    await applyBlend();
  });
}

// ── Topbar trigger ────────────────────────────────────────────────────────────

function mountTopbarButton() {
  const tocBtn = document.getElementById("tocBtn");
  if (!tocBtn) return;
  if (document.getElementById("mixMenuTrigger")) return;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "menubar-title";
  btn.id = "mixMenuTrigger";
  btn.textContent = "Mix";
  btn.title = "Voice blending";

  btn.addEventListener("click", openModal);
  tocBtn.parentNode.insertBefore(btn, tocBtn);
}

// ── Public init ───────────────────────────────────────────────────────────────

export async function initVoiceBlending() {
  mountTopbarButton();
  try {
    const data = await fetchJSON(`${API_BLEND_LOAD}?profile=bleeding`);
    if (data && typeof data === "object") {
      blendEnabled = !!data.enabled;
      if (data.slots && Array.isArray(data.slots) && data.slots.length > 0) {
        slots = data.slots.slice(0, MAX_SLOTS);
        while (slots.length < MAX_SLOTS)
          slots.push({ active: false, voice: "", weight: 0.00 });
      }
      const active = slots.filter((s) => s.active && s.voice);
      const expression = active.map((s) => `${s.voice}(${fmt(s.weight)})`).join("+");
      state.blendEnabled = blendEnabled;
      state.blendExpression = blendEnabled ? (data.expression || expression) : "";
    }
  } catch (e) {
    // defaults
  }
  window.refreshBlendingVoices = refreshBlendingVoices;

  // Drawer header blending controls
  const drawerToggle = document.getElementById("drawerBlendToggleContainer");
  drawerToggle?.addEventListener("click", async () => {
    blendEnabled = !blendEnabled;
    updateToggleUI();
    await applyBlend();
  });

  document.getElementById("drawerBlendInfoGradeBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    openGradeInfoModal();
  });

  document.getElementById("drawerMixBtn")?.addEventListener("click", () => {
    openModal();
  });

  updateToggleUI();
  renderIcons();

  // Pre-warm voices and profiles in background so opening Mix is instant
  Promise.all([fetchVoices(), fetchProfiles()]).catch(() => {});
}
