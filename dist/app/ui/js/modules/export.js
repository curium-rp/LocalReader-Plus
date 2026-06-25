import { state } from './state.js';
import { fetchJSON } from './api.js';
import { showToast, renderIcons } from './ui.js';

let exportPollInterval = null;
let ffmpegPollInterval = null;

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
        const toggleSeparateFiles = document.getElementById('toggleSeparateFiles'); // 🌟 NEW
        
        let startPage = null;
        let endPage = null;
        let startIndex = null; 
        let endIndex = null;   
        let isSingleMode = toggleSingleChapter.checked;
        let isSeparateMode = toggleSeparateFiles.checked; // 🌟 NEW
        let selectedSingleIndex = null;

        let tocItems = state.tocMap && state.tocMap.length > 0 
            ? [...state.tocMap] 
            : [{ title: "Start of Document", page_index: 0, level: 1 }];

        const maxPage = state.currentPages ? state.currentPages.length : 1;
        tocItems.push({ title: "End of Book", page_index: maxPage, level: 1, isVirtual: true });

        function renderExportToc() {
            const list = document.getElementById('exportTocList');
            const rangeLabel = document.getElementById('exportRangeLabel');
            list.innerHTML = '';

            const fragment = document.createDocumentFragment();

            tocItems.forEach((item, index) => {
                const div = document.createElement('div');
                div.className = 'cursor-pointer px-3 py-2.5 rounded-lg text-sm transition-all border select-none flex justify-between items-center ';
                
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
                if (isSeparateMode) {
                    rangeLabel.textContent = `Separate Files Queue`;
                    rangeLabel.className = "text-sm font-semibold text-orange-400";
                } else {
                    rangeLabel.textContent = `Custom Range Selected`;
                    rangeLabel.className = "text-sm font-semibold text-green-400";
                }
            } else {
                rangeLabel.textContent = "Entire Book (Default)";
                rangeLabel.className = "text-sm font-semibold text-blue-400";
            }
        }

        renderExportToc();

        // 🌟 SURGICAL FIX: Task Generator (Converts logic into a queue)
        const getTasks = (format) => {
            let tasks = [];
            
            if (isSingleMode && selectedSingleIndex !== null) {
                tasks.push({ format, startPage, endPage, fileLabel: tocItems[selectedSingleIndex].title });
            } else if (!isSingleMode && startIndex !== null && endIndex !== null) {
                if (isSeparateMode) {
                    // SEPARATE MODE: Auto-slice the range into multiple individual tasks
                    for (let i = startIndex; i < endIndex; i++) {
                        let sPage = tocItems[i].page_index;
                        let ePage = tocItems[i+1] ? tocItems[i+1].page_index : maxPage;
                        if (sPage >= ePage) ePage = sPage + 1;
                        tasks.push({ format, startPage: sPage, endPage: ePage, fileLabel: tocItems[i].title });
                    }
                } else {
                    // COMBINED MODE: One massive file
                    tasks.push({ format, startPage, endPage, fileLabel: `${tocItems[startIndex].title} - ${tocItems[endIndex].title}` });
                }
            } else {
                // FULL BOOK
                tasks.push({ format, startPage: null, endPage: null, fileLabel: "Full Book" });
            }
            return tasks;
        };

        const confirmExport = (tasks, format) => {
            let rangeStr = "";
            if (tasks.length > 1) {
                rangeStr = `a queue of ${tasks.length} separate chapters`;
            } else if (isSingleMode || (startIndex !== null && endIndex !== null)) {
                rangeStr = `selected chapter(s)`;
            } else {
                rangeStr = `the entire document`;
            }
            return confirm(`This will export ${rangeStr} to ${format.toUpperCase()}.\n\nContinue?`);
        };

        const doClearRange = () => {
            startPage = null; endPage = null; startIndex = null; endIndex = null; selectedSingleIndex = null;
            renderExportToc();
        };

        // 🌟 SURGICAL FIX: Mutual Exclusion Toggles
        const onToggleSingle = (e) => {
            isSingleMode = e.target.checked;
            if (isSingleMode) {
                isSeparateMode = false;
                toggleSeparateFiles.checked = false;
            }
            doClearRange(); 
        };

        const onToggleSeparate = (e) => {
            isSeparateMode = e.target.checked;
            if (isSeparateMode) {
                isSingleMode = false;
                toggleSingleChapter.checked = false;
            }
            renderExportToc();
        };

        const cleanup = () => {
            modal.classList.add('hidden');
            btnMp3.removeEventListener('click', onMp3);
            btnWav.removeEventListener('click', onWav);
            btnCancel.removeEventListener('click', onCancel);
            if (btnCancelDesktop) btnCancelDesktop.removeEventListener('click', onCancel);
            btnClearRange.removeEventListener('click', doClearRange);
            toggleSingleChapter.removeEventListener('change', onToggleSingle);
            toggleSeparateFiles.removeEventListener('change', onToggleSeparate);
            document.removeEventListener('keydown', onEsc);
        };

        const onMp3 = () => { 
            const tasks = getTasks('mp3');
            if (!confirmExport(tasks, 'mp3')) return; 
            cleanup(); resolve(tasks); 
        };
        const onWav = () => { 
            const tasks = getTasks('wav');
            if (!confirmExport(tasks, 'wav')) return; 
            cleanup(); resolve(tasks); 
        };
        const onCancel = () => { cleanup(); resolve(null); };
        const onEsc = (e) => { if (e.key === 'Escape') { e.preventDefault(); onCancel(); } };

        btnMp3.addEventListener('click', onMp3);
        btnWav.addEventListener('click', onWav);
        btnCancel.addEventListener('click', onCancel);
        if (btnCancelDesktop) btnCancelDesktop.addEventListener('click', onCancel);
        btnClearRange.addEventListener('click', doClearRange);
        toggleSingleChapter.addEventListener('change', onToggleSingle);
        toggleSeparateFiles.addEventListener('change', onToggleSeparate);
        document.addEventListener('keydown', onEsc);
    });
}

