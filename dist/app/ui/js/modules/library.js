import { state } from "./state.js";
import { fetchJSON, fetchBlob } from "./api.js";
import { showToast, renderIcons, stripHTML, highlightSearchTerm, showFootnoteModal } from "./ui.js";

export async function openFootnote(targetId) {
    if (!targetId || !state.currentPages) return;
    
    const cleanId = targetId.split('#').pop();
    let footnoteHTML = null;
    let foundPageIdx = -1;
    
    // Priority search: Current page -> Forward -> Backward
    const searchOrder = [state.viewPageIndex];
    for (let i = state.viewPageIndex + 1; i < state.currentPages.length; i++) searchOrder.push(i);
    for (let i = state.viewPageIndex - 1; i >= 0; i--) searchOrder.push(i);
    
    for (const idx of searchOrder) {
        const pageHtml = state.currentPages[idx];
        if (pageHtml.includes(`id="${cleanId}"`) || pageHtml.includes(`id='${cleanId}'`)) {
            const parser = new DOMParser();
            const doc = parser.parseFromString(pageHtml, 'text/html');
            const targetEl = doc.getElementById(cleanId) || doc.querySelector(`[id="${cleanId}"]`);
            
            if (targetEl) {
                const container = targetEl.closest('aside, li, p[epub\\:type="footnote"], div[epub\\:type="footnote"], .epub-footnote') 
                                || targetEl.closest('p') 
                                || targetEl.parentElement;
                footnoteHTML = container ? container.innerHTML : targetEl.innerHTML;
                foundPageIdx = idx;
                break;
            }
        }
    }
    
    if (footnoteHTML) {
        showFootnoteModal(footnoteHTML, async () => {
            state.viewPageIndex = foundPageIdx;
            state.autoScrollEnabled = false;
            await renderPage();
            setTimeout(() => {
                const targetEl = document.getElementById(cleanId);
                if (targetEl) {
                    const scrollTarget = targetEl.closest('.sentence') || targetEl;
                    scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    scrollTarget.classList.add('bg-blue-600/40', 'rounded', 'px-1', 'transition-colors', 'duration-500');
                    setTimeout(() => scrollTarget.classList.remove('bg-blue-600/40', 'px-1'), 1500);
                }
            }, 100);
        });
    } else {
        showToast("Footnote content not found.");
    }
}

export async function loadLibrary() {
  const libraryPanel = document.getElementById("libraryPanel");
  try {
    const items = await fetchJSON(`/api/library?t=${Date.now()}`);
    libraryPanel.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
      libraryPanel.innerHTML = '<div class="p-4 text-xs text-zinc-500 italic">Library is empty. Upload a PDF to start.</div>';
      return;
    }
    const fragment = document.createDocumentFragment();
    items
      .sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))
      .forEach((item) => {
        const isSelected = state.currentDoc?.id === item.id;
        const div = document.createElement("div");
        div.className = `group p-3 rounded-xl cursor-pointer border transition-all ${
          isSelected ? "bg-blue-600/10 border-blue-600/50 text-blue-400" : "bg-zinc-900/50 border-zinc-800 text-zinc-400 hover:border-zinc-700"
        }`;
        div.innerHTML = `
                <div class="flex items-start justify-between gap-2">
                    <div class="flex items-start gap-3 min-w-0" data-action="select-doc" data-id="${item.id}">
                        <i data-lucide="file" class="w-4 h-4 mt-0.5 shrink-0"></i>
                        <div class="min-w-0">
                            <p class="text-xs font-bold leading-tight break-words">${item.fileName}</p>
                            <p class="text-[10px] opacity-60 mt-1">Page ${(item.currentPage || 0) + 1}/${item.totalPages}</p>
                        </div>
                    </div>
                    <button data-action="delete-doc" data-id="${item.id}" class="p-1 hover:bg-red-500/20 hover:text-red-500 rounded-md transition-colors opacity-0 group-hover:opacity-100 shrink-0">
                        <i data-lucide="x" class="w-3.5 h-3.5"></i>
                    </button>
                </div>`;
        fragment.appendChild(div);
      });
    libraryPanel.appendChild(fragment);
    renderIcons();
  } catch (e) {
    libraryPanel.innerHTML = '<div class="p-4 text-xs text-red-500 italic">Failed to load library.</div>';
  }
}

