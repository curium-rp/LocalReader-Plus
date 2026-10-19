// Falco practice owns a bounded cache, independent of standalone reader audio.
export class SyncSpeechCache {
    constructor(synthesize, maxBytes = 32 * 1024 * 1024) {
        this.synthesize = synthesize;
        this.maxBytes = maxBytes;
        this.entries = new Map();
        this.bytes = 0;
    }

    remove(key) {
        const entry = this.entries.get(key);
        if (!entry) return;
        entry.controller?.abort();
        this.bytes -= entry.bytes || 0;
        this.entries.delete(key);
    }

    retain(units) {
        const allowed = new Set(units.map(unit => unit.key));
        for (const key of this.entries.keys()) if (!allowed.has(key)) this.remove(key);
    }

    cancelPending() {
        for (const [key, entry] of this.entries) if (entry.controller) this.remove(key);
    }

    clear() {
        for (const key of this.entries.keys()) this.remove(key);
    }

    get(unit) {
        const existing = this.entries.get(unit.key);
        if (existing) return existing.promise;
        const controller = new AbortController();
        const entry = { controller, bytes: 0, promise: null };
        const timeout = setTimeout(() => controller.abort(), 25000);
        entry.promise = Promise.resolve().then(() => this.synthesize(unit, controller.signal)).then(buffer => {
            if (controller.signal.aborted || this.entries.get(unit.key) !== entry) {
                throw new DOMException("Audio preparation cancelled.", "AbortError");
            }
            const bytes = buffer.length * buffer.numberOfChannels * 4 || 0;
            // Oversized single buffers may play, but are not retained alongside a window.
            if (bytes > this.maxBytes) this.remove(unit.key);
            else {
                for (const [key, cached] of this.entries) {
                    if (this.bytes + bytes <= this.maxBytes) break;
                    if (key !== unit.key && !cached.controller) this.remove(key);
                }
                entry.controller = null;
                entry.bytes = bytes;
                this.bytes += bytes;
            }
            return buffer;
        }).catch(error => {
            if (this.entries.get(unit.key) === entry) this.remove(unit.key);
            throw error;
        }).finally(() => clearTimeout(timeout));
        this.entries.set(unit.key, entry);
        return entry.promise;
    }

    async preload(units, valid) {
        for (const unit of units) {
            if (!valid()) return;
            try { await this.get(unit); }
            catch (error) { if (error.name === "AbortError" || !valid()) return; }
        }
    }
}
