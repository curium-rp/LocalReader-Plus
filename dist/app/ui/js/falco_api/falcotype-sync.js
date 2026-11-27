import { state } from "../modules/state.js";
import { getSentencesForPage, getReaderSpeechPage, getReaderPageContent, getCurrentReaderElement, readerUnitKind, isReaderTextBlank,
    isReaderFigure, renderPage } from "../modules/library.js";
import { stopPlayback, startPlayback, pauseSyncPlayback, resumeSyncPlayback, prepareSyncPlayback, preCacheNextSentences, clearSyncPrecache, saveProgress,
    showSyncPosition, releaseSpeechHold } from "../modules/tts.js";
import { syncState, isSyncActive, invalidateSyncSelection, trackSyncSelection, setSyncSession, clearTimeCutAudio, suppressSyncAudio,
    behaviorDelay } from "./falcotype-sync-state.js";

let renderedPage = null, renderedElements = null;
let practiceTimer = null, automatic = null, expiredPractice = null;
let mediaWait = null;

// A race with the other app, not a failure: the bridge acknowledges it as "stale".
const staleError = message => Object.assign(new Error(message), { name: "StaleSelection" });
const engineLoading = () => syncState.managed && syncState.engine === "loading";
let chunkPaintToken = null, chunkPaintKey = "", completedChunk = null;

const chunkLimitKey = practice => JSON.stringify([practice?.session_id, practice?.run_id,
    practice?.selection_revision, practice?.chunk]);
async function stopAtChunkBoundary(practice) {
    const chunk = practice?.chunk;
    // Only the part that reaches the target ends the run: a page's end is a handoff.
    if (practice?.limit_kind !== "chunk" || !chunk?.last || chunk.page_index !== state.readingPageIndex ||
        state.currentSentenceIndex + 1 < chunk.end) return false;
    const key = chunkLimitKey(practice);
    if (completedChunk === key) return true;
    const doc = state.currentDoc, revision = syncState.selectionRevision, session = syncState.sessionId;
    const current = () => syncState.practiceStatus === practice && state.currentDoc === doc &&
        syncState.selectionRevision === revision && syncState.sessionId === session && !syncState.userPaused;
    if (current()) completedChunk = key;
    return true;
}

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

// Media block (bridge API v5), published with the selection it describes. The
// Reader reports the Behavior delay (Img/S); `confirmed` once it has accepted
// FalcoType's running media status. FalcoType counts the delay in its own window
// and ends an image/scene with one media_next. A figure is a text unit: FalcoType
// shows its picture for the Img delay, then the caption is typed as usual.
export function syncMediaBlock() {
    const element = !syncState.transitioning && getCurrentReaderElement();
    if (!isSyncActive() || syncState.advancement !== "status" || !element) return null;
    const kind = readerUnitKind(element), figure = isReaderFigure(element);
    if (!figure && !["image", "scene"].includes(kind)) return null;
    const practice = syncState.practiceStatus;
    return { selection_revision: syncState.selectionRevision, kind: figure ? "image" : kind, figure,
        delay_ms: kind === "scene" ? behaviorDelay("S", 1000) : behaviorDelay("Img", 3000),
        confirmed: !figure && !!practice && practice.selection_revision === syncState.selectionRevision && practice.media_waiting &&
            practice.run_state === "running" && !syncState.userPaused && !syncState.bookEnded && !automatic };
}

// The Reader never advances an image/scene itself; it only prepares the next speech.
async function continueMedia() {
    const media = syncMediaBlock();
    if (!media || media.figure) { mediaWait = null; return false; }
    if (!mediaWait || mediaWait.session !== syncState.sessionId || mediaWait.revision !== syncState.selectionRevision) {
        mediaWait = { session: syncState.sessionId, revision: syncState.selectionRevision };
        // Synthesize the text after the media during its wait, so speech is
        // ready when FalcoType continues instead of holding typing at 0.
        if (syncState.audioEnabled) void preCacheNextSentences();
    }
    return true;
}

