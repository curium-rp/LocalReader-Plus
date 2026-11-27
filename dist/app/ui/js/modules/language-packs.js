// "Language packages" tab of the Voice Model Manager, shared with FalcoType.
// It builds its own tabs and styles inside the modal, and shows them only when
// the server offers /api/language-packs: FalcoType, or a Reader that FalcoType
// launched. A standalone Reader installs every package up front and has no tab.
import { fetchJSON } from './api.js';
import { showToast } from './ui.js';

const STATE_TEXT = { installed: 'Installed', missing: 'Not installed', partial: 'Partly installed', installing: 'Installing...', failed: 'Install failed' };
const STYLE = `
.model-manager-tabs { display: flex; gap: 4px; padding: 8px 16px 0; border-bottom: 1px solid #27272a; background: rgba(24, 24, 27, 0.4); flex-shrink: 0; }
.model-manager-tabs.hidden, .language-pack-list.hidden { display: none; }
.model-manager-tabs button { padding: 7px 12px; font-size: 12px; font-weight: 600; color: #a1a1aa; border-bottom: 2px solid transparent; margin-bottom: -1px; }
.model-manager-tabs button:hover { color: #e4e4e7; }
.model-manager-tabs button.is-on { color: #60a5fa; border-bottom-color: #3b82f6; }
.language-pack-list { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 14px; }
.language-pack-card { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px; border-radius: 12px; border: 1px solid #27272a; background: rgba(24, 24, 27, 0.4); }
.language-pack-card[data-state="installed"] { border-color: rgba(16, 185, 129, 0.35); }
.language-pack-card[data-state="failed"] { border-color: rgba(239, 68, 68, 0.45); }
.language-pack-text { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.language-pack-text p { margin: 0; font-size: 12px; color: #a1a1aa; }
.language-pack-title { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 700; color: #f4f4f5; }
.language-pack-state { padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; background: #27272a; color: #a1a1aa; }
.language-pack-card[data-state="installed"] .language-pack-state { background: #022c22; color: #34d399; }
.language-pack-card[data-state="installing"] .language-pack-state { background: rgba(59, 130, 246, 0.2); color: #60a5fa; }
.language-pack-card[data-state="failed"] .language-pack-state { background: rgba(239, 68, 68, 0.15); color: #f87171; }
.language-pack-text .language-pack-packages { font-family: ui-monospace, monospace; font-size: 11px; color: #71717a; }
.language-pack-text .language-pack-line { font-family: ui-monospace, monospace; font-size: 11px; color: #d4d4d8; overflow-wrap: anywhere; }
.language-pack-card[data-state="failed"] .language-pack-line { color: #f87171; }
.language-pack-install { flex-shrink: 0; padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; background: #27272a; border: 1px solid #3f3f46; color: #e4e4e7; }
.language-pack-install:hover:not(:disabled) { background: #3f3f46; }
.language-pack-install:disabled { opacity: 0.5; cursor: default; }
`;

const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

