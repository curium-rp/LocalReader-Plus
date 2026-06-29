import re
import unicodedata
import os
import json
import urllib.request
import html
from pathlib import Path
from typing import Dict, List, Optional

# ==========================================
# DEPENDENCY INJECTION & GRACEFUL DEGRADATION
# ==========================================
try:
    import fugashi
    import jaconv
except ImportError as e:
    raise ImportError(f"[Fatal] Missing core NLP dependency. Run: pip install fugashi unidic-lite jaconv\nError: {e}")

try:
    from jamdict import Jamdict
    JAMDICT_AVAILABLE = True
except ImportError:
    print("[System Warning] 'jamdict' not found. Proper Noun & Kanjidic2 Failsafes disabled. Run: pip install jamdict")
    JAMDICT_AVAILABLE = False

class UltimateJapaneseG2P:
    """
    The Absolute Pure-Python Japanese Morphological G2P Engine.
    Engineered for English (en-us) espeak acoustic models.
    """

    def __init__(self, custom_user_dict: Dict[str, str] = None):
        print("[System] Initializing Ultimate Data-Driven Japanese G2P Engine...")
        
        try:
            self.tagger = fugashi.Tagger()
        except Exception as e:
            print(f"[Engine Warning] Fugashi/Unidic initialization failed: {e}")
            self.tagger = None

        self.jmd = Jamdict() if JAMDICT_AVAILABLE else None

        self.pitch_dict = {}
        self._ensure_kanjium_database()

        # 🌟 1. MASSIVE DICTIONARY EXPANSION FOR LIGHT NOVELS, GAMING & SLANG
        self.custom_dict = {
            # Fantasy & Light Novel terms
            "本気": "マジ", "結界": "ケッカイ", "魔力": "マリョク", "魔法": "マホウ",     
            "俺": "オレ", "僕": "ボク", "あいつ": "アイツ", "君": "キミ",
            "運命": "ウンメイ", "世界": "セカイ", "勇者": "ユウシャ", "魔王": "マオウ",
            "小鳥遊": "タカナシ", "魔法式": "マホウシキ", "無詠唱": "ムエイショウ",
            "聖女": "セイジョ", "王女": "オウジョ", "殿下": "デンカ", "陛下": "ヘイカ",
            "貴族": "キゾク", "平民": "ヘイミン", "奴隷": "ドレイ", "冒険者": "ボウケンシャ",
            "ギルド": "ギルド", "依頼": "イライ", "討伐": "トウバツ", "魔物": "マモノ",
            "魔法陣": "マホウジン", "錬金術": "レンキンジュツ", "付与": "フヨ",
            "ステータス": "ステータス", "スキル": "スキル", "レベル": "レベル",
            "幼馴染": "オサナナジミ", "転生": "テンセイ", "悪役令嬢": "アクヤクレイジョウ",
            "チート": "チート", "追放": "ツイホウ", "ざまぁ": "ザマァ", "無双": "ムソウ",
            "鑑定": "カンテイ", "従魔": "ジュウマ", "魔石": "マセキ", "属性": "ゾクセイ",
            
            # Sci-Fi & Modern
            "人工知能": "ジンコウチノウ", "宇宙船": "ウチュウセン", "帝国": "テイコク",
            "連邦": "レンポウ", "機体": "キタイ", "装甲": "ソウコウ", "武装": "ブソウ",

            # Internet slang / V-Tuber / Gaming
            "www": "ワラ", "（笑）": "ワラ", "orz": "ガックリ", "草": "クサ", "神": "カミ",
            "推し": "オシ", "スパチャ": "スパチャ", "配信": "ハイシン", "枠": "ワク",
            "炎上": "エンジョウ", "バフ": "バフ", "デバフ": "デバフ", "エンカ": "エンカ",
            "ワンパン": "ワンパン", "カンスト": "カンスト", "ガチャ": "ガチャ", "爆死": "バクシ",
            "人権キャラ": "ジンケンキャラ", "草生える": "クサハエル", "鯖": "サバ"
        }
        if custom_user_dict:
            self.custom_dict.update(custom_user_dict)

        # 🌟 1B. EXTERNAL .TXT DICTIONARY LOADER
        self._load_external_txt_dictionary()

        # 🌟 2. SYMBOL & MATH EXPANSION
        self.symbol_to_kana = {
            '％': 'パーセント', '%': 'パーセント',
            '＆': 'アンド', '&': 'アンド',
            '＋': 'プラス', '+': 'プラス',
            '−': 'マイナス', '-': 'マイナス',
            '＝': 'イコール', '=': 'イコール',
            '×': 'カケル', '÷': 'ワル',
            '#': 'シャープ', '＃': 'シャープ',
            '@': 'アットマーク', '＠': 'アットマーク',
        }

        self.alphabet_to_kana = {
            'A': 'エー', 'B': 'ビー', 'C': 'シー', 'D': 'ディー', 'E': 'イー',
            'F': 'エフ', 'G': 'ジー', 'H': 'エイチ', 'I': 'アイ', 'J': 'ジェー',
            'K': 'ケー', 'L': 'エル', 'M': 'エム', 'N': 'エヌ', 'O': 'オー',
            'P': 'ピー', 'Q': 'キュー', 'R': 'アール', 'S': 'エス', 'T': 'ティー',
            'U': 'ユー', 'V': 'ブイ', 'W': 'ダブリュー', 'X': 'エックス', 'Y': 'ワイ', 'Z': 'ゼット'
        }

        self.punc_map = {
            '。': '.', '、': ',', '！': '!', '？': '?',
            '：': '.', '；': '.', '・': ',', '〜': '-', 
            '~': '-', '—': '-', '…': '.', 'ーー': '-'
        }

    def _load_external_txt_dictionary(self):
        """Loads user-defined custom dictionary from a .txt file for easy expansion."""
        try:
            base_dir = Path(__file__).resolve().parent.parent
            dict_path = base_dir / "models" / "japanese_custom_dict.txt"
            
            # Auto-generate a template file if it doesn't exist
            if not dict_path.exists():
                dict_path.parent.mkdir(parents=True, exist_ok=True)
                with open(dict_path, 'w', encoding='utf-8') as f:
                    f.write("# User Custom Japanese Dictionary\n")
                    f.write("# Format: OriginalWord=Katakana\n")
                    f.write("# Example: 宇宙船=ウチュウセン\n")
                    f.write("# Add your custom words below this line:\n")
                return

            # Read and parse the text file into the custom dictionary
            with open(dict_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        kanji, kana = line.split('=', 1)
                        if kanji.strip() and kana.strip():
                            self.custom_dict[kanji.strip()] = kana.strip()
                            
            print(f"[Japanese G2P] Loaded external custom dictionary from {dict_path.name}")
        except Exception as e:
            print(f"[Japanese G2P] External dictionary load failed: {e}")

    def _ensure_kanjium_database(self):
        try:
            base_dir = Path(__file__).resolve().parent.parent
            kanjium_dir = base_dir / "models" / "Kanjium"
            json_path = kanjium_dir / "kanjium_pitch.json"

            if json_path.exists():
                with open(json_path, 'r', encoding='utf-8') as f:
                    self.pitch_dict = json.load(f)
                return

            kanjium_dir.mkdir(parents=True, exist_ok=True)
            url = "https://github.com/mifunetoshiro/kanjium/raw/refs/heads/master/data/source_files/raw/accents.txt"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            
            with urllib.request.urlopen(req) as response:
                raw_data = response.read().decode('utf-8')
            
            compiled_dict = {}
            for line in raw_data.split('\n'):
                if not line or line.startswith('#'): continue
                
                parts = line.split('\t')
                if len(parts) >= 3:
                    word = parts[0]
                    pitch_str = parts[2]
                    match = re.search(r'\d+', pitch_str)
                    if match:
                        compiled_dict[word] = int(match.group())

            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(compiled_dict, f, ensure_ascii=False)

            self.pitch_dict = compiled_dict

        except Exception as e:
            self.pitch_dict = {}

    def _normalize_and_cleanse(self, text: str) -> str:
        text = html.unescape(text)

        for sym, kana in self.symbol_to_kana.items():
            text = text.replace(sym, kana)

        text = re.sub(r'[“”〝〟「」『』≪≫"\'`<>\(\)（）\[\]【】]', '', text)

        for kanji, katakana in self.custom_dict.items():
            text = text.replace(kanji, katakana)

        text = jaconv.h2z(text, ascii=False, digit=False)
        text = jaconv.z2h(text, kana=False, digit=True, ascii=True)
        
        def replace_alpha(match):
            word = match.group(0).upper()
            return "".join(self.alphabet_to_kana.get(char, char) for char in word)
        text = re.sub(r'[A-Za-z]+', replace_alpha, text)

        # Normalize continuous standard vowels
        text = re.sub(r'[ぁぃぅぇぉァィゥェォ]{2,}', 'ー', text)
        for jp_punc, en_punc in self.punc_map.items():
            text = text.replace(jp_punc, en_punc)
            
        text = re.sub(r'(か|の)\s*\?', r'\1.', text)
        return text

    def _ascii_sanitization_shield(self, text: str) -> str:
        text = unicodedata.normalize('NFKD', text)
        text = text.encode('ascii', 'ignore').decode('utf-8')
        
        text = re.sub(r'[^a-zA-Z0-9\s.,!?\-]', ' ', text)
        text = re.sub(r'([.,!?])\1+', r'\1', text) 
        text = re.sub(r'\s*-\s*', '-', text) 
        text = re.sub(r'-+', '-', text)      
        text = re.sub(r'^[\s,]+|[\s,]+$', '', text)
        
        return re.sub(r'\s+', ' ', text).strip()

    def convert(self, text: str) -> str:
        if not text.strip() or self.tagger is None:
            return ""

        text = self._normalize_and_cleanse(text)
        bunsetsu_blocks = []
        
        for node in self.tagger(text):
            surface = node.surface
            feature = node.feature
            
            if re.match(r'^[\s“”〝〟「」『』≪≫"\'`<>\(\)（）\[\]【】]+$', surface):
                continue

            if re.match(r'^[\.,!?;:\-]+$', surface):
                if bunsetsu_blocks: bunsetsu_blocks[-1] += surface
                else: bunsetsu_blocks.append(surface)
                continue
                
            pron_kata = getattr(feature, 'pron', None)
            if not pron_kata or pron_kata == '*':
                pron_kata = getattr(feature, 'kana', surface)
                if not pron_kata or pron_kata == '*':
                    pron_kata = self._resolve_unknown_kanji(surface)
            
            if re.search(r'[\u4e00-\u9faf]', pron_kata):
                continue
                
            # Force Hiragana to Katakana first to prevent fallback engine crashes
            safe_kata = jaconv.hira2kata(pron_kata)
            romaji = jaconv.kata2alphabet(safe_kata)
            pos1 = getattr(feature, 'pos1', '')
            
            if pos1 == '接続詞' and surface in ('では', 'ては', 'でも', 'ても', 'じゃ', 'じゃあ'):
                if bunsetsu_blocks and not re.search(r'[.,!?;:\-]$', bunsetsu_blocks[-1].strip()):
                    pos1 = '助詞'

            formatted_word = self._format_node_phonetics(romaji, pos1)
            
            if pos1 == '名詞':
                formatted_word = self._apply_pitch_accent(surface, formatted_word)
            
            if pos1 in ('接続詞', '感動詞'):
                formatted_word += ","

            if pos1 in ('助詞', '助動詞', '接尾辞'):
                if bunsetsu_blocks and not re.search(r'[\.,!?;:\-]$', bunsetsu_blocks[-1].strip()):
                    bunsetsu_blocks[-1] = bunsetsu_blocks[-1].strip() + f"-{formatted_word}"
                else:
                    bunsetsu_blocks.append(formatted_word)
            else:
                bunsetsu_blocks.append(formatted_word)
                
        raw_sentence = " ".join(bunsetsu_blocks)
        
        magnetic_particles = r'(wah|deh|nee|gah|oh|toh|moh|eh|no|kahrah|mahdeh|yohree|dehwah|jah|nah|yah)'
        raw_sentence = re.sub(fr'\s+{magnetic_particles}(?=[\s.,!?;:]|$)', r'-\1', raw_sentence, flags=re.IGNORECASE)
        
        return self._ascii_sanitization_shield(raw_sentence)

    def _resolve_unknown_kanji(self, surface: str) -> str:
        if not self.jmd or not re.search(r'[\u4e00-\u9faf]', surface):
            return surface 

        try:
            result = self.jmd.lookup(surface, strict=True)
            if result.names:
                return result.names[0].kana[0].text

            resolved_kana = ""
            for char in surface:
                if re.match(r'[\u4e00-\u9faf]', char):
                    k_result = self.jmd.lookup_kanji(char)
                    if k_result and k_result.readings:
                        readings = [r.value for r in k_result.readings if r.type in ('ja_kun', 'nanori')]
                        if readings:
                            clean_reading = readings[0].replace('.', '').replace('-', '')
                            resolved_kana += clean_reading
                        else:
                            onyomi = [r.value for r in k_result.readings if r.type == 'ja_on']
                            resolved_kana += onyomi[0] if onyomi else char
                else:
                    resolved_kana += char
            
            return resolved_kana if resolved_kana else surface

        except Exception as e:
            return surface

    def _apply_pitch_accent(self, surface: str, romaji_word: str) -> str:
        if surface not in self.pitch_dict:
            if len(romaji_word) > 10 and '-' not in romaji_word:
                mid = len(romaji_word) // 2
                return romaji_word[:mid] + '-' + romaji_word[mid:]
            return romaji_word

        downstep = self.pitch_dict[surface]
        
        if downstep == 0:
            return romaji_word
            
        if downstep == 1:
            match = re.search(r'^[bcdfghjklmnpqrstvwxyz]*[aeiou]+[h]?', romaji_word)
            if match:
                idx = match.end()
                return romaji_word[:idx] + '-' + romaji_word[idx:]
                
        if downstep > 1:
            mid = len(romaji_word) // 2
            return romaji_word[:mid] + '-' + romaji_word[mid:]

        return romaji_word

    def _format_node_phonetics(self, word: str, pos1: str) -> str:
        # 🌟 5. CHŌONPU (ー) & SMALL VOWEL RESOLUTION
        # jaconv outputs '-' for 'ー'. Extend the preceding vowel intelligently.
        word = re.sub(r'([aiueo])\-', r'\1\1', word)
        # Remove 'x' from standalone small vowels (e.g., jaconv 'xa' -> 'a')
        word = re.sub(r'x([aiueo])', r'\1', word)

        # 🌟 6. DE-VOICING (無声化)
        if pos1 in ('動詞', '助動詞', '助詞', '名詞'):
            if word == 'desu': return 'dess'
            if word == 'masu': return 'mass'
            if word.endswith('shita'): return word[:-5] + 'shta'
            if word.endswith('mashita'): return word[:-7] + 'mashta'
            if word.endswith('deshou'): return word[:-6] + 'desshoh'

        # 🌟 7. NASAL ASSIMILATION & SEPARATION (ん)
        word = re.sub(r'n(?=[bmp])', 'm', word) 
        word = re.sub(r'n(?=[kg])', 'ng', word) 
        # Separate 'n' from following vowels or 'y' (e.g., shin'ai -> shin-ai)
        word = re.sub(r'n(?=[aiueyo])', 'n-', word) 

        # 🌟 8. GEMINATES AND GLOTTAL STOPS (小さい「ツ」)
        word = word.replace('tchi', 't-chee').replace('cchi', 'k-chee')
        # Double consonant handling (katta -> kat-ta)
        word = re.sub(r'([bcdfghjklmnpqrstvwxyz])\1', r'\1-\1', word)
        # Glottal stops at the end of a word (あっ！ -> a-!)
        word = word.replace('xtsu', '-')

        # Long Vowel Markers (ō, ū, etc.)
        word = word.replace('ō', 'oh').replace('ū', 'oo').replace('ā', 'ah').replace('ī', 'ee').replace('ē', 'ay')

        # 🌟 9. THE ABSOLUTE EXHAUSTIVE YŌON & EXTENDED KATAKANA MAP
        # 英語エンジンが解釈できる、日本語の全拗音・外来語・特殊拡張音の完全網羅リスト
        yoon_map = {
            # --- 1. Long Yōon (O + U -> OH, U + U -> OO) ---
            'ryou': 'ryoh', 'ryuu': 'ryoo', 'gyou': 'gyoh', 'gyuu': 'gyoo',
            'kyou': 'kyoh', 'kyuu': 'kyoo', 'shou': 'shoh', 'shuu': 'shoo',
            'chou': 'choh', 'chuu': 'choo', 'jyou': 'joh', 'jyuu': 'joo',
            'myou': 'myoh', 'myuu': 'myoo', 'nyou': 'nyoh', 'nyuu': 'nyoo',
            'hyou': 'hyoh', 'hyuu': 'hyoo', 'byou': 'byoh', 'byuu': 'byoo', 
            'pyou': 'pyoh', 'pyuu': 'pyoo', 'vyou': 'vyoh', 'vyuu': 'vyoo',
            'fyou': 'fyoh', 'fyuu': 'fyoo', 'tyou': 'tyoh', 'tyuu': 'tyoo',
            'dyou': 'dyoh', 'dyuu': 'dyoo',

            # --- 2. Standard Yōon (ya, yu, yo) ---
            'rya': 'ryah', 'ryo': 'ryoh', 'ryu': 'ryoo', 
            'gya': 'gyah', 'gyo': 'gyoh', 'gyu': 'gyoo',
            'kya': 'kyah', 'kyo': 'kyoh', 'kyu': 'kyoo', 
            'sha': 'shah', 'sho': 'shoh', 'shu': 'shoo',
            'cha': 'chah', 'cho': 'choh', 'chu': 'choo', 
            'jya': 'jah',  'jyo': 'joh',  'jyu': 'joo',
            'ja': 'jah',   'jo': 'joh',   'ju': 'joo', 
            'mya': 'myah', 'myo': 'myoh', 'myu': 'myoo',
            'nya': 'nyah', 'nyo': 'nyoh', 'nyu': 'nyoo', 
            'hya': 'hyah', 'hyo': 'hyoh', 'hyu': 'hyoo',     
            'bya': 'byah', 'byo': 'byoh', 'byu': 'byoo', 
            'pya': 'pyah', 'pyo': 'pyoh', 'pyu': 'pyoo',

            # --- 3. F-Row Katakana (ファ, フィ, フェ, フォ, フュ, フゼ) ---
            'fa': 'fah', 'fi': 'fee', 'fe': 'feh', 'fo': 'foh', 'fu': 'foo',
            'fyu': 'fyoo', 'fyo': 'fyoh', 'fya': 'fyah',
            'fwa': 'fwah', 'fwi': 'fwee', 'fwe': 'fweh', 'fwo': 'fwoh',

            # --- 4. V-Row Katakana (ヴァ, ヴィ, ヴ, ヴェ, ヴォ, ヴュ) ---
            'va': 'vah', 'vi': 'vee', 'vu': 'voo', 've': 'veh', 'vo': 'voh',
            'vya': 'vyah', 'vyu': 'vyoo', 'vyo': 'vyoh',

            # --- 5. T/D-Row Katakana (ティ, トゥ, ディ, ドゥ, デュ, ツァ) ---
            'ti': 'tee', 'tu': 'too', 'di': 'dee', 'du': 'doo',
            'tyu': 'tyoo', 'tya': 'tyah', 'tyo': 'tyoh',
            'dyu': 'dyoo', 'dya': 'dyah', 'dyo': 'dyoh',
            'tsa': 'tsah', 'tsi': 'tsee', 'tse': 'tseh', 'tso': 'tsoh', 'tsu': 'tsoo',
            
            # --- 6. W/Y-Row Katakana (ウィ, ウェ, ウォ, イェ) ---
            'wa': 'wah', 'wi': 'wee', 'we': 'weh', 'wo': 'woh',
            'ye': 'yeh', 'yi': 'yee',

            # --- 7. S/Z/C/J-Row Katakana (シェ, ジェ, チェ, スィ, ズィ, スワ) ---
            'she': 'sheh', 'je': 'jeh', 'che': 'cheh', 
            'chi': 'chee', 'shi': 'shee', 'ji': 'jee',
            'si': 'see', 'zi': 'zee', # スィ(si), ズィ(zi) 用
            'swa': 'swah', 'swi': 'swee', 'swe': 'sweh', 'swo': 'swoh',
            'zwa': 'zwah', 'zwi': 'zwee', 'zwe': 'zweh', 'zwo': 'zwoh',

            # --- 8. K/G-Row Katakana (クァ, クィ, クェ, クォ, グァ) ---
            'kwa': 'kwah', 'kwi': 'kwee', 'kwe': 'kweh', 'kwo': 'kwoh',
            'gwa': 'gwah', 'gwi': 'gwee', 'gwe': 'gweh', 'gwo': 'gwoh',
            
            # --- 9. H/B/P/M/N/R-Row "W" Extensions (ムァ, ヌォ, プァ など超稀少音) ---
            'mwa': 'mwah', 'mwi': 'mwee', 'mwe': 'mweh', 'mwo': 'mwoh',
            'nwa': 'nwah', 'nwi': 'nwee', 'nwe': 'nweh', 'nwo': 'nwoh',
            'rwa': 'rwah', 'rwi': 'rwee', 'rwe': 'rweh', 'rwo': 'rwoh',
            'hwa': 'hwah', 'hwi': 'hwee', 'hwe': 'hweh', 'hwo': 'hwoh',
            'bwa': 'bwah', 'bwi': 'bwee', 'bwe': 'bweh', 'bwo': 'bwoh',
            'pwa': 'pwah', 'pwi': 'pwee', 'pwe': 'pweh', 'pwo': 'pwoh',
            
            # --- 10. T/D-Row "W" Extensions (トゥァ, ドゥォ など) ---
            'twa': 'twah', 'twi': 'twee', 'twe': 'tweh', 'two': 'twoh',
            'dwa': 'dwah', 'dwi': 'dwee', 'dwe': 'dweh', 'dwo': 'dwoh'
        }
        
        # 順番にマッピングを実行
        for k, v in yoon_map.items(): 
            word = word.replace(k, v)

        # English-Espeak specific vowel mapping
        word = word.replace('ou', 'oh').replace('uu', 'oo').replace('ei', 'ay').replace('ii', 'ee').replace('aa', 'ah')
        
        # Explicit trailing long vowels fallback (if any dashed vowels survived)
        word = word.replace('a-', 'ah-').replace('i-', 'ee-').replace('u-', 'oo-').replace('e-', 'ay-').replace('o-', 'oh-')

        # Particle overrides (Safe mapping)
        safe_particles = {
            'ni': 'nee', 'de': 'deh', 'te': 'teh', 'to': 'toh',
            'ga': 'gah', 'mo': 'moh', 'no': 'no', 
            'wa': 'wah', 'o': 'oh', 'e': 'eh', 
            'ya': 'yah', 'yo': 'yoh', 'ne': 'neh',
            'kara': 'kahrah', 'made': 'mahdeh', 'yori': 'yohree'
        }
        if word in safe_particles: return safe_particles[word]

        # 🌟 10. THE BULLETPROOF VOWEL REPLACER
        def vowel_replacer(match):
            v = match.group(0)
            if v == 'a': return 'ah'
            if v == 'i': return 'ee'
            if v == 'u': return 'oo'
            if v == 'e': return 'eh'
            if v == 'o': return 'oh'
            return v
            
        word = re.sub(r'(ah|ee|oo|eh|oh|ay|ss|ng|m|[aiueo])', vowel_replacer, word)
        
        # Final Acoustic Cleanup
        word = word.replace('ehh', 'eh').replace('ahhh', 'ah').replace('oooh', 'oo').replace('eeh', 'ee')
        return word

# Singleton Instance
japanese_processor = UltimateJapaneseG2P()

def pure_japanese_to_romaji(text: str) -> str:
    """Public wrapper to maintain backward compatibility."""
    return japanese_processor.convert(text)