export function changeSyncSession(sessionId, advancement = "command") {
    if (sessionId === syncState.sessionId && advancement === syncState.advancement) return;
    clearTimeout(practiceTimer);
    automatic = null;
    mediaWait = null;
    expiredPractice = null;
    // A session starts and ends in standby: it never inherits sound.
    stopPlayback();
    clearSyncPrecache();
    renderedPage = null;
    renderedElements = null;
    setSyncSession(sessionId, advancement);
    displayPracticeChunk();
}

// `run`: the Book run made this move (a followed position, a handoff, a time cut or a
// select command). Any other move is the user's (a pick, Previous, a jump): it raises the
// epoch at once, so a followed position already on its way can't pull the Reader back,
// and FalcoType starts over from here. A run's own move never looks like the user's.
export async function selectSyncPosition(pageIndex, sentenceIndex, preparedSentences = null, preserveCut = false, { run = false } = {}) {
    // Launched by FalcoType, a pick before the session is the same silent selection.
    if (!isSyncActive() && !syncState.managed) throw new Error("Practice sync is not active.");
    if (syncState.book !== "ready") throw new Error(syncState.book === "loading" ? "The book is still loading." : "No book is open.");
    const doc = state.currentDoc;
    if (!doc || !Number.isInteger(pageIndex) || pageIndex < 0 || pageIndex >= state.currentPages.length ||
        !Number.isInteger(sentenceIndex) || sentenceIndex < 0) throw new Error("Invalid reader position.");
    if (!run) { syncState.followEpoch++; syncState.sleepPending = false; }
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
        // A page that failed to load is an error here, never an empty page to pass.
        const sentences = preparedSentences || (previousPage && getCurrentReaderElement() ? state.readingSentences :
            await getSentencesForPage(pageIndex, { requireLoaded: true }));
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
        // The selection is the page's content. Its layout waits for a frame, which a minimized window may not paint.
        if (!reusePage) await renderPage({ waitForLayout: false });
        if (state.currentDoc !== doc || session !== syncState.sessionId || selectedGeneration !== syncState.generation) {
            throw new Error("Reader selection changed while rendering.");
        }
        renderedPage = page;
        renderedElements = state.sentenceElements;
        // Highlight, view, progress and the monitor, at once: speech may never come.
        showSyncPosition();
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
            // Only a page that loaded empty is passed; one that failed stops the handoff.
            const sentences = await getSentencesForPage(page, { requireLoaded: true });
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
    const selected = await selectSyncPosition(page, index, nextSentences, true, { run: true });
    if (routed && syncState.skipRoute[0] === routed) syncState.skipRoute.shift();
    return { ...selected, interrupted };
}

function paintPracticeChunk(chunk) {
    displayPracticeChunk(chunk);
    if (!syncState.chunk) return;
    syncState.practiceChunkSource = { doc: state.currentDoc, page: syncState.chunk.page_index, revision: syncState.chunk.page_revision,
        markup: state.currentPages[syncState.chunk.page_index] };
    syncState.practiceSources.set(syncState.chunk.page_index, syncState.practiceChunkSource);
}

// The run's first position, for a later result action that goes back to it.
function recordRunStart(status) {
    const start = status.run_start;
    if (!status.run_id || !start || syncState.runCheckpoints.has(status.run_id) || !state.currentPages[start.page_index]) return;
    syncState.runCheckpoints.set(status.run_id, { start, doc: state.currentDoc, markup: state.currentPages[start.page_index] });
    while (syncState.runCheckpoints.size > 32) syncState.runCheckpoints.delete(syncState.runCheckpoints.keys().next().value);
}

