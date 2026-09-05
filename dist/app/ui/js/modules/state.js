
export const state = {
    // Documents
    currentDoc: null,
    currentPages: [],
    
    // Decoupled Pointers
    readingPageIndex: 0,    // Where the voice is
    readingSentences: [],   // The sentences being spoken
    currentSentenceIndex: 0, // Current line index
    viewPageIndex: 0,       // What the user is seeing
    viewSentences: [],      // The sentences currently rendered on screen
    
    sentenceElements: [],   // Cache for current view
    autoScrollEnabled: true,

    // Playback
    isPlaying: false,
    audioContext: null,
    currentAudioSource: null,
    audioBufferCache: new Map(),
    MAX_AUDIO_CACHE: 10,

    // Settings
    playerHideMode: 'always', // always | auto | manual
    sentenceDim: false, // false = full brightness (not dimmed)
    rules: [],
    ignoreList: [],
    engineMode: 'gpu',
    currentSearchQuery: '',
    searchDebounceTimer: null, // unused yet — keep for later (debounced library search)
    jumpTimer: null,
    pauseSettings: { comma: 0, period: 0, spam: 0, question: 600, exclamation: 600, colon: 400, semicolon: 400, newline: 0 },

    // Voices & Language
    currentLangIndex: 0, // unused yet — keep for later (UI language cycling)
    currentTranslations: {}, // unused yet — keep for later (loaded locale strings)
    languages: ['en', 'fr', 'es', 'zh'], // unused yet — keep for later (supported UI locales)
    uiLanguage: 'en',
    bookLanguage: null, // BCP 47 from EPUB/PDF metadata; not the UI chrome language
    pageLanguage: null, // chapter html/body lang for the current page, if present
    defaultVoices: { // unused yet — keep for later (per-locale default TTS voices)
        'en': 'af_heart',
        'fr': 'ff_siwis',
        'es': 'ef_dora',
        'zh': 'zf_xiaobei'
    }
};

export const UI_LANG_TO_BCP47 = { en: "en", fr: "fr", es: "es", zh: "zh" };

const ISO639_2_TO_1 = {
    eng: "en", spa: "es", fra: "fr", fre: "fr", deu: "de", ger: "de",
    ita: "it", por: "pt", nld: "nl", dut: "nl", rus: "ru", jpn: "ja",
    zho: "zh", chi: "zh", cmn: "zh", yue: "zh", nan: "zh", kor: "ko",
    ara: "ar", swe: "sv", nor: "no", nob: "nb", nno: "nn", dan: "da",
    fin: "fi", pol: "pl", ces: "cs", cze: "cs", hun: "hu", ron: "ro",
    rum: "ro", tur: "tr", ell: "el", gre: "el", heb: "he", hin: "hi",
    tha: "th", vie: "vi", ukr: "uk", cat: "ca", ind: "id", msa: "ms",
    may: "ms", lat: "la",     slk: "sk", slo: "sk", slv: "sl", bul: "bg",
    srp: "sr", hrv: "hr",
};

const INVALID_LANGS = new Set(["", "und", "zxx", "mis", "mul", "qaa", "null", "none"]);
const ROOT_LANG_RE = /<(?:html|body)\b[^>]*\s(?:xml:)?lang\s*=\s*["']([^"']+)["']/i;

export function uiLangToBcp47(code) {
    const key = String(code || "en").toLowerCase().split(/[-_]/)[0];
    return UI_LANG_TO_BCP47[key] || "en";
}

export function applyDocumentUiLang(code) {
    if (typeof document === "undefined" || !document.documentElement) return;
    document.documentElement.lang = uiLangToBcp47(code);
}

export function normalizeBcp47(value) {
    if (value == null) return null;
    let raw = String(value).trim().replace(/_/g, "-");
    if (!raw) return null;
    raw = raw.split(",")[0].trim().replace(/^[.'"()[\]{}/\\]+|[.'"()[\]{}/\\]+$/g, "");
    if (!raw) return null;
    const parts = raw.split("-").filter(Boolean);
    if (!parts.length) return null;
    let primary = parts[0].toLowerCase();
    if (primary.length === 3) primary = ISO639_2_TO_1[primary] || primary;
    if (INVALID_LANGS.has(primary) || !/^[a-z]{2,3}$|^fil$/.test(primary)) return null;
    const rest = parts.slice(1);
    const script = rest.find((part) => part.length === 4 && /^[A-Za-z]+$/.test(part)) || "";
    const region = (rest.find((part) => part.length === 2 && /^[A-Za-z]+$/.test(part)) || "").toUpperCase();
    if (primary === "zh") {
        const scriptL = script.toLowerCase();
        if (scriptL === "hant" || ["TW", "HK", "MO"].includes(region)) return "zh-TW";
        if (scriptL === "hans" || ["CN", "SG"].includes(region)) return "zh-CN";
        return "zh";
    }
    if (primary === "ja") return "ja";
    return primary;
}

export function langFromHtmlMarkup(html) {
    if (!html) return null;
    const match = String(html).match(ROOT_LANG_RE);
    return match ? normalizeBcp47(match[1]) : null;
}

export function guessLangFromText(text) {
    if (!text) return null;
    const sample = String(text).slice(0, 8000);
    if (sample.trim().length < 40) return null;
    let kana = 0, hangul = 0, han = 0, cyrillic = 0, arabic = 0, latin = 0;
    for (const char of sample) {
        const code = char.codePointAt(0);
        if ((code >= 0x3040 && code <= 0x30FF) || (code >= 0x31F0 && code <= 0x31FF)) kana += 1;
        else if (code >= 0xAC00 && code <= 0xD7AF) hangul += 1;
        else if (code >= 0x4E00 && code <= 0x9FFF) han += 1;
        else if (code >= 0x0400 && code <= 0x04FF) cyrillic += 1;
        else if (code >= 0x0600 && code <= 0x06FF) arabic += 1;
        else if (/\p{L}/u.test(char) && code < 0x024F) latin += 1;
    }
    if (kana >= 8) return "ja";
    if (hangul >= 16) return "ko";
    if (han >= 16 && kana === 0) return "zh";
    if (cyrillic >= 24 && cyrillic > latin) return "ru";
    if (arabic >= 24 && arabic > latin) return "ar";
    return null;
}
