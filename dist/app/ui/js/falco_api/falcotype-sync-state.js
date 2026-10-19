import { state } from "../modules/state.js";

export const syncState = {
    sessionId: null, selectionRevision: 0, selectionKey: "", generation: 0,
    transitioning: false,
    continuationFrom: null, playbackGeneration: 0,
    advancement: "command", practiceStatus: null, practiceDeadline: 0,
    practicePlaybackGeneration: 0, practiceAttempt: 0,
    playback: "idle", userPaused: false, audioEnabled: true, audioEnded: false, practiceComplete: false, bookEnded: false, error: "",
    mediaWaiting: false, mediaRemaining: 0,
    chunk: null,
    practiceChunkSource: null,
    practiceSources: new Map(),
    runCheckpoints: new Map(), runAction: null,
    heard: new Map(), skipPlayed: false, timeCut: null, cutRecords: new Map(), skipRoute: [], skipToEnd: false, cutAwaitInput: false,
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
    syncState.mediaWaiting = false; syncState.mediaRemaining = 0;
}

export function trackSyncSelection(pageRevision = 0) {
    const key = JSON.stringify([state.currentDoc?.id, state.readingPageIndex,
        state.currentSentenceIndex, pageRevision]);
    if (key !== syncState.selectionKey) {
        // Automatic selections invalidate first. A changed live key is manual
        // navigation/source replacement and retires the old no-replay context.
        if (syncState.selectionKey) clearTimeCutAudio();
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
    clearTimeCutAudio();
    invalidateSyncSelection();
}

export function syncPlaybackSnapshot() {
    return { state: syncState.playback, user_paused: syncState.userPaused, audio_enabled: syncState.audioEnabled, audio_ended: syncState.audioEnded,
        chunk: syncState.chunk,
        time_cut: syncState.timeCut,
        run_action: syncState.runAction,
        practice_complete: syncState.practiceComplete, book_ended: syncState.bookEnded,
        continuation_from: syncState.continuationFrom,
        practice_sequence: syncState.practiceStatus?.sequence || 0,
        media_waiting: syncState.mediaWaiting, media_remaining_ms: Math.max(0, Math.ceil(syncState.mediaRemaining)),
        error: syncState.error.slice(0, 1024) };
}
