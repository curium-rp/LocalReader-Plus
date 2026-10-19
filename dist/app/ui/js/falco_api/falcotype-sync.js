import { state } from "../modules/state.js";
import { getSentencesForPage, getReaderSpeechPage, readerChapterScopes, getReaderPageContent, getCurrentReaderElement, readerUnitKind, isReaderTextBlank,
    renderPage, clearActiveSentenceHighlights } from "../modules/library.js";
import { stopPlayback, startPlayback, pauseSyncPlayback, resumeSyncPlayback, prepareSyncPlayback, preCacheNextSentences, clearSyncPrecache, saveProgress } from "../modules/tts.js";
import { updateProgressDisplay } from "../modules/progress.js";
import { syncState, isSyncActive, invalidateSyncSelection, trackSyncSelection, setSyncSession, clearTimeCutAudio, recordSyncAudio, suppressSyncAudio } from "./falcotype-sync-state.js";

let renderedPage = null, renderedElements = null;
let practiceTimer = null, automatic = null, expiredPractice = null;
let mediaWait = null;
let chunkPaintToken = null, chunkPaintKey = "";

function displayPracticeChunk(chunk = null) {
    syncState.chunk = chunk;
    const page = getReaderPageContent();
    const matched = chunk && page && chunk.book_id === String(page.bookId) && chunk.page_index === page.pageIndex &&
        chunk.page_revision === page.revision && chunk.start <= state.currentSentenceIndex && state.currentSentenceIndex < chunk.end;
    if (!matched) syncState.chunk = null;
    const valid = matched && state.viewPageIndex === page.pageIndex;
    const root = document.getElementById("textContent"), key = valid ? JSON.stringify(chunk) : "";
    if (chunkPaintToken === root?.firstChild && chunkPaintKey === key) return;
    chunkPaintToken = root?.firstChild; chunkPaintKey = key;
    for (const element of document.querySelectorAll("#textContent [data-falco-unit]")) {
        const index = Number(element.getAttribute("data-falco-unit"));
        element.classList.toggle("falcotype-chunk-unit", !!valid && index >= chunk.start && index < chunk.end);
    }
}

export function holdPracticeMedia() { if (mediaWait) mediaWait.active = false; }

async function continueMedia() {
    const practice = syncState.practiceStatus, element = getCurrentReaderElement();
    if (!isSyncActive() || syncState.advancement !== "status" || !element || !["image", "scene"].includes(readerUnitKind(element))) {
        mediaWait = null; syncState.mediaWaiting = false; syncState.mediaRemaining = 0; return false;
    }
    const now = performance.now();
    if (!mediaWait || mediaWait.session !== syncState.sessionId || mediaWait.revision !== syncState.selectionRevision) {
        const kind = readerUnitKind(element) === "image" ? "Img" : "S";
        const configured = state.behaviorSettings?.[kind] ?? (kind === "Img" ? 3000 : 1000);
        mediaWait = { session: syncState.sessionId, revision: syncState.selectionRevision,
            remaining: Number.isFinite(configured) ? Math.max(0, configured) : 0, last: now, active: false, attempt: null };
    }
    const valid = !!practice && practice.selection_revision === syncState.selectionRevision && practice.media_waiting &&
        practice.run_state === "running" && !syncState.userPaused && !syncState.transitioning && !syncState.bookEnded && !automatic;
    const elapsed = now - mediaWait.last;
    if (valid && mediaWait.active) mediaWait.remaining = Math.max(0, mediaWait.remaining - elapsed);
    mediaWait.last = now; mediaWait.active = valid;
    syncState.mediaWaiting = valid; syncState.mediaRemaining = mediaWait.remaining;
    if (!valid || (practice.media_enabled && mediaWait.remaining > 0) || mediaWait.attempt === practice.sequence) return true;
    const wait = mediaWait, job = {}, session = syncState.sessionId;
    const from = syncState.selectionRevision;
    automatic = job; wait.attempt = practice.sequence;
    const allowed = () => automatic === job && session === syncState.sessionId && syncState.practiceStatus === practice &&
        !syncState.userPaused && practice.run_state === "running" && practice.media_waiting;
    try { await finishAndAdvance(readerUnitKind(element) === "image", true, allowed, true); }
    catch (error) {
        if (allowed() && syncState.selectionRevision === from && error.name !== "AbortError") {
            syncState.error = error.message; syncState.playback = "error"; stopPlayback();
        }
    } finally { if (automatic === job) automatic = null; }
    return true;
}

