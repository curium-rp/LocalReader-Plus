import { state } from "../modules/state.js";

export const syncState = {
    // Launched by FalcoType. Known from the first script on, before the bridge connects.
    managed: typeof window !== "undefined" && new URLSearchParams(window.location?.search || "").get("embedded") === "1",
    // none | loading | ready | failed. A position exists for FalcoType only when ready:
    // the pages, their sentence list and the saved place are all final by then.
    book: "none",
    // loading | ready | failed. Ready means the model and its voice list both loaded.
    engine: "loading", engineError: "",
    // The sleep timer ended where a pause can't hold: the next speech pauses instead.
    sleepPending: false,
    sessionId: null, selectionRevision: 0, selectionKey: "", generation: 0,
    transitioning: false,
    continuationFrom: null, playbackGeneration: 0,
    advancement: "command", practiceStatus: null, practiceDeadline: 0,
    practicePlaybackGeneration: 0, practiceAttempt: 0,
    playback: "idle", userPaused: false, audioEnabled: true, audioEnded: false, practiceComplete: false, bookEnded: false, error: "",
    chunk: null,
    practiceChunkSource: null,
    practiceSources: new Map(),
    runCheckpoints: new Map(), runAction: null,
    heard: new Map(), skipPlayed: false, timeCut: null, cutRecords: new Map(), skipRoute: [], skipToEnd: false, cutAwaitInput: false,
    onAudioEnded: null,
    // Follow mode (Kokoro off): the position FalcoType asked for, the id of the last one
    // opened here, and the count of the user's own moves, which retires older positions.
    follow: null, followApplied: 0, followEpoch: 0,
};

export function clearTimeCutAudio() {
    syncState.heard.clear(); syncState.skipPlayed = false;
    syncState.practiceChunkSource = null;
    syncState.practiceSources.clear();
    syncState.runCheckpoints.clear(); syncState.runAction = null;
    syncState.timeCut = null; syncState.skipRoute = []; syncState.skipToEnd = false; syncState.cutAwaitInput = false; syncState.cutRecords.clear();
}

export function recordSyncAudio() {
    if (!isSyncActive()) return;
    const page = state.readingPageIndex, source = state.currentPages[page];
    let entry = syncState.heard.get(page);
    if (!entry || entry.doc !== state.currentDoc || entry.source !== source) {
        entry = { doc: state.currentDoc, source, units: new Set() }; syncState.heard.set(page, entry);
    }
    entry.units.add(state.currentSentenceIndex);
}

export function suppressSyncAudio() {
    const entry = syncState.heard.get(state.readingPageIndex);
    if (!isSyncActive() || !syncState.skipPlayed || entry?.doc !== state.currentDoc ||
        entry.source !== state.currentPages[state.readingPageIndex] || !entry.units.has(state.currentSentenceIndex)) return false;
    syncState.audioEnded = true; syncState.playback = "ended";
    state.isPlaying = false; syncState.userPaused = false; syncState.error = "";
    return true;
}

export const isSyncActive = () => syncState.sessionId !== null;

export function invalidateSyncSelection() {
    syncState.generation++;
    syncState.transitioning = false;
    syncState.selectionRevision++;
    syncState.selectionKey = "";
    syncState.playback = "idle";
    syncState.userPaused = false;
    syncState.audioEnded = false;
    syncState.practiceComplete = false;
    syncState.bookEnded = false;
    syncState.error = "";
    syncState.continuationFrom = null;
    syncState.chunk = null;
}

// Opening a book leaves the old selection, its error and any position still on the way to it.
export function retireSyncSelection() {
    syncState.sleepPending = false;
    clearTimeCutAudio();
    invalidateSyncSelection();
    syncState.followEpoch++;
}

export function trackSyncSelection(pageRevision = 0) {
    const key = JSON.stringify([state.currentDoc?.id, state.readingPageIndex,
        state.currentSentenceIndex, pageRevision]);
    if (key !== syncState.selectionKey) {
        // Automatic selections invalidate first. A changed live key is manual
        // navigation/source replacement and retires the old no-replay context,
        // and any position FalcoType asked for before it.
        if (syncState.selectionKey) { clearTimeCutAudio(); syncState.followEpoch++; }
        invalidateSyncSelection();
        syncState.selectionKey = key;
    }
    return syncState.selectionRevision;
}

export function setSyncSession(sessionId, advancement = "command") {
    if (syncState.sessionId === sessionId && syncState.advancement === advancement) return;
    syncState.sessionId = sessionId;
    syncState.advancement = advancement;
    syncState.practiceStatus = null;
    syncState.practiceDeadline = 0;
    syncState.practiceAttempt = 0;
    syncState.audioEnabled = true;
    syncState.sleepPending = false;
    syncState.follow = null; syncState.followApplied = 0;
    clearTimeCutAudio();
    invalidateSyncSelection();
}

export function behaviorDelay(key, fallback) {
    const configured = state.behaviorSettings?.[key] ?? fallback;
    return Number.isFinite(configured) ? Math.min(600000, Math.max(0, Math.round(configured))) : 0;
}

export function syncPlaybackSnapshot() {
    return { state: syncState.playback, user_paused: syncState.userPaused, audio_enabled: syncState.audioEnabled, audio_ended: syncState.audioEnded,
        chunk: syncState.chunk,
        time_cut: syncState.timeCut,
        run_action: syncState.runAction,
        practice_complete: syncState.practiceComplete, book_ended: syncState.bookEnded,
        continuation_from: syncState.continuationFrom,
        practice_sequence: syncState.practiceStatus?.sequence || 0,
        error: syncState.error.slice(0, 1024),
        follow: { epoch: syncState.followEpoch, id: syncState.followApplied },
        media_delays: { image: behaviorDelay("Img", 3000), scene: behaviorDelay("S", 1000) } };
}
