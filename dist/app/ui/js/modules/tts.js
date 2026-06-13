import { state } from "./state.js";
import { fetchJSON, fetchBlob, API_URL } from "./api.js";
import { showToast, stripHTML, renderIcons } from "./ui.js";
import { renderPage, getSentencesForPage } from "./library.js";

export function initAudioContext() {
  if (!state.audioContext) {
    state.audioContext = new (
      window.AudioContext || window.webkitAudioContext
    )();
    console.log("[WebAudio] AudioContext initialized");
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

  // Create new source node
  const source = state.audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(state.audioContext.destination);

  source.onended = async () => {
    state.currentAudioSource = null;
    state.currentSentenceIndex++;
    console.log(`Sentence ended, moving to ${state.currentSentenceIndex}`);
    await playNext();  // Must settle state before pre-caching (page transitions update readingSentences async)
    preCacheNextSentences();
  };

  state.currentAudioSource = source;
  source.start(0);
  console.log(`[WebAudio] Playing buffer: ${audioBuffer.duration.toFixed(2)}s`);
}

export function stopPlayback() {
  state.isPlaying = false;
  // Update UI directly for speed
  const playIcon = document.getElementById("playIcon");
  if (playIcon) {
    playIcon.setAttribute("data-lucide", "play");
    renderIcons();
  }

  if (state.currentAudioSource) {
    try {
      state.currentAudioSource.onended = null; // Prevent triggering 'playNext' on stop
      state.currentAudioSource.stop();
      state.currentAudioSource.disconnect();
    } catch (e) {}
    state.currentAudioSource = null;
  }
}

export async function playNext() {
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
      (el, i) =>
        (el.className = `sentence ${i === state.currentSentenceIndex ? "active-sentence" : ""}`),
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
  console.log(`Synthesizing sentence ${state.currentSentenceIndex}: "${cleanText.substring(0, 30)}..."`);

  const voiceSelect = document.getElementById("voiceSelect");
  const speedRange = document.getElementById("speedRange");

  const lookupKey = `${state.readingPageIndex}_${targetIndex}_${voiceSelect.value}_${speedRange.value}`;

  if (state.audioBufferCache.has(lookupKey)) {
    console.log(`[WebAudio] CACHE HIT - Playing cached buffer instantly`);
    playAudioBuffer(state.audioBufferCache.get(lookupKey));
    return;
  }

  // ==========================================
  // SURGICAL FIX: Prevent GPU Double-Tap 
  // ==========================================
  if (!state.fetchingKeys) state.fetchingKeys = new Set();
  if (state.fetchingKeys.has(lookupKey)) {
    console.log(`[TTS] Preloader is currently working on this sentence. Waiting 500ms...`);
    setTimeout(() => playNext(), 500); 
    return;
  }
  
  state.fetchingKeys.add(lookupKey); 

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
        engine: state.ttsEngine || "kokoro"
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Synthesis failed");
    }

    const blob = await res.blob();
    initAudioContext();

    const arrayBuffer = await blob.arrayBuffer();

    if (!state.isPlaying || state.currentSentenceIndex !== targetIndex) {
      console.log(`[TTS] Discarding synthesis result - Index mismatch`);
      return;
    }

    const audioBuffer = await state.audioContext.decodeAudioData(arrayBuffer);
    state.audioBufferCache.set(lookupKey, audioBuffer);

    if (state.audioBufferCache.size > state.MAX_AUDIO_CACHE) {
      const firstKey = state.audioBufferCache.keys().next().value;
      state.audioBufferCache.delete(firstKey);
    }

    playAudioBuffer(audioBuffer);
  } catch (e) {
    console.error("Synthesis error:", e);
    showToast(e.message);
    stopPlayback();
  } finally {
    // ALWAYS unlock when finished or failed
    state.fetchingKeys.delete(lookupKey);
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

  // 2. Clear existing jump timer to prevent overlapping jumps
  if (state.jumpTimer) {
    clearTimeout(state.jumpTimer);
    state.jumpTimer = null;
  }

  state.currentSentenceIndex = i;
  await renderPage(); // Update UI highlight and content

  // Ensure state reflects that we are intended to be playing
  if (!state.isPlaying) {
    initAudioContext();
    state.isPlaying = true;
    const playIcon = document.getElementById("playIcon");
    if (playIcon) {
      playIcon.setAttribute("data-lucide", "pause");
      renderIcons();
    }
  }

  // 3. Buffer for 2 seconds then start playing 
  console.log(`[TTS] Buffering 2 seconds for jump to index ${i}...`);
  state.jumpTimer = setTimeout(() => {
    state.jumpTimer = null;
    playNext();
  }, 1000);
}

