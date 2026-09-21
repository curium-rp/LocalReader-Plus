import { state } from './state.js';
import { fetchJSON } from './api.js';
import { renderIcons, showToast } from './ui.js';

export function updateEngineStatusUI(status, selectedModelExists) {
    const engineStatusDot = document.getElementById('engineStatusDot');
    const setupArea = document.getElementById('setupArea');
    const setupBtn = document.getElementById('setupBtn');
    const exportArea = document.getElementById('exportArea');
    const drawerActiveModelName = document.getElementById('drawerActiveModelName');
    const gpuStatusEl = document.getElementById('gpuStatus');
    const cpuStatusEl = document.getElementById('cpuStatus');

    const gpuReady = !!status.available_models?.['kokoro-v1.0'] || !!status.available_models?.gpu;
    const cpuReady = !!status.available_models?.['kokoro-v1.0-int8'] || !!status.available_models?.cpu;

    if (gpuStatusEl) {
        gpuStatusEl.innerHTML = `<span class="${gpuReady ? 'text-green-400 font-bold' : 'text-zinc-600'}">${gpuReady ? '✓' : '✗'}</span> fp32: Best quality`;
    }
    if (cpuStatusEl) {
        cpuStatusEl.innerHTML = `<span class="${cpuReady ? 'text-green-400 font-bold' : 'text-zinc-600'}">${cpuReady ? '✓' : '✗'}</span> int8: Faster, low RAM`;
    }

    const hasAnyModel = Object.values(status.available_models || {}).some(v => v === true);

    if (drawerActiveModelName && status.active_model) {
        const modelNames = {
            'kokoro-v1.1': 'Kokoro v1.1-zh (FP32)',
            'kokoro-v1.0': 'Kokoro v1.0 (FP32)',
            'kokoro-v1.0-int8': 'Kokoro v1.0 (INT8)'
        };
        drawerActiveModelName.textContent = modelNames[status.active_model] || status.active_model;
    }

    const setEngineDot = (className) => {
        if (engineStatusDot) engineStatusDot.className = `sidebar-status-dot ${className}`;
    };

    if (status.is_downloading) {
        setEngineDot("w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse");
        if (exportArea) exportArea.style.display = 'none';
        if (setupArea) setupArea.style.display = 'block';
        if (setupBtn) {
            setupBtn.disabled = true;
            const pct = status.download_progress ? ` (${status.download_progress}%)` : '';
            setupBtn.innerHTML = `<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i><span class="text-xs font-bold">Downloading${pct}...</span>`;
        }
        renderIcons();
    } else if (status.is_loading && hasAnyModel) {
        setEngineDot("w-2.5 h-2.5 rounded-full bg-yellow-500 animate-pulse");
        if (setupArea) setupArea.style.display = 'none';
        if (exportArea) exportArea.style.display = 'none';
    } else if (status.model_loaded) {
        setEngineDot("w-2.5 h-2.5 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]");
        if (setupArea) setupArea.style.display = 'none';
        if (state.currentDoc) {
            if (exportArea) exportArea.style.display = 'block';
        } else {
            if (exportArea) exportArea.style.display = 'none';
        }
    } else {
        setEngineDot("w-2.5 h-2.5 rounded-full bg-red-600");
        if (exportArea) exportArea.style.display = 'none';
        if (setupArea) setupArea.style.display = 'block';
        if (setupBtn) {
            setupBtn.disabled = false;
            setupBtn.innerHTML = '<i data-lucide="download-cloud" class="w-3.5 h-3.5"></i><span class="text-xs font-bold">Setup Voice Engine</span>';
        }
        renderIcons();
    }
}