export async function processJsonData(pagesText, fileName, explicitDocId = null, imageMap = null, tocMap = null) {
    try {
        const docId = explicitDocId || crypto.randomUUID(); 
        const newDoc = {
            id: docId, fileName: fileName, totalPages: pagesText.length,
            currentPage: 0, lastSentenceId: null, lastSentenceIndex: 0, lastAccessed: Date.now(),
        };

        await fetchJSON("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(newDoc) });
        
        const contentPayload = { id: docId, pages: pagesText };
        if (imageMap) contentPayload.image_map = imageMap;
        if (tocMap) contentPayload.toc_map = tocMap;

        await fetch("/api/library/content", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(contentPayload) });

        selectDocument(newDoc);
        showToast("Book added to library");
    } catch (err) {
        showToast("Failed to process document: " + err.message);
    }
}

export async function processPdfBlob(blob, fileName) {
    showToast("Processing PDF with native backend engine...");
    
    // Generate the unique ID upfront so we can send it to the backend route
    const docId = crypto.randomUUID(); 
    
    const formData = new FormData();
    formData.append("file", blob, fileName);

    try {
        // Send to our new PyMuPDF backend route
        const response = await fetch(`/api/convert/pdf?id=${docId}`, {
            method: "POST",
            body: formData
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `Server error: ${response.status}`);
        }

        const data = await response.json();
        
        // Feed the extracted pages, image map, and TOC directly into the unified JSON processor
        await processJsonData(
            data.pages, 
            fileName.replace(/\.pdf$/i, ""), 
            docId, 
            data.image_map, 
            data.toc_map
        );

    } catch (err) { 
        console.error("PDF Conversion Error:", err);
        showToast("Failed to process PDF: " + err.message); 
    }
}

export async function selectDocument(item) {
    if (!window._hasSyncedSettingsOnOpen) {
        try {
            const savedSettings = await fetchJSON(`/api/settings?t=${Date.now()}`).catch(() => null);
            if (savedSettings) {
                const voiceSelect = document.getElementById("voiceSelect");
                if (voiceSelect && savedSettings.voice && voiceSelect.value !== savedSettings.voice) {
                    voiceSelect.value = savedSettings.voice;
                    state.voice = savedSettings.voice;
                }
            }
        } catch (err) {}
        window._hasSyncedSettingsOnOpen = true;
    }

    state.currentDoc = item;
    showToast(`Opening ${item.fileName}...`);
    const textContent = document.getElementById("textContent");
    if (textContent) {
        textContent.classList.remove("hidden");
        textContent.innerHTML = '<div class="text-zinc-500 p-4 animate-pulse">Loading document content...</div>';
    }

    try {
        const data = await fetchJSON(`/api/library/content/${item.id}`);
        state.currentPages = data.pages;
        
        state.smartStartPage = data.smart_start_page || 0;
        state.tocMap = data.toc_map || [];

        if ((item.currentPage || 0) === 0 && state.smartStartPage > 0) {
            state.readingPageIndex = state.smartStartPage;
            state.viewPageIndex = state.smartStartPage;
            state.currentSentenceIndex = 0;
            showToast(`Have a good day`);
        } else {
            state.readingPageIndex = item.currentPage || 0;
            state.viewPageIndex = item.currentPage || 0;
            state.currentSentenceIndex = item.lastSentenceIndex || 0;
        }

        state.readingSentences = await getSentencesForPage(state.readingPageIndex);

        const docTitle = document.getElementById("docTitle");
        const pageNav = document.getElementById("pageNav");
        const controls = document.getElementById("controls");
        const emptyState = document.getElementById("emptyState");
        const prevPage = document.getElementById("prevPage");
        const nextPage = document.getElementById("nextPage");
        const pageInput = document.getElementById("pageInput");
        const searchBtn = document.getElementById("searchBtn");
        const exportArea = document.getElementById("exportArea");
        const textSizeArea = document.getElementById("textSizeArea");
        
        if (docTitle) docTitle.textContent = item.fileName;
        if (pageNav) { pageNav.classList.remove("opacity-50", "pointer-events-none"); pageNav.removeAttribute("data-inactive"); }
        if (prevPage) prevPage.disabled = false;
        if (nextPage) nextPage.disabled = false;
        if (pageInput) pageInput.disabled = false;
        if (controls) controls.classList.remove("hidden");
        if (emptyState) emptyState.classList.add("hidden");
        if (searchBtn) searchBtn.classList.remove("hidden");

        if (exportArea && window.isEngineReady) exportArea.style.display = 'block';
        if (textSizeArea && window.isEngineReady) textSizeArea.style.display = 'block';

        state.autoScrollEnabled = true;

        await renderPage(); 
        renderTOC(); 
        loadLibrary(); 
    } catch (e) {
        console.error("Select document error:", e);
        showToast("Failed to load document content");
        if (textContent) textContent.innerHTML = '';
    }
}