// 🌟 SURGICAL FIX: Queue Runner Engine
export async function startExport() {
    const voiceSelect = document.getElementById('voiceSelect');
    const speedRange = document.getElementById('speedRange');

    if (!state.currentDoc) { showToast("No document selected"); return; }
    if (!window.isEngineReady) { showToast("Voice engine not ready"); return; }

    try {
        const tasks = await selectExportFormat();
        if (!tasks || tasks.length === 0) return; 

        if (tasks[0].format === "mp3") {
            const status = await fetchJSON(`/api/ffmpeg/status?t=${Date.now()}`);
            if (!status.is_installed) {
                showFFMPEGDownloadModal();
                return;
            }
        }

        const exportModal = document.getElementById('exportModal');
        exportModal.classList.remove('hidden');
        document.getElementById('playBtn').disabled = true;

        let lastOutputFile = "";

        // Loop through and process tasks one by one
        for (let i = 0; i < tasks.length; i++) {
            const task = tasks[i];
            
            document.getElementById('exportComplete').classList.add('hidden');
            document.getElementById('exportError').classList.add('hidden');
            document.getElementById('exportProgress').textContent = '0%';
            document.getElementById('exportProgressBar').style.width = '0%';
            
            const taskStatusLabel = tasks.length > 1 ? `(File ${i + 1}/${tasks.length}) ` : "";
            document.getElementById('exportStatus').textContent = `${taskStatusLabel}Initializing: ${task.fileLabel}...`;
            
            const exportEta = document.getElementById('exportEta');
            if (exportEta) {
                exportEta.classList.remove('hidden');
                exportEta.textContent = 'ETA: Calculating...';
            }

            window.exportStartTime = Date.now();

            await fetchJSON(`/api/export/audio`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    doc_id: state.currentDoc.id,
                    voice: voiceSelect.value,
                    speed: parseFloat(speedRange.value),
                    rules: state.rules,
                    ignore_list: state.ignoreList,
                    format: task.format,
                    start_page: task.startPage,
                    end_page: task.endPage,
                    pause_settings: state.pauseSettings,
                    behavior_settings: state.behaviorSettings,
                    file_label: task.fileLabel
                })
            });

            // Pause loop and wait for the backend to finish this specific file
            const output = await runExportPolling(taskStatusLabel);
            if (output.error) {
                break; // Stop queue instantly if backend crashes
            }
            lastOutputFile = output.file;
        }

        // Entire Queue Complete
        if (lastOutputFile) {
            document.getElementById('exportStatus').textContent = tasks.length > 1 ? 'All exports complete!' : 'Export complete!';
            
            // Format to show just the folder name instead of individual file
            const folderName = lastOutputFile.split('/')[0];
            document.getElementById('exportFilePath').textContent = `./Audio files/${folderName}`;
            
            document.getElementById('exportComplete').classList.remove('hidden');
            document.getElementById('playBtn').disabled = false;
            renderIcons();
        }

    } catch (e) {
        console.error(e);
        showToast("Export failed: " + e.message);
    }
}