export function changeSyncSession(sessionId, advancement = "command") {
    if (sessionId === syncState.sessionId && advancement === syncState.advancement) return;
    const adopt = !syncState.sessionId && sessionId && advancement === "status" && state.isPlaying && getCurrentReaderElement();
    if (adopt) trackSyncSelection(getReaderPageContent()?.revision);
    const selectionKey = syncState.selectionKey;
    clearTimeout(practiceTimer);
    automatic = null;
    mediaWait = null;
    expiredPractice = null;
    if (!adopt) stopPlayback();
    clearSyncPrecache();
    renderedPage = null;
    renderedElements = null;
    setSyncSession(sessionId, advancement);
    displayPracticeChunk();
    if (adopt) {
        // Existing audio callbacks read sync ownership at completion; keep their source and position.
        syncState.selectionKey = selectionKey;
        syncState.playback = state.currentAudioSource ? "playing" : "loading";
        if (state.currentAudioSource) recordSyncAudio();
        void preCacheNextSentences();
    }
}

export async function selectSyncPosition(pageIndex, sentenceIndex, preparedSentences = null, preserveCut = false) {
    if (!isSyncActive()) throw new Error("Practice sync is not active.");
    const doc = state.currentDoc;
    if (!doc || !Number.isInteger(pageIndex) || pageIndex < 0 || pageIndex >= state.currentPages.length ||
        !Number.isInteger(sentenceIndex) || sentenceIndex < 0) throw new Error("Invalid reader position.");
    const previousPage = pageIndex === state.readingPageIndex ? getReaderPageContent() : null;
    const reusePage = previousPage && previousPage === renderedPage && state.viewPageIndex === pageIndex &&
        renderedElements === state.sentenceElements && renderedElements.length > 0 &&
        renderedElements.every(element => element.isConnected);
    if (!preserveCut) clearTimeCutAudio();
    stopPlayback();
    const playbackGeneration = syncState.playbackGeneration;
    invalidateSyncSelection();
    mediaWait = null;
    syncState.transitioning = true;
    const generation = syncState.generation, session = syncState.sessionId;
    let selectedGeneration = generation;
    try {
        const sentences = preparedSentences || (previousPage && getCurrentReaderElement() ? state.readingSentences : await getSentencesForPage(pageIndex));
        if (state.currentDoc !== doc || session !== syncState.sessionId || generation !== syncState.generation) {
            throw new Error("Reader selection changed while loading the page.");
        }
        if (sentenceIndex >= sentences.length) throw new Error("Sentence is outside the selected page.");
        state.readingPageIndex = pageIndex;
        state.currentSentenceIndex = sentenceIndex;
        state.readingSentences = sentences;
        state.viewPageIndex = pageIndex;
        state.autoScrollEnabled = true;
        const page = getReaderPageContent();
        doc.currentPage = pageIndex;
        doc.lastSentenceIndex = sentenceIndex;
        doc.lastSentenceId = page?.elements[sentenceIndex]?.id || null;
        trackSyncSelection(page?.revision);
        selectedGeneration = syncState.generation;
        syncState.transitioning = true;
        if (!reusePage) await renderPage();
        if (state.currentDoc !== doc || session !== syncState.sessionId || selectedGeneration !== syncState.generation) {
            throw new Error("Reader selection changed while rendering.");
        }
        clearActiveSentenceHighlights();
        if (state.viewPageIndex === pageIndex) state.sentenceElements.forEach((el, index) => el.classList.toggle("active-sentence", index === sentenceIndex));
        renderedPage = page;
        renderedElements = state.sentenceElements;
        updateProgressDisplay();
        await saveProgress();
        if (state.currentDoc !== doc || session !== syncState.sessionId || selectedGeneration !== syncState.generation) {
            throw new Error("Reader selection changed while saving progress.");
        }
        return { revision: syncState.selectionRevision, playbackGeneration };
    } finally {
        if (session === syncState.sessionId && selectedGeneration === syncState.generation) syncState.transitioning = false;
    }
}

