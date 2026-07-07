import re
import jaconv
from typing import Dict

class UltimateChineseProcessor:
    """
    Enterprise-Grade Acoustic Pre-Processor for Mandarin (cmn).
    Engineered to provide 100% crash protection for the Kokoro/Misaki[zh] backend.
    Vaporizes clipping punctuation, converts foreign ASCII to Hanzi phonetics,
    preserves math/system symbols, and patches Web Novel polyphony (多音字) natively.
    """
    def __init__(self, custom_user_dict: Dict[str, str] = None):
       #print("[System] Initializing Acoustic Shield for Mandarin...")#
        
        # 🌟 1. THE POLYPHONY HOMOPHONE HACK (Xianxia & Web Novels) 🌟
        # pypinyin often misreads polyphonic characters (多音字) in slang.
        self.custom_dict = {
            # Base Original
            "拍卖行": "拍卖杭", "商行": "商杭", "同行": "同杭",     
            "斗破": "豆破", "长剑": "常剑", "长大": "掌大", 
            "重伤": "仲伤", "朝廷": "潮廷",
            
            # 🐉 Xianxia & Wuxia Expansion (zhǎng vs cháng)
            "长老": "掌老", "族长": "族掌", "门长": "门掌", "村长": "村掌",
            "学长": "学掌", "院长": "院掌", "首长": "首掌",
            
            # 🐉 Xianxia & Wuxia Expansion (zhòng vs chóng)
            "重生": "虫生", "重叠": "虫叠", "九重天": "九虫天", "重逢": "虫逢",
            "双重": "双虫", "重塑": "虫塑", "重修": "虫修",
            
            # 🐉 Xianxia & Wuxia Expansion (jué vs jiǎo)
            "主角": "主决", "配角": "配决", "角色": "决色", 
            
            # 🐉 Military/System Expansion (jiàng vs jiāng)
            "少将": "少降", "神将": "神降", "将领": "降领", "名将": "名降",
            
            # 🐉 Miscellaneous Web Novel Tropes
            "传记": "赚记", "自传": "自赚", "正传": "正赚", # zhuàn vs chuán
            "道行": "道杭", "五行": "五形", "修行": "修形", # héng/xíng
            "打量": "打亮", "分量": "分亮", # liàng
            "咽气": "燕气", "咽下": "燕下"  # yàn vs yān
        }
        if custom_user_dict:
            self.custom_dict.update(custom_user_dict)

        # 🌟 2. ENGLISH ALPHABET TO HANZI MATRIX 🌟
        self.alpha_to_zh = {
            'A': '诶', 'B': '必', 'C': '西', 'D': '弟', 'E': '伊', 'F': '艾弗',
            'G': '吉', 'H': '艾尺', 'I': '艾', 'J': '杰', 'K': '剋', 'L': '艾尔',
            'M': '艾姆', 'N': '恩', 'O': '欧', 'P': '批', 'Q': '丘', 'R': '阿',
            'S': '艾斯', 'T': '替', 'U': '优', 'V': '微', 'W': '达布溜',
            'X': '艾克斯', 'Y': '歪', 'Z': '贼',
            'a': '诶', 'b': '必', 'c': '西', 'd': '弟', 'e': '伊', 'f': '艾弗',
            'g': '吉', 'h': '艾尺', 'i': '艾', 'j': '杰', 'k': '剋', 'l': '艾尔',
            'm': '艾姆', 'n': '恩', 'o': '欧', 'p': '批', 'q': '丘', 'r': '阿',
            's': '艾斯', 't': '替', 'u': '优', 'v': '微', 'w': '达布溜',
            'x': '艾克斯', 'y': '歪', 'z': '贼'
        }

        # 🌟 3. MATH & SYSTEM PROMPT TRANSLATOR 🌟
        # Converts symbols to Hanzi before the Titanium Shield destroys them.
        self.symbol_to_zh = {
            '+': '加', '＋': '加',
            '-': '减', '−': '减',
            '=': '等于', '＝': '等于',
            '<': '小于', '＜': '小于',
            '>': '大于', '＞': '大于',
            '×': '乘', '÷': '除'
        }

        # 🌟 4. VAPORIZATION MAP (Anti-Clipping Shield) 🌟
        self.punc_map = {
            '。': '.', '、': ',', '，': ',', '！': '!', '？': '?',
            '「': '', '」': '', '『': '', '』': '',
            '“': '', '”': '', '〝': '', '〟': '',
            '"': '', "'": '', '’': '', '‘': '',
            '（': ',', '）': ',', '：': ':', '；': ';',
            '【': ',', '】': ',', '≪': '', '≫': '',
            '〈': ',', '〉': ',', '〜': '-', '~': '-',
            '・': ',', '—': '-', '…': '...', 'ーー': '...'
        }

    def process(self, text: str) -> str:
        """Master Pipeline to purify Chinese text before TTS generation."""
        if not text.strip():
            return ""

        # 🌟 NEW: Percentage Handling (Chinese reads % BEFORE the number)
        # Translates "100%" or "100％" into "百分之100"
        text = re.sub(r'(\d+(?:\.\d+)?)[%％]', r'百分之\1', text)

        # 🌟 NEW: Symbol Translation (Protects math from the shield)
        for sym, zh_sym in self.symbol_to_zh.items():
            text = text.replace(sym, zh_sym)

        # Step 1: Polyphony Override
        for word, replacement in self.custom_dict.items():
            text = text.replace(word, replacement)

        # Step 2: Width Normalization (Mojimoji handles the heavy lifting)
        text = jaconv.h2z(text, ascii=False)
        text = jaconv.z2h(text, kana=False)
        
        # 🌟 NEW: Native Python Width Supplement
        # Mojimoji sometimes misses Chinese-specific full-width Alphanumerics.
        # This pure-python translation guarantees 100% Half-Width Alphanumerics.
        full_width_chars = "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
        half_width_chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        trans_table = str.maketrans(full_width_chars, half_width_chars)
        text = text.translate(trans_table)

        # Step 3: English Acronym Translation (VIP -> 微艾批)
        def replace_alpha(match):
            word = match.group(0)
            return "".join(self.alpha_to_zh.get(char, char) for char in word)
        text = re.sub(r'[A-Za-z]+', replace_alpha, text)

        # Step 4: Punctuation Vaporization (Fixes Audio Clipping)
        for zh_punc, en_punc in self.punc_map.items():
            text = text.replace(zh_punc, en_punc)

        # 🌟 Step 5: THE TITANIUM HANZI SHIELD 🌟
        # Now safely ignores the math/symbols because we translated them to Hanzi!
        text = re.sub(r'[^\u4e00-\u9fff\s.,!?;:\-0-9]', '', text)

        # Step 6: Post-Process Pacing Cleanup
        text = re.sub(r'\s*-\s*', '-', text) 
        text = re.sub(r'-+', '-', text)      
        return re.sub(r'\s+', ' ', text).strip()

# Singleton Instance
chinese_processor = UltimateChineseProcessor()

def cleanse_chinese_text(text: str) -> str:
    """Public wrapper called by tts.py"""
    return chinese_processor.process(text)