export function renderTOC() {
    const tocList = document.getElementById('tocList');
    if (!tocList) return;
    tocList.innerHTML = '';
    if (!state.tocMap || state.tocMap.length === 0) {
        tocList.innerHTML = '<div class="p-4 text-xs text-zinc-500 italic">No Table of Contents available.</div>';
        return;
    }
    const fragment = document.createDocumentFragment();
    
    state.tocMap.forEach(item => {
        const div = document.createElement('div');
        const paddingLeft = item.level === 1 ? '0.5rem' : item.level === 2 ? '1.5rem' : '2.5rem';
        div.className = `cursor-pointer py-2 px-2 hover:bg-zinc-800 text-sm transition-colors border-l-2 border-transparent hover:border-blue-500`;
        div.style.paddingLeft = paddingLeft;
        div.innerHTML = `<div class="flex justify-between items-center opacity-80 hover:opacity-100"><span class="truncate pr-2 ${item.level === 1 ? 'font-bold text-zinc-200' : 'text-zinc-400'}">${item.title}</span><span class="text-[10px] text-zinc-500 shrink-0">Pg ${item.page_index + 1}</span></div>`;
        
        div.onclick = async () => {
            const tocModal = document.getElementById('tocModal');
            if (tocModal) tocModal.classList.add('hidden');

            const targetSentences = await getSentencesForPage(item.page_index);
            let targetIndex = 0;

            if (targetSentences && targetSentences.length > 0) {
                for (let i = 0; i < targetSentences.length; i++) {
                    const rawSentence = targetSentences[i];
                    if ((item.target_tts_id && (rawSentence.includes(`id="${item.target_tts_id}"`) || rawSentence.includes(`id='${item.target_tts_id}'`))) ||
                        (item.id && (rawSentence.includes(`id="${item.id}"`) || rawSentence.includes(`id='${item.id}'`) || rawSentence.includes(`data-orig-id="${item.id}"`)))) {
                        targetIndex = i;
                        break;
                    }
                }
            }

            // 3. THE EXPLORER FIX: Only move the CAMERA (viewPageIndex), do NOT change the reading position!

            // 3. THE EXPLORER FIX: Only move the CAMERA (viewPageIndex), do NOT change the reading position!
            // This preserves the user's bookmark, keeps the "Back to Reading" button visible, and stops audio from auto-playing.
            state.viewPageIndex = item.page_index;
            state.autoScrollEnabled = false; // Disable auto-scroll so renderPage() doesn't fight us

            // 4. Render the page silently without triggering TTS
            await renderPage();

            // 5. Manually scroll the screen down to the specific heading
            const sentences = document.querySelectorAll('.sentence');
            if (sentences && sentences[targetIndex]) {
                const targetEl = sentences[targetIndex];
                const scrollContainer = document.querySelector(".content-area");
                if (scrollContainer) {
                    // requestAnimationFrame ensures the DOM has physically painted before we calculate pixels
                    requestAnimationFrame(() => {
                        setTimeout(() => {
                            const elRect = targetEl.getBoundingClientRect();
                            const containerRect = scrollContainer.getBoundingClientRect();
                            
                            const relativeTop = elRect.top - containerRect.top + scrollContainer.scrollTop;
                            
                            // Scroll so the heading is nicely visible near the top third of the screen
                            scrollContainer.scrollTop = Math.max(0, relativeTop - (containerRect.height / 3));
                        }, 50); // 50ms buffer to guarantee Heavy PDFs are fully arranged
                    });
                }
            }
        };
        fragment.appendChild(div);
    });
    tocList.appendChild(fragment);
    updateActiveTOC();
}

