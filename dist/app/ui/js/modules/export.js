import { state } from './state.js';
import { fetchJSON } from './api.js';
import { showToast, renderIcons } from './ui.js';

let exportPollInterval = null;
let ffmpegPollInterval = null;

// Helper to handle the UI popup flow and Esc key cancellation
function selectExportFormat() {
    return new Promise((resolve) => {
        const modal = document.getElementById('exportFormatModal');
        modal.classList.remove('hidden');
        renderIcons();

        const btnMp3 = document.getElementById('btnExportMp3');
        const btnWav = document.getElementById('btnExportWav');
        const btnCancel = document.getElementById('btnCancelFormatSelect');
        const btnCancelDesktop = document.getElementById('btnCancelFormatSelectDesktop');
        const btnClearRange = document.getElementById('btnClearRange');
        const toggleSingleChapter = document.getElementById('toggleSingleChapter');
        
        // State Machine Variables
        let startPage = null;
        let endPage = null;
        let startIndex = null; 
        let endIndex = null;   
        let isSingleMode = toggleSingleChapter.checked;
        let selectedSingleIndex = null;

        // Hoist the array so the MP3/WAV buttons can read the titles without crashing
        let tocItems = state.tocMap && state.tocMap.length > 0 
            ? [...state.tocMap] 
            : [{ title: "Start of Document", page_index: 0, level: 1 }];

        const maxPage = state.currentPages ? state.currentPages.length : 1;
        tocItems.push({ title: "End of Book", page_index: maxPage, level: 1, isVirtual: true });

        // Populate TOC for selection
        function renderExportToc() {
            const list = document.getElementById('exportTocList');
            const rangeLabel = document.getElementById('exportRangeLabel');
            list.innerHTML = '';

            const fragment = document.createDocumentFragment();

            tocItems.forEach((item, index) => {
                const div = document.createElement('div');
                div.className = 'cursor-pointer px-3 py-2.5 rounded-lg text-sm transition-all border select-none flex justify-between items-center ';
                
                // Color Logic mapped strictly to the Array Index, NOT the page_index!
                let isStart = !isSingleMode && startIndex === index && !item.isVirtual;
                let isEnd = !isSingleMode && endIndex === index;
                let isBetween = !isSingleMode && startIndex !== null && endIndex !== null && index > startIndex && index < endIndex;
                let isSingle = isSingleMode && selectedSingleIndex === index && !item.isVirtual;

                if (isSingle) {
                    div.className += 'bg-purple-500/20 border-purple-500 text-purple-300 font-bold';
                    div.innerHTML = `<span class="truncate pr-2">${item.title}</span><span class="text-[10px] bg-purple-500 text-white px-2 py-0.5 rounded-full font-bold">SELECTED</span>`;
                } else if (isStart) {
                    div.className += 'bg-green-500/20 border-green-500 text-green-300 font-bold';
                    div.innerHTML = `<span class="truncate pr-2">${item.title}</span><span class="text-[10px] bg-green-500 text-black px-2 py-0.5 rounded-full font-bold">START</span>`;
                } else if (isEnd) {
                    div.className += 'bg-red-500/20 border-red-500 text-red-300 font-bold';
                    div.innerHTML = `<span class="truncate pr-2">${item.title}</span><span class="text-[10px] bg-red-500 text-white px-2 py-0.5 rounded-full font-bold">END</span>`;
                } else if (isBetween) {
                    div.className += 'bg-blue-500/10 border-blue-500/30 text-zinc-300';
                    div.innerHTML = `<span class="truncate pr-2">${item.title}</span>`;
                } else {
                    const paddingLeft = item.level === 1 ? '0.5rem' : item.level === 2 ? '1.5rem' : '2.5rem';
                    div.style.paddingLeft = paddingLeft;
                    div.className += 'border-transparent text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200';
                    div.innerHTML = `<span class="truncate pr-2 ${item.level === 1 ? 'font-bold' : ''}">${item.title}</span>${item.isVirtual ? '' : `<span class="text-[10px] text-zinc-600">Pg ${item.page_index + 1}</span>`}`;
                }

                div.onclick = () => {
                    // Prevent selecting the "End of Book" virtual marker in Single Mode
                    if (item.isVirtual && isSingleMode) return; 
                    
                    if (isSingleMode) {
                        selectedSingleIndex = index;
                        startPage = item.page_index;
                        
                        if (tocItems[index + 1]) {
                            endPage = Math.max(startPage + 1, tocItems[index + 1].page_index);
                        } else {
                            endPage = maxPage;
                        }
                    } else {
                        if (startIndex === null) {
                            startIndex = index;
                            startPage = item.page_index;
                        } else if (endIndex === null) {
                            if (index <= startIndex) {
                                startIndex = index;
                                startPage = item.page_index;
                            } else {
                                endIndex = index;
                                endPage = item.page_index;
                            }
                        } else {
                            startIndex = index;
                            startPage = item.page_index;
                            endIndex = null;
                            endPage = null;
                        }
                    }
                    renderExportToc(); 
                };

                fragment.appendChild(div);
            });

            list.appendChild(fragment);

            if (isSingleMode && selectedSingleIndex !== null) {
                rangeLabel.textContent = `Single Chapter Selected`;
                rangeLabel.className = "text-sm font-semibold text-purple-400";
            } else if (!isSingleMode && (startIndex !== null || endIndex !== null)) {
                rangeLabel.textContent = `Custom Range Selected`;
                rangeLabel.className = "text-sm font-semibold text-green-400";
            } else {
                rangeLabel.textContent = "Entire Book (Default)";
                rangeLabel.className = "text-sm font-semibold text-blue-400";
            }
        }

        renderExportToc();

        // Dynamic Title Generator
        const getLabel = () => {
            if (isSingleMode && selectedSingleIndex !== null) {
                return tocItems[selectedSingleIndex].title;
            } else if (!isSingleMode && startIndex !== null && endIndex !== null) {
                return `${tocItems[startIndex].title} - ${tocItems[endIndex].title}`;
            }
            return "Full Book";
        };

        // Listeners & Handlers
        const doClearRange = () => {
            startPage = null;
            endPage = null;
            startIndex = null;
            endIndex = null;
            selectedSingleIndex = null;
            renderExportToc();
        };

        const onToggleMode = (e) => {
            isSingleMode = e.target.checked;
            doClearRange(); // Always clear selection when swapping modes to prevent bugs
        };

        const cleanup = () => {
            modal.classList.add('hidden');
            btnMp3.removeEventListener('click', onMp3);
            btnWav.removeEventListener('click', onWav);
            btnCancel.removeEventListener('click', onCancel);
            if (btnCancelDesktop) btnCancelDesktop.removeEventListener('click', onCancel);
            btnClearRange.removeEventListener('click', doClearRange);
            toggleSingleChapter.removeEventListener('change', onToggleMode);
            document.removeEventListener('keydown', onEsc);
        };

        const confirmExport = (format) => {
            const rangeStr = (startPage !== null || endPage !== null || isSingleMode) ? "selected chapters" : "the entire document";

            return confirm(`This will export ${rangeStr} to ${format.toUpperCase()}.\n\nContinue?`);
        };

        const onMp3 = () => { 
            if (!confirmExport('mp3')) return; // Abort if they click cancel on the estimate box
            cleanup(); 
            resolve({ format: 'mp3', startPage, endPage, fileLabel: getLabel() }); 
        };
        const onWav = () => { 
            if (!confirmExport('wav')) return; // Abort if they click cancel on the estimate box
            cleanup(); 
            resolve({ format: 'wav', startPage, endPage, fileLabel: getLabel() }); 
        };
        const onCancel = () => { cleanup(); resolve(null); };
        const onEsc = (e) => { 
            if (e.key === 'Escape') {
                e.preventDefault();
                onCancel(); 
            }
        };

        btnMp3.addEventListener('click', onMp3);
        btnWav.addEventListener('click', onWav);
        btnCancel.addEventListener('click', onCancel);
        if (btnCancelDesktop) btnCancelDesktop.addEventListener('click', onCancel);
        btnClearRange.addEventListener('click', doClearRange);
        toggleSingleChapter.addEventListener('change', onToggleMode);
        document.addEventListener('keydown', onEsc);
    });
}

