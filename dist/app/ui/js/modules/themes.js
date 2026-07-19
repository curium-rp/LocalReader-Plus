import { showToast, renderIcons } from "./ui.js";

export async function initThemeSystem() {
    const contentArea = document.querySelector('.content-area');
    if (contentArea) {
        contentArea.style.filter = '';
        contentArea.style.transition = '';
    }

    const styleId = 'localreader-safe-themes';
    let styleEl = document.getElementById(styleId);
    if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = styleId;
        document.head.appendChild(styleEl);
    }

    const palettes = {
        'soft-white': `
            body.theme-soft-white .content-area { background-color: #f9fafb !important; }
            body.theme-soft-white #readerContent, body.theme-soft-white .sentence, body.theme-soft-white #currentSentencePreview { color: #4b5563 !important; }
            body.theme-soft-white header, body.theme-soft-white #controls { background-color: #f3f4f6 !important; border-color: #e5e7eb !important; }
            body.theme-soft-white #docTitle { color: #374151 !important; }
            body.theme-soft-white .active-sentence { background-color: #e5e7eb !important; border-left-color: #9ca3af !important; color: #111827 !important; font-weight: 600; }
            body.theme-soft-white .dimmed-text { color: #9ca3af !important; }
            body.theme-soft-white #pageNav { background-color: #f3f4f6 !important; border-color: #e5e7eb !important; color: #4b5563 !important; }
            body.theme-soft-white #pageInput { background-color: #f9fafb !important; color: #4b5563 !important; border-color: #e5e7eb !important; }
            body.theme-soft-white #pageTotal { color: #6b7280 !important; }
            body.theme-soft-white #bookmarkStatus { color: #6b7280 !important; }
            body.theme-soft-white #controls button:not(#playBtn), body.theme-soft-white header button { color: #9ca3af !important; }
            body.theme-soft-white #controls button:not(#playBtn):hover, body.theme-soft-white header button:hover { color: #111827 !important; }
        `,
        'dark-soft': `
            body.theme-dark-soft .content-area { background-color: #121212 !important; }
            body.theme-dark-soft #readerContent, body.theme-dark-soft .sentence, body.theme-dark-soft #currentSentencePreview { color: #8b8b8b !important; }
            body.theme-dark-soft header, body.theme-dark-soft #controls { background-color: #1a1a1a !important; border-color: #2a2a2a !important; }
            body.theme-dark-soft #docTitle { color: #8b8b8b !important; }
            body.theme-dark-soft .active-sentence { background-color: #2a2a2a !important; border-left-color: #555555 !important; color: #b3b3b3 !important; }
            body.theme-dark-soft .dimmed-text { color: #555555 !important; }
            body.theme-dark-soft #pageNav { background-color: #1a1a1a !important; border-color: #2a2a2a !important; color: #8b8b8b !important; }
            body.theme-dark-soft #pageInput { background-color: #121212 !important; color: #8b8b8b !important; border-color: #2a2a2a !important; }
            body.theme-dark-soft #pageTotal { color: #666666 !important; }
            body.theme-dark-soft #bookmarkStatus { color: #555555 !important; }
            body.theme-dark-soft #controls button:not(#playBtn), body.theme-dark-soft header button { color: #666666 !important; }
            body.theme-dark-soft #controls button:not(#playBtn):hover, body.theme-dark-soft header button:hover { color: #b3b3b3 !important; }
        `,
        'sepia-contrast': `
            body.theme-sepia-contrast .content-area { background-color: #fdf6e3 !important; }
            body.theme-sepia-contrast #readerContent, body.theme-sepia-contrast .sentence, body.theme-sepia-contrast #currentSentencePreview { color: #2c2116 !important; }
            body.theme-sepia-contrast header, body.theme-sepia-contrast #controls { background-color: #f4ecd8 !important; border-color: #e0d5ba !important; }
            body.theme-sepia-contrast #docTitle { color: #4a3824 !important; }
            body.theme-sepia-contrast .active-sentence { background-color: #fceea7 !important; border-left-color: #b45309 !important; color: #2c2116 !important; }
            body.theme-sepia-contrast .dimmed-text { color: #9a8c78 !important; }
            body.theme-sepia-contrast #pageNav { background-color: #f4ecd8 !important; border-color: #e0d5ba !important; color: #2c2116 !important; }
            body.theme-sepia-contrast #pageInput { background-color: #fdf6e3 !important; color: #2c2116 !important; border-color: #e0d5ba !important; }
            body.theme-sepia-contrast #pageTotal { color: #786551 !important; }
            body.theme-sepia-contrast #bookmarkStatus { color: #b45309 !important; }
            body.theme-sepia-contrast #controls button:not(#playBtn), body.theme-sepia-contrast header button { color: #786551 !important; }
            body.theme-sepia-contrast #controls button:not(#playBtn):hover, body.theme-sepia-contrast header button:hover { color: #2c2116 !important; }
        `,
        'sepia-soft': `
            body.theme-sepia-soft .content-area { background-color: #f4e8d1 !important; }
            body.theme-sepia-soft #readerContent, body.theme-sepia-soft .sentence, body.theme-sepia-soft #currentSentencePreview { color: #6b543a !important; }
            body.theme-sepia-soft header, body.theme-sepia-soft #controls { background-color: #eaddc5 !important; border-color: #d4c4a9 !important; }
            body.theme-sepia-soft #docTitle { color: #6b543a !important; }
            body.theme-sepia-soft .active-sentence { background-color: #e3d1ae !important; border-left-color: #9c7b54 !important; color: #4f3e2b !important; }
            body.theme-sepia-soft .dimmed-text { color: #a39178 !important; }
            body.theme-sepia-soft #pageNav { background-color: #eaddc5 !important; border-color: #d4c4a9 !important; color: #6b543a !important; }
            body.theme-sepia-soft #pageInput { background-color: #f4e8d1 !important; color: #6b543a !important; border-color: #d4c4a9 !important; }
            body.theme-sepia-soft #pageTotal { color: #8c775d !important; }
            body.theme-sepia-soft #bookmarkStatus { color: #8c775d !important; }
            body.theme-sepia-soft #controls button:not(#playBtn), body.theme-sepia-soft header button { color: #8c775d !important; }
            body.theme-sepia-soft #controls button:not(#playBtn):hover, body.theme-sepia-soft header button:hover { color: #4f3e2b !important; }
        `,
        'twilight': `
            body.theme-twilight .content-area { background-color: #292d3e !important; }
            body.theme-twilight #readerContent, body.theme-twilight .sentence, body.theme-twilight #currentSentencePreview { color: #a6accd !important; }
            body.theme-twilight header, body.theme-twilight #controls { background-color: #1b1e2b !important; border-color: #32374d !important; }
            body.theme-twilight #docTitle { color: #a6accd !important; }
            body.theme-twilight .active-sentence { background-color: #363c52 !important; border-left-color: #82aaff !important; color: #ffffff !important; }
            body.theme-twilight .dimmed-text { color: #697098 !important; }
            body.theme-twilight #pageNav { background-color: #1b1e2b !important; border-color: #32374d !important; color: #a6accd !important; }
            body.theme-twilight #pageInput { background-color: #292d3e !important; color: #a6accd !important; border-color: #32374d !important; }
            body.theme-twilight #pageTotal { color: #828bb8 !important; }
            body.theme-twilight #bookmarkStatus { color: #82aaff !important; }
            body.theme-twilight #controls button:not(#playBtn), body.theme-twilight header button { color: #697098 !important; }
            body.theme-twilight #controls button:not(#playBtn):hover, body.theme-twilight header button:hover { color: #ffffff !important; }
        `
    };

    const themes = [
        { id: 'dark', name: 'Dark Default', bg: '#09090b', text: '#ffffff', icon: 'D' },
        { id: 'dark-soft', name: 'Dark Soft', bg: '#121212', text: '#8b8b8b', icon: 'S' },
        { id: 'soft-white', name: 'Soft White', bg: '#f9fafb', text: '#4b5563', icon: 'W' },
        { id: 'sepia-contrast', name: 'Sepia Contrast', bg: '#fdf6e3', text: '#2c2116', icon: 'C' },
        { id: 'sepia-soft', name: 'Sepia Soft', bg: '#f4e8d1', text: '#6b543a', icon: 'S' },
        { id: 'twilight', name: 'Twilight (Gray)', bg: '#292d3e', text: '#a6accd', icon: 'T' }
    ];

    // 1. Hook into the ORIGINAL LocalReader text without modifying HTML
    const logoContainer = document.querySelector('.sidebar .flex.items-center.gap-3');
    const logo = logoContainer ? logoContainer.querySelector('h1') : null;

    if (!logo) return;

    // Make the original text clickable and add a tiny palette icon so they know it's a button
    logo.style.cursor = 'pointer';
    logo.title = 'Themes';
    logo.classList.add('hover:text-blue-400', 'transition-colors');
    if (!logo.innerHTML.includes('lucide="palette"')) {
        logo.innerHTML = 'LocalReader <i data-lucide="palette" class="inline w-4 h-4 ml-1 text-zinc-500 hover:text-blue-400 transition-colors"></i>';
    }

    // 2. Build the FLOATING MODAL (100% Prevents UI Overlap in sidebar)
    let themeModal = document.getElementById('themePickerModal');
    if (!themeModal) {
        themeModal = document.createElement('div');
        themeModal.id = 'themePickerModal';
        themeModal.className = 'fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[3000] hidden';
        
        themeModal.innerHTML = `
            <div class="bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl p-6 max-w-sm w-full mx-4" onclick="event.stopPropagation()">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-lg font-bold text-white flex items-center gap-2">
                        <i data-lucide="palette" class="w-5 h-5 text-blue-500"></i> Appearance Themes
                    </h3>
                    <button id="closeThemeModalBtn" class="text-zinc-500 hover:text-red-500 transition-colors">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <div id="themeGrid" class="grid grid-cols-2 gap-3"></div>
            </div>
        `;
        document.body.appendChild(themeModal);
        
        // Close modal when clicking X or outside the box
        themeModal.addEventListener('click', () => themeModal.classList.add('hidden'));
        document.getElementById('closeThemeModalBtn').onclick = () => themeModal.classList.add('hidden');
    }

    const themeGrid = document.getElementById('themeGrid');

    const applyTheme = async (themeId, saveToBackend = true) => {
        document.body.classList.remove('theme-soft-white', 'theme-dark-soft', 'theme-sepia-contrast', 'theme-sepia-soft', 'theme-twilight');
        
        if (themeId === 'dark') {
            styleEl.textContent = ''; 
        } else {
            document.body.classList.add(`theme-${themeId}`);
            styleEl.textContent = palettes[themeId];
        }
        
        themeGrid.querySelectorAll('button').forEach(btn => {
            if (btn.dataset.id === themeId) {
                btn.classList.add('ring-1', 'ring-blue-500', 'bg-zinc-800');
                btn.classList.remove('opacity-60', 'bg-zinc-900');
            } else {
                btn.classList.remove('ring-1', 'ring-blue-500', 'bg-zinc-800');
                btn.classList.add('opacity-60', 'bg-zinc-900');
            }
        });
        
        localStorage.setItem('lr_theme', themeId);
        renderIcons(); 

        if (saveToBackend) {
            try {
                await fetch('/api/theme', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ theme_id: themeId })
                });
            } catch (e) { }
        }
    };

    // 3. Populate the Modal Grid with Color Circles + Names
    themeGrid.innerHTML = '';
    themes.forEach(theme => {
        const btn = document.createElement('button');
        btn.dataset.id = theme.id;
        btn.className = 'flex items-center gap-3 p-3 rounded-xl border border-zinc-800 transition-all text-left bg-zinc-900 hover:bg-zinc-800 hover:border-zinc-700 shadow-md';
        
        btn.innerHTML = `
            <div class="w-5 h-5 rounded-full flex-shrink-0 shadow-inner border border-white/20 flex items-center justify-center font-bold text-[10px]" style="background-color: ${theme.bg}; color: ${theme.text}">${theme.icon}</div>
            <span class="text-xs font-bold text-zinc-300 truncate">${theme.name}</span>
        `;
        
        btn.onclick = () => {
            applyTheme(theme.id);
            showToast('Theme applied');
        };
        themeGrid.appendChild(btn);
    });

    // 4. Open Modal when Original Logo is Clicked
    logo.onclick = (e) => {
        e.stopPropagation();
        themeModal.classList.remove('hidden');
        renderIcons();
    };

    let currentThemeId = localStorage.getItem('lr_theme') || 'dark';
    try {
        const res = await fetch('/api/theme');
        if (res.ok) {
            const data = await res.json();
            if (data.theme_id) currentThemeId = data.theme_id;
        }
    } catch (e) {}
    
    applyTheme(currentThemeId, false);
}