export async function advanceSync(skipImage = false, allowed = null) {
    if (syncState.advancement === "status" && !allowed) throw new Error("Practice status owns automatic advancement.");
    allowed ||= () => true;
    const check = () => {
        if (!allowed()) throw new DOMException("Practice handoff was stopped.", "AbortError");
    };
    check();
    if (syncState.transitioning) throw new Error("Reader position is still loading.");
    const element = getCurrentReaderElement();
    if (!isSyncActive() || !element) throw new Error("No current sentence is ready.");
    const kind = readerUnitKind(element);
    if (skipImage) {
        if (kind !== "image") throw new Error("Only an image-only unit can be skipped.");
    } else if (kind === "image" || (syncState.audioEnabled && !syncState.audioEnded) || !syncState.practiceComplete) {
        throw new Error("Audio and practice must finish before advancing.");
    }
    const doc = state.currentDoc, generation = syncState.generation, session = syncState.sessionId;
    const playbackGeneration = syncState.playbackGeneration;
    if (syncState.skipToEnd && !syncState.skipRoute.length) {
        stopPlayback(); syncState.bookEnded = true; syncState.skipToEnd = false; return;
    }
    let page = state.readingPageIndex, index = state.currentSentenceIndex + 1;
    let nextSentences = null;
    const routed = syncState.skipRoute[0];
    if (routed) { page = routed.page_index; index = routed.sentence_index; }
    if (!routed && index >= state.readingSentences.length) {
        index = 0;
        do {
            page++;
            if (page >= state.currentPages.length) {
                stopPlayback();
                syncState.bookEnded = true;
                return;
            }
            const sentences = await getSentencesForPage(page);
            check();
            if (state.currentDoc !== doc || generation !== syncState.generation || session !== syncState.sessionId) {
                throw new Error("Reader selection changed while loading the next page.");
            }
            if (sentences.length) { nextSentences = sentences; break; }
        } while (page < state.currentPages.length);
    }
    // A deliberate Chapter skip visits its intervening media in order. Their
    // existing Show/Hide delays own these handoffs; no text is marked complete.
    const interrupted = playbackGeneration !== syncState.playbackGeneration;
    check();
    const selected = await selectSyncPosition(page, index, nextSentences, true);
    if (routed && syncState.skipRoute[0] === routed) syncState.skipRoute.shift();
    return { ...selected, interrupted };
}

