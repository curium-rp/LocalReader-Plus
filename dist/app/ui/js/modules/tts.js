import { state } from "./state.js";
import { fetchJSON, fetchBlob, API_URL } from "./api.js";
import { showToast, stripHTML, renderIcons } from "./ui.js";
import { renderPage, getSentencesForPage } from "./library.js";

let saveProgressTimeout = null;
let currentSynthesisId = 0; // 🌟 ADDED: Bulletproof lock to prevent voice overlap

export function initAudioContext() {
  if (!state.audioContext) {
    state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    console.log("[WebAudio] AudioContext initialized");
  }
  if (state.audioContext.state === "suspended") {
    state.audioContext.resume();
  }
}

export function playAudioBuffer(audioBuffer, bType = "N", displayChars = "") {
  if (state.currentAudioSource) {
    try {
      state.currentAudioSource.stop();
      state.currentAudioSource.disconnect();
    } catch (e) {}
  }

  const source = state.audioContext.createBufferSource();
  source.buffer = audioBuffer;
  
  // 🌟 THE BRILLIANT FIX: Use a GainNode to mute pure pauses locally.
  const gainNode = state.audioContext.createGain();
  const hasNarrativeText = /[a-zA-Z0-9\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\uFF00-\uFF9F\u4E00-\u9FAF\u3400-\u4DBF]/.test(displayChars);
  
  if ((bType === "Img" || bType === "S") && !hasNarrativeText) {
      gainNode.gain.value = 0.001; // Effectively muted
  } else {
      gainNode.gain.value = 1.0;
  }

  source.connect(gainNode);
  gainNode.connect(state.audioContext.destination);

  source.onended = async () => {
    state.currentAudioSource = null;
    if (state.jumpTimer) {
        clearInterval(state.jumpTimer);
        state.jumpTimer = null;
    }
    
    // Clean up UI instantly when audio finishes
    const preview = document.getElementById("currentSentencePreview");
    if (preview && displayChars) preview.textContent = displayChars;
    
    const monitor = document.getElementById("monitorSentenceText");
    if (monitor && displayChars) monitor.textContent = displayChars;

    state.currentSentenceIndex++;
    console.log(`Sentence ended, moving to ${state.currentSentenceIndex}`);
    await playNext();  
    preCacheNextSentences();
  };

  state.currentAudioSource = source;
  source.start(0);

  // --- DYNAMIC AUDIO-SYNCED VISUAL TIMER ---
  const currentSentencePreview = document.getElementById("currentSentencePreview");
  if (bType === "Img" || bType === "S") {
      const durationMs = audioBuffer.duration * 1000;
      const endTime = Date.now() + durationMs;
      
      if (state.jumpTimer) clearInterval(state.jumpTimer);
      
      state.jumpTimer = setInterval(() => {
          if (!state.isPlaying) return clearInterval(state.jumpTimer);
          const remaining = endTime - Date.now();
          const monitorText = document.getElementById("monitorSentenceText");
          
          if (remaining > 0) {
              const textWithTime = `${displayChars} (${Math.ceil(remaining / 1000)}s)`;
              if (currentSentencePreview) currentSentencePreview.textContent = textWithTime;
              if (monitorText) monitorText.textContent = textWithTime;
          } else {
              clearInterval(state.jumpTimer);
              if (currentSentencePreview) currentSentencePreview.textContent = displayChars;
              if (monitorText) monitorText.textContent = displayChars;
          }
      }, 100);
      
      const initText = `${displayChars} (${Math.ceil(durationMs / 1000)}s)`;
      if (currentSentencePreview) currentSentencePreview.textContent = initText;
      
      const monitorText = document.getElementById("monitorSentenceText");
      if (monitorText) monitorText.textContent = initText;
  } else {
      if (currentSentencePreview) currentSentencePreview.textContent = displayChars;
      const monitorText = document.getElementById("monitorSentenceText");
      if (monitorText) monitorText.textContent = displayChars;
  }

  console.log(`[WebAudio] Playing buffer: ${audioBuffer.duration.toFixed(2)}s | Type: ${bType} | Muted: ${gainNode.gain.value < 1}`);
}

export function stopPlayback() {
  state.isPlaying = false;
  currentSynthesisId++; // 🌟 Instantly invalidate any pending server downloads

  const playIcon = document.getElementById("playIcon");
  if (playIcon) {
    playIcon.setAttribute("data-lucide", "play");
    renderIcons();
  }

  if (state.jumpTimer) {
    clearInterval(state.jumpTimer); 
    state.jumpTimer = null;
  }

  if (state.currentAudioSource) {
    try {
      state.currentAudioSource.onended = null; 
      state.currentAudioSource.stop();
      state.currentAudioSource.disconnect();
    } catch (e) {}
    state.currentAudioSource = null;
  }
}

