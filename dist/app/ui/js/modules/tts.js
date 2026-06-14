import { state } from "./state.js";
import { fetchJSON, API_URL } from "./api.js";
import { showToast, stripHTML, renderIcons } from "./ui.js";
import { renderPage, getSentencesForPage } from "./library.js";

if (!state.audioBufferCache) state.audioBufferCache = new Map();
if (!state.inFlightRequests) state.inFlightRequests = new Map();

// --- EXECUTION LOCK ---
// Prevents the "restart over" bug by tracking the absolute latest playback request
let playNextSessionId = 0; 

export function initAudioContext() {
  if (!state.audioContext || state.audioContext.state === 'closed') {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    try {
      state.audioContext = new AudioCtx({ sampleRate: 48000 });
    } catch (e) {
      state.audioContext = new AudioCtx(); 
    }
  }
  if (state.audioContext.state === "suspended") {
    state.audioContext.resume();
  }
}

export function playAudioBuffer(audioBuffer) {
  if (state.currentAudioSource) {
    try {
      state.currentAudioSource.stop();
      state.currentAudioSource.disconnect();
    } catch (e) {}
  }

  const source = state.audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.playbackRate.value = 1.0; 
  source.connect(state.audioContext.destination);

  source.onended = async () => {
    state.currentAudioSource = null;
    state.currentSentenceIndex++;
    await playNext();  
    preCacheNextSentences();
  };

  state.currentAudioSource = source;
  source.start(0);
}

export function stopPlayback() {
  state.isPlaying = false;
  const playIcon = document.getElementById("playIcon");
  if (playIcon) {
    playIcon.setAttribute("data-lucide", "play");
    renderIcons();
  }

  if (state.currentAudioSource) {
    try {
      state.currentAudioSource.onended = null;
      state.currentAudioSource.stop();
      state.currentAudioSource.disconnect();
    } catch (e) {}
    state.currentAudioSource = null;
  }

  if (state.inFlightRequests) {
      for (const [key, req] of state.inFlightRequests.entries()) {
          req.controller.abort();
      }
      state.inFlightRequests.clear();
  }
}

async function getSynthesizedAudio(lookupKey, payload, priority = "preload") {
  if (state.audioBufferCache.has(lookupKey)) {
    return state.audioBufferCache.get(lookupKey);
  }

  if (state.inFlightRequests.has(lookupKey)) {
    const req = state.inFlightRequests.get(lookupKey);
    if (priority === "high" && req.priority === "preload") {
        req.priority = "high"; 
    }
    return await req.promise;
  }

  if (priority === "high") {
      for (const [key, req] of state.inFlightRequests.entries()) {
          if (req.priority === "preload") {
              req.controller.abort();
              state.inFlightRequests.delete(key);
          }
      }
  }

  const controller = new AbortController();
  
  const reqPromise = (async () => {
    try {
        const res = await fetch(`${API_URL}/api/synthesize`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal
        });

        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || "Synthesis failed");
        }

        const blob = await res.blob();
        initAudioContext();
        const arrayBuffer = await blob.arrayBuffer();
        const audioBuffer = await state.audioContext.decodeAudioData(arrayBuffer);
        
        state.audioBufferCache.set(lookupKey, audioBuffer);
        return audioBuffer;
    } catch (e) {
        if (e.name !== "AbortError") {
            console.error("Synthesis fetch error:", e);
        }
        throw e;
    } finally {
        state.inFlightRequests.delete(lookupKey);
    }
  })();

  state.inFlightRequests.set(lookupKey, {
      promise: reqPromise,
      controller: controller,
      priority: priority
  });

  return await reqPromise;
}

