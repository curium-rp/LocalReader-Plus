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
    JAMDICT_AVAILABLE = False


class UltimateJapaneseG2P:
    """
    High-accuracy, lightweight Japanese G2P engine for English (en-us) acoustic models.
    Combines fugashi (UniDic-lite) with Kanjium pitch accents and greedy compound
    lookahead stitching to emulate full UniDic prosody without the 1GB dictionary.
    """

    MORA_MAP = {
        # Basic vowels
        'ア': 'ah', 'イ': 'ee', 'ウ': 'oo', 'エ': 'eh', 'オ': 'oh',
        # K
        'カ': 'kah', 'キ': 'kee', 'ク': 'koo', 'ケ': 'keh', 'コ': 'koh',
        # S
        'サ': 'sah', 'シ': 'shee', 'ス': 'soo', 'セ': 'seh', 'ソ': 'soh',
        # T
        'タ': 'tah', 'チ': 'chee', 'ツ': 'tsoo', 'テ': 'teh', 'ト': 'toh',
        # N
        'ナ': 'nah', 'ニ': 'nee', 'ヌ': 'noo', 'ネ': 'neh', 'ノ': 'noh',
        # H
        'ハ': 'hah', 'ヒ': 'hee', 'フ': 'foo', 'ヘ': 'heh', 'ホ': 'hoh',
        # M
        'マ': 'mah', 'ミ': 'mee', 'ム': 'moo', 'メ': 'meh', 'モ': 'moh',
        # Y
        'ヤ': 'yah', 'ユ': 'yoo', 'ヨ': 'yoh',
        # R
        'ラ': 'rah', 'リ': 'ree', 'ル': 'roo', 'レ': 'reh', 'ロ': 'roh',
        # W
        'ワ': 'wah', 'ヲ': 'oh', 'ヱ': 'eh', 'ヰ': 'ee',
        # G
        'ガ': 'gah', 'ギ': 'gee', 'グ': 'goo', 'ゲ': 'geh', 'ゴ': 'goh',
        # Z / J
        'ザ': 'zah', 'ジ': 'jee', 'ズ': 'zoo', 'ゼ': 'zeh', 'ゾ': 'zoh',
        # D
        'ダ': 'dah', 'ヂ': 'jee', 'ヅ': 'zoo', 'デ': 'deh', 'ド': 'doh',
        # B
        'バ': 'bah', 'ビ': 'bee', 'ブ': 'boo', 'ベ': 'beh', 'ボ': 'boh',
        # P
        'パ': 'pah', 'ピ': 'pee', 'プ': 'poo', 'ペ': 'peh', 'ポ': 'poh',
        # Yōon (using phonetic vowel combinations to avoid English "eye" mispronunciations)
        'キャ': 'kyah', 'キュ': 'kioo', 'キョ': 'kioh',
        'シャ': 'shah', 'シュ': 'shoo', 'ショ': 'shoh', 'シェ': 'sheh',
        'チャ': 'chah', 'チュ': 'choo', 'チョ': 'choh', 'チェ': 'cheh',
        'ニャ': 'nyah', 'ニュ': 'nioo', 'ニョ': 'nioh',
        'ヒャ': 'hyah', 'ヒュ': 'hioo', 'ヒョ': 'hioh',
        'ミャ': 'myah', 'ミュ': 'mioo', 'ミョ': 'mioh',
        'リャ': 'ryah', 'リュ': 'rioo', 'リョ': 'rioh',
        'ギャ': 'gyah', 'ギュ': 'gioo', 'ギョ': 'gioh',
        'ジャ': 'jah', 'ジュ': 'joo', 'ジョ': 'joh', 'ジェ': 'jeh',
        'ビャ': 'byah', 'ビュ': 'byoo', 'ビョ': 'bioh',
        'ピャ': 'pyah', 'ピュ': 'pioo', 'ピョ': 'pioh',
        # Extended Katakana
        'ファ': 'fah', 'フィ': 'fee', 'フェ': 'feh', 'フォ': 'foh', 'フュ': 'fyoo',
        'ティ': 'tee', 'トゥ': 'too', 'ディ': 'dee', 'ドゥ': 'doo', 'デュ': 'dyoo',
        'ツァ': 'tsah', 'ツィ': 'tsee', 'ツェ': 'tseh', 'ツォ': 'tsoh',
        'ウィ': 'wee', 'ウェ': 'weh', 'ウォ': 'woh',
        'ヴァ': 'vah', 'ヴィ': 'vee', 'ヴ': 'voo', 'ヴェ': 'veh', 'ヴォ': 'voh',
        'スィ': 'see', 'ズィ': 'zee',
        'クァ': 'kwah', 'クィ': 'kwee', 'クェ': 'kweh', 'クォ': 'kwoh',
        'グァ': 'gwah', 'グィ': 'gwee', 'グェ': 'gweh', 'グォ': 'gwoh',
        'イェ': 'yeh'
    }

    SMALL_KANA = set('ァィゥェォャュョヮぁぃゥぇぉゃゅょゎ')
    KANJI_NUMS = {'0': '〇', '1': '一', '2': '二', '3': '三', '4': '四', '5': '五', '6': '六', '7': '七', '8': '八', '9': '九'}
    DIGIT_TO_KANA = {'0': 'ゼロ', '1': 'イチ', '2': 'ニ', '3': 'サン', '4': 'ヨン', '5': 'ゴ', '6': 'ロク', '7': 'ナナ', '8': 'ハチ', '9': 'キュウ'}

    def __init__(self, custom_user_dict: Dict[str, str] = None):
        try:
            self.tagger = fugashi.Tagger()
        except Exception as e:
            print(f"[Engine Warning] Fugashi/Unidic initialization failed: {e}")
            self.tagger = None

        self.jmd = Jamdict() if JAMDICT_AVAILABLE else None
        self.pitch_dict = {}
        self._ensure_kanjium_database()

        # Massively expanded terms for light novels, fantasy, and internet slang
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

            # Internet slang / Gaming
            "www": "ワラ", "（笑）": "ワラ", "orz": "ガックリ", "草": "クサ", "神": "カミ",
            "推し": "オシ", "スパチャ": "スパチャ", "配信": "ハイシン", "枠": "ワク",
            "炎上": "エンジョウ", "バフ": "バフ", "デバフ": "デバフ", "エンカ": "エンカ",
            "ワンパン": "ワンパン", "カンスト": "カンスト", "ガチャ": "ガチャ", "爆死": "バクシ",
            "人権キャラ": "ジンケンキャラ", "草生える": "クサハエル", "鯖": "サバ",

            # Tech, Web, Gaming & Modern Media
            "Wi-Fi": "ワイファイ", "WiFi": "ワイファイ", "wifi": "ワイファイ",
            "Twitter": "ツイッター", "YouTube": "ユーチューブ", "Amazon": "アマゾン",
            "Google": "グーグル", "LINE": "ライン", "iPhone": "アイフォーン",
            "iPad": "アイパッド", "Web": "ウェブ", "web": "ウェブ",
            "TikTok": "ティックトック", "SNS": "エスエヌエス", "AI": "エーアイ",
            "VR": "ブイアール", "AR": "エーアール", "PC": "ピーシー",
            "CPU": "シーピーユー", "GPU": "ジーピーユー", "OS": "オーエス",
            "URL": "ユーアールエル", "ID": "アイディー", "OK": "オーケー",
            "NG": "エヌジー", "BGM": "ビージーエム", "SE": "エスイー",
            "RPG": "アールピージー", "FPS": "エフピーエス", "PV": "ピーブイ",
            "MV": "エムブイ", "CD": "シーディー", "DVD": "ディーブイディー",
            "BD": "ブルーレイ", "TV": "テレビ", "CM": "シーエム",
            "HP": "エイチピー", "MP": "エムピー", "EXP": "イーエックスピー",
            "Lv": "レベル", "LV": "レベル", "km": "キロメートル",
            "kg": "キログラム", "cm": "センチメートル", "mm": "ミリメートル",
            "mg": "ミリグラム", "ml": "ミリリットル", "kcal": "キロカロリー"
        }
        if custom_user_dict:
            self.custom_dict.update(custom_user_dict)

        self._load_external_txt_dictionary()

        self.symbol_to_kana = {
            '％': 'パーセント', '%': 'パーセント',
            '＆': 'アンド', '&': 'アンド',
            '＋': 'プラス', '+': 'プラス',
            '−': 'マイナス',
            '＝': 'イコール', '=': 'イコール',
            '×': 'カケル', '÷': 'ワル',
            '#': 'シャープ', '＃': 'シャープ',
            '@': 'アットマーク', '＠': 'アットマーク',
            '¥': 'エン', '￥': 'エン', '$': 'ドル',
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
        """Loads user-defined custom dictionary from a .txt file."""
        try:
            base_dir = Path(__file__).resolve().parent.parent
            dict_path = base_dir / "models" / "japanese_custom_dict.txt"

            if not dict_path.exists():
                dict_path.parent.mkdir(parents=True, exist_ok=True)
                with open(dict_path, 'w', encoding='utf-8') as f:
                    f.write("# User Custom Japanese Dictionary\n")
                    f.write("# Format: OriginalWord=Katakana\n")
                    f.write("# Example: 宇宙船=ウチュウセン\n")
                    f.write("# Add your custom words below this line:\n")
                return

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
        """Loads the compact Kanjium pitch database."""
        try:
            base_dir = Path(__file__).resolve().parent.parent
            json_path = base_dir / "models" / "Kanjium" / "kanjium_pitch.json"

            with open(json_path, "r", encoding="utf-8") as f:
                self.pitch_dict = json.load(f)
        except Exception as e:
            print(f"[Japanese G2P] Pitch database load failed: {e}")
            self.pitch_dict = {}

    def get_downstep(self, surface: str, reading_hira: str) -> Optional[int]:
        """Resolves downstep integer using surface and reading for exact homograph accuracy."""
        entry = self.pitch_dict.get(surface)
        if entry is None:
            entry = self.pitch_dict.get(reading_hira)
        if isinstance(entry, int):
            return entry
        if isinstance(entry, dict):
            if reading_hira in entry:
                return entry[reading_hira]
            return next(iter(entry.values()), 0)
        return None

    def _int_to_kanji(self, n: int) -> str:
        """Converts positive integer to Japanese Kanji representation for natural reading."""
        if n == 0:
            return '〇'
        units = ['', '十', '百', '千']
        big_units = ['', '万', '億', '兆']

        def chunk4(val):
            res = ''
            s = str(val).zfill(4)
            for i, d in enumerate(s):
                digit = int(d)
                if digit != 0:
                    pos = 3 - i
                    unit = units[pos]
                    if digit == 1 and pos > 0:
                        res += unit
                    else:
                        res += self.KANJI_NUMS[d] + unit
            return res

        res = ''
        big_idx = 0
        while n > 0:
            c = n % 10000
            if c > 0:
                res = chunk4(c) + big_units[big_idx] + res
            n //= 10000
            big_idx += 1
        return res

    def _normalize_numbers(self, text: str) -> str:
        """Converts Arabic numbers to Kanji so morphological analyzer produces Japanese readings."""
        # 1. Currency prefixes
        text = re.sub(r'[¥￥]\s*(\d+(?:,\d{3})*(?:\.\d+)?)', r'\1円', text)
        text = re.sub(r'\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)', r'\1ドル', text)

        # 2. Hyphenated numbers (phone numbers, postal codes: 03-1234-5678)
        def replace_phone(m):
            parts = m.group(0).split('-')
            kana_parts = ["".join(self.DIGIT_TO_KANA.get(d, d) for d in p) for p in parts]
            return "、".join(kana_parts)
        text = re.sub(r'(?<![a-zA-Z0-9])\d{2,4}(?:-\d{2,4}){1,2}(?![a-zA-Z0-9])', replace_phone, text)

        # 3. Decimals: 1.5 -> 一点五, 3.14 -> 三点一四, 0.5 -> 〇点五
        def replace_decimal(m):
            whole_str = m.group(1).replace(',', '')
            whole_val = int(whole_str) if whole_str else 0
            whole = self._int_to_kanji(whole_val)
            dec = "".join(self.KANJI_NUMS.get(d, d) for d in m.group(2))
            return f"{whole}点{dec}"
        text = re.sub(r'(?<![a-zA-Z0-9])(\d+(?:,\d{3})*)\.(\d+)(?![a-zA-Z0-9])', replace_decimal, text)

        # 4. Comma numbers and integers: 1,500 -> 千五百, 4 -> 四
        def replace_int(m):
            val_str = m.group(1).replace(',', '')
            try:
                val = int(val_str)
                if 0 <= val <= 999999999999:
                    return self._int_to_kanji(val)
                return m.group(0)
            except Exception:
                return m.group(0)
        text = re.sub(r'(?<![a-zA-Z0-9,\.])(\d{1,3}(?:,\d{3})+|\d+)(?![a-zA-Z0-9])', replace_int, text)

        return text

    def _normalize_and_cleanse(self, text: str) -> str:
        text = html.unescape(text)

        text = re.sub(r'[“”〝〟「」『』≪≫"\'`<>\(\)（）\[\]【】]', '', text)

        # Full-width digits and ASCII normalized to half-width
        text = jaconv.h2z(text, ascii=False, digit=True)
        text = jaconv.z2h(text, kana=False, digit=True, ascii=True)

        # Apply multi-character custom dictionary terms (length >= 2) before tokenization
        for kanji, katakana in sorted(self.custom_dict.items(), key=lambda x: len(x[0]), reverse=True):
            if len(kanji) >= 2 and kanji in text:
                text = text.replace(kanji, katakana)

        # Convert Arabic numbers, phone numbers, and decimals into Japanese kanji/kana
        text = self._normalize_numbers(text)

        for sym, kana in self.symbol_to_kana.items():
            text = text.replace(sym, kana)

        def replace_alpha(match):
            word = match.group(0).upper()
            return "".join(self.alphabet_to_kana.get(char, char) for char in word)
        text = re.sub(r'[A-Za-z]+', replace_alpha, text)

        for jp_punc, en_punc in self.punc_map.items():
            text = text.replace(jp_punc, en_punc)

        return text

    def _stitch_compounds(self, tokens: List[Dict]) -> List[Dict]:
        """Greedy lookahead stitcher to merge A-units into compound words using Kanjium."""
        result = []
        i = 0
        n = len(tokens)
        while i < n:
            matched = False
            for w in (3, 2):
                if i + w <= n:
                    combo_surf = ''.join(tokens[j]['surface'] for j in range(i, i + w))
                    if combo_surf in self.pitch_dict:
                        combo_pron = ''.join(tokens[j]['pron'] for j in range(i, i + w))
                        combo_pos = tokens[i]['pos1']
                        result.append({
                            'surface': combo_surf,
                            'pron': combo_pron,
                            'pos1': combo_pos,
                            'is_compound': True
                        })
                        i += w
                        matched = True
                        break
            if not matched:
                result.append(tokens[i])
                i += 1
        return result

    def _get_morae(self, kata: str) -> List[str]:
        """Segments Katakana reading into phonological morae."""
        morae = []
        i = 0
        n = len(kata)
        while i < n:
            c = kata[i]
            if i + 1 < n and kata[i + 1] in self.SMALL_KANA:
                morae.append(c + kata[i + 1])
                i += 2
            else:
                morae.append(c)
                i += 1
        return morae

    def _morae_to_phonetic(self, morae: List[str]) -> List[str]:
        """Converts a sequence of morae into English-acoustic syllables in a single pass."""
        out = []
        n = len(morae)
        for i, m in enumerate(morae):
            # 1. Chōonpu (ー): extends previous vowel without creating duplicate syllable
            if m == 'ー':
                continue

            # 2. Vowel extension 'ウ' after 'o' vowels (e.g. コウ -> koh, ノウ -> noh)
            if m == 'ウ' and out and out[-1].endswith(('oh', 'o')):
                continue

            # 3. Vowel extension 'イ' after 'e' vowels (e.g. セイ -> seh)
            if m == 'イ' and out and out[-1].endswith(('eh', 'e')):
                continue

            # 4. Sokuon (ッ / small tsu)
            if m == 'ッ':
                next_m = morae[i + 1] if i + 1 < n else ''
                next_rom = self.MORA_MAP.get(next_m, '')
                if next_rom.startswith('ch'):
                    out.append('t-')
                elif next_rom:
                    out.append(next_rom[0])
                else:
                    out.append('-')
                continue

            # 5. Hatsuon (ン / syllabic nasal)
            if m == 'ン':
                next_m = morae[i + 1] if i + 1 < n else ''
                next_rom = self.MORA_MAP.get(next_m, '')
                if next_rom.startswith(('b', 'm', 'p')):
                    out.append('m')
                elif next_m and next_m in 'アイウエオヤユヨ':
                    out.append("n'")
                else:
                    out.append('n')
                continue

            # 6. Standard mora lookup
            if m in self.MORA_MAP:
                out.append(self.MORA_MAP[m])
            else:
                rom = jaconv.kata2alphabet(m)
                rom = rom.replace('o-', 'oh').replace('u-', 'oo').replace('a-', 'ah')
                out.append(rom if rom else m)

        return out

    def _assemble_word(self, phonetic_morae: List[str], downstep: Optional[int]) -> str:
        """Assembles phonetic morae with pitch accent prosody."""
        if not phonetic_morae:
            return ""

        word = "".join(phonetic_morae)

        if downstep and downstep > 0 and len(phonetic_morae) >= 3:
            if 0 < downstep < len(phonetic_morae):
                part1 = "".join(phonetic_morae[:downstep])
                part2 = "".join(phonetic_morae[downstep:])
                # Never insert hyphen before geminates, moraic codas, or sokuon stops
                if (len(part1) >= 2 and len(part2) >= 2
                        and not part2.startswith(('t-', 'k-', 'n-', 'n', 'm', 'tt', 'kk', 'ss', 'pp'))
                        and not part1.endswith('-')):
                    word = f"{part1}-{part2}"

        return word

    def _resolve_unknown_kanji(self, surface: str) -> str:
        """Resolves unread kanji using the Kanjium database."""
        if surface in self.pitch_dict:
            entry = self.pitch_dict[surface]
            if isinstance(entry, dict):
                readings = list(entry.keys())
                if readings:
                    return jaconv.hira2kata(readings[0])

        if self.jmd and re.search(r'[\u4e00-\u9faf]', surface):
            try:
                result = self.jmd.lookup(surface, strict=True)
                if result.names:
                    return jaconv.hira2kata(result.names[0].kana[0].text)
            except Exception:
                pass

        return surface

    def convert(self, text: str) -> str:
        if not text.strip() or self.tagger is None:
            return ""

        text = self._normalize_and_cleanse(text)
        tokens = []

        # 1. Morphological analysis with Fugashi
        for node in self.tagger(text):
            surface = node.surface
            feature = node.feature
            pos1 = getattr(feature, 'pos1', '')

            if re.match(r'^[\s“”〝〟「」『』≪≫"\'`<>\(\)（）\[\]【】]+$', surface):
                continue

            if re.match(r'^[\.,!?;:\-]+$', surface):
                tokens.append({'surface': surface, 'pron': surface, 'pos1': '記号'})
                continue

            pron = getattr(feature, 'pron', None)
            if not pron or pron == '*':
                pron = getattr(feature, 'kana', surface)
                if not pron or pron == '*':
                    pron = self._resolve_unknown_kanji(surface)

            # Single-token custom dictionary override
            if surface in self.custom_dict:
                pron = self.custom_dict[surface]

            # Homograph resolution: 一日
            if surface == '一日':
                is_month_date = False
                if tokens and tokens[-1]['surface'].endswith(('月', '朔')):
                    is_month_date = True
                pron = 'ツイタチ' if is_month_date else 'イチニチ'

            tokens.append({
                'surface': surface,
                'pron': jaconv.hira2kata(pron),
                'pos1': pos1
            })

        # 2. Multi-token compound stitching
        tokens = self._stitch_compounds(tokens)

        # 3. Phonetic conversion with pitch accent and bunsetsu binding
        bunsetsu_blocks = []
        i = 0
        n = len(tokens)

        particle_map = {
            'は': 'wah', 'が': 'gah', 'の': 'noh', 'を': 'oh',
            'に': 'nee', 'で': 'deh', 'へ': 'eh', 'と': 'toh',
            'も': 'moh', 'や': 'yah', 'ね': 'neh', 'よ': 'yoh',
            'か': 'kah', 'から': 'kahrah', 'まで': 'mahdeh', 'より': 'yohree'
        }

        while i < n:
            tok = tokens[i]
            surface = tok['surface']
            pron = tok['pron']
            pos1 = tok['pos1']

            if re.match(r'^[\.,!?;:\-]+$', surface):
                if bunsetsu_blocks:
                    bunsetsu_blocks[-1] += surface
                else:
                    bunsetsu_blocks.append(surface)
                i += 1
                continue

            # Auxiliary verb devoicing & merging
            if surface in ('です', 'デス'):
                bunsetsu_blocks.append('dess')
                i += 1
                continue
            if surface in ('でした', 'デシタ'):
                bunsetsu_blocks.append('dehshtah')
                i += 1
                continue
            if surface in ('ます', 'マス'):
                if bunsetsu_blocks and not re.search(r'[\.,!?;:\-]$', bunsetsu_blocks[-1].strip()):
                    bunsetsu_blocks[-1] = bunsetsu_blocks[-1].strip() + "-mahs"
                else:
                    bunsetsu_blocks.append('mahs')
                i += 1
                continue
            if surface in ('ました', 'マシタ'):
                if bunsetsu_blocks and not re.search(r'[\.,!?;:\-]$', bunsetsu_blocks[-1].strip()):
                    bunsetsu_blocks[-1] = bunsetsu_blocks[-1].strip() + "-mahshtah"
                else:
                    bunsetsu_blocks.append('mahshtah')
                i += 1
                continue

            # Colloquial negative ending ん (e.g. 買えません -> kahehmahsehn)
            if surface == 'ん' and bunsetsu_blocks:
                bunsetsu_blocks[-1] = bunsetsu_blocks[-1].rstrip('-') + "n"
                i += 1
                continue

            # Colloquial particle って
            if surface == 'って' and bunsetsu_blocks:
                bunsetsu_blocks[-1] = bunsetsu_blocks[-1].rstrip('-') + "tteh"
                i += 1
                continue

            # Particles
            if pos1 == '助詞' and surface in particle_map:
                p_rom = particle_map[surface]
                if bunsetsu_blocks and not re.search(r'[\.,!?;:\-]$', bunsetsu_blocks[-1].strip()):
                    bunsetsu_blocks[-1] = bunsetsu_blocks[-1].strip() + f"-{p_rom}"
                else:
                    bunsetsu_blocks.append(p_rom)
                i += 1
                continue

            reading_hira = jaconv.kata2hira(pron)
            downstep = self.get_downstep(surface, reading_hira)

            morae = self._get_morae(pron)
            phonetic_morae = self._morae_to_phonetic(morae)
            formatted_word = self._assemble_word(phonetic_morae, downstep)

            if pos1 in ('接続詞', '感動詞'):
                formatted_word += ","

            # Prefixes (接頭辞 like お, ご) bind to following word with hyphen
            if pos1 == '接頭辞':
                formatted_word += "-"
                bunsetsu_blocks.append(formatted_word)
                i += 1
                continue

            if bunsetsu_blocks and bunsetsu_blocks[-1].endswith("-"):
                bunsetsu_blocks[-1] += formatted_word
            elif pos1 in ('助詞', '助動詞', '接尾辞'):
                if bunsetsu_blocks and not re.search(r'[\.,!?;:\-]$', bunsetsu_blocks[-1].strip()):
                    bunsetsu_blocks[-1] = bunsetsu_blocks[-1].strip() + f"-{formatted_word}"
                else:
                    bunsetsu_blocks.append(formatted_word)
            else:
                bunsetsu_blocks.append(formatted_word)

            i += 1

        raw_sentence = " ".join(bunsetsu_blocks)

        # Clean ASCII representation
        raw_sentence = unicodedata.normalize('NFKD', raw_sentence)
        raw_sentence = raw_sentence.encode('ascii', 'ignore').decode('utf-8')
        raw_sentence = re.sub(r'[^a-zA-Z0-9\s.,!?\'\-]', ' ', raw_sentence)
        raw_sentence = re.sub(r'([.,!?])\1+', r'\1', raw_sentence)
        raw_sentence = re.sub(r'\s*-\s*', '-', raw_sentence)
        raw_sentence = re.sub(r'-+', '-', raw_sentence)
        raw_sentence = re.sub(r'\s+', ' ', raw_sentence).strip()
        return raw_sentence


# Singleton Instance
japanese_processor = UltimateJapaneseG2P()

def pure_japanese_to_romaji(text: str) -> str:
    """Public wrapper to maintain backward compatibility."""
    return japanese_processor.convert(text)