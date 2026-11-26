"""Built-in style note for historical Chinese novels: telling a historical title from a modern one, and the note a run gets when nobody chose one."""
import db

# From the 2026-11-26 genre style tests (owner's blind picks plus chrF against human
# translations): this note brought every engine closer to the preferred human style on
# historical novels, and scored no better than the default prompt on modern ones, so
# modern novels keep the default prompt. Spelling is left to the title's English variant.
HISTORICAL_STYLE_NOTE = (
    "Translate in the voice of a polished literary translation:\n"
    "- Single quotation marks for dialogue ('like this'), double only inside them; "
    "spaced em dashes ( — ) for asides and interruptions.\n"
    "- Keep the source's point of view and tense exactly: first person stays first person, "
    "third person stays third person. Never change who is narrating.\n"
    "- Match the source's tone. Where it is comic, keep the wry, self-mocking voice and let "
    "the humour land in English: deadpan asides, mock-formal phrasing, playful exaggeration. "
    "Never flatten a joke into plain description.\n"
    "- Render idioms and set phrases as natural English with the same effect; keep a vivid "
    "Chinese image literally only when it is striking and still clear in context.\n"
    "- Varied sentence length: long, rolling narrative sentences broken up by short, punchy "
    "lines and one-word reactions.\n"
    "- Narration is fairly formal; use contractions mainly in dialogue and casual asides.\n"
    "- Names in pinyin with no tones. Keep untranslatable kinship and address terms in pinyin "
    "(Dage, Jiejie); translate titles (Your Highness, Prince Consort, Princess). Use English "
    "equivalents for martial-arts terms (acupoints, qinggong as \"lightness skill\").\n"
    "- Faithful to meaning and every beat of the scene; add nothing, drop nothing, no added "
    "explanations in the text."
)

# Words that are common in one setting and rare in the other, in simplified and
# traditional script. A title is called historical only on a clear majority, so a
# mixed or unclear text (a transmigration story, a short sample) keeps the default.
_HISTORICAL = (
    "公主 殿下 陛下 皇上 皇帝 朕 本宫 本宮 王爷 王爺 侯爷 侯爺 将军 將軍 师姐 師姐 师兄 師兄 "
    "师尊 師尊 师父 師父 宗门 宗門 江湖 丫鬟 奴婢 驸马 駙馬 寨主 京城 京师 京師 娘娘 太子 微臣 "
    "姑娘 公子 府中 宫中 宮中").split()
_MODERN = (
    "手机 手機 电话 電話 公司 学校 學校 大学 大學 老师 老師 同学 同學 电脑 電腦 微信 汽车 汽車 "
    "地铁 地鐵 医院 醫院 总裁 總裁 经理 經理 酒店 咖啡 网上 網上 视频 視頻 机场 機場 短信 "
    "出租车 出租車 高中 外卖 外賣 学长 學長 学姐 學姐 宿舍").split()
_SAMPLE_LINES = 400
_MIN_HITS = 3
_MAJORITY = 3


def detect_setting(text: str) -> str | None:
    """'historical', 'modern', or None when the text doesn't clearly say."""
    historical = sum(text.count(w) for w in _HISTORICAL)
    modern = sum(text.count(w) for w in _MODERN)
    if historical >= _MIN_HITS and historical >= _MAJORITY * modern:
        return "historical"
    if modern >= _MIN_HITS and modern >= _MAJORITY * historical:
        return "modern"
    return None


def title_setting(drama: dict) -> str | None:
    """detect_setting over the start of a Chinese novel title's source text; None for
    any other title."""
    if drama.get("content_mode") != "novel_narration" or (drama.get("source_language") or "zh") != "zh":
        return None
    _, rows = db.load_line_page(drama["id"], 0, _SAMPLE_LINES)
    return detect_setting("".join(r.get("zh") or "" for r in rows))


def default_style_note(drama: dict, settings_note: str) -> str:
    """The style note for a run nobody wrote one for: the Settings default when there is
    one, else the historical note for a historical novel, else none."""
    if settings_note:
        return settings_note
    return HISTORICAL_STYLE_NOTE if title_setting(drama) == "historical" else ""