export function applyPracticeStatus(status, transitMs = 0) {
    if (!status || syncState.advancement !== "status" || status.session_id !== syncState.sessionId) return;
    const previous = syncState.practiceStatus;
    if (previous && status.sequence < previous.sequence) return;
    if (previous?.run_id && previous.run_id === status.run_id && previous.run_state === "finished" &&
        (status.run_state !== "finished" || status.outcome !== previous.outcome)) return;
    if (status.selection_revision === syncState.selectionRevision) {
        displayPracticeChunk(status.chunk);
        if (syncState.chunk) syncState.practiceChunkSource = { doc: state.currentDoc,
            page: syncState.chunk.page_index, revision: syncState.chunk.page_revision,
            markup: state.currentPages[syncState.chunk.page_index] };
        if (syncState.chunk) syncState.practiceSources.set(syncState.chunk.page_index, syncState.practiceChunkSource);
        const start = status.run_start, page = getReaderPageContent();
        if (status.run_id && start && !syncState.runCheckpoints.has(status.run_id) && page &&
            start.book_id === String(page.bookId) && start.page_index === page.pageIndex && start.page_revision === page.revision &&
            start.sentence_index === state.currentSentenceIndex) {
            syncState.runCheckpoints.set(status.run_id, { start, doc: state.currentDoc, markup: state.currentPages[start.page_index] });
            while (syncState.runCheckpoints.size > 32) syncState.runCheckpoints.delete(syncState.runCheckpoints.keys().next().value);
        }
    }
    const unlimited = status.limit_kind === "unlimited";
    const deadline = unlimited || status.clock_paused ? Infinity : performance.now() + Math.max(0, status.remaining_ms - transitMs);
    if (previous && status.sequence === previous.sequence) {
        // Heartbeats and uncertain POST retries cannot extend an already accepted deadline.
        if (!status.media_waiting && status.run_state === "running") syncState.practiceDeadline = Math.min(syncState.practiceDeadline, deadline);
    } else {
        syncState.practiceStatus = status;
        syncState.practiceDeadline = status.media_waiting || status.run_state !== "running" ? Infinity : deadline;
        syncState.practicePlaybackGeneration = syncState.playbackGeneration;
        if (status.run_state === "running" && !status.media_waiting && !status.clock_paused && (unlimited || status.remaining_ms > 0)) syncState.cutAwaitInput = false;
        if (status.media_waiting && status.run_state === "running" && !status.user_paused) syncState.userPaused = false;
        if (status.selection_revision === syncState.selectionRevision) syncState.practiceComplete = status.typing_complete;
        if (status.run_state === "preparing" && status.selection_revision === syncState.selectionRevision && syncState.playback === "error") {
            syncState.error = "";
            syncState.playback = syncState.audioEnded ? "ended" : "paused";
        }
        if (status.run_state === "finished") { syncState.userPaused = false; stopPlayback(); }
        else if (status.run_state === "paused" && status.user_paused) {
            void pauseSyncPlayback().catch(error => { syncState.error = error.message; syncState.playback = "error"; stopPlayback(); });
        } else if (status.run_state === "paused" && !syncState.userPaused) stopPlayback();
    }
    clearTimeout(practiceTimer);
    if (status.run_state === "running" && !unlimited && !status.clock_paused && !status.media_waiting && expiredPractice !== syncState.practiceStatus) {
        const current = syncState.practiceStatus;
        practiceTimer = setTimeout(() => {
            if (syncState.practiceStatus === current && syncState.sessionId === current.session_id) {
                expiredPractice = current;
                stopPlayback();
            }
        }, Math.max(0, syncState.practiceDeadline - performance.now()));
    }
}

export function practiceAdvanceGate() {
    const practice = syncState.practiceStatus;
    if (!isSyncActive() || syncState.advancement !== "status") return "inactive";
    if (syncState.userPaused) return "paused";
    if (!practice || practice.selection_revision !== syncState.selectionRevision) return "status_pending";
    if (practice.run_state !== "running") return practice.run_state;
    if (performance.now() >= syncState.practiceDeadline) return "run_expired";
    if (syncState.bookEnded) return "book_end";
    if (syncState.playback === "error") return "error";
    if (syncState.practicePlaybackGeneration !== syncState.playbackGeneration) return "paused";
    if (syncState.transitioning || automatic) return "advancing";
    const element = getCurrentReaderElement();
    if (!element || ["image", "scene"].includes(readerUnitKind(element)) || isReaderTextBlank(element.textContent)) return "media_pending";
    if (!practice.typing_complete) return "typing_pending";
    if (syncState.audioEnabled && !syncState.audioEnded) return "audio_pending";
    return syncState.practiceAttempt === practice.sequence ? "retry_required" : "ready";
}

async function finishAndAdvance(skipImage, autoplay, allowed = () => true, media = false) {
    if (media) { stopPlayback(); syncState.audioEnded = true; }
    if (!skipImage && syncState.audioEnabled && !syncState.audioEnded) throw new Error("Audio must finish before continuing practice.");
    if (autoplay && syncState.audioEnabled && !window.isEngineReady) throw new Error("Reader speech engine is not ready.");
    const from = syncState.selectionRevision;
    const doc = state.currentDoc, session = syncState.sessionId;
    syncState.practiceComplete = true;
    const selected = await advanceSync(skipImage, allowed);
    if (doc !== state.currentDoc || session !== syncState.sessionId ||
        (selected && selected.revision !== syncState.selectionRevision)) throw new Error("Reader selection changed during continuation.");
    if (syncState.bookEnded) return;
    // Continuation identifies the committed selection, independently of audio.
    // Blank/media handoffs can preserve a run without starting next-run speech.
    syncState.continuationFrom = from;
    if (!allowed() || selected.interrupted || selected.playbackGeneration !== syncState.playbackGeneration) {
        syncState.playback = "paused";
        return;
    }
    const next = getCurrentReaderElement();
    if (autoplay && !syncState.cutAwaitInput && syncState.audioEnabled && next && !["image", "scene"].includes(readerUnitKind(next)) &&
        !isReaderTextBlank(next.textContent)) startPlayback();
}