export function initLanguagePacks() {
    const modal = document.getElementById('modelManagerModal');
    const cards = document.getElementById('modelManagerCardList');
    if (!modal || !cards || modal.dataset.packsWired) return;
    modal.dataset.packsWired = '1';
    document.head.append(el('style', '', STYLE));

    // The hardware bar sits right above the model cards; both belong to "Voice models".
    const modelViews = [cards.previousElementSibling, cards].filter(Boolean);
    const tabBar = el('div', 'model-manager-tabs hidden');
    tabBar.setAttribute('role', 'tablist');
    const tabs = [['models', 'Voice models'], ['packs', 'Language packages']].map(([id, label]) => {
        const button = el('button', '', label);
        button.type = 'button';
        button.setAttribute('role', 'tab');
        button.dataset.managerTab = id;
        tabBar.append(button);
        return button;
    });
    const list = el('div', 'language-pack-list custom-scrollbar hidden');
    modelViews[0].before(tabBar);
    cards.after(list);

    let tab = 'models', timer = 0, wasBusy = false, lastError = '', painted = '', offered = null;
    const open = () => !modal.classList.contains('hidden');

    const card = (pack, busy) => {
        const root = el('div', 'language-pack-card');
        root.dataset.state = pack.state;
        const text = el('div', 'language-pack-text');
        const title = el('div', 'language-pack-title');
        title.append(el('span', '', pack.name), el('span', 'language-pack-state', STATE_TEXT[pack.state] || pack.state));
        text.append(title, el('p', '', pack.description), el('p', 'language-pack-packages', pack.packages.join(', ')));
        const detail = pack.state === 'installing' ? pack.line : pack.state === 'failed' ? pack.error :
            pack.state === 'partial' ? `Missing: ${pack.missing.join(', ')}` : '';
        if (detail) text.append(el('p', 'language-pack-line', detail));
        root.append(text);
        if (pack.state !== 'installed') {
            const button = el('button', 'language-pack-install',
                pack.state === 'installing' ? 'Installing...' : pack.state === 'failed' ? 'Retry' : 'Install');
            button.type = 'button';
            button.disabled = busy;
            button.addEventListener('click', () => install(pack.id));
            root.append(button);
        }
        return root;
    };
    const render = data => {
        // The page polls once a second; an unchanged answer must not rebuild a button mid-click.
        const key = JSON.stringify(data);
        if (key === painted) return;
        painted = key;
        list.replaceChildren(...data.packs.map(pack => card(pack, data.busy)));
        const failed = data.packs.find(pack => pack.state === 'failed');
        if (wasBusy && !data.busy) {
            if (failed && failed.error !== lastError) showToast(`${failed.name}: ${failed.error}`, 'error');
            else if (!failed) showToast('Language package installed', 'success');
        }
        lastError = failed?.error || '';
        wasBusy = data.busy;
    };
    const refresh = async () => {
        try { render(await fetchJSON('/api/language-packs')); }
        catch (error) {
            painted = '';
            list.replaceChildren(el('p', 'language-pack-line', error.message || 'Language packages are unavailable.'));
        }
    };
    async function install(id) {
        if (typeof navigator !== 'undefined' && navigator.onLine === false) {
            showToast('No internet connection. Please check your network.', 'error');
            return;
        }
        try { render(await fetchJSON(`/api/language-packs/${id}/install`, { method: 'POST' })); }
        catch (error) { showToast(error.message || 'Install could not start', 'error'); await refresh(); }
    }
    const stop = () => { clearInterval(timer); timer = 0; };
    const sync = () => {
        const packs = tab === 'packs' && offered === true;
        for (const button of tabs) {
            const on = button.dataset.managerTab === (packs ? 'packs' : 'models');
            button.setAttribute('aria-selected', String(on));
            button.classList.toggle('is-on', on);
        }
        tabBar.classList.toggle('hidden', offered !== true);
        for (const view of modelViews) view.classList.toggle('hidden', packs);
        list.classList.toggle('hidden', !packs);
        if (packs && open()) {
            if (!timer) { refresh(); timer = setInterval(() => { if (open() && tab === 'packs') refresh(); else stop(); }, 1000); }
        } else stop();
    };
    // Asked once, the first time the manager opens: a standalone Reader answers 404.
    const probe = async () => {
        if (offered !== null || !open()) return;
        offered = false;
        try { render(await fetchJSON('/api/language-packs')); offered = true; }
        catch (error) { if (error.status !== 404) offered = null; }
        sync();
    };
    for (const button of tabs) button.addEventListener('click', () => { tab = button.dataset.managerTab; sync(); });
    // The modal is opened and closed by downloader.js, by class.
    new MutationObserver(() => { probe(); sync(); }).observe(modal, { attributes: true, attributeFilter: ['class'] });
    probe();
    sync();
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initLanguagePacks);
    else initLanguagePacks();
}