// --- Initial Setup / Legacy Popup Handling ---
export function showInt8WarningModal(onConfirm) {
    let modal = document.getElementById("int8WarningModal");
    if (!modal) {
        modal = document.createElement("div");
        modal.id = "int8WarningModal";
        modal.className = "fixed inset-0 z-[9999] flex items-center justify-center bg-black/70 backdrop-blur-sm p-4";
        document.body.appendChild(modal);
    }
    modal.innerHTML = `
      <div class="bg-zinc-900 border border-zinc-700 rounded-xl p-5 max-w-md w-full shadow-2xl space-y-4 text-white">
        <div class="flex items-center gap-2.5 text-amber-400 font-bold text-sm">
          <svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
          </svg>
          <span>Hardware Acceleration Notice</span>
        </div>
        <div class="text-xs text-zinc-300 leading-relaxed space-y-2">
          <p>Please try <strong class="text-green-400">FP32</strong> first!</p>
          <p>The <strong class="text-zinc-100">INT8</strong> model requires specialized CPU hardware acceleration (<code class="bg-zinc-800 px-1 py-0.5 rounded text-zinc-300 font-mono text-[11px]">AVX-512 VNNI</code>). Without it, INT8 is actually <span class="text-red-400 font-semibold">slower and uses more CPU</span> than FP32.</p>
        </div>
        <div class="flex items-center justify-end gap-2.5 pt-2 text-xs">
          <button id="int8CancelBtn" class="py-1.5 px-3.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors font-medium">
            Cancel
          </button>
          <button id="int8ConfirmBtn" class="py-1.5 px-3.5 bg-amber-600 hover:bg-amber-500 text-white rounded-lg transition-colors font-semibold shadow-md shadow-amber-900/20">
            Download INT8 Anyway
          </button>
        </div>
      </div>
    `;
    modal.classList.remove("hidden");

    const close = () => modal.classList.add("hidden");

    modal.querySelector("#int8ConfirmBtn").onclick = () => {
        close();
        if (onConfirm) onConfirm();
    };
    modal.querySelector("#int8CancelBtn").onclick = () => {
        close();
    };
    modal.onclick = (e) => {
        if (e.target === modal) close();
    };
}

export async function startKokoroDownload(modelType) {
    if (modelType !== "gpu" && modelType !== "cpu") return;
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
        showToast("No internet connection. Please check your network.");
        return;
    }
    try {
        await fetchJSON(`/api/system/setup?model_type=${modelType}`, { method: "POST" });
        showToast(modelType === "gpu" ? "Downloading FP32 model..." : "Downloading INT8 model...");
    } catch (e) {
        const errMsg = (typeof navigator !== "undefined" && !navigator.onLine)
            ? "No internet connection. Please check your network."
            : (e.message || "Download failed");
        showToast(errMsg);
    }
}

let engineDownloadWired = false;
export function wireEngineDownloadPopup() {
    if (engineDownloadWired) return;
    engineDownloadWired = true;
    const onPick = (e) => {
        e.stopPropagation();
        const btn = e.target.closest("[data-download-model]");
        if (!btn || btn.disabled) return;
        const modelType = btn.dataset.downloadModel;
        const popup = e.currentTarget;
        if (popup) popup.classList.add("hidden");

        if (modelType === "cpu") {
            showInt8WarningModal(
                () => startKokoroDownload("cpu")
            );
        } else {
            startKokoroDownload("gpu");
        }
    };
    document.getElementById("engineStatusPopup")?.addEventListener("click", onPick);
    document.getElementById("kokoroDownloadPopup")?.addEventListener("click", onPick);
}

// --- Model Management Modal ---
let modelManagerPollTimer = null;

function startModelManagerPoll() {
    stopModelManagerPoll();
    modelManagerPollTimer = setInterval(async () => {
        const modal = document.getElementById('modelManagerModal');
        if (!modal || modal.classList.contains('hidden')) {
            stopModelManagerPoll();
            return;
        }
        await refreshModelManagerData();
    }, 1000);
}

function stopModelManagerPoll() {
    if (modelManagerPollTimer) {
        clearInterval(modelManagerPollTimer);
        modelManagerPollTimer = null;
    }
}

export async function openModelManagerModal() {
    const modal = document.getElementById('modelManagerModal');
    if (!modal) return;
    modal.classList.remove('hidden');
    renderIcons();
    await refreshModelManagerData();
    startModelManagerPoll();
}

export function closeModelManagerModal() {
    const modal = document.getElementById('modelManagerModal');
    if (modal) modal.classList.add('hidden');
    stopModelManagerPoll();
}

export async function refreshModelManagerData() {
    try {
        const data = await fetchJSON('/api/system/models');
        renderModelManagerCatalog(data);
    } catch (e) {
        console.error('Failed to fetch model catalog:', e);
        const statusMsg = document.getElementById('modelManagerStatusMsg');
        if (statusMsg) statusMsg.textContent = 'Failed to load model catalog';
    }
}

let _lastModelCatalogFingerprint = null;
let _wasDownloading = false;
let _lastReportedError = null;

function getModelCatalogFingerprint(data) {
    // Captures everything that changes card structure: install state, active model, download target, error
    return JSON.stringify({
        active: data.active_model,
        models: (data.models || []).map((m) => ({
            id: m.id,
            installed: m.installed,
            is_active: m.is_active,
            disk_size_mb: m.disk_size_mb,
            download_size_mb: m.download_size_mb,
            voices_already_installed: m.voices_already_installed,
        })),
        is_loading: data.is_loading,
        is_downloading: data.is_downloading,
        downloading_model: data.downloading_model,
        last_error: data.last_error,
    });
}