export async function maybeContinuePractice() {
    if (await continueMedia()) return;
    if (practiceAdvanceGate() !== "ready") return;
    const practice = syncState.practiceStatus;
    const session = syncState.sessionId, generation = syncState.playbackGeneration;
    const job = {};
    automatic = job;
    syncState.practiceAttempt = practice.sequence;
    const current = () => automatic === job && session === syncState.sessionId && syncState.practiceStatus === practice &&
        practice.run_state === "running" && performance.now() < syncState.practiceDeadline;
    const from = syncState.selectionRevision;
    try {
        await finishAndAdvance(false, true, () => current() &&
            (syncState.selectionRevision !== from || syncState.playbackGeneration === generation));
    } catch (error) {
        if (current() && error.name !== "AbortError" && syncState.selectionRevision === from) {
            syncState.error = error.message;
            syncState.playback = "error";
            stopPlayback();
        }
    } finally { if (automatic === job) automatic = null; }
}

export async function selectPreviousSync() {
    if (state.currentSentenceIndex > 0) return selectSyncPosition(state.readingPageIndex, state.currentSentenceIndex - 1);
    stopPlayback();
    const doc = state.currentDoc, generation = syncState.generation, session = syncState.sessionId;
    for (let page = state.readingPageIndex - 1; page >= 0; page--) {
        const sentences = await getSentencesForPage(page);
        if (doc !== state.currentDoc || generation !== syncState.generation || session !== syncState.sessionId) {
            throw new Error("Reader selection changed while loading the previous page.");
        }
        if (sentences.length) return selectSyncPosition(page, sentences.length - 1);
    }
}

