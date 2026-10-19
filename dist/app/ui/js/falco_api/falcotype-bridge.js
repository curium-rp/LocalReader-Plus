import { state } from "../modules/state.js";
import { getSyncPageContent } from "../modules/library.js";
import { syncState, trackSyncSelection, syncPlaybackSnapshot } from "./falcotype-sync-state.js";
import { applySyncCommand, changeSyncSession, applyPracticeStatus, maybeContinuePractice, practiceAdvanceGate, holdPracticeMedia } from "./falcotype-sync.js";
import { stopPlayback } from "../modules/tts.js";

const historyLabel = value => [...String(value || "").replace(/[\uD800-\uDFFF]/gu, "\uFFFD").normalize("NFC")].slice(0, 512).join("");
const filenameLabel = value => historyLabel(String(value || "").replace(/\\/g, "/").split("/").at(-1));

export function currentSelection(page = getSyncPageContent()) {
    if (!page || syncState.transitioning) return null;
    const previousRevision = syncState.selectionRevision;
    const revision = trackSyncSelection(page.page_revision);
    if (syncState.sessionId && revision !== previousRevision && (state.isPlaying || state.currentAudioSource)) stopPlayback();
    return { book_id: page.book_id, title: historyLabel(state.currentDoc.title) || filenameLabel(state.currentDoc.fileName),
        page_index: page.page_index, page_revision: page.page_revision,
        sentence_index: state.currentSentenceIndex, sentence_count: page.units.length,
        selection_revision: revision, kind: page.units[state.currentSentenceIndex].kind };
}

export function historyPageMetadata(page) {
    const toc = (Array.isArray(state.tocMap) ? state.tocMap : []).filter(item => item && Number.isInteger(item.page_index) &&
        item.page_index >= 0 && Number.isFinite(Number(item.level)));
    const level = toc.length ? Math.min(...toc.map(item => Number(item.level))) : 0;
    const top = toc.filter(item => Number(item.level) === level).sort((a, b) => a.page_index - b.page_index);
    const byId = new Map(top.map((item, index) => [JSON.stringify([item.page_index,
        String(item.target_tts_id || item.id || item.anchor_id || "").slice(0, 512)]), { title: historyLabel(item.title), index }]));
    return { ...page, filename: filenameLabel(state.currentDoc?.fileName),
        chapters: (page.chapters || []).map(scope => ({ ...scope, ...(byId.get(scope.id) || {}) })) };
}

export async function initFalcoTypeBridge() {
    if (new URLSearchParams(window.location.search).get("embedded") !== "1") return;
    let stopped = false, contextReady = false, pageKey = "", ack = null, applying = null, retiredSession = null;
    let timer, leaseTimer, controller;
    const request = async (path, body) => {
        const requestController = new AbortController();
        controller = requestController;
        const timeout = setTimeout(() => requestController.abort(), path === "page" ? 4000 : 1500);
        try {
            const response = await fetch(`/api/falcotype/${path}`, {
                method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body), signal: requestController.signal, cache: "no-store",
            });
            if (!response.ok) {
                const result = await response.json().catch(() => ({}));
                const error = new Error(typeof result?.detail === "string" ? result.detail : "Reader sync request failed.");
                error.status = response.status;
                throw error;
            }
            return await response.json();
        } finally { clearTimeout(timeout); }
    };
    const updateLease = (sync, transitMs = 0) => {
        // Never revive a locally expired session from a delayed heartbeat response.
        if (stopped || (sync.session_id && sync.session_id === retiredSession)) return false;
        clearTimeout(leaseTimer);
        const remaining = Math.max(0, sync.remaining_ms - transitMs);
        const expireLease = () => {
            retiredSession = sync.session_id;
            changeSyncSession(null);
            ack = null;
            applying = null;
        };
        // A response that used up its remaining lease cannot authorize work on arrival.
        if (sync.session_id && remaining <= 0) { expireLease(); return false; }
        if (sync.session_id !== syncState.sessionId) {
            changeSyncSession(sync.session_id, sync.advancement || "command");
            ack = null;
            applying = null;
        }
        if (sync.session_id) leaseTimer = setTimeout(expireLease, remaining);
        return true;
    };
    const publish = async () => {
        try {
            if (!contextReady) {
                const context = await request("context");
                if (stopped) return;
                if (!context.enabled) { stopped = true; return; }
                contextReady = true;
            }
            const page = syncState.transitioning ? null : getSyncPageContent();
            const key = page ? JSON.stringify([page.book_id, page.page_index, page.page_revision]) : "";
            if (page && key !== pageKey) {
                await request("page", historyPageMetadata(page));
                if (stopped) return;
                pageKey = key;
            } else if (!page && !syncState.transitioning) pageKey = "";
            const selectedPage = syncState.transitioning ? null : getSyncPageContent();
            const selectedKey = selectedPage ? JSON.stringify([selectedPage.book_id, selectedPage.page_index, selectedPage.page_revision]) : "";
            if (key !== selectedKey) return;
            const sentAt = performance.now();
            const result = await request("current", { current: currentSelection(selectedPage),
                playback: { ...syncPlaybackSnapshot(), advance_gate: practiceAdvanceGate() },
                session_id: syncState.sessionId, ack, transitioning: syncState.transitioning });
            if (stopped) return;
            const transitMs = performance.now() - sentAt;
            if (!updateLease(result.sync, transitMs)) return;
            applyPracticeStatus(result.practice, transitMs);
            const command = result.command;
            if (command && !applying && command.id !== ack?.id) {
                const job = { session: command.session_id };
                applying = job;
                // Keep lease/control polling alive while a chapter is loading.
                applySyncCommand(command).then(() => {
                    if (applying === job && syncState.sessionId === job.session) ack = { id: command.id, status: "applied", error: "" };
                }).catch(error => {
                    if (applying === job && syncState.sessionId === job.session) ack = { id: command.id, status: "rejected", error: error.message.slice(0, 1024) };
                }).finally(() => { if (applying === job) applying = null; });
            }
            if (!applying) void maybeContinuePractice();
        } catch (error) {
            holdPracticeMedia();
            const temporary = error.name === "AbortError" || error instanceof TypeError || error.status >= 500;
            if (!contextReady) {
                // Startup can race server readiness. Retry transient failures without a reload.
                if (!temporary) stopped = true;
                return;
            }
            if (error.status === 409) pageKey = "";
            // Network retries must not publish a false "no book" or discard the host's RAM run.
            // Invalid/oversized content is still explicit; the watchdog handles connection loss.
            if (!stopped && !temporary && error.status !== 409) {
                pageKey = "";
                try {
                    const sentAt = performance.now();
                    const result = await request("current", { current: null,
                        playback: { ...syncPlaybackSnapshot(), state: "error", error: error.message.slice(0, 1024) },
                        session_id: syncState.sessionId });
                    if (!stopped) updateLease(result.sync, performance.now() - sentAt);
                } catch { /* The independent lease timer stops audio if the connection stays down. */ }
            }
        } finally {
            if (!stopped) timer = setTimeout(publish, contextReady ? 250 : 1000);
        }
    };
    window.addEventListener("pagehide", () => {
        stopped = true;
        clearTimeout(timer);
        clearTimeout(leaseTimer);
        controller?.abort();
        changeSyncSession(null);
    }, { once: true });
    publish();
}