function buildCardHtml(model, data) {
    // isActive requires the model to be both installed on disk AND the active engine.
    // On fresh/freeze start the backend returns is_active=false for all uninstalled models.
    const isActive = Boolean(model.installed && model.is_active);
    const isDownloading = data.is_downloading && data.downloading_model === model.id;

    const langBadges = (model.languages || [])
        .map((lang) => `<span class="px-1.5 py-0.5 rounded text-[10px] bg-zinc-800 text-zinc-400 font-medium">${lang}</span>`)
        .join(' ');

    let tagBadge = '';
    if (model.id === 'kokoro-v1.0') {
        tagBadge = `<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-green-500/20 text-green-400 border border-green-500/40">Recommended</span>`;
    } else if (model.id === 'kokoro-v1.0-int8') {
        tagBadge = `<span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-800 text-zinc-400 border border-zinc-700">Fast / Low RAM</span>`;
    } else if (model.id === 'kokoro-v1.1') {
        tagBadge = `<span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-300 border border-amber-500/30">Optional (ZH/EN)</span>`;
    }

    const dlSize = model.download_size_mb ?? model.total_size_mb;
    const hasSharedVoicesReady = !model.installed && model.voices_already_installed;

    let actionHtml = '';
    if (isActive) {
        // In Use: show badge only. No delete button — user must switch first.
        actionHtml = `
            <span class="px-3 py-1.5 rounded-lg text-xs font-bold bg-green-500/20 text-green-400 border border-green-500/40 flex items-center gap-1.5">
                <i data-lucide="check" class="w-3.5 h-3.5"></i>
                <span>In Use</span>
            </span>
        `;
    } else if (model.installed) {
        actionHtml = `
            <div class="flex items-center gap-2">
                <button
                    type="button"
                    class="btn-select-model px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold transition-colors flex items-center gap-1.5 shadow-md shadow-blue-900/20"
                    data-model-id="${model.id}"
                    ${data.is_loading || data.is_downloading ? 'disabled' : ''}
                >
                    <i data-lucide="check-circle" class="w-3.5 h-3.5"></i>
                    <span>Use Model</span>
                </button>
                <button
                    type="button"
                    class="btn-delete-model p-1.5 text-zinc-400 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                    data-model-id="${model.id}"
                    title="Delete model files"
                    ${data.is_loading || data.is_downloading ? 'disabled' : ''}
                >
                    <i data-lucide="trash-2" class="w-4 h-4"></i>
                </button>
            </div>
        `;
    } else if (isDownloading) {
        const pctText = data.download_progress ? ` (${data.download_progress}%)` : '';
        actionHtml = `
            <span class="px-3 py-1.5 rounded-lg text-xs font-bold bg-blue-500/20 text-blue-400 border border-blue-500/40 flex items-center gap-1.5 animate-pulse">
                <i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>
                <span>Downloading${pctText}...</span>
            </span>
        `;
    } else {
        const sharedVoiceNote = hasSharedVoicesReady ? ' <span class="text-[10px] text-zinc-400 font-normal">(voices ready)</span>' : '';
        actionHtml = `
            <button
                type="button"
                class="btn-download-model px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 rounded-lg text-xs font-semibold transition-colors flex items-center gap-1.5"
                data-model-id="${model.id}"
                ${data.is_downloading ? 'disabled' : ''}
            >
                <i data-lucide="download" class="w-3.5 h-3.5"></i>
                <span>Download (${dlSize} MB)${sharedVoiceNote}</span>
            </button>
        `;
    }

    const sizeBadgeHtml = model.installed
        ? `<span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-emerald-950 text-emerald-400 border border-emerald-800">Installed (${model.disk_size_mb} MB)</span>`
        : hasSharedVoicesReady
        ? `<span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-800/80 text-zinc-400" title="Voice pack already downloaded, only ${dlSize} MB model weights needed">${dlSize} MB (voices shared)</span>`
        : `<span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-800/80 text-zinc-500">${dlSize} MB</span>`;

    return {
        cardClass: `p-4 rounded-xl border transition-all ${
            isActive
                ? 'bg-zinc-900/90 border-blue-500/50 shadow-lg shadow-blue-500/5'
                : 'bg-zinc-900/40 border-zinc-800 hover:border-zinc-700'
        }`,
        innerHTML: `
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div class="space-y-1">
                    <div class="flex items-center gap-2">
                        <span class="font-bold text-sm text-zinc-100">${model.name}</span>
                        <span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-zinc-800 text-blue-400 border border-zinc-700">
                            ${model.precision}
                        </span>
                        ${tagBadge}
                        ${sizeBadgeHtml}
                    </div>
                    <p class="text-xs text-zinc-400">${model.description}</p>
                    <div class="flex flex-wrap items-center gap-1.5 pt-1">
                        <span class="text-[10px] font-semibold text-zinc-500">${model.voice_count} voices:</span>
                        ${langBadges}
                    </div>
                </div>
                <div class="shrink-0 flex items-center sm:self-center">
                    ${actionHtml}
                </div>
            </div>
        `,
    };
}