export async function playNext() {
  currentSynthesisId++; // 🌟 Generate unique lock ID for this synthesis request
  const mySynthesisId = currentSynthesisId;

  // 🌟 SELF-RECOVERING ONE-TIME CHECK (Session Memory)
  // Ensures the language switcher isn't interrupted on subsequent plays or jumps
  if (!window.hasCheckedVoiceMismatch) {
    window.hasCheckedVoiceMismatch = true;
    
    // 50ms micro-delay to align with the first payload timing
    await new Promise(resolve => setTimeout(resolve, 50)); 
    
    try {
      const currentSettings = await fetchJSON(`/api/settings`);
      const vs = document.getElementById("voiceSelect");
      
      // If a mismatch exists between the DOM and the saved truth, snap it back
      if (vs && currentSettings.voice_id && vs.value !== currentSettings.voice_id) {
        vs.value = currentSettings.voice_id;
      }
    } catch (err) {
      console.error("Voice mismatch recovery failed", err);
    }
  }

  const targetIndex = state.currentSentenceIndex;
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
    if (active && state.autoScrollEnabled) active.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  saveProgress();

  let bType = "N";
  const currentEl = state.sentenceElements ? state.sentenceElements[state.currentSentenceIndex] : null;

  if (currentEl) {
      const hMatch = currentEl.closest('h1, h2, h3, h4, h5, h6');
      if (hMatch) bType = hMatch.tagName.toUpperCase(); 
      else if (currentEl.tagName.toLowerCase() === 'img' || currentEl.querySelector('img, svg')) bType = "Img";
      else if (currentEl.tagName.toLowerCase() === 's' || currentEl.closest('.scene-break')) bType = "S";
  } else {
      const hMatch = text.match(/<h([1-6])/i);
      if (hMatch) bType = "H" + hMatch[1];
      else if (/<img|<svg/i.test(text) || /\[IMAGE_/i.test(text)) bType = "Img";
      else if (/<s\b/i.test(text) || /class="scene-break"/i.test(text)) bType = "S";
  }

  let cleanText = stripHTML(text);
  if (text.endsWith('\n')) cleanText += '\n'; 

  if (bType === "Img" && cleanText.trim() === "") cleanText = "Image.";
  if (bType === "S" && cleanText.trim() === "") cleanText = "•••";

  let displayChars = cleanText.trim();
  if (bType === "Img") {
      displayChars = "🖼️ [Viewing Image]";
  } else if (bType === "S" && currentEl) {
      displayChars = currentEl.textContent.trim() || cleanText.trim() || "•••";
  }

  const currentSentencePreview = document.getElementById("currentSentencePreview");
  if (currentSentencePreview) currentSentencePreview.textContent = `⏳ Loading...`;

  console.log(`Synthesizing sentence ${state.currentSentenceIndex}: "${cleanText.substring(0, 30)}..." | Type: ${bType}`);

  // 🌟  IMAGE BYPASS 
  // Generate a silent buffer locally for Images and empty Scene Breaks.
  // This prevents the TTS API from crashing on punctuation or "Image." strings
  // which previously caused `stopPlayback()` to trigger and permanently stick the player!
  if (bType === "Img" || (bType === "S" && cleanText.trim() === "•••")) {
      initAudioContext();
      const durationSeconds = bType === "Img" ? 1.5 : 1.0; // 1.5s pause for images, 1s for breaks
      const sampleRate = state.audioContext.sampleRate || 44100;
      const silentBuffer = state.audioContext.createBuffer(1, Math.floor(sampleRate * durationSeconds), sampleRate);
      
      if (!state.isPlaying || currentSynthesisId !== mySynthesisId) return;
      
      playAudioBuffer(silentBuffer, bType, displayChars);
      return; // STOP execution here. Do NOT send to backend!
  }

  const voiceSelect = document.getElementById("voiceSelect");
  const speedRange = document.getElementById("speedRange");
  const lookupKey = `${state.readingPageIndex}_${targetIndex}_${voiceSelect.value}_${speedRange.value}`;
  
  if (state.audioBufferCache.has(lookupKey)) {
    if (!state.isPlaying || currentSynthesisId !== mySynthesisId) return; // 🌟 Final check before cache play
    playAudioBuffer(state.audioBufferCache.get(lookupKey), bType, displayChars);
    return;
  }

  try {
    const res = await fetch(`${API_URL}/api/synthesize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: cleanText,
        voice: voiceSelect.value,
        speed: parseFloat(speedRange.value),
        rules: state.rules,
        ignore_list: state.ignoreList,
        pause_settings: state.pauseSettings,
        behavior_settings: state.behaviorSettings,
        behavior_type: bType
      }),
    });

    if (!res.ok) throw new Error("Synthesis failed");

    const blob = await res.blob();
    initAudioContext();
    const arrayBuffer = await blob.arrayBuffer();

    // 🌟 THE BULLETPROOF CHECK: Abort if user jumped or paused while waiting for network
    if (!state.isPlaying || currentSynthesisId !== mySynthesisId) return;

    const audioBuffer = await state.audioContext.decodeAudioData(arrayBuffer);
    state.audioBufferCache.set(lookupKey, audioBuffer);
    
    // 🌟 Double-check after decoding (since decodeAudioData is asynchronous)
    if (!state.isPlaying || currentSynthesisId !== mySynthesisId) return;

    playAudioBuffer(audioBuffer, bType, displayChars);
  } catch (e) {
    if (currentSynthesisId !== mySynthesisId) return; 
    
    console.error("Synthesis error:", e);
    showToast(e.message);
    stopPlayback();
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
  // 1. Stop current audio immediately and kill its listeners
  if (state.currentAudioSource) {
    try {
      state.currentAudioSource.onended = null;
      state.currentAudioSource.stop();
      state.currentAudioSource.disconnect();
    } catch (e) {}
    state.currentAudioSource = null;
  }

  // 🌟 FIX: Increment the synthesis ID to instantly orphan any pending slow server downloads
  currentSynthesisId++;

  // 2. Clear existing visual timers
  if (state.jumpTimer) {
    clearInterval(state.jumpTimer);
    state.jumpTimer = null;
  }

  state.currentSentenceIndex = i;
  
  if (state.sentenceElements && state.sentenceElements[i]) {
    const targetEl = state.sentenceElements[i];
    if (state.currentDoc) {
        // 🌟 THE MISSING LINE FIX: Always update the mathematical fallback index!
        // This guarantees renderPage() knows exactly where you are, even if the element has no ID.
        state.currentDoc.lastSentenceIndex = i;

        // 🌟 THE RUBBER-BAND FIX: 
        if (targetEl.hasAttribute('id')) {
            state.currentDoc.lastSentenceId = targetEl.getAttribute('id');
        } else {
            state.currentDoc.lastSentenceId = null; 
        }
    }
  }

  await renderPage(); // Update UI highlight and content instantly

  // 🌟 UI FIX: Force the Play/Pause UI to update and sync the "Active/Blue" state unconditionally
  initAudioContext();
  state.isPlaying = true;
  const playIcon = document.getElementById("playIcon");
  if (playIcon) {
    playIcon.setAttribute("data-lucide", "pause");
    // Force DOM to recognize the state change so the blue CSS applies immediately
    void playIcon.offsetWidth; 
    renderIcons();
  }

  console.log(`[TTS] Instant jump to index ${i}...`);
  playNext();
}

export async function saveProgress() {
  if (!state.currentDoc) return;

  const currentEl = state.sentenceElements ? state.sentenceElements[state.currentSentenceIndex] : null;
  let sentenceIdString = null;
  if (currentEl && currentEl.hasAttribute('id')) {
      sentenceIdString = currentEl.getAttribute('id');
  }

  state.currentDoc.currentPage = state.readingPageIndex;
  state.currentDoc.lastSentenceId = sentenceIdString;
  state.currentDoc.lastSentenceIndex = state.currentSentenceIndex;

  const statusEl = document.getElementById("bookmarkStatus");
  if (statusEl) {
    statusEl.classList.remove("opacity-0", "animate-pulse");
    void statusEl.offsetWidth; 
    statusEl.classList.add("animate-pulse");
    setTimeout(() => {
      statusEl.classList.remove("animate-pulse");
    }, 1000);
  }

  if (saveProgressTimeout) {
      clearTimeout(saveProgressTimeout);
  }

  saveProgressTimeout = setTimeout(async () => {
      try {
        await fetchJSON(`/api/library/progress/${state.currentDoc.id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            currentPage: state.currentDoc.currentPage,
            lastSentenceId: state.currentDoc.lastSentenceId,     
            lastSentenceIndex: state.currentDoc.lastSentenceIndex, 
            lastAccessed: Date.now(),
          }),
        });
        console.log(`[Checkpoint] Saved to disk. ID: ${sentenceIdString} | Fallback Index: ${state.currentDoc.lastSentenceIndex}`);
      } catch (e) {
        console.error("[Checkpoint] Save progress mapping failed", e);
      }
  }, 2000); 
}