async function applyTimeCut(intent, manual = false) {
    const echo = manual ? "runAction" : "timeCut";
    const signature = JSON.stringify(intent), prior = syncState.cutRecords.get(intent.operation_id);
    if (prior && prior.signature !== signature) throw new Error("Time-cut identity was reused with different data.");
    if (prior?.result) return prior.result;
    const practice = syncState.practiceStatus, chunk = intent.chunk;
    if (syncState.advancement !== "status" || !practice || practice.run_state !== "finished" ||
        (!manual && (practice.limit_kind === "unlimited" || practice.remaining_ms !== 0 || practice.run_id && practice.outcome !== "time_limit")) ||
        !practice.chunk || Object.keys(chunk).some(key => practice.chunk[key] !== chunk[key])) throw new Error("Time cut requires the captured expired chunk.");
    const page = getReaderPageContent();
    const source = syncState.practiceChunkSource;
    const headSource = syncState.practiceSources.get(intent.head.page_index);
    if (!prior && (!page || String(page.bookId) !== chunk.book_id || !source || source.doc !== state.currentDoc ||
        source.page !== chunk.page_index || source.revision !== chunk.page_revision || source.markup !== state.currentPages[source.page] ||
        (practice.selection_revision !== syncState.selectionRevision && syncState.continuationFrom !== practice.selection_revision))) {
        throw new Error("Reader moved away from the expired run.");
    }
    if (intent.mode === "loop" && headSource && (headSource.doc !== state.currentDoc ||
        headSource.markup !== state.currentPages[intent.head.page_index])) throw new Error("The captured chunk head was edited.");
    const doc = state.currentDoc, session = syncState.sessionId, generation = syncState.generation;
    const check = (source = true) => {
        if (doc !== state.currentDoc || session !== syncState.sessionId || syncState.practiceStatus !== practice ||
            source && generation !== syncState.generation || syncState[echo]?.operation_id !== intent.operation_id)
            throw new DOMException("Time cut was superseded.", "AbortError");
    };
    // Only started sources are heard. A generated cache entry is deliberately
    // absent from this ledger. Stop status has already retired live playback.
    stopPlayback(); automatic = null; mediaWait = null;
    syncState[echo] = { operation_id: intent.operation_id, status: "pending", selection_revision: syncState.selectionRevision,
        page_index: state.readingPageIndex, sentence_index: state.currentSentenceIndex,
        book_ended: false, skipped: intent.mode === "skip" };
    let record = prior;
    if (!record) {
        let destination = null;
        const route = [];
        if (intent.mode === "loop") destination = intent.head;
        else if (intent.mode === "word" && !intent.word_complete)
            destination = intent.cursor;
        else {
            let p = chunk.page_index;
            let index = intent.mode === "word" ? intent.cursor.sentence_index + 1 : chunk.end;
            const chapter = intent.mode === "skip" && chunk.preset === "chapter";
            // A Chapter may continue across physical HTML files. Scan canonical
            // metadata one page at a time, without synthesizing skipped speech.
            if (chapter) index = intent.cursor.sentence_index + 1;
            for (; p < state.currentPages.length && !destination; p++, index = 0) {
                const content = await getReaderSpeechPage(p, { requireLoaded: true }); check();
                if (!content) continue;
                const scopes = readerChapterScopes(content.elements, p);
                for (let i = index; i < content.elements.length; i++) {
                    const scope = scopes.find(scope => scope.start <= i && i < scope.end);
                    if (!chapter || scope?.id !== chunk.chapter_id) { destination = { page_index: p, sentence_index: i }; break; }
                    if (["image", "scene"].includes(readerUnitKind(content.elements[i]))) route.push({ page_index: p, sentence_index: i });
                }
            }
        }
        if (destination) route.push(destination);
        record = { signature, route, endAfterMedia: !destination && route.length > 0, result: null };
        syncState.cutRecords.set(intent.operation_id, record);
        while (syncState.cutRecords.size > 32) syncState.cutRecords.delete(syncState.cutRecords.keys().next().value);
    }
    check(); syncState.skipPlayed = true; syncState.cutAwaitInput = true;
    const target = record.route[0];
    if (target) await selectSyncPosition(target.page_index, target.sentence_index, null, true);
    else syncState.bookEnded = true;
    check(false);
    if (target) suppressSyncAudio();
    syncState.skipRoute = record.route.slice(1);
    // A final media delay is allowed before the end state is published.
    syncState.skipToEnd = record.endAfterMedia;
    record.result = { operation_id: intent.operation_id, status: "applied", selection_revision: syncState.selectionRevision,
        page_index: state.readingPageIndex, sentence_index: state.currentSentenceIndex,
        book_ended: syncState.bookEnded, skipped: intent.mode === "skip" };
    syncState[echo] = record.result;
}

async function applyRunAction(action) {
    const previous = syncState.cutRecords.get(action.operation_id), signature = JSON.stringify(action);
    if (previous?.actionSignature && previous.actionSignature !== signature) throw new Error("Result action identity was reused.");
    if (previous?.result && previous.actionSignature === signature) return previous.result;
    const checkpoint = syncState.runCheckpoints.get(action.run_id), practice = syncState.practiceStatus;
    if (!checkpoint || checkpoint.doc !== state.currentDoc || checkpoint.markup !== state.currentPages[checkpoint.start.page_index] ||
        practice?.run_id !== action.run_id || practice.run_state !== "finished" ||
        !(["manual_stop", "book_end", "abandoned"].includes(practice.outcome) ||
            practice.outcome === "time_limit" && syncState.bookEnded && action.action === "restart")) throw new Error("The saved run is no longer available in this reader session.");
    try {
        return await applyTimeCut({ operation_id: action.operation_id, mode: action.action === "restart" ? "loop" : "word",
            chunk: action.chunk, cursor: action.cursor, word_complete: action.action === "continue" && action.word_complete,
            head: action.action === "restart" ? { page_index: checkpoint.start.page_index, sentence_index: checkpoint.start.sentence_index } :
                { page_index: action.chunk.page_index, sentence_index: action.chunk.start } }, true);
    } finally {
        const record = syncState.cutRecords.get(action.operation_id);
        if (record) record.actionSignature = signature;
    }
}