function wireCardListHandlers(cardList, data) {
    cardList.querySelectorAll('.btn-select-model').forEach((btn) => {
        btn.onclick = async () => {
            const modelId = btn.dataset.modelId;
            btn.disabled = true;
            btn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i><span>Switching...</span>';
            renderIcons();
            try {
                showToast(`Switching to ${modelId}...`);
                state.audioBufferCache?.clear();
                await fetchJSON('/api/system/models/select', {
                    method: 'POST',
                    body: JSON.stringify({ model_id: modelId }),
                });

                // Poll until model switch completes in backend engine
                let attempts = 0;
                while (attempts < 30) {
                    await new Promise((r) => setTimeout(r, 400));
                    attempts++;
                    try {
                        const status = await fetchJSON(`/api/system/status?t=${Date.now()}`);
                        if (!status.is_loading && status.active_model === modelId && status.model_loaded) {
                            break;
                        }
                    } catch (_) {}
                }

                _lastModelCatalogFingerprint = null; // force full re-render
                await refreshModelManagerData();

                // Refresh main reader voices for the new model
                if (typeof window.loadVoices === 'function') {
                    await window.loadVoices();
                }

                // Refresh blending slots and voice choices for the new model
                if (typeof window.refreshBlendingVoices === 'function') {
                    await window.refreshBlendingVoices();
                }

                showToast(`Successfully switched to ${modelId}`);
            } catch (e) {
                showToast(e.message || 'Failed to switch model');
                _lastModelCatalogFingerprint = null;
                await refreshModelManagerData();
            }
        };
    });

    cardList.querySelectorAll('.btn-download-model').forEach((btn) => {
        btn.onclick = async () => {
            const modelId = btn.dataset.modelId;

            const executeDownload = async () => {
                if (typeof navigator !== "undefined" && navigator.onLine === false) {
                    showToast("No internet connection. Please check your network.");
                    return;
                }
                btn.disabled = true;
                btn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i><span>Starting...</span>';
                renderIcons();
                try {
                    showToast(`Download started for ${modelId}...`);
                    await fetchJSON('/api/system/models/download', {
                        method: 'POST',
                        body: JSON.stringify({ model_id: modelId }),
                    });
                    startModelManagerPoll();
                    _lastModelCatalogFingerprint = null;
                    await refreshModelManagerData();
                } catch (e) {
                    const errMsg = (typeof navigator !== "undefined" && !navigator.onLine)
                        ? "No internet connection. Please check your network."
                        : (e.message || 'Download failed');
                    showToast(errMsg);
                    _lastModelCatalogFingerprint = null;
                    await refreshModelManagerData();
                }
            };

            if (modelId === 'kokoro-v1.0-int8') {
                showInt8WarningModal(() => executeDownload());
            } else {
                await executeDownload();
            }
        };
    });

    cardList.querySelectorAll('.btn-delete-model').forEach((btn) => {
        btn.onclick = async () => {
            const modelId = btn.dataset.modelId;
            const targetModel = (data.models || []).find((m) => m.id === modelId);
            const name = targetModel ? targetModel.name : modelId;
            const size = targetModel ? targetModel.disk_size_mb : 0;
            if (!confirm(`Delete ${name} files from disk (${size} MB)?`)) return;

            btn.disabled = true;
            try {
                showToast(`Deleting ${name}...`);
                const res = await fetchJSON('/api/system/models/delete', {
                    method: 'POST',
                    body: JSON.stringify({ model_id: modelId }),
                });
                showToast(res.message || 'Deleted model files');
                state.audioBufferCache?.clear();
                _lastModelCatalogFingerprint = null;
                await refreshModelManagerData();
                if (typeof window.loadVoices === 'function') {
                    await window.loadVoices();
                }
            } catch (e) {
                showToast(e.message || 'Delete failed');
                _lastModelCatalogFingerprint = null;
                await refreshModelManagerData();
            }
        };
    });
}