export async function getSentencesForPage(pageIndex) {
    if (!state.currentPages || !state.currentPages[pageIndex]) return [];
    const pageText = state.currentPages[pageIndex];

    if (pageText.includes('<n ') || pageText.includes('<n>') || pageText.includes('class="epub-image"') || pageText.includes('<s>') || /<h[1-6]/i.test(pageText)) {        
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = pageText;
        const elements = Array.from(tempDiv.querySelectorAll('n, s, img.epub-image, h1, h2, h3, h4, h5, h6'));
        return elements.map(el => {
            if (el.tagName.match(/^(img|s|h[1-6])$/i)) return el.outerHTML; 
            
            const clone = el.cloneNode(true);
            clone.querySelectorAll('a[epub\\:type="noteref"], a[href*="#R_"], .epub-noteref').forEach(ref => {
                const parentText = ref.parentElement ? ref.parentElement.textContent.trim() : "";
                const refText = ref.textContent.trim();
                const nextText = ref.nextSibling && ref.nextSibling.nodeType === 3 ? ref.nextSibling.textContent.trim() : "";
                
                const isDefinition = parentText.startsWith(refText) || nextText.startsWith(':');
                
                if (isDefinition) {
                    const cleanNum = refText.replace(/[\[\]\(\)]/g, '');
                    ref.textContent = `Footnote ${cleanNum}, `;
                    if (ref.nextSibling && ref.nextSibling.nodeType === 3) {
                        ref.nextSibling.textContent = ref.nextSibling.textContent.replace(/^[\s:,\-]+/, ' ');
                    }
                } else {
                    ref.remove();
                }
            });
            
            let text = clone.textContent;
            const pause = parseInt(el.getAttribute('data-pause') || "0");
            if (pause > 0) text = `[PAUSE_${pause}] ` + text;
            return text;
        });
    }

    let text = pageText.replace(/(\[H[1-6]\].*?\[\/H[1-6]\])/g, "\n\n$1\n\n").replace(/(\[SCENE_BREAK\])/g, "\n\n$1\n\n");
    text = text.replace(/\n\n+/g, "<!PARAGRAPH!>").replace(/([^.!?:;。！？：；])\n/g, "$1 ").replace(/<!PARAGRAPH!>/g, "\n\n").replace(/  +/g, " ");

    const abbreviations = ["Mr", "Mrs", "Ms", "Dr", "Prof", "St", "Rd", "Ave", "Capt", "Gen", "Sen", "Rep", "Gov", "Fig", "No", "Op", "vs", "etc", "e\\.g", "i\\.e", "Inc", "Ltd", "Co"];
    const abbrRegex = new RegExp(`\\b(${abbreviations.join('|')})\\.(?=\\s)`, 'gi');
    const protectedText = text.replace(abbrRegex, '$1<DOT>');

    const sentences = [];
    const segmenter = new Intl.Segmenter(state.uiLanguage || 'en', { granularity: 'sentence' });

    for (const segmentItem of segmenter.segment(protectedText)) {
        let s = segmentItem.segment.trim().replace(/<DOT>/g, '.').replace(/^[\"\'\u201c\u2018\u201d\u2019]+(?=[\"\'\u201c\u2018\u201d\u2019])/, '').replace(/[\"\'\u201c\u2018\u201d\u2019]+$/, (match) => match.length > 1 ? match[0] : match);
        if (s) {
            if (s.includes("[DIM]") && !s.includes("[/DIM]")) s += "[/DIM]";
            if (!s.includes("[DIM]") && s.includes("[/DIM]")) s = "[DIM]" + s;
            const hStartMatch = s.match(/\[H[1-6]\]/);
            const hEndMatch = s.match(/\[\/H[1-6]\]/);
            if (hStartMatch && !hEndMatch) s += `[/${hStartMatch[0].replace('[','').replace(']','')}]`;
            if (!hStartMatch && hEndMatch) s = `[${hEndMatch[0].replace('[/','').replace(']','')}]` + s;
            if (segmentItem.segment.includes('\n')) s += '\n'; 
            sentences.push(s);
        }
    }
    return sentences;
}



export async function renderPage() {
    const textContent = document.getElementById("textContent");
    const pageInput = document.getElementById("pageInput");
    const pageTotal = document.getElementById("pageTotal");
    const scrollContainer = document.querySelector(".content-area");
    const currentSentencePreview = document.getElementById("currentSentencePreview");
    const backToReadingBtn = document.getElementById("backToReadingBtn");

    if (!state.currentPages || !state.currentPages[state.viewPageIndex]) {
        if (textContent) textContent.innerHTML = '<div class="text-zinc-500 p-4">Error: Page not found</div>';
        return;
    }

    const hiddenModeBackBtn = document.getElementById("hiddenModeBackBtn");
    
    if (state.viewPageIndex !== state.readingPageIndex || !state.autoScrollEnabled) {
        // Scrolled away
        if (state.manualHidePlayer) {
            if (backToReadingBtn) { backToReadingBtn.classList.add('hidden'); backToReadingBtn.classList.remove('flex'); }
            if (hiddenModeBackBtn) { hiddenModeBackBtn.classList.replace('opacity-0', 'opacity-100'); hiddenModeBackBtn.classList.replace('pointer-events-none', 'pointer-events-auto'); }
        } else {
            if (hiddenModeBackBtn) { hiddenModeBackBtn.classList.replace('opacity-100', 'opacity-0'); hiddenModeBackBtn.classList.replace('pointer-events-auto', 'pointer-events-none'); }
            if (backToReadingBtn) { backToReadingBtn.classList.remove('hidden'); backToReadingBtn.classList.add('flex'); }
        }
    } else {
        // On track
        if (backToReadingBtn) { backToReadingBtn.classList.add('hidden'); backToReadingBtn.classList.remove('flex'); }
        if (hiddenModeBackBtn) { hiddenModeBackBtn.classList.replace('opacity-100', 'opacity-0'); hiddenModeBackBtn.classList.replace('pointer-events-auto', 'pointer-events-none'); }
    }

    state.viewSentences = await getSentencesForPage(state.viewPageIndex);
    const pageText = state.currentPages[state.viewPageIndex];
    const isReadingCurrentPage = state.viewPageIndex === state.readingPageIndex;
    
    // 🌟 FIX: Safety check to prevent a stale index from overwriting the next page
    const isOnSavedPage = state.currentDoc && state.viewPageIndex === (state.currentDoc.currentPage || 0);

    if (textContent) {
        textContent.innerHTML = "";
        
        if (pageText.includes('<n ') || pageText.includes('<n>') || pageText.includes('class="epub-image"') || pageText.includes('<s>') || /<h[1-6]/i.test(pageText)) {            
            textContent.innerHTML = pageText;
            state.sentenceElements = Array.from(textContent.querySelectorAll('n, s, img.epub-image, h1, h2, h3, h4, h5, h6'));
            
            if (isReadingCurrentPage && state.currentDoc && isOnSavedPage) {
                let positionFound = false;
                if (state.currentDoc.lastSentenceId) {
                    const structuralIdIndex = state.sentenceElements.findIndex(el => el.getAttribute('id') === state.currentDoc.lastSentenceId);
                    if (structuralIdIndex !== -1) {
                        state.currentSentenceIndex = structuralIdIndex;
                        positionFound = true;
                    }
                }
                if (!positionFound && typeof state.currentDoc.lastSentenceIndex === 'number') {
                    if (state.currentDoc.lastSentenceIndex >= 0 && state.currentDoc.lastSentenceIndex < state.sentenceElements.length) {
                        state.currentSentenceIndex = state.currentDoc.lastSentenceIndex;
                    } else state.currentSentenceIndex = 0; 
                }
            }

            state.sentenceElements.forEach((tag, i) => {
                tag.classList.add('sentence'); 
                if (isReadingCurrentPage && i === state.currentSentenceIndex) tag.classList.add('active-sentence');
                tag.onclick = (e) => {
                    e.stopPropagation(); // 🌟 PHANTOM ROUTER: Prevent click from bubbling up to parent header and double-firing
                    state.readingPageIndex = state.viewPageIndex;
                    state.readingSentences = [...state.viewSentences];
                    state.autoScrollEnabled = true; 
                    window.dispatchEvent(new CustomEvent("jump-to-sentence", { detail: i }));
                };
            });
        } else {
            const fragment = document.createDocumentFragment();
            state.viewSentences.forEach((s, i) => {
                const span = document.createElement("span");
                span.className = `sentence ${(isReadingCurrentPage && i === state.currentSentenceIndex) ? "active-sentence" : ""}`;
                let cleanS = s;
                const hMatch = cleanS.match(/\[(H[1-6])\](.*?)\[\/\1\]/);
                const imgMatch = cleanS.match(/\[IMAGE_(\d+)\]/);

                if (hMatch) span.innerHTML = `<${hMatch[1].toLowerCase()} class="book-heading ${hMatch[1].toLowerCase()}">${hMatch[2]}</${hMatch[1].toLowerCase()}>`;
                else if (imgMatch) span.innerHTML = `<img src="/api/library/image/${state.currentDoc?.id}/${imgMatch[1]}" class="epub-image" onload="if(this.naturalWidth < 150 && this.naturalHeight < 150) { this.classList.add('epub-icon'); }" loading="lazy" alt="Illustration" />`;
                else if (cleanS.includes("[SCENE_BREAK]")) span.innerHTML = `<div class="scene-break">♦ ♦ ♦</div>`;
                else {
                    if (cleanS.includes("[DIM]")) span.innerHTML = cleanS.replace(/\[DIM\](.*?)\[\/DIM\]/g, '<span class="dimmed-text">$1</span>');
                    else span.textContent = cleanS;
                }

                span.onclick = () => {
                    state.readingPageIndex = state.viewPageIndex;
                    state.readingSentences = [...state.viewSentences];
                    state.autoScrollEnabled = true; 
                    window.dispatchEvent(new CustomEvent("jump-to-sentence", { detail: i }));
                };
                fragment.appendChild(span);
            });
            textContent.appendChild(fragment);
            state.sentenceElements = Array.from(textContent.querySelectorAll(".sentence"));
        }
    }

    if (pageInput) pageInput.value = state.viewPageIndex + 1;
    if (pageTotal) pageTotal.textContent = state.currentPages.length;

    // 🌟 FIX: THE BULLETPROOF MATHEMATICAL FOCUS CAMERA
    if (scrollContainer) {
        if (!isReadingCurrentPage) {
            scrollContainer.scrollTop = 0;
        } 
        else if (state.autoScrollEnabled) {
            // requestAnimationFrame ensures the DOM has physically painted before we calculate pixels
            requestAnimationFrame(() => {
                setTimeout(() => {
                    const activeEl = document.querySelector('.active-sentence');
                    if (activeEl) {
                        // Mathematically locate the exact pixel depth of the sentence
                        const elRect = activeEl.getBoundingClientRect();
                        const containerRect = scrollContainer.getBoundingClientRect();
                        
                        const relativeTop = elRect.top - containerRect.top + scrollContainer.scrollTop;
                        const centerPosition = relativeTop - (containerRect.height / 2) + (elRect.height / 2);
                        
                        // Force the scroll container to snap perfectly to the center
                        scrollContainer.scrollTop = Math.max(0, centerPosition);
                    } else {
                        scrollContainer.scrollTop = 0;
                    }
                }, 20); // 20ms buffer to guarantee Heavy PDFs are fully arranged
            });
        }
    }

    const currentReadingSentence = (state.readingSentences && state.readingSentences.length > 0) ? state.readingSentences[state.currentSentenceIndex] : "";
    if (currentSentencePreview && currentReadingSentence) {
        const cleanText = currentReadingSentence.replace(/\[PAUSE_\d+\]\s*/g, '');
        
        let bType = "N";
        if (/<h[1-6]/i.test(currentReadingSentence)) bType = "H";
        else if (/<img|<svg/i.test(currentReadingSentence)) bType = "Img";
        else if (/<s\b/i.test(currentReadingSentence) || /class="scene-break"/i.test(currentReadingSentence)) bType = "S";
        
        const validTags = /<\/?(?:n|s|p|div|h[1-6]|span|font|a|b|i|u|em|strong|del|figure|blockquote|img|image|svg|picture|hr|li|ul|ol|table|tr|td|th|tbody|thead|tfoot|section|article|aside|nav|main|header|footer)\b[^>]*>/gi;
        let finalStr = cleanText.replace(validTags, '').trim();
        
        // 🌟 UI Rescue Interceptor: Restores TOC titles for images wrapped in Headers
        if (finalStr === "" && bType.startsWith("H")) {
            const idMatch = currentReadingSentence.match(/(?:id|data-orig-id)=['"]([^'"]+)['"]/);
            if (idMatch && state.tocMap) {
                const matchedToc = state.tocMap.find(t => t.target_tts_id === idMatch[1] || t.id === idMatch[1]);
                if (matchedToc && matchedToc.title) {
                    finalStr = matchedToc.title;
                }
            }
        }
        
        // Final fallback for raw UI display 
        if (bType === "Img" && finalStr === "") finalStr = "🖼️ [Viewing Image]";
        if (bType === "S" && finalStr === "") finalStr = "•••";
        
        currentSentencePreview.textContent = finalStr;
        
        const monitorText = document.getElementById("monitorSentenceText");
        if (monitorText) monitorText.textContent = finalStr;
    }

    const pageImages = document.querySelectorAll('#textContent img.epub-image');
    if (pageImages.length > 0) {
        const imgObserver = new IntersectionObserver((entries, observer) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    
                    const reveal = () => {
                        if (img.naturalWidth > 0 && img.naturalWidth < 150 && img.naturalHeight < 150) {
                            img.classList.add('epub-icon');
                        }
                        img.classList.remove('lazy-prep');
                        img.classList.add('lazy-loaded');
                    };
                    
                    if (img.complete) {
                        setTimeout(reveal, 50);
                    } else {
                        img.addEventListener('load', reveal);
                    }
                    
                    observer.unobserve(img);
                }
            });
        }, { root: null, rootMargin: '800px 0px' });

        pageImages.forEach(img => {
            img.classList.add('lazy-prep');
            imgObserver.observe(img);
        });
    }

   if (state.currentSearchQuery && typeof highlightSearchTerm === "function") highlightSearchTerm(state.currentSearchQuery, state.searchMatchCase, state.searchWholeWord);
    
    const footnoteElements = textContent.querySelectorAll(
        'a[epub\\:type="noteref"], a[epub\\:type="footnote"], a[epub\\:type="backlink"], a[href*="#R_"], a[id*="R_"], .epub-noteref, .epub-footnote, p[epub\\:type="footnote"], div[epub\\:type="footnote"], aside[epub\\:type="footnote"], li[epub\\:type="footnote"]'
    );

    footnoteElements.forEach(ref => {
        const href = ref.getAttribute('href') || '';
        const id = ref.getAttribute('id') || '';
        const epubType = (ref.getAttribute('epub:type') || '').toLowerCase();
        const isDefinition = epubType === 'footnote' || epubType === 'backlink' || id.startsWith('R_') || ref.classList.contains('epub-footnote');

        if (!isDefinition) {
            // --- 1. CALLOUT / IN-TEXT NOTE (POPUP TRIGGER) ---
            ref.classList.add('relative', 'group', 'cursor-pointer', 'text-blue-400', 'hover:text-blue-300', 'transition-colors', 'font-bold');
            ref.style.display = 'inline';
            ref.style.margin = '0 2px';
            ref.style.padding = '0';

            if (!ref.querySelector('.footnote-tooltip')) {
                const tooltip = document.createElement('span');
                tooltip.className = 'footnote-tooltip absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-max whitespace-nowrap px-2.5 py-1.5 bg-[#27272a] text-zinc-100 text-[11px] font-bold rounded-md opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50 shadow-xl border border-zinc-600';
                tooltip.style.textIndent = '0px';
                tooltip.style.lineHeight = '1';
                tooltip.textContent = 'View Footnote';
                ref.appendChild(tooltip);
            }

            ref.onclick = async (e) => {
                e.preventDefault();
                e.stopPropagation();
                if (href) openFootnote(href);
            };
        } else {
            // --- 2. DEFINITION / ENDNOTE (JUMP-BACK TRIGGER) ---
            if (ref.tagName === 'A') {
                ref.classList.add('cursor-pointer', 'text-zinc-900', 'bg-blue-500', 'hover:bg-blue-400', 'font-bold', 'inline-flex', 'items-center', 'px-1.5', 'py-0.5', 'rounded', 'text-[10px]', 'uppercase', 'mx-1');
                if (ref.textContent.trim().length <= 2 || ref.textContent.includes('↩') || ref.textContent.includes('↑')) {
                    ref.textContent = '↩ Back';
                }
            } else {
                ref.classList.add('cursor-pointer', 'hover:bg-blue-900/20', 'rounded', 'transition-colors', 'block', 'p-1');
            }
            
            ref.onclick = async (e) => {
                e.preventDefault();
                e.stopPropagation();

                const cleanHref = href.split('#').pop();
                const cleanId = id;
                if (!cleanHref && !cleanId) return;
                
                let targetPageIdx = -1;
                let foundSelector = "";

                // Priority search: Backward -> Current Page -> Forward
                const searchOrder = [];
                for (let i = state.viewPageIndex - 1; i >= 0; i--) searchOrder.push(i);
                searchOrder.push(state.viewPageIndex);
                for (let i = state.viewPageIndex + 1; i < state.currentPages.length; i++) searchOrder.push(i);

                for (const idx of searchOrder) {
                    const pageHtml = state.currentPages[idx];
                    
                    if (cleanHref && (pageHtml.includes(`id="${cleanHref}"`) || pageHtml.includes(`id='${cleanHref}'`))) {
                        targetPageIdx = idx;
                        foundSelector = `[id="${cleanHref}"]`;
                        break;
                    }
                    if (cleanId && (pageHtml.includes(`href="#${cleanId}"`) || pageHtml.includes(`href='#${cleanId}'`))) {
                        targetPageIdx = idx;
                        foundSelector = `[href="#${cleanId}"]`;
                        break;
                    }
                    if (cleanHref && (pageHtml.includes(`href="#${cleanHref}"`) || pageHtml.includes(`href='#${cleanHref}'`))) {
                         targetPageIdx = idx;
                         foundSelector = `[href="#${cleanHref}"]`;
                         break;
                    }
                }

                if (targetPageIdx !== -1) {
                    state.viewPageIndex = targetPageIdx;
                    state.autoScrollEnabled = false;
                    await renderPage();

                    setTimeout(() => {
                        const targetEl = document.querySelector(foundSelector);
                        if (targetEl) {
                            const scrollTarget = targetEl.closest('.sentence') || targetEl;
                            scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            scrollTarget.classList.add('bg-blue-600/40', 'rounded', 'px-1', 'transition-colors', 'duration-500');
                            setTimeout(() => scrollTarget.classList.remove('bg-blue-600/40', 'px-1'), 1500);
                        }
                    }, 100);
                } else {
                    if (typeof showToast === "function") showToast("Original note position not found.");
                }
            };
        }
    });

    try { updateActiveTOC(); } catch (e) { console.error("TOC Sync Error:", e); }
}