export async function saveProgress() {
  if (state.currentDoc) {
    // Optimistic UI
    const statusEl = document.getElementById("bookmarkStatus");
    if (statusEl) {
      statusEl.classList.remove("opacity-0");
      statusEl.classList.add("animate-pulse");
      setTimeout(() => {
        statusEl.classList.remove("animate-pulse");
        // Optional: Fade out after 2s if desired, or keep it visible as "Last saved..."
      }, 1000);
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
    } catch (e) {
      console.error("Save progress failed", e);
    }
  }
}

// Lock to prevent multiple network stampedes
let isPreloading = false;

export async function preCacheNextSentences() {
  const MAX_FORWARD = 5; // How many sentences to look ahead
  const MAX_CACHE_SIZE = 10; // 5 forward + 1 playing + 4 backward history

  if (!state.audioContext || isPreloading) return;
  isPreloading = true;

  try {
    const voiceSelect = document.getElementById("voiceSelect");
    const speedRange = document.getElementById("speedRange");

    // ==========================================
    // 1. GARBAGE COLLECTION (The 3-Sentence Rewind)
    // ==========================================
    // Because JS Maps remember insertion order, the first item is always the oldest.
    // If we have more than 10 items, we just delete the oldest ones. 
    // This perfectly preserves your backwards history without doing complex page math!
    while (state.audioBufferCache.size > MAX_CACHE_SIZE) {
      const oldestKey = state.audioBufferCache.keys().next().value;
      state.audioBufferCache.delete(oldestKey);
    }

    // ==========================================
    // 2. FORWARD PRELOADER (Cross-Page Supported)
    // ==========================================
    let targetPageIndex = state.readingPageIndex;
    let targetSentenceIndex = state.currentSentenceIndex;
    let targetSentences = state.readingSentences;

    for (let i = 1; i <= MAX_FORWARD; i++) {
      targetSentenceIndex++; 

      // Safely cross the page boundary if needed
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
          break; // End of the book
        }
      }

      const nextText = targetSentences[targetSentenceIndex];
      if (!nextText || typeof nextText !== "string") continue;

      // Clean the text and skip empty lines to protect the GPU
      let cleanText = stripHTML(nextText).replace(/[\u200B-\u200D\uFEFF]/g, '').trim();
      if (nextText.endsWith('\n')) cleanText += '\n'; // <-- Keep it for the backend
  
      if (cleanText.trim().length < 2) continue;

      const cacheKey = `${targetPageIndex}_${targetSentenceIndex}_${voiceSelect.value}_${speedRange.value}`;

      // If we already have it in RAM, skip it!
      if (state.audioBufferCache.has(cacheKey)) continue;

      // ==========================================
      // AWAIT FETCH: Wait for the GPU to finish before asking for the next one
      // ==========================================
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
          engine: state.ttsEngine
        }),
      });

      if (res.ok) {
        const blob = await res.blob();
        const arrayBuffer = await blob.arrayBuffer();
        const audioBuffer = await state.audioContext.decodeAudioData(arrayBuffer);
        state.audioBufferCache.set(cacheKey, audioBuffer);
        console.log(`[Sliding Window] Ready: Page ${targetPageIndex}, Sentence ${targetSentenceIndex}`);
      }
    }
  } catch (error) {
    console.error("[Preloader] Error:", error);
  } finally {
    isPreloading = false; // Always unlock when finished!
  }
}

export async function loadVoices() {
  try {
    // Delegate all UI and Voice loading to the master function in app.js
    // This perfectly supports Kokoro, F5, and Fish-TTS cloned voices!
    if (typeof window.refreshVoiceDropdown === 'function') {
      await window.refreshVoiceDropdown();
      return true;
    }
    return false;
  } catch (error) {
    console.error("Error loading voices:", error);
    const voiceSelect = document.getElementById("voiceSelect");
    if (voiceSelect) {
        voiceSelect.innerHTML = "<option disabled>Error loading voices</option>";
    }
    return false;
  }
}