export async function startExport() {
    const voiceSelect = document.getElementById('voiceSelect');
    const speedRange = document.getElementById('speedRange');

    if (!state.currentDoc) {
        showToast("No document selected");
        return;
    }
    if (!window.isEngineReady) {
        showToast("Voice engine not ready");
        return;
    }

    try {
        const exportResult = await selectExportFormat();
        if (!exportResult) return; 

        const { format, startPage, endPage, fileLabel } = exportResult;

        if (format === "mp3") {
            const status = await fetchJSON(`/api/ffmpeg/status?t=${Date.now()}`);
            if (!status.is_installed) {
                showFFMPEGDownloadModal();
                return;
            }
        }

        await fetchJSON(`/api/export/audio`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                doc_id: state.currentDoc.id,
                voice: voiceSelect.value,
                speed: parseFloat(speedRange.value),
                rules: state.rules,
                ignore_list: state.ignoreList,
                format: format,
                start_page: startPage,
                end_page: endPage,
                pause_settings: state.pauseSettings,
                behavior_settings: state.behaviorSettings,
                file_label: fileLabel
            })
        });

        const exportModal = document.getElementById('exportModal');
        const exportStatus = document.getElementById('exportStatus');

        exportModal.classList.remove('hidden');
        document.getElementById('exportComplete').classList.add('hidden');
        document.getElementById('exportError').classList.add('hidden');
        document.getElementById('exportProgress').textContent = '0%';
        document.getElementById('exportProgressBar').style.width = '0%';
        exportStatus.textContent = 'Initializing export...';
        
        const exportEta = document.getElementById('exportEta');
        if (exportEta) {
            exportEta.classList.remove('hidden');
            exportEta.textContent = 'ETA: Calculating...';
        }
        
        document.getElementById('playBtn').disabled = true;

        window.exportStartTime = Date.now(); // 🌟 Store exact start time for dynamic ETA math
        startExportPolling();

    } catch (e) {
        console.error(e);
        showToast("Export failed: " + e.message);
    }
}

