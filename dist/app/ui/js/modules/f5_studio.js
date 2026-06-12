import { state } from "./state.js";
import { fetchJSON } from "./api.js";
import { showToast, renderIcons } from "./ui.js";

export class F5Studio {
    constructor(wrapperId) {
        this.wrapper = document.getElementById(wrapperId);
        this.audioPlayer = new Audio(); 
        this.isCreating = false;
    }

    async mount() {
        if (!this.wrapper) return;
        
        this.wrapper.innerHTML = `
            <div class="f5-studio-container bg-zinc-950/50 border border-zinc-800 rounded-lg p-3 space-y-3 shadow-inner">
                <div class="flex justify-between items-center border-b border-zinc-800/50 pb-2">
                    <span class="text-xs font-bold text-blue-400 uppercase tracking-wider flex items-center gap-1">
                        <i data-lucide="sparkles" class="w-3.5 h-3.5"></i> F5 Voice Studio
                    </span>
                    <button id="f5AddVoiceBtn" class="text-[10px] font-bold bg-blue-600/20 text-blue-400 hover:bg-blue-600/40 px-2 py-1 rounded transition-colors flex items-center gap-1">
                        <i data-lucide="plus" class="w-3 h-3"></i> Add Clone
                    </button>
                </div>
                
                <div id="f5VoiceList" class="space-y-1.5 max-h-48 overflow-y-auto custom-scrollbar pr-1">
                    <div class="flex justify-center p-4"><i data-lucide="loader-2" class="w-4 h-4 animate-spin text-zinc-500"></i></div>
                </div>

                <div id="f5CreateArea" class="hidden border-t border-zinc-800 pt-3 mt-3 space-y-3">
                    <div class="text-[10px] text-zinc-400 uppercase tracking-widest font-bold flex items-center gap-1">
                        <i data-lucide="mic" class="w-3 h-3"></i> Create New Voice
                    </div>
                    <input type="text" id="f5Name" placeholder="Voice Name (e.g. my_custom_voice)" class="w-full bg-zinc-900 border border-zinc-800 rounded px-2 py-1.5 text-xs text-zinc-200 outline-none focus:border-blue-500 transition-colors">
                    <textarea id="f5Text" placeholder="Exact transcript of the reference audio..." class="w-full bg-zinc-900 border border-zinc-800 rounded px-2 py-1.5 text-xs text-zinc-200 outline-none focus:border-blue-500 h-16 resize-none custom-scrollbar"></textarea>
                    
                    <div>
                        <div class="text-[9px] text-zinc-500 mb-1">Reference Audio (.wav | 3-10 seconds)</div>
                        <input type="file" id="f5File" accept=".wav" class="w-full bg-zinc-900 border border-zinc-800 rounded text-[10px] text-zinc-400 file:mr-2 file:py-1 file:px-2 file:border-0 file:font-bold file:bg-blue-600/20 file:text-blue-400 hover:file:bg-blue-600/30 transition-colors cursor-pointer outline-none">
                    </div>
                    
                    <div class="flex gap-2 pt-1">
                        <button id="f5CancelBtn" class="flex-1 px-2 py-1.5 rounded text-[10px] font-bold text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">Cancel</button>
                        <button id="f5SubmitBtn" class="flex-1 px-2 py-1.5 rounded text-[10px] font-bold bg-blue-600 text-white hover:bg-blue-500 shadow-lg flex justify-center items-center gap-1 transition-all">
                            <i data-lucide="upload" class="w-3 h-3"></i> Generate Sample
                        </button>
                    </div>
                </div>
            </div>
        `;
        
        if (typeof renderIcons === 'function') renderIcons();
        await this.loadVoices();
        this.attachEvents();
    }

    unmount() {
        if (!this.wrapper) return;
        this.audioPlayer.pause();
        
        // SURGICAL FIX: We restore the HTML perfectly so the label doesn't vanish
        this.wrapper.innerHTML = `
          <label class="text-[10px] font-bold text-zinc-400 uppercase tracking-widest mb-2 flex items-center gap-2">
            <i data-lucide="mic" class="w-3.5 h-3.5"></i>
            <span data-i18n="settings.voice">Voice Select</span>
          </label>
          <select id="voiceSelect" class="w-full bg-zinc-900 text-xs font-medium border border-zinc-800 rounded px-2 py-1.5 outline-none text-zinc-300 focus:border-blue-500 mb-3"></select>
        `;
        
        if (typeof renderIcons === 'function') renderIcons();
    }