// Follow mode (Kokoro off): FalcoType moves through the book and this page shows where
// it is. Its speech is off, and FalcoType alone pauses it, so the status releases a pause.
function followPractice(status) {
    if (state.currentDoc && status.run_start?.book_id === String(state.currentDoc.id)) recordRunStart(status);
    if (syncState.audioEnabled) { stopPlayback(); syncState.audioEnabled = false; clearSyncPrecache(); }
    if (!status.user_paused) syncState.userPaused = false;
    syncState.follow = status.follow;
    paintPracticeChunk(status.chunk);
    void applyFollow();
}

// A position whose page failed to load is tried again about once a second, not on
// every poll. FalcoType asks again with a new position, which is tried at once.
const FOLLOW_RETRY_MS = 1000;
let followFailure = null;

// The followed position this page still has to open, if it can open it here.
export function followPending() {
    const target = syncState.follow, doc = state.currentDoc;
    return !!target && isSyncActive() && syncState.book === "ready" && target.epoch === syncState.followEpoch && target.id !== syncState.followApplied &&
        !!doc && target.book_id === String(doc.id) &&
        !(followFailure?.id === target.id && performance.now() < followFailure.retryAt);
}

// Opens the position FalcoType asked for, or the first unit after it on a later page,
// one move at a time. A move of the user's own (a new epoch) retires older positions,
// and a newer position retires one still loading its page.
let followJob = null;
async function applyFollow() {
    const target = syncState.follow, doc = state.currentDoc;
    if (followJob || !followPending()) return;
    const job = {}, session = syncState.sessionId, epoch = syncState.followEpoch;
    followJob = job;
    const left = () => doc !== state.currentDoc || session !== syncState.sessionId || epoch !== syncState.followEpoch ||
        syncState.follow?.id !== target.id;
    try {
        let page = target.page_index, index = target.sentence_index;
        const here = page === state.readingPageIndex && index === state.currentSentenceIndex && !!getCurrentReaderElement() && !syncState.bookEnded;
        if (!here) {
            let sentences = null;
            for (; page < state.currentPages.length; page++, index = 0) {
                // Only a page that loaded empty is passed. One that failed to load throws,
                // so the run never skips a chapter or reports the book's end for it.
                sentences = page === state.readingPageIndex && getCurrentReaderElement() ? state.readingSentences :
                    await getSentencesForPage(page, { requireLoaded: true });
                if (left()) return;
                if (index < sentences.length) break;
            }
            if (page >= state.currentPages.length) { stopPlayback(); syncState.bookEnded = true; }
            else await selectSyncPosition(page, index, page === state.readingPageIndex ? null : sentences, true, { run: true });
            if (left()) return;
        }
        followFailure = null;
        syncState.followApplied = target.id;
        paintPracticeChunk(syncState.practiceStatus?.chunk);
    } catch (error) {
        // Tried again after FOLLOW_RETRY_MS; FalcoType gives up on a page that never opens.
        if (!left()) {
            followFailure = { id: target.id, retryAt: performance.now() + FOLLOW_RETRY_MS };
            console.warn("Reader could not follow the Book position:", error);
        }
    } finally {
        if (followJob === job) followJob = null;
        if (syncState.follow && syncState.follow.id !== target.id) void applyFollow();
    }
}