let isPreloading = false;

export async function preCacheNextSentences() {
  const MAX_FORWARD = 5; 
  const MAX_CACHE_SIZE = 10; 

  if (!state.audioContext || isPreloading) return;
  isPreloading = true;

  try {
    const voiceSelect = document.getElementById("voiceSelect");
    const speedRange = document.getElementById("speedRange");

    while (state.audioBufferCache.size > MAX_CACHE_SIZE) {
      const oldestKey = state.audioBufferCache.keys().next().value;
      state.audioBufferCache.delete(oldestKey);
    }

    let targetPageIndex = state.readingPageIndex;
    let targetSentenceIndex = state.currentSentenceIndex;
    let targetSentences = state.readingSentences;

    for (let i = 1; i <= MAX_FORWARD; i++) {
      targetSentenceIndex++; 

      if (targetSentenceIndex >= targetSentences.length) {
        if (targetPageIndex < state.currentPages.length - 1) {
          targetPageIndex++;
          targetSentenceIndex = 0; 
          try {
            targetSentences = await getSentencesForPage(targetPageIndex);
            if (!targetSentences || targetSentences.length === 0) break;
          } catch (err) {
            break;
          }
        } else {
          break; 
        }
      }

      const nextText = targetSentences[targetSentenceIndex];
      if (!nextText || typeof nextText !== "string") continue;

      let bType = "N";
      let nextEl = null;
      
      if (targetPageIndex === state.readingPageIndex && state.sentenceElements) {
          nextEl = state.sentenceElements[targetSentenceIndex];
      }

      if (nextEl) {
          const hMatch = nextEl.closest('h1, h2, h3, h4, h5, h6');
          if (hMatch) bType = hMatch.tagName.toUpperCase();
          else if (nextEl.tagName.toLowerCase() === 'img' || nextEl.querySelector('img, svg')) bType = "Img";
          else if (nextEl.tagName.toLowerCase() === 's' || nextEl.closest('.scene-break')) bType = "S";
      } else {
          const hMatch = nextText.match(/<h([1-6])/i);
          if (hMatch) bType = "H" + hMatch[1];
          else if (/<img|<svg/i.test(nextText)) bType = "Img";
          else if (/<s\b/i.test(nextText) || /class="scene-break"/i.test(nextText)) bType = "S";
      }

      let cleanText = stripHTML(nextText).replace(/[\u200B-\u200D\uFEFF]/g, '').trim();
      if (nextText.endsWith('\n')) cleanText += '\n'; 
  
      const hasNarrativeText = /[a-zA-Z0-9\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\uFF00-\uFF9F\u4E00-\u9FAF\u3400-\u4DBF]/.test(cleanText);
      
      if (bType === "N" && cleanText.trim().length < 2 && !hasNarrativeText) continue;
      
      // 🌟 SKIP network preloading for local elements to prevent backend errors!
      if (bType === "Img" || (bType === "S" && cleanText.trim() === "•••")) continue;

      const cacheKey = `${targetPageIndex}_${targetSentenceIndex}_${voiceSelect.value}_${speedRange.value}`;
      if (state.audioBufferCache.has(cacheKey)) continue;

      const res = await fetch(`/api/synthesize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: cleanText,
          voice: voiceSelect.value,
          speed: parseFloat(speedRange.value),
          rules: state.rules,
          ignore_list: state.ignoreList,
          pause_settings: state.pauseSettings,
          behavior_settings: state.behaviorSettings,
          behavior_type: bType
        }),
      });

      if (res.ok) {
        const blob = await res.blob();
        const arrayBuffer = await blob.arrayBuffer();
        const audioBuffer = await state.audioContext.decodeAudioData(arrayBuffer);
        state.audioBufferCache.set(cacheKey, audioBuffer);
      }
    }
  } catch (error) {
    console.error("[Preloader] Error:", error);
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
        if (region && gender) {
          label = `${voice.name} (${region} ${gender})`;
        } else {
          label = state.translations?.voices?.[voice.id] || voice.name;
        }

        option.textContent = label;
        group.appendChild(option);
      });
      voiceSelect.appendChild(group);
    });

    if (currentVoice) {
      const exists = Array.from(voiceSelect.options).some((opt) => opt.value === currentVoice);
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
    console.error("Error loading voices:", error);
    voiceSelect.innerHTML = "<option disabled>Error loading voices</option>";
    return false;
  }
}