export async function playNext() {
  const targetIndex = state.currentSentenceIndex;
  playNextSessionId++; // Register a unique ID for this specific execution
  const currentSession = playNextSessionId;

  if (!state.isPlaying || !window.isEngineReady) {
    stopPlayback();
    return;
  }

  const text = state.readingSentences[state.currentSentenceIndex];
  if (!text || typeof text !== "string") {
    if (state.readingPageIndex < state.currentPages.length - 1) {
      state.readingPageIndex++;
      state.currentSentenceIndex = 0;
      state.audioBufferCache.clear(); 
      state.readingSentences = await getSentencesForPage(state.readingPageIndex);

      if (state.autoScrollEnabled) {
        state.viewPageIndex = state.readingPageIndex;
        await renderPage();
      } else if (state.viewPageIndex === state.readingPageIndex) {
        await renderPage();
      }
      await playNext();
    } else {
      stopPlayback();
    }
    return;
  }

  if (state.viewPageIndex === state.readingPageIndex) {
    state.sentenceElements.forEach(
      (el, i) => (el.className = `sentence ${i === state.currentSentenceIndex ? "active-sentence" : ""}`)
    );
    const active = state.sentenceElements[state.currentSentenceIndex];
    if (active && state.autoScrollEnabled)
      active.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const currentSentencePreview = document.getElementById("currentSentencePreview");
  if (currentSentencePreview) currentSentencePreview.textContent = stripHTML(text);

  saveProgress();

  let cleanText = stripHTML(text);
  if (text.endsWith('\n')) cleanText += '\n'; 

  const voiceSelect = document.getElementById("voiceSelect");
  const speedRange = document.getElementById("speedRange");
  const upscaleToggle = document.getElementById("upscaleAudioToggle");
  
  const useUpscaler = upscaleToggle ? upscaleToggle.checked : false;
  const lookupKey = `${state.readingPageIndex}_${targetIndex}_${voiceSelect.value}_${speedRange.value}_${useUpscaler}`;

  const payload = {
    text: cleanText,
    voice: voiceSelect.value,
    speed: parseFloat(speedRange.value),
    rules: state.rules,
    ignore_list: state.ignoreList,
    pause_settings: state.pauseSettings,
    use_upscaler: useUpscaler 
  };

  try {
    const audioBuffer = await getSynthesizedAudio(lookupKey, payload, "high");

    // SAFETY CHECK: Abort playback if the user clicked next/toggled again while we were fetching
    if (!state.isPlaying || state.currentSentenceIndex !== targetIndex || currentSession !== playNextSessionId) {
      return; 
    }

    playAudioBuffer(audioBuffer);
  } catch (e) {
    if (e.name !== "AbortError") {
        console.error("Synthesis error:", e);
        showToast(e.message);
        stopPlayback();
    }
  }
}

export function togglePlayback() {
  const playIcon = document.getElementById("playIcon");
  if (state.isPlaying) {
    stopPlayback();
  } else {
    initAudioContext();
    state.isPlaying = true;
    if (playIcon) {
      playIcon.setAttribute("data-lucide", "pause");
      renderIcons();
    }
    playNext();
  }
}

export async function jumpToSentence(i) {
  if (state.currentAudioSource) {
    try {
      state.currentAudioSource.onended = null;
      state.currentAudioSource.stop();
      state.currentAudioSource.disconnect();
    } catch (e) {}
    state.currentAudioSource = null;
  }

  if (state.inFlightRequests) {
      for (const [key, req] of state.inFlightRequests.entries()) {
          req.controller.abort();
      }
      state.inFlightRequests.clear();
  }

  if (state.jumpTimer) {
    clearTimeout(state.jumpTimer);
    state.jumpTimer = null;
  }

  state.currentSentenceIndex = i;
  await renderPage(); 

  if (!state.isPlaying) {
    initAudioContext();
    state.isPlaying = true;
    const playIcon = document.getElementById("playIcon");
    if (playIcon) {
      playIcon.setAttribute("data-lucide", "pause");
      renderIcons();
    }
  }

  state.jumpTimer = setTimeout(() => {
    state.jumpTimer = null;
    playNext();
  }, 400); 
}

export async function saveProgress() {
  if (state.currentDoc) {
    const statusEl = document.getElementById("bookmarkStatus");
    if (statusEl) {
      statusEl.classList.remove("opacity-0");
      statusEl.classList.add("animate-pulse");
      setTimeout(() => statusEl.classList.remove("animate-pulse"), 1000);
    }
    try {
      await fetchJSON(`/api/library`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...state.currentDoc,
          currentPage: state.readingPageIndex,
          lastSentenceIndex: state.currentSentenceIndex,
          lastAccessed: Date.now(),
        }),
      });
    } catch (e) {}
  }
}