export function applyPracticeStatus(status, transitMs = 0) {
    if (!status || syncState.advancement !== "status" || status.session_id !== syncState.sessionId) return;
    if (status.limit_kind === undefined) status = { limit_kind: "finite", ...status };
    const previous = syncState.practiceStatus;
    if (previous && status.sequence < previous.sequence) return;
    if (previous?.run_id && previous.run_id === status.run_id && previous.run_state === "finished" &&
        (status.run_state !== "finished" || status.outcome !== previous.outcome)) return;
    if (status.limit_kind !== "chunk") completedChunk = null;
    if (status.follow) followPractice(status);
    else if (status.selection_revision === syncState.selectionRevision) {
        paintPracticeChunk(status.chunk);
        const start = status.run_start, page = getReaderPageContent();
        if (start && page && start.book_id === String(page.bookId) && start.page_index === page.pageIndex && start.page_revision === page.revision &&
            start.sentence_index === state.currentSentenceIndex) recordRunStart(status);
    }
    const unlimited = status.limit_kind !== "finite";
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
        // A figure's picture has no speech to resume: its status releases the hold, as media's does.
        if (status.run_state !== "paused" && !status.user_paused && syncMediaBlock()?.figure) syncState.userPaused = false;
        if (status.selection_revision === syncState.selectionRevision) syncState.practiceComplete = status.typing_complete;
        if (status.run_state === "preparing" && status.selection_revision === syncState.selectionRevision && syncState.playback === "error") {
            syncState.error = "";
            syncState.playback = syncState.audioEnded ? "ended" : "paused";
        }
        if (status.run_state === "finished") { syncState.userPaused = false; syncState.sleepPending = false; stopPlayback(); }
        else if (status.run_state === "paused" && status.pause_echo) {
            // This page's own pause coming back. A Play pressed meanwhile stands.
        } else if (status.run_state === "paused" && status.user_paused) {
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
    // A followed run moves itself; this page only shows where it is.
    if (practice?.follow) return "following";
    if (syncState.userPaused) return "paused";
    if (!practice || practice.selection_revision !== syncState.selectionRevision) return "status_pending";
    if (practice.run_state !== "running") return practice.run_state;
    if (performance.now() >= syncState.practiceDeadline) return "run_expired";
    if (syncState.bookEnded) return "book_end";
    if (syncState.book !== "ready") return "book_loading";
    if (syncState.playback === "error") return "error";
    if (syncState.practicePlaybackGeneration !== syncState.playbackGeneration) return "paused";
    if (syncState.transitioning || automatic) return "advancing";
    if (practice.limit_kind === "chunk" && completedChunk === chunkLimitKey(practice)) return "chunk_complete";
    const element = getCurrentReaderElement();
    if (!element || ["image", "scene"].includes(readerUnitKind(element)) || isReaderTextBlank(element.textContent)) return "media_pending";
    if (!practice.typing_complete) return "typing_pending";
    if (syncState.audioEnabled && !syncState.audioEnded) return "audio_pending";
    return syncState.practiceAttempt === practice.sequence ? "retry_required" : "ready";
}

async function finishAndAdvance(skipImage, autoplay, allowed = () => true, media = false) {
    if (media) { stopPlayback(); syncState.audioEnded = true; }
    if (!skipImage && syncState.audioEnabled && !syncState.audioEnded) throw new Error("Audio must finish before continuing practice.");
    // A voice that is loading doesn't hold the book: the next unit waits for it in standby.
    if (autoplay && syncState.audioEnabled && !window.isEngineReady && !engineLoading()) throw new Error("Reader speech engine is not ready.");
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
    // A shown figure's picture comes first: FalcoType plays its caption after the Img delay.
    const shownFigure = next && isReaderFigure(next) && syncState.practiceStatus?.media_enabled !== false;
    if (autoplay && !engineLoading() && !syncState.cutAwaitInput && syncState.audioEnabled && next && !["image", "scene"].includes(readerUnitKind(next)) &&
        !shownFigure && !isReaderTextBlank(next.textContent)) startPlayback();
}

export async function maybeContinuePractice() {
    if (await continueMedia()) return;
    if (practiceAdvanceGate() !== "ready") return;
    const practice = syncState.practiceStatus;
    if (await stopAtChunkBoundary(practice)) return;
    if (syncState.practiceStatus !== practice || practiceAdvanceGate() !== "ready") return;
    const session = syncState.sessionId, generation = syncState.playbackGeneration;
    const job = {};
    automatic = job;
    syncState.practiceAttempt = practice.sequence;
    const current = () => automatic === job && session === syncState.sessionId && syncState.practiceStatus === practice &&
        practice.run_state === "running" && performance.now() < syncState.practiceDeadline;
    const from = syncState.selectionRevision;
    try {
        await finishAndAdvance(false, !!practice.run_start, () => current() &&
            (syncState.selectionRevision !== from || syncState.playbackGeneration === generation));
    } catch (error) {
        if (current() && error.name !== "AbortError" && syncState.selectionRevision === from) {
            syncState.error = error.message;
            syncState.playback = "error";
            stopPlayback();
        }
    } finally { if (automatic === job) automatic = null; }
}

// The sleep timer. A plain stop would look like cut-off speech to FalcoType, which restarts it,
// so a run gets the user's pause. A handoff, a Prepare, media and a figure's picture clear
// that pause by themselves: there the next speech pauses instead.
export function sleepSyncPlayback() {
    if (!isSyncActive() || syncState.advancement !== "status" || !syncState.audioEnabled ||
        syncState.practiceStatus?.run_state === "finished") return stopPlayback();
    const element = getCurrentReaderElement();
    const holds = state.isPlaying || element && !syncState.transitioning && syncState.playback !== "loading" &&
        !["image", "scene"].includes(readerUnitKind(element)) && !isReaderFigure(element);
    if (holds) return pauseSyncPlayback();
    syncState.sleepPending = true;
}

// The user's Next before a session: the next unit, selected in standby.
export async function selectNextPosition() {
    if (state.currentSentenceIndex + 1 < state.readingSentences.length) {
        return selectSyncPosition(state.readingPageIndex, state.currentSentenceIndex + 1);
    }
    const doc = state.currentDoc, generation = syncState.generation;
    for (let page = state.readingPageIndex + 1; page < state.currentPages.length; page++) {
        const sentences = await getSentencesForPage(page, { requireLoaded: true });
        if (doc !== state.currentDoc || generation !== syncState.generation) return;
        if (sentences.length) return selectSyncPosition(page, 0);
    }
}

export async function selectPreviousSync() {
    if (state.currentSentenceIndex > 0) return selectSyncPosition(state.readingPageIndex, state.currentSentenceIndex - 1);
    stopPlayback();
    const doc = state.currentDoc, generation = syncState.generation, session = syncState.sessionId;
    for (let page = state.readingPageIndex - 1; page >= 0; page--) {
        const sentences = await getSentencesForPage(page, { requireLoaded: true });
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
        (!manual && (practice.limit_kind !== "finite" || practice.remaining_ms !== 0 || practice.run_id && practice.outcome !== "time_limit")) ||
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
    // The run is over: what is spoken next uses the voice settings of now.
    releaseSpeechHold();
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
            // The next canonical unit, scanning page metadata one page at a time
            // without synthesizing skipped speech.
            for (; p < state.currentPages.length && !destination; p++, index = 0) {
                const content = await getReaderSpeechPage(p, { requireLoaded: true }); check();
                if (content && index < content.elements.length) destination = { page_index: p, sentence_index: index };
            }
        }
        if (destination) route.push(destination);
        record = { signature, route, endAfterMedia: !destination && route.length > 0, result: null };
        syncState.cutRecords.set(intent.operation_id, record);
        while (syncState.cutRecords.size > 32) syncState.cutRecords.delete(syncState.cutRecords.keys().next().value);
    }
    check(); syncState.cutAwaitInput = true;
    // Only a cut at the current position stays silent, since it can land mid-chunk.
    // Any other move starts speaking again, and a new pass forgets what was heard.
    syncState.skipPlayed = intent.mode === "word";
    if (!syncState.skipPlayed) syncState.heard.clear();
    const target = record.route[0];
    if (target) await selectSyncPosition(target.page_index, target.sentence_index, null, true, { run: true });
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
        !(["manual_stop", "chunk_end", "book_end", "abandoned"].includes(practice.outcome) ||
            practice.outcome === "time_limit" && action.action === "run_start")) throw new Error("The saved run is no longer available in this reader session.");
    const mode = { run_start: "loop", chunk_start: "loop", word: "word", next_chunk: "skip" }[action.action];
    try {
        return await applyTimeCut({ operation_id: action.operation_id, mode,
            chunk: action.chunk, cursor: action.cursor, word_complete: action.action === "word" && action.word_complete,
            head: action.action === "run_start" ? { page_index: checkpoint.start.page_index, sentence_index: checkpoint.start.sentence_index } :
                action.head || { page_index: action.chunk.page_index, sentence_index: action.chunk.start } }, true);
    } finally {
        const record = syncState.cutRecords.get(action.operation_id);
        if (record) record.actionSignature = signature;
    }
}