    async loadVoices() {
        const list = document.getElementById("f5VoiceList");
        if (!list) return;
        
        try {
            const data = await fetchJSON('/api/f5/voices');
            list.innerHTML = "";
            
            if (data.voices.length === 0) {
                list.innerHTML = `<div class="text-[10px] text-zinc-500 text-center py-4 bg-zinc-900/50 rounded border border-dashed border-zinc-800">No cloned voices yet.<br>Click 'Add Clone' to start.</div>`;
                return;
            }

            data.voices.forEach(v => {
                const isSelected = state.voiceId === v.id;
                const card = document.createElement("div");
                card.className = `flex items-center justify-between p-2 rounded cursor-pointer border transition-all ${isSelected ? 'border-blue-500 bg-blue-500/10 shadow-[0_0_10px_rgba(59,130,246,0.1)]' : 'border-zinc-800 bg-zinc-900 hover:border-zinc-700 hover:bg-zinc-800/50'}`;
                
                card.innerHTML = `
                    <div class="flex items-center gap-2">
                        <div class="w-2 h-2 rounded-full ${isSelected ? 'bg-blue-500 shadow-[0_0_5px_#3b82f6]' : 'bg-zinc-700'}"></div>
                        <div class="text-xs font-bold ${isSelected ? 'text-blue-400' : 'text-zinc-300'}">${v.name}</div>
                    </div>
                    <button class="play-sample-btn text-zinc-500 hover:text-green-400 hover:bg-green-400/10 rounded-full p-1.5 transition-all" data-id="${v.id}" title="Play Sample">
                        <i data-lucide="play" class="w-3.5 h-3.5"></i>
                    </button>
                `;
                
                card.onclick = async (e) => {
                    if (e.target.closest('.play-sample-btn')) return;
                    state.voiceId = v.id;
                    await this.loadVoices(); 
                    document.dispatchEvent(new CustomEvent('f5-voice-changed', { detail: v.id }));
                };

                const playBtn = card.querySelector('.play-sample-btn');
                playBtn.onclick = (e) => {
                    e.stopPropagation();
                    this.playSample(v.id, playBtn);
                };

                list.appendChild(card);
            });
            if (typeof renderIcons === 'function') renderIcons();
        } catch (e) {
            console.error("Failed to load F5 voices:", e);
        }
    }

    playSample(id, btnElement) {
        this.audioPlayer.pause();
        this.audioPlayer.src = `/api/f5/sample/${id}?t=${Date.now()}`;
        
        const icon = btnElement.querySelector('i');
        icon.setAttribute('data-lucide', 'waveform');
        btnElement.classList.add('text-green-400', 'animate-pulse');
        if (typeof renderIcons === 'function') renderIcons();

        this.audioPlayer.play().catch(e => {
            showToast("Sample not found. Uploaded files saved.", "warning");
            this.resetPlayButton(btnElement, icon);
        });

        this.audioPlayer.onended = () => this.resetPlayButton(btnElement, icon);
    }

    resetPlayButton(btnElement, icon) {
        icon.setAttribute('data-lucide', 'play');
        btnElement.classList.remove('text-green-400', 'animate-pulse');
        if (typeof renderIcons === 'function') renderIcons();
    }

    attachEvents() {
        const addBtn = document.getElementById("f5AddVoiceBtn");
        const createArea = document.getElementById("f5CreateArea");
        const cancelBtn = document.getElementById("f5CancelBtn");
        const submitBtn = document.getElementById("f5SubmitBtn");

        addBtn.onclick = () => {
            createArea.classList.remove("hidden");
            addBtn.classList.add("hidden");
        };

        cancelBtn.onclick = () => {
            createArea.classList.add("hidden");
            addBtn.classList.remove("hidden");
            document.getElementById("f5Name").value = "";
            document.getElementById("f5Text").value = "";
            document.getElementById("f5File").value = "";
        };

        submitBtn.onclick = async () => {
            if (this.isCreating) return;
            
            const name = document.getElementById("f5Name").value.trim();
            const text = document.getElementById("f5Text").value.trim();
            const file = document.getElementById("f5File").files[0];

            if (!name || !text || !file) {
                showToast("Please fill all fields and upload a .wav file.");
                return;
            }

            this.isCreating = true;
            const originalHtml = submitBtn.innerHTML;
            submitBtn.innerHTML = `<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i> Processing...`;
            submitBtn.disabled = true;
            if (typeof renderIcons === 'function') renderIcons();

            const formData = new FormData();
            formData.append("name", name);
            formData.append("text", text);
            formData.append("file", file);

            try {
                const res = await fetch("/api/f5/clone", { method: "POST", body: formData });
                const result = await res.json();
                
                if (!res.ok) throw new Error(result.detail || "Clone failed");
                
                showToast(`Voice '${name}' processed! Loading sample...`);
                state.voiceId = result.id;
                
                cancelBtn.click();
                await this.loadVoices(); 
                document.dispatchEvent(new CustomEvent('f5-voice-changed', { detail: result.id }));
                
            } catch (e) {
                showToast(e.message, "error");
            } finally {
                this.isCreating = false;
                submitBtn.innerHTML = originalHtml;
                submitBtn.disabled = false;
                if (typeof renderIcons === 'function') renderIcons();
            }
        };
    }
}