function renderModelManagerCatalog(data) {
    const cardList = document.getElementById('modelManagerCardList');
    const hwBadge = document.getElementById('modelManagerHardwareBadge');
    const statusMsg = document.getElementById('modelManagerStatusMsg');

    if (hwBadge) {
        const hwText = (data.active_hardware || 'CPU').toUpperCase();
        hwBadge.textContent = hwText === 'GPU' ? 'GPU (CUDA / DirectML)' : hwText;
        hwBadge.className = `px-2 py-0.5 rounded text-[11px] font-mono font-semibold ${
            hwText.includes('GPU') ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-zinc-800 text-zinc-300'
        }`;
    }

    if (statusMsg) {
        if (data.is_downloading) {
            const pctText = data.download_progress ? ` (${data.download_progress}%)` : '';
            statusMsg.textContent = data.download_stage || `Downloading ${data.downloading_model || 'model'}${pctText}...`;
            statusMsg.className = 'text-xs text-zinc-400';
        } else if (data.last_error && !data.is_loading) {
            statusMsg.textContent = data.last_error;
            statusMsg.className = 'text-xs text-red-400 font-medium';
        } else if (data.is_loading) {
            statusMsg.textContent = 'Loading engine...';
            statusMsg.className = 'text-xs text-zinc-400';
        } else {
            statusMsg.textContent = '';
            statusMsg.className = 'text-xs text-zinc-400';
        }
    }

    // Trigger toast if background download failed with an error
    if (_wasDownloading && !data.is_downloading && data.last_error && data.last_error !== _lastReportedError) {
        _lastReportedError = data.last_error;
        showToast(data.last_error);
    } else if (data.is_downloading) {
        _lastReportedError = null;
    }
    _wasDownloading = Boolean(data.is_downloading);

    if (!cardList) return;

    const fingerprint = getModelCatalogFingerprint(data);
    if (fingerprint === _lastModelCatalogFingerprint) {
        // Only progress text changed — update downloading card's action area in-place, no DOM wipe
        if (data.is_downloading && data.downloading_model) {
            const pctText = data.download_progress ? ` (${data.download_progress}%)` : '';
            cardList.querySelectorAll('[data-model-card-id]').forEach((card) => {
                if (card.dataset.modelCardId === data.downloading_model) {
                    const actionEl = card.querySelector('.model-card-action');
                    if (actionEl) {
                        actionEl.innerHTML = `
                            <span class="px-3 py-1.5 rounded-lg text-xs font-bold bg-blue-500/20 text-blue-400 border border-blue-500/40 flex items-center gap-1.5 animate-pulse">
                                <i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>
                                <span>Downloading${pctText}...</span>
                            </span>
                        `;
                        renderIcons();
                    }
                }
            });
        }
        return;
    }
    _lastModelCatalogFingerprint = fingerprint;

    // Full re-render (structure changed)
    cardList.innerHTML = '';

    (data.models || []).forEach((model) => {
        const card = document.createElement('div');
        card.dataset.modelCardId = model.id;
        const { cardClass, innerHTML } = buildCardHtml(model, data);
        card.className = cardClass;

        const inner = document.createElement('div');
        inner.innerHTML = innerHTML;
        // Wrap action area so we can update it in-place
        const actionWrap = inner.querySelector('.shrink-0');
        if (actionWrap) actionWrap.classList.add('model-card-action');

        card.appendChild(inner.firstElementChild);
        cardList.appendChild(card);
    });

    renderIcons();
    wireCardListHandlers(cardList, data);
}

let modelManagerWired = false;
export function wireModelManagerModal() {
    if (modelManagerWired) return;
    modelManagerWired = true;

    const modal = document.getElementById('modelManagerModal');
    const closeBtn = document.getElementById('closeModelManagerModalBtn');
    const triggerBtn = document.getElementById('modelManagerBtn');
    const drawerBtn = document.getElementById('openModelManagerFromDrawerBtn');
    const setupBtn = document.getElementById('setupBtn');

    if (closeBtn) closeBtn.addEventListener('click', closeModelManagerModal);
    if (triggerBtn) triggerBtn.addEventListener('click', () => openModelManagerModal());
    if (drawerBtn) drawerBtn.addEventListener('click', () => openModelManagerModal());
    if (setupBtn) setupBtn.addEventListener('click', () => openModelManagerModal());

    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModelManagerModal();
        });
    }
}

export function initDownloader() {
    wireModelManagerModal();
    wireEngineDownloadPopup();
}

// Auto-wire listeners on module load
initDownloader();