function startExportPolling() {
    if (exportPollInterval) clearInterval(exportPollInterval);
    exportPollInterval = setInterval(async () => {
        try {
            const status = await fetchJSON(`/api/export/status?t=${Date.now()}`);
            if (status.error) {
                clearInterval(exportPollInterval);
                document.getElementById('exportError').classList.remove('hidden');
                document.getElementById('exportErrorMsg').textContent = status.error;
                document.getElementById('exportStatus').textContent = 'Export failed';
                document.getElementById('playBtn').disabled = false;
                return;
            }

            if (status.is_exporting) {
                const percent = status.total > 0 ? Math.round((status.progress / status.total) * 100) : 0;
                document.getElementById('exportProgress').textContent = `${percent}%`;
                document.getElementById('exportProgressBar').style.width = `${percent}%`;
                
                // 🌟 SURGICAL FIX: Dynamic ETA Calculation
                const exportEta = document.getElementById('exportEta');
                if (exportEta && status.progress > 0 && status.total > 0 && window.exportStartTime) {
                    const elapsedMs = Date.now() - window.exportStartTime;
                    
                    // Calculate speed: milliseconds per item processed
                    const msPerItem = elapsedMs / status.progress;
                    const remainingItems = status.total - status.progress;
                    
                    const remainingMs = remainingItems * msPerItem;
                    const remainingTotalSeconds = Math.max(0, Math.floor(remainingMs / 1000));
                    
                    const etaMins = Math.floor(remainingTotalSeconds / 60).toString().padStart(2, '0');
                    const etaSecs = (remainingTotalSeconds % 60).toString().padStart(2, '0');
                    
                    exportEta.textContent = `ETA: ${etaMins}:${etaSecs}`;
                }
                
                if (status.progress === status.total) {
                    document.getElementById('exportStatus').textContent = `Finalizing format...`;
                    if (exportEta) exportEta.textContent = 'Please wait...';
                } else {
                    document.getElementById('exportStatus').textContent = `Processing segment ${status.progress} of ${status.total}...`;
                }
            } else if (status.output_file) {
                clearInterval(exportPollInterval);
                document.getElementById('exportProgress').textContent = '100%';
                document.getElementById('exportProgressBar').style.width = '100%';
                document.getElementById('exportStatus').textContent = 'Export complete!';
                
                const exportEta = document.getElementById('exportEta');
                if (exportEta) exportEta.classList.add('hidden');

                document.getElementById('exportFilePath').textContent = `./Audio files/${status.output_file}`;
                document.getElementById('exportComplete').classList.remove('hidden');
                document.getElementById('playBtn').disabled = false;
                renderIcons();
            }
        } catch (e) {
            console.error("Export polling error:", e);
        }
    }, 1000);
}