export async function applySyncCommand(command) {
    const page = getReaderPageContent();
    trackSyncSelection(page?.revision);
    if (command.session_id === syncState.sessionId && command.type === "time_cut" && syncState.cutRecords.get(command.time_cut?.operation_id)?.result)
        return applyTimeCut(command.time_cut);
    if (command.session_id === syncState.sessionId && command.type === "run_action" && syncState.cutRecords.get(command.run_action?.operation_id)?.result)
        return applyRunAction(command.run_action);
    if (command.session_id !== syncState.sessionId || command.selection_revision !== syncState.selectionRevision) {
        throw new Error("Sync session or selected sentence has changed.");
    }
    if (syncState.advancement === "status" && ["complete", "advance", "continue", "skip_image"].includes(command.type)) {
        throw new Error("Automatic advancement is owned by practice status.");
    }
    if (syncState.advancement === "status" && ["prepare", "play", "resume"].includes(command.type)) {
        const practice = syncState.practiceStatus;
        // Status and commands travel independently. A queued Play must not undo a newer stop.
        if (!practice || practice.selection_revision !== syncState.selectionRevision ||
            !["preparing", "running"].includes(practice.run_state) ||
            (practice.run_state === "running" && (practice.clock_paused || performance.now() >= syncState.practiceDeadline))) {
            throw new Error("Practice must be ready before starting audio.");
        }
    }
    switch (command.type) {
        case "run_action": return applyRunAction(command.run_action);
        case "time_cut": return applyTimeCut(command.time_cut);
        case "select": return selectSyncPosition(command.target.page_index, command.target.sentence_index);
        case "audio":
            stopPlayback();
            syncState.audioEnabled = command.audio_enabled;
            if (syncState.audioEnabled) syncState.audioEnded = false;
            else clearSyncPrecache();
            return;
        case "prepare":
            if (!syncState.audioEnabled) throw new Error("Kokoro is disabled for this practice.");
            if (!window.isEngineReady) throw new Error("Reader speech engine is not ready.");
            return prepareSyncPlayback();
        case "play":
            if (!syncState.audioEnabled) throw new Error("Kokoro is disabled for this practice.");
            if (!getCurrentReaderElement()) throw new Error("No current sentence is ready.");
            if (!window.isEngineReady) throw new Error("Reader speech engine is not ready.");
            return startPlayback();
        case "pause": return syncState.advancement === "status" && syncState.practiceStatus?.run_state !== "finished" ? pauseSyncPlayback() : stopPlayback();
        case "resume": return resumeSyncPlayback();
        case "complete": syncState.practiceComplete = true; return;
        case "advance": return advanceSync();
        case "continue": {
            if (!getCurrentReaderElement()) throw new Error("No current sentence is ready.");
            if (readerUnitKind(getCurrentReaderElement()) === "image") throw new Error("Only an image-only unit can be skipped.");
            if (syncState.audioEnabled && !syncState.audioEnded) throw new Error("Audio must finish before continuing practice.");
            if (syncState.audioEnabled && !window.isEngineReady) throw new Error("Reader speech engine is not ready.");
            return finishAndAdvance(false, true);
        }
        case "media_next": {
            const element = getCurrentReaderElement(), practice = syncState.practiceStatus;
            if (!element || (!["image", "scene"].includes(readerUnitKind(element)) && !isReaderTextBlank(element.textContent))) {
                throw new Error("Media Continue cannot skip text that requires typing.");
            }
            if (!practice || practice.selection_revision !== syncState.selectionRevision ||
                !["preparing", "running"].includes(practice.run_state) ||
                (practice.run_state === "running" && performance.now() >= syncState.practiceDeadline)) {
                throw new Error("Practice is not ready to continue media.");
            }
            return finishAndAdvance(readerUnitKind(element) === "image", practice.run_state === "running", () =>
                syncState.practiceStatus === practice && syncState.sessionId === practice.session_id &&
                (practice.run_state !== "running" || performance.now() < syncState.practiceDeadline) &&
                !syncState.userPaused, true);
        }
        case "skip_image": return advanceSync(true);
        default: throw new Error("Unknown reader command.");
    }
}