// 🌟 SURGICAL FIX: Promise-based Polling (Allows the Queue to Wait)
function runExportPolling(taskStatusLabel) {
    return new Promise((resolve) => {
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
                    resolve({ error: true });
                    return;
                }

                if (status.is_exporting) {
                    const percent = status.total > 0 ? Math.round((status.progress / status.total) * 100) : 0;
                    document.getElementById('exportProgress').textContent = `${percent}%`;
                    document.getElementById('exportProgressBar').style.width = `${percent}%`;
                    
                    const exportEta = document.getElementById('exportEta');
                    if (exportEta && status.progress > 0 && status.total > 0 && window.exportStartTime) {
                        const elapsedMs = Date.now() - window.exportStartTime;
                        const msPerItem = elapsedMs / status.progress;
                        const remainingItems = status.total - status.progress;
                        const remainingMs = remainingItems * msPerItem;
                        const remainingTotalSeconds = Math.max(0, Math.floor(remainingMs / 1000));
                        
                        const etaMins = Math.floor(remainingTotalSeconds / 60).toString().padStart(2, '0');
                        const etaSecs = (remainingTotalSeconds % 60).toString().padStart(2, '0');
                        exportEta.textContent = `ETA: ${etaMins}:${etaSecs}`;
                    }
                    
                    if (status.progress === status.total) {
                        document.getElementById('exportStatus').textContent = `${taskStatusLabel}Finalizing format...`;
                        if (exportEta) exportEta.textContent = 'Please wait...';
                    } else {
                        document.getElementById('exportStatus').textContent = `${taskStatusLabel}Processing segment ${status.progress} of ${status.total}...`;
                    }
                } else if (status.output_file) {
                    clearInterval(exportPollInterval);
                    document.getElementById('exportProgress').textContent = '100%';
                    document.getElementById('exportProgressBar').style.width = '100%';
                    
                    const exportEta = document.getElementById('exportEta');
                    if (exportEta) exportEta.classList.add('hidden');
                    
                    resolve({ error: false, file: status.output_file });
                }
            } catch (e) {
                console.error("Export polling error:", e);
            }
        }, 1000);
    });
}

export function cancelExport() {
    if (exportPollInterval) clearInterval(exportPollInterval);
    fetchJSON(`/api/export/cancel`, { method: 'POST' }).catch(console.error);
    document.getElementById('exportModal').classList.add('hidden');
    document.getElementById('playBtn').disabled = false;
    // By clearing interval, the queue stops automatically. 
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
    fetchJSON(`/api/export/open-location`, { method: 'POST' })
        .then(() => {
            showToast("Opening Audio folder...");
            setTimeout(() => document.getElementById('exportModal').classList.add('hidden'), 1000);
        })
        .catch(e => showToast("Error: " + e.message));
}