let isPreloading = false;
export async function preCacheNextSentences() {
  const MAX_FORWARD = 3; 
  const MAX_CACHE_SIZE = 15; 

  if (!state.audioContext || isPreloading) return;
  isPreloading = true;

  try {
    const voiceSelect = document.getElementById("voiceSelect");
    const speedRange = document.getElementById("speedRange");
    const upscaleToggle = document.getElementById("upscaleAudioToggle"); 
    
    const useUpscaler = upscaleToggle ? upscaleToggle.checked : false;

    while (state.audioBufferCache.size > MAX_CACHE_SIZE) {
      const oldestKey = state.audioBufferCache.keys().next().value;
      state.audioBufferCache.delete(oldestKey);
    }

    let targetPageIndex = state.readingPageIndex;
    let targetSentenceIndex = state.currentSentenceIndex;
    let targetSentences = state.readingSentences;

    for (let let_i = 1; let_i <= MAX_FORWARD; let_i++) {
      targetSentenceIndex++; 

      if (Math.abs(state.currentSentenceIndex - targetSentenceIndex) > MAX_FORWARD + 2) {
          break;
      }

      if (targetSentenceIndex >= targetSentences.length) {
          targetPageIndex++;
          targetSentenceIndex = 0; 
          try {
            targetSentences = await getSentencesForPage(targetPageIndex);
            if (!targetSentences || targetSentences.length === 0) break;
          } catch (err) { break; }
      }

      const nextText = targetSentences[targetSentenceIndex];
      if (!nextText || typeof nextText !== "string") continue;

      let cleanText = stripHTML(nextText).replace(/[\u200B-\u200D\uFEFF]/g, '').trim();
      if (nextText.endsWith('\n')) cleanText += '\n'; 
      if (cleanText.trim().length < 2) continue;

      const cacheKey = `${targetPageIndex}_${targetSentenceIndex}_${voiceSelect.value}_${speedRange.value}_${useUpscaler}`;
      
      const payload = {
        text: cleanText,
        voice: voiceSelect.value,
        speed: parseFloat(speedRange.value),
        rules: state.rules,
        ignore_list: state.ignoreList,
        pause_settings: state.pauseSettings,
        use_upscaler: useUpscaler 
      };

      try {
        await getSynthesizedAudio(cacheKey, payload, "preload");
      } catch (e) {
        if (e.name === "AbortError") break; 
      }
    }
  } finally {
    isPreloading = false; 
  }
}