export function updateActiveTOC() {
    const tocList = document.getElementById('tocList');
    if (!tocList || !state.tocMap || state.tocMap.length === 0) return;
    
    const items = tocList.children;
    if (!items || items.length === 0 || items[0].classList.contains('italic')) return;

    let lastPrevIdx = -1;
    for (let i = 0; i < state.tocMap.length; i++) {
        if (state.tocMap[i].page_index < state.viewPageIndex) lastPrevIdx = i;
    }

    const currentPageTOC = [];
    for (let i = 0; i < state.tocMap.length; i++) {
        if (state.tocMap[i].page_index === state.viewPageIndex) {
            currentPageTOC.push({ index: i, item: state.tocMap[i] });
        }
    }

    let activeIdx = lastPrevIdx;

    if (currentPageTOC.length > 0) {
        let matchedIdx = -1;
        const scrollContainer = document.querySelector(".content-area");
        
        let querySelectors = ["#textContent h1", "#textContent h2", "#textContent h3", "#textContent h4", "#textContent h5", "#textContent h6", "#textContent .book-heading"];
        currentPageTOC.forEach(t => {
            if (t.item.target_tts_id) {
                querySelectors.push(`#textContent [id="${t.item.target_tts_id}"]`);
            }
            if (t.item.id) {
                querySelectors.push(`#textContent [id="${t.item.id}"]`);
                querySelectors.push(`#textContent [data-orig-id="${t.item.id}"]`);
            }
        });
        
        const rawHeadings = Array.from(document.querySelectorAll(querySelectors.join(", ")));
        const renderedHeadings = [...new Set(rawHeadings)].sort((a, b) => {
            return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
        });
        
        if (renderedHeadings.length > 0 && scrollContainer) {
            const containerTop = scrollContainer.getBoundingClientRect().top;
            const triggerPoint = scrollContainer.scrollTop < 10 ? scrollContainer.clientHeight : scrollContainer.clientHeight * 0.6; 
            
            let activeDomIndex = -1;
            for (let i = renderedHeadings.length - 1; i >= 0; i--) {
                const rect = renderedHeadings[i].getBoundingClientRect();
                const relativeTop = rect.top - containerTop;
                if (relativeTop <= triggerPoint) {
                    activeDomIndex = i;
                    break;
                }
            }

            if (activeDomIndex !== -1) {
                const activeEl = renderedHeadings[activeDomIndex];
                const activeId = activeEl.getAttribute('id');
                const origId = activeEl.getAttribute('data-orig-id');
                
                let foundMatch = false;
                for (let i = currentPageTOC.length - 1; i >= 0; i--) {
                    const tocItem = currentPageTOC[i].item;
                    if ((tocItem.target_tts_id && activeId === tocItem.target_tts_id) || 
                        (tocItem.id && (activeId === tocItem.id || origId === tocItem.id))) {
                        matchedIdx = currentPageTOC[i].index;
                        foundMatch = true;
                        break;
                    }
                }
                
                if (!foundMatch) {
                    const fallbackIndex = Math.min(activeDomIndex, currentPageTOC.length - 1);
                    matchedIdx = currentPageTOC[fallbackIndex].index;
                }
            } else {
                if (lastPrevIdx === -1) matchedIdx = currentPageTOC[0].index;
            }
        } else {
             matchedIdx = currentPageTOC[0].index;
        }
        
        if (matchedIdx !== -1) activeIdx = matchedIdx;
    }

    if (activeIdx === -1) activeIdx = 0;

    for (let i = 0; i < items.length; i++) {
        const div = items[i];
        const titleSpan = div.querySelector('span.truncate');
        const isMatch = (i === activeIdx);
        
        div.classList.toggle('bg-blue-600/20', isMatch);
        div.classList.toggle('border-blue-500', isMatch);
        div.classList.toggle('border-transparent', !isMatch);
        
        if (titleSpan) {
            titleSpan.classList.toggle('text-blue-400', isMatch);
            titleSpan.classList.toggle('brightness-125', isMatch);
        }
    }
}