export function cancelExport() {
    if (exportPollInterval) clearInterval(exportPollInterval);
    fetchJSON(`/api/export/cancel`, { method: 'POST' }).catch(console.error);
    document.getElementById('exportModal').classList.add('hidden');
    document.getElementById('playBtn').disabled = false;
}

function showFFMPEGDownloadModal() {
    const modal = document.getElementById('ffmpegModal');
    modal.classList.remove('hidden');
    document.getElementById('ffmpegDownloadSection').classList.add('hidden');
    document.getElementById('ffmpegComplete').classList.add('hidden');
    document.getElementById('ffmpegError').classList.add('hidden');
    document.getElementById('startFFMPEGDownload').classList.remove('hidden');
    renderIcons();
}

export async function startFFMPEGDownload() {
    try {
        await fetchJSON(`/api/ffmpeg/install`, { method: 'POST' });

        document.getElementById('startFFMPEGDownload').classList.add('hidden');
        document.getElementById('ffmpegDownloadSection').classList.remove('hidden');
        document.getElementById('ffmpegProgress').textContent = '0%';
        document.getElementById('ffmpegStatus').textContent = 'Starting download...';

        startFFMPEGPolling();
    } catch (e) {
        document.getElementById('ffmpegError').classList.remove('hidden');
        document.getElementById('ffmpegErrorMsg').textContent = e.message;
    }
}

function startFFMPEGPolling() {
    if (ffmpegPollInterval) clearInterval(ffmpegPollInterval);
    ffmpegPollInterval = setInterval(async () => {
        try {
            const status = await fetchJSON(`/api/ffmpeg/status?t=${Date.now()}`);
            if (status.error) {
                clearInterval(ffmpegPollInterval);
                document.getElementById('ffmpegError').classList.remove('hidden');
                document.getElementById('ffmpegErrorMsg').textContent = status.error;
                return;
            }

            if (status.is_downloading) {
                const percent = status.total > 0 ? Math.round((status.progress / status.total) * 100) : 0;
                document.getElementById('ffmpegProgress').textContent = `${percent}%`;
                document.getElementById('ffmpegProgressBar').style.width = `${percent}%`;
                document.getElementById('ffmpegStatus').textContent = status.message || 'Downloading...';
            } else if (status.is_installed) {
                clearInterval(ffmpegPollInterval);
                document.getElementById('ffmpegDownloadSection').classList.add('hidden');
                document.getElementById('ffmpegComplete').classList.remove('hidden');
                renderIcons();
                setTimeout(() => {
                    document.getElementById('ffmpegModal').classList.add('hidden');
                    startExport();
                }, 1000);
            }
        } catch (e) { console.error("FFMPEG polling error:", e); }
    }, 500);
}

export function openExportLocation() {
    // Self-service folder opening. Simple and bulletproof.
    fetchJSON(`/api/export/open-location`, { method: 'POST' })
        .then(() => {
            showToast("Opening Audio folder...");
            setTimeout(() => document.getElementById('exportModal').classList.add('hidden'), 1000);
        })
        .catch(e => showToast("Error: " + e.message));
}