export async function loadVoices() {
  const voiceSelect = document.getElementById("voiceSelect");
  try {
    const currentVoice = voiceSelect.value;
    const data = await fetchJSON(`/api/voices/available`);
    const categories = data.categories || {};
    voiceSelect.innerHTML = "";
    
    const sortedKeys = Object.keys(categories).sort((a, b) => {
      if (a.startsWith("en") && !b.startsWith("en")) return -1;
      if (!a.startsWith("en") && b.startsWith("en")) return 1;
      return a.localeCompare(b);
    });

    sortedKeys.forEach((langCode) => {
      const category = categories[langCode];
      const group = document.createElement("optgroup");
      group.label = state.translations?.languages?.[langCode] || category.label;
      
      category.voices.forEach((voice) => {
        const voiceId = voice.id.toLowerCase();
        const cleanId = voiceId.includes("_") ? voiceId.split("_").pop() : voiceId;
        if (["alpha", "beta", "omega", "psi"].includes(cleanId)) return;

        const option = document.createElement("option");
        option.value = voice.id;

        let label = voice.name;
        const attrs = state.translations?.voice_attributes || {};

        const getAttrs = (vid) => {
          if (vid.startsWith("af_")) return [attrs.american, attrs.female];
          if (vid.startsWith("am_")) return [attrs.american, attrs.male];
          if (vid.startsWith("bf_")) return [attrs.british, attrs.female];
          if (vid.startsWith("bm_")) return [attrs.british, attrs.male];
          if (vid.startsWith("ff_")) return [attrs.french, attrs.female];
          if (vid.startsWith("jf_")) return [attrs.japanese, attrs.female];
          if (vid.startsWith("jm_")) return [attrs.japanese, attrs.male];
          if (vid.startsWith("ef_")) return [attrs.spanish, attrs.female];
          if (vid.startsWith("em_")) return [attrs.spanish, attrs.male];
          if (vid.startsWith("zf_")) return [attrs.chinese, attrs.female];
          if (vid.startsWith("zm_")) return [attrs.chinese, attrs.male];
          if (vid.startsWith("if_")) return [attrs.italian, attrs.female];
          if (vid.startsWith("im_")) return [attrs.italian, attrs.male];
          if (vid.startsWith("pf_")) return [attrs.portuguese, attrs.female];
          if (vid.startsWith("pm_")) return [attrs.portuguese, attrs.male];
          if (vid === "santa") return [attrs.spanish, attrs.male];
          return [];
        };

        const [region, gender] = getAttrs(voice.id);
        if (region && gender) label = `${voice.name} (${region} ${gender})`;
        else label = state.translations?.voices?.[voice.id] || voice.name;

        option.textContent = label;
        group.appendChild(option);
      });
      voiceSelect.appendChild(group);
    });

    if (currentVoice) {
      const exists = Array.from(voiceSelect.options).some(opt => opt.value === currentVoice);
      if (exists) voiceSelect.value = currentVoice;
    }
    if (voiceSelect.options.length === 0) {
      const option = document.createElement("option");
      option.textContent = "No voices found (Download Engine)";
      option.disabled = true;
      voiceSelect.appendChild(option);
    }
    return true;
  } catch (error) {
    voiceSelect.innerHTML = "<option disabled>Error loading voices</option>";
    return false;
  }
}

// --- PIPELINE FLUSH LISTENER ---
document.addEventListener("DOMContentLoaded", () => {
    const checkToggle = setInterval(() => {
        const upscaleToggle = document.getElementById("upscaleAudioToggle");
        if (upscaleToggle) {
            clearInterval(checkToggle);
            upscaleToggle.addEventListener("change", async () => {
                
                // 1. Lock the UI toggle to prevent spamming and crashing the server queue
                upscaleToggle.disabled = true;

                // 2. Give UI feedback for the loading process
                if (upscaleToggle.checked) {
                    showToast("Upscaler Active: Processing High-Res Audio...");
                } else {
                    showToast("Upscaler Disabled: Switching to Standard Audio...");
                }

                state.audioBufferCache.clear();
                
                if (state.inFlightRequests) {
                    for (const [key, req] of state.inFlightRequests.entries()) {
                        req.controller.abort();
                    }
                    state.inFlightRequests.clear();
                }
                
                if (state.audioContext) {
                    try {
                        if (state.currentAudioSource) {
                            state.currentAudioSource.onended = null;
                            state.currentAudioSource.stop();
                            state.currentAudioSource.disconnect();
                            state.currentAudioSource = null;
                        }
                        await state.audioContext.close();
                    } catch (e) {}
                    state.audioContext = null;
                }
                
                initAudioContext();

                // 3. Request the new audio file
                if (state.isPlaying) {
                    await playNext();
                }

                // 4. Unlock the UI toggle now that processing is stable
                upscaleToggle.disabled = false;
            });
        }
    }, 500);
});