export async function applySyncCommand(command) {
    if (syncState.book !== "ready") throw staleError("The Reader is loading its book.");
    const page = getReaderPageContent();
    trackSyncSelection(page?.revision);
    if (command.session_id === syncState.sessionId && command.type === "time_cut" && syncState.cutRecords.get(command.time_cut?.operation_id)?.result)
        return applyTimeCut(command.time_cut);
    if (command.session_id === syncState.sessionId && command.type === "run_action" && syncState.cutRecords.get(command.run_action?.operation_id)?.result)
        return applyRunAction(command.run_action);
    if (command.session_id !== syncState.sessionId || command.selection_revision !== syncState.selectionRevision) {
        throw staleError("Sync session or selected sentence has changed.");
    }
    if (syncState.advancement === "status" && ["complete", "advance", "continue", "skip_image"].includes(command.type)) {
        throw new Error("Automatic advancement is owned by practice status.");
    }
    if (syncState.advancement === "status" && ["prepare", "play", "resume"].includes(command.type)) {
        const practice = syncState.practiceStatus;
        // Status and commands travel independently. A queued Play must not undo a newer stop.
        if (!practice || practice.selection_revision !== syncState.selectionRevision) throw staleError("Practice status is for another sentence.");
        if (!["preparing", "running"].includes(practice.run_state) ||
            (practice.run_state === "running" && (practice.clock_paused || performance.now() >= syncState.practiceDeadline))) {
            throw new Error("Practice must be ready before starting audio.");
        }
    }
    switch (command.type) {
        case "run_action": return applyRunAction(command.run_action);
        case "time_cut": return applyTimeCut(command.time_cut);
        case "select": return selectSyncPosition(command.target.page_index, command.target.sentence_index, null, false, { run: true });
        case "audio":
            stopPlayback();
            syncState.audioEnabled = command.audio_enabled;
            if (syncState.audioEnabled) syncState.audioEnded = false;
            else clearSyncPrecache();
            return;
        case "prepare":
            if (!syncState.audioEnabled) throw new Error("Kokoro is disabled for this practice.");
            if (engineLoading()) throw staleError("The Reader is loading its voice.");
            if (!window.isEngineReady) throw new Error("Reader speech engine is not ready.");
            return prepareSyncPlayback();
        case "play":
            if (!syncState.audioEnabled) throw new Error("Kokoro is disabled for this practice.");
            if (!getCurrentReaderElement()) throw new Error("No current sentence is ready.");
            if (engineLoading()) throw staleError("The Reader is loading its voice.");
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
            if (!practice || practice.selection_revision !== syncState.selectionRevision) throw staleError("Practice status is for another sentence.");
            if (!["preparing", "running"].includes(practice.run_state) ||
                (practice.run_state === "running" && performance.now() >= syncState.practiceDeadline)) {
                throw new Error("Practice is not ready to continue media.");
            }
            // A media wait reports running before the first input; only a started run speaks on.
            return finishAndAdvance(readerUnitKind(element) === "image", practice.run_state === "running" && !!practice.run_start, () =>
                syncState.practiceStatus === practice && syncState.sessionId === practice.session_id &&
                (practice.run_state !== "running" || performance.now() < syncState.practiceDeadline) &&
                !syncState.userPaused, true);
        }
        case "skip_image": return advanceSync(true);
        default: throw new Error("Unknown reader command.");
    }
}
