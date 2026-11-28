"""Builds the VTuber name entries of the built-in language packs from Hololist profiles,
the hololive and NIJISANJI talent lists, and Wikidata.

    python tools/vtuber_name_packs.py fetch --cache DIR [--refresh-max N]
    python tools/vtuber_name_packs.py reference --cache DIR
    python tools/vtuber_name_packs.py build --cache DIR [--review FILE] [--exclude FILE]

`fetch` keeps a resumable cache of the public profile fields a names glossary needs
(DIR/profiles.jsonl); birthdays, heights, likes and the site's own prose are not kept.
A run fetches every profile it has not seen, then re-checks up to N of the oldest ones,
so a weekly run picks up new debuts and renames without re-reading the whole site.
Hololist's robots.txt disallows URLs with a query string, so only plain paths are read,
one request every few seconds.

`reference` saves the agencies' own spellings (DIR/official.json), which win over Hololist's,
and every VTuber on Wikidata with its Japanese and Chinese names (DIR/wikidata.json, CC0).

`build` turns the cache into entries of language_pack_data/{ja,zh}-vtuber-names.json. Entries
without a `link` are hand-written and kept as they are; the generated ones are replaced.
A name or nickname that is also an ordinary word, a single character, or used by two
VTubers is held back and listed in the review file instead, because packs match by plain
substring. tools/vtuber_name_overrides.json is applied last, so a hand fix survives every
rebuild. `--exclude` names a file kept outside the repository, one term per line: an entry
whose text holds one as a whole word (any case) is left out, for names that must not be
published.

Needs `requests`, `opencc-python-reimplemented` and `wordfreq` (build only).
"""
import argparse
import html
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACK_DIR = REPO_ROOT / "language_pack_data"
OVERRIDES = Path(__file__).resolve().parent / "vtuber_name_overrides.json"
# Run as `python tools/vtuber_name_packs.py` sys.path[0] is tools/, not the repo.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
import language_packs  # noqa: E402
SITE = "https://hololist.net"
# Hololist country categories and the pack language whose streams name them.
CATEGORIES = {"jp": "ja", "tw": "zh", "cn": "zh", "hk": "zh"}
PLATFORMS = ("youtube.com", "youtu.be", "twitch.tv", "bilibili.com", "b23.tv")
DELAY_SECONDS = 2.5
REFRESH_AFTER_DAYS = 28
# A Zipf frequency (log10 per billion words) at or above this marks an ordinary word.
COMMON_WORD_ZIPF = 2.5
# Both halves of a Chinese fan name at least this common mark it as ordinary words put together;
# a single character must be much more common than a word to count.
COMPOUND_WORD_ZIPF = 3.0
COMPOUND_CHAR_ZIPF = 4.0
# A name this short is checked against the inside of common words too.
MAX_PIECE_LEN = 3
# A VTuber's own name longer than this is in the word lists because the VTuber is famous
# (キズナアイ), not because it is an ordinary word; nicknames and fan names are checked at any length.
MAX_CHECKED_LEN = 4
# A generated build keeping fewer than this share of the last build's entries (once it had a few
# hundred) is refused without --force: an empty or wrong cache would otherwise wipe the pack.
MIN_KEPT_SHARE = 0.5
GUARDED_FROM = 200
# Common Chinese surnames: a three-character nickname starting with one may be the legal name of
# the person behind the avatar, which the public packs must not carry.
SURNAMES = set("王李张張刘劉陈陳杨楊黄黃赵趙吴吳周徐孙孫马馬朱胡郭何高林罗羅郑鄭梁谢謝宋唐许許韩韓冯馮邓鄧曹彭"
               "曾肖萧蕭田董袁潘于蒋蔣蔡余杜叶葉程苏蘇魏吕呂丁任沈姚卢盧姜崔钟鐘谭譚陆陸汪范金石廖贾賈夏韦韋"
               "付方白邹鄒孟熊秦邱江尹薛闫閻段雷侯龙龍史陶黎贺賀顾顧毛郝龚龔邵万萬钱錢严嚴覃武戴莫孔向汤湯"
               "温溫康施文牛樊葛邢安齐齊易乔喬伍庞龐颜顏倪庄莊聂聶章鲁魯岳翟殷詹申欧歐耿关關兰蘭焦俞左柳甘"
               "祝包宁寧尚符舒阮柯纪紀梅童凌毕畢单單季裴霍涂成苗谷盛曲翁冉骆駱蓝藍路游辛靳管柴蒙鲍鮑华華喻"
               "祁蒲房滕屈饶饒解牟艾尤阳陽时時穆农農司卓古吉缪繆简簡车車项項连連芦蘆麦麥褚娄婁窦竇戚岑景党"
               "宫宮费費卜冷晏席卫衛米柏宗瞿桂全佟应應臧闵閔苟邬鄔边邊卞姬师師和仇栾欒隋商刁沙荣榮巫寇桑郎"
               "甄丛叢仲虞敖巩鞏明佘池查麻苑迟遲邝鄺")
# A hiragana-only name this short (はお, ひの) turns up inside everyday sentences, since packs match
# plain substrings; only an agency or Wikidata listing it vouches for it.
HIRAGANA = re.compile(r"[ぁ-ゖー]+")
KANA = re.compile(r"[ぁ-ゖァ-ヺー]+")
MAX_UNCONFIRMED_KANA = 3
CONFIRMING = ("https://www.wikidata.org/", "https://hololive.hololivepro.com/", "https://www.nijisanji.jp/")
HANZI3 = re.compile(r"[㐀-鿿豈-﫿]{3}")
BRACKETS = re.compile(r"[()（）「」【】\[\]]")
INVISIBLE = ("Cc", "Cf")
# Two-character diminutives anyone can be called (小梦, 阿水, 老王, 曉曉, 珞哥): they name no one in particular.
GENERIC_NICKNAME = re.compile(r"[小阿老].|.[哥姐妹仔醬酱兒儿]|(.)\1")
NAV_SLUGS = {"about", "affiliations", "anniversary", "announcement", "birthday", "category", "contact",
             "content", "cookie-policy", "debut", "feeds", "gender", "language", "latest", "live", "mbti",
             "membership", "model", "newest", "news", "privacy-policy", "random", "retirement", "tags",
             "terms-of-service", "top", "type", "upcoming", "watch", "affiliation", "tag", "zodiac"}
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯豈-﫿]")


# --- fetch -----------------------------------------------------------------

def _get(session, url):
    for attempt in range(4):
        try:
            resp = session.get(url, timeout=30)
            time.sleep(DELAY_SECONDS)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.text
        except Exception as exc:  # noqa: BLE001 -- a flaky page is retried, then skipped
            print(f"retry {url}: {type(exc).__name__}", file=sys.stderr, flush=True)
            time.sleep(DELAY_SECONDS * 4 * (attempt + 1))
    return None


def _section(body, sid):
    m = re.search(rf'<section id="{sid}"[^>]*>(.*?)</section>', body, re.S)
    return m.group(1).split("</h2>", 1)[-1] if m else ""


def _visible(text):
    # Zero-width spaces copied from the site would make a name never match a transcript.
    return "".join(ch for ch in text if unicodedata.category(ch) not in INVISIBLE).strip()


def _text(fragment):
    return _visible(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))))


def _links(fragment):
    return [_text(a) for a in re.findall(r"<a [^>]*>(.*?)</a>", fragment, re.S)]


def parse_profile(slug, category, body) -> dict:
    """The fields a names glossary uses, from one profile page."""
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    links = _section(body, "links")
    channels = links.split("Socials")[0]
    description = _text(_section(body, "description"))
    fan_native = re.search(r'fanbase is called "[^"(]*?\(([^)]+)\)"', description)
    return {
        "slug": slug, "category": category,
        "name": _text(h1.group(1)) if h1 else slug,
        "original": _text(_section(body, "original-name")),
        "nicknames": [_text(li) for li in re.findall(r"<li>(.*?)</li>", _section(body, "nickname"), re.S)],
        "fanbase": _text(_section(body, "fanbase")),
        "fanbase_native": fan_native.group(1).strip() if fan_native else "",
        "affiliation": _links(_section(body, "affiliation")),
        "gender": _text(_section(body, "gender")),
        "status": _text(_section(body, "status")),
        "platforms": sorted({h for h in re.findall(r'href="https?://(?:www\.|m\.|live\.|space\.)?([^/"]+)', channels)
                             if h.endswith(PLATFORMS)}),
        "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def _category_slugs(session, category):
    """(slugs, complete): complete is False when any list page failed to load."""
    first = _get(session, f"{SITE}/category/{category}/")
    if not first:
        return [], False
    last = max([int(n) for n in re.findall(rf"/category/{category}/page/(\d+)/", first)] or [1])
    slugs, complete = [], True
    for page in range(1, last + 1):
        body = first if page == 1 else _get(session, f"{SITE}/category/{category}/page/{page}/")
        if not body:
            complete = False
            continue
        grid = body[body.find('<div class="row g-2">'):].split("Showing ")[0]
        for slug in re.findall(r'<a href="https://hololist\.net/([^"/?]+)/">\s*<img', grid):
            if slug not in NAV_SLUGS and slug not in slugs:
                slugs.append(slug)
    return slugs, complete


def load_cache(cache_dir: Path) -> dict:
    path = cache_dir / "profiles.jsonl"
    rows = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row["slug"]] = row
    return rows


def _save_cache(cache_dir: Path, rows: dict):
    tmp = cache_dir / "profiles.jsonl.tmp"
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()), encoding="utf-8")
    tmp.replace(cache_dir / "profiles.jsonl")


def fetch(cache_dir: Path, refresh_max: int):
    import requests
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = load_cache(cache_dir)
    session = requests.Session()
    session.headers["User-Agent"] = "glossary-builder (low-rate; respects robots.txt)"
    today = datetime.now(timezone.utc)
    listing = cache_dir / "listed.json"
    saved = json.loads(listing.read_text(encoding="utf-8")) if listing.exists() else {}
    if saved.get("date") == today.strftime("%Y-%m-%d") and saved.get("complete"):
        # A run that was stopped part-way resumes without walking every list page again.
        listed, complete = saved["listed"], saved["complete"]
    else:
        listed, complete = {}, True
        for category in CATEGORIES:
            slugs, whole = _category_slugs(session, category)
            complete = complete and whole
            for slug in slugs:
                listed.setdefault(slug, category)
            print(f"{category}: {sum(1 for c in listed.values() if c == category)} listed", flush=True)
        listing.write_text(json.dumps({"date": today.strftime("%Y-%m-%d"), "complete": complete,
                                       "listed": listed}), encoding="utf-8")
    stale = sorted((r["fetched"], s) for s, r in rows.items() if s in listed
                   and (today - datetime.strptime(r["fetched"], "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
                   >= REFRESH_AFTER_DAYS)
    todo = [s for s in listed if s not in rows] + [s for _, s in stale[:refresh_max]]
    for n, slug in enumerate(todo, 1):
        body = _get(session, f"{SITE}/{slug}/")
        if body:
            rows[slug] = parse_profile(slug, listed[slug], body)
        if n % 10 == 0 or n == len(todo):
            _save_cache(cache_dir, rows)
            print(f"fetched {n}/{len(todo)}", flush=True)
    # A profile no longer listed in any category was removed or moved off these countries. Only a
    # complete listing can show that: a failed page would otherwise wipe its profiles from the cache.
    if complete:
        for slug in [s for s in rows if s not in listed]:
            del rows[slug]
    _save_cache(cache_dir, rows)


WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_ITEM = "https://www.wikidata.org/wiki/"
WIKIDATA_VTUBER = "Q55155641"
WIKIDATA_LANGS = ("en", "ja", "zh", "zh-hans", "zh-hant", "zh-tw", "zh-cn", "zh-hk")
WIKIDATA_GENDER = {"Q6581072": "Female", "Q6581097": "Male"}
HOLOLIVE_TALENTS = "https://hololive.hololivepro.com/en/talents/"
NIJISANJI_TALENTS = "https://www.nijisanji.jp/talents"


def _official(session) -> list:
    """[{native, en, link, agency}] from the agencies' own talent lists: the spelling of record."""
    out = []
    body = _get(session, HOLOLIVE_TALENTS) or ""
    for link, en, native in re.findall(
            r'<a href="(https://hololive\.hololivepro\.com/en/talents/[^"/]+/)">.*?<h3>\s*(.*?)<span>(.*?)</span>',
            body, re.S):
        out.append({"native": _text(native), "en": _text(en), "link": link, "agency": "hololive production"})
    body = _get(session, NIJISANJI_TALENTS) or ""
    m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
    try:
        livers = json.loads(m.group(1))["props"]["pageProps"]["allLivers"] if m else []
    except (ValueError, KeyError, TypeError):
        livers = []
    for liver in livers:
        if liver.get("name") and liver.get("enName") and liver.get("slug"):
            out.append({"native": liver["name"].strip(), "en": liver["enName"].strip(),
                        "link": f"https://www.nijisanji.jp/talents/l/{liver['slug']}", "agency": "NIJISANJI"})
    return out


def _wikidata(session) -> list:
    """[{qid, gender, labels: {language: [label]}}] for every VTuber character on Wikidata (CC0).
    Labels only, and no item that is a human (Q5): those labels and any item's aliases can hold
    the real name of the person behind the avatar."""
    langs = ", ".join(f'"{lang}"' for lang in WIKIDATA_LANGS)
    query = f"""SELECT ?i ?text ?g WHERE {{
      {{ ?i wdt:P106 wd:{WIKIDATA_VTUBER} }} UNION {{ ?i wdt:P31 wd:{WIKIDATA_VTUBER} }}
      FILTER NOT EXISTS {{ ?i wdt:P31 wd:Q5 }}
      ?i rdfs:label ?text FILTER(LANG(?text) IN ({langs}))
      OPTIONAL {{ ?i wdt:P21 ?g }} }}"""
    resp = session.get(WIKIDATA_SPARQL, params={"query": query, "format": "json"}, timeout=120)
    time.sleep(DELAY_SECONDS)
    resp.raise_for_status()
    items = {}
    for b in resp.json()["results"]["bindings"]:
        qid = b["i"]["value"].rsplit("/", 1)[-1]
        item = items.setdefault(qid, {"qid": qid, "gender": "", "labels": {}})
        item["gender"] = item["gender"] or WIKIDATA_GENDER.get(b.get("g", {}).get("value", "").rsplit("/", 1)[-1], "")
        lang, text = b["text"]["xml:lang"], _visible(b["text"]["value"])
        item["labels"][lang] = [text]
    return sorted(items.values(), key=lambda i: int(i["qid"][1:]))


def fetch_reference(cache_dir: Path):
    """The agencies' talent lists and the Wikidata VTubers, refreshed whole each run (three requests)."""
    import requests
    cache_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    # Wikidata asks for a User-Agent with contact details; give yours at run time, never in the repo.
    session.headers["User-Agent"] = os.environ.get("GLOSSARY_BUILDER_UA",
                                                   "glossary-builder (low-rate; respects robots.txt)")
    official = _official(session)
    previous = cache_dir / "official.json"
    if previous.exists():
        # One agency's page failing must not drop that agency's spellings while the other loads.
        read = {o["agency"] for o in official}
        official += [o for o in json.loads(previous.read_text(encoding="utf-8")) if o["agency"] not in read]
    for name, rows in (("official.json", official), ("wikidata.json", _wikidata(session))):
        if not rows:
            # A failed or changed page keeps the last good copy instead of emptying it.
            print(f"{name}: nothing read, kept the previous file", flush=True)
            continue
        (cache_dir / name).write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"{name}: {len(rows)}", flush=True)


# --- build -----------------------------------------------------------------

def _split_nickname(nickname):
    """("Rin Rin", "鈴鈴") from "Rin Rin (鈴鈴)"; None when there is no native-script form."""
    m = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", nickname)
    if not m:
        return None
    reading, native = m.group(1).strip(), m.group(2).strip()
    return (reading, native) if CJK.search(native) and not CJK.search(reading) else None


def _pronoun(gender):
    return {"Female": "she/her", "Male": "he/him"}.get(gender, "")


class _Words:
    """Ordinary-word check: a name that is also a common word would fire in normal speech."""

    def __init__(self):
        from wordfreq import get_frequency_dict
        import opencc
        self.freq = {"zh": get_frequency_dict("zh"), "ja": get_frequency_dict("ja")}
        self.t2s = opencc.OpenCC("t2s")
        floor = 10 ** (COMPOUND_WORD_ZIPF - 9)
        self.pieces = {lang: {w[i:j] for w, f in freq.items() if f >= floor
                              for i in range(len(w)) for j in range(i + 1, min(i + MAX_PIECE_LEN, len(w)) + 1)
                              if j - i < len(w)}
                       for lang, freq in self.freq.items()}

    def zipf(self, word, language):
        if language == "zh":
            word = self.t2s.convert(word)
        f = self.freq[language].get(word)
        return math.log10(f) + 9 if f else 0.0

    def is_common(self, word, language):
        return self.zipf(word, language) >= COMMON_WORD_ZIPF

    def inside_common_word(self, word, language):
        """True when a short word is part of a longer common one (メダ in メダル, 猫头 in 猫头鹰): packs
        match plain substrings, so it would fire inside that word."""
        if len(word) > MAX_PIECE_LEN:
            return False
        return (self.t2s.convert(word) if language == "zh" else word) in self.pieces[language]

    def is_compound(self, word, language):
        """True when the word splits into two ordinary words (小+松鼠, 五分+熟). Chinese only:
        the word lists hold few three-character compounds, so 小松鼠 itself reads as rare."""
        if language != "zh" or len(word) > 4:
            return False
        return any(all(self.zipf(part, language) >= (COMPOUND_CHAR_ZIPF if len(part) == 1 else COMPOUND_WORD_ZIPF)
                       for part in (word[:i], word[i:])) for i in range(1, len(word)))


def _variants(source, language, converters):
    """The source plus its other Chinese script, since packs match the exact characters."""
    if language != "zh":
        return [source]
    out = [source]
    for conv in converters:
        other = conv.convert(source)
        if other not in out:
            out.append(other)
    return out


COUNTRY = {"jp": "Japanese", "tw": "Taiwanese", "cn": "Chinese", "hk": "Hong Kong"}


def _note(row, nickname: bool) -> str:
    """Short facts sent with the entry, so the model knows who the name is and which pronoun fits."""
    who = f"{COUNTRY[row['category']]} VTuber"
    facts = [f"Nickname of {row['name']}, {who}" if nickname else who]
    if _pronoun(row["gender"]):
        facts.append(_pronoun(row["gender"]))
    agency = [a for a in row["affiliation"] if a and a != "Independent"]
    facts.append(", ".join(agency) if agency else "independent")
    if row["fanbase"]:
        facts.append(f"fans: {row['fanbase']}")
    if row["status"] and row["status"] != "Active":
        facts.append(row["status"].lower())
    return ("; ".join(facts) + ". Keep the name as written; never turn it into he/she.")[:300]


def _original_names(row, converters) -> set:
    """The VTuber's own name as written, joined, and in the other Chinese script: what another
    source's label must equal to be the same VTuber (a nickname could belong to someone else)."""
    names = {row["original"], _joined(row["original"])}
    names.update(conv.convert(n) for n in list(names) for conv in converters)
    return {n for n in names if n and CJK.search(n)}


def _official_en(o):
    # The talent list prefixes a graduated or affiliate talent's name with a badge ("[Alum] ").
    return re.sub(r"^\[[^\]]*\]\s*", "", o["en"]).strip()


def _joined(name):
    # Speech never carries the separator, so the joined form is what a transcript holds.
    return re.sub(r"[\s·・]", "", name)


def candidates(rows, official=(), wikidata=()):
    """[(language, source, en, note, link, kind, owner)] before filtering. An agency's own
    spelling replaces Hololist's for the same name; Wikidata adds the other-language names
    (Chinese names of Japanese VTubers, for one) and VTubers Hololist lacks."""
    import opencc
    converters = (opencc.OpenCC("t2s"), opencc.OpenCC("s2t"))
    spelled = {o["native"]: o for o in official}
    spelled.update({_joined(o["native"]): o for o in official})
    out = []
    by_native = {}
    for row in rows:
        if row.get("platforms"):
            for native in _original_names(row, converters):
                by_native.setdefault(native, row)
    for row in rows:
        if not row.get("platforms"):
            continue
        language = CATEGORIES[row["category"]]
        link = f"{SITE}/{row['slug']}/"
        names = []
        if row["original"] and CJK.search(row["original"]):
            names.append((row["original"], row["name"], "name"))
            joined = _joined(row["original"])
            if joined != row["original"]:
                names.append((joined, row["name"], "name"))
        for nick in row["nicknames"]:
            pair = _split_nickname(nick)
            if pair:
                names.append((pair[1], pair[0], "nickname"))
        if row["fanbase_native"] and row["fanbase"]:
            fan_ens = [x for x in re.split(r"\s*[&/,]\s*", row["fanbase"]) if x]
            fan_natives = [x for x in re.split(r"\s*[&/,、]\s*", row["fanbase_native"]) if x]
            # "Ling Qiu & Ghost Ball" is one fan name and its meaning; two native names pair up
            # with two English ones only when the counts agree.
            pairs = zip(fan_natives, fan_ens) if len(fan_natives) == len(fan_ens) else [(fan_natives[0], fan_ens[0])]
            for fan_native, fan_en in pairs:
                names.append((fan_native, f"{fan_en} ({row['name']}'s fans)", "fans"))
        for native, en, kind in names:
            if kind == "name" and native in spelled:
                en = _official_en(spelled[native])
            note = (f"The fans of {row['name']}, a {COUNTRY[row['category']]} VTuber." if kind == "fans"
                    else _note(row, kind == "nickname"))
            for source in _variants(native, language, converters):
                out.append((language, source, en, note, link, kind, row["slug"]))
    for o in official:
        if o["native"] not in by_native and _joined(o["native"]) not in by_native and CJK.search(o["native"]):
            note = f"VTuber; {o['agency']}. Keep the name as written; never turn it into he/she."
            for native in {o["native"], _joined(o["native"])}:
                out.append(("ja", native, _official_en(o), note, o["link"], "name", o["link"]))
    for item in wikidata:
        labels = item["labels"]
        natives = [(lang, t) for lang, texts in labels.items() if lang != "en" for t in texts if CJK.search(t)]
        row = next((by_native[t] for _, t in natives if t in by_native), None)
        official_hit = next((spelled[t] for _, t in natives if t in spelled), None)
        en = (_official_en(official_hit) if official_hit else row["name"] if row
              else (labels.get("en") or [""])[0])
        if not en or CJK.search(en):
            continue
        if row:
            note, link, owner = _note(row, False), f"{SITE}/{row['slug']}/", row["slug"]
        else:
            facts = ["VTuber"] + ([_pronoun(item["gender"])] if _pronoun(item["gender"]) else [])
            note = "; ".join(facts) + ". Keep the name as written; never turn it into he/she."
            link = f"{WIKIDATA_ITEM}{item['qid']}"
            owner = official_hit["link"] if official_hit else item["qid"]
        for lang, text in natives:
            language = "ja" if lang == "ja" else "zh"
            for source in _variants(text, language, converters):
                out.append((language, source, en, note, link, "name", owner))
    return out


def filter_candidates(cands, words, taken, inner_terms=None):
    """(kept, held): kept entries per language, and held-back ones with the reason. `inner_terms`:
    per language, the other built-in packs' terms (ちゃん, 殿下, 少爷, 様). Every pack's entries claim
    their text even when the pack is off, so 本少爷 kept as a name would hide the comic pack's 少爷
    in ordinary speech. A one-character term counts only at the end (旦那様), or every name with 姫
    in it would go."""
    inner_terms = inner_terms or {}
    by_source = {}
    for c in cands:
        c = (c[0], _visible(c[1]).rstrip("。．"), _visible(c[2]), _visible(c[3])) + tuple(c[4:])
        by_source.setdefault((c[0], c[1]), []).append(c)
    kept, held = {"ja": [], "zh": []}, []
    for (language, source), group in by_source.items():
        owners = {c[6] for c in group}
        c = next((g for g in group if g[5] == "name"), group[0])
        reason = ""
        if source in taken.get(language, ()):
            continue
        if len(CJK.findall(source)) < 2:
            reason = "one character"
        elif len(source) > 40:
            reason = "too long"
        elif len(owners) > 1 and len({g[6] for g in group if g[5] == "name"}) != 1:
            reason = "used by " + ", ".join(sorted(owners))
        elif BRACKETS.search(source):
            reason = "has brackets"
        elif (len(source) <= MAX_UNCONFIRMED_KANA and HIRAGANA.fullmatch(source)
              and not any(g[4].startswith(CONFIRMING) for g in group)):
            reason = "short hiragana name"
        elif words.inside_common_word(source, language):
            reason = "part of a common word"
        elif (len(source) <= MAX_CHECKED_LEN or c[5] != "name" or KANA.fullmatch(source)) and words.is_common(
                source, language):
            reason = f"ordinary word (frequency {words.zipf(source, language):.1f})"
        elif (c[5] != "name" or c[4].startswith(WIKIDATA_ITEM)) and (
                inner := next((x for x in sorted(inner_terms.get(language, ())) if x != source and (
                    source.endswith(x) if len(x) == 1 else x in source)), None)):
            # A VTuber's own name keeps its claim: 姫森 holding 姫 is the name, not the word.
            reason = f"holds another pack's term {inner}"
        elif c[5] == "nickname" and language == "zh" and source[0] in SURNAMES and HANZI3.fullmatch(source):
            reason = "may be a personal name"
        elif c[5] == "nickname" and len(source) == 2 and GENERIC_NICKNAME.fullmatch(source):
            reason = "generic nickname form"
        elif c[5] == "fans" and words.is_compound(source, language):
            reason = "fan name made of ordinary words"
        elif c[5] == "fans" and len(source) <= 2:
            # Short fan names are often ordinary words the word lists are too small to know (墓守).
            reason = "two-character fan name"
        if reason:
            held.append((language, source, c[2], c[4], reason))
        else:
            kept[language].append({"source": source, "en": c[2], "category": "streamer",
                                   "note": c[3], "link": c[4]})
    return kept, held


def _pack_text(pack) -> str:
    """The pack file with one entry per line, so a weekly rebuild's diff shows each changed name."""
    head = {k: v for k, v in pack.items() if k != "entries"}
    lines = [json.dumps(e, ensure_ascii=False) for e in pack["entries"]]
    return (json.dumps(head, ensure_ascii=False, indent=1)[:-2] + ',\n "entries": [\n  '
            + ",\n  ".join(lines) + "\n ]\n}\n")


def _excluded(entry, patterns) -> bool:
    text = " ".join((entry["source"], entry["en"], entry["note"], entry["link"]))
    return any(p.search(text) for p in patterns)


def _excluded_reference(reference, rows, converters):
    """The official and Wikidata records naming a left-out VTuber, matched by its name in any
    script or order, since an exclude term written one way would miss a label written another."""
    names = set()
    for row in rows:
        names |= _original_names(row, converters)
        for nick in row.get("nicknames", ()):
            pair = _split_nickname(nick)
            if pair:
                names.update(_variants(pair[1], "zh", converters))
    names.discard("")

    def hit(record):
        texts = ([record["native"]] if "native" in record
                 else [t for lang, ts in record.get("labels", {}).items() if lang != "en" for t in ts])
        return any(v in names for t in texts for v in _variants(_joined(t), "zh", converters) + [t])
    return {name: [r for r in records if not hit(r)] for name, records in reference.items()}


def build(cache_dir: Path, review: Path, exclude: Path = None, force: bool = False):
    rows = list(load_cache(cache_dir).values())
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}
    packs, hand, taken = {}, {}, {"ja": set(), "zh": set()}
    for language in ("ja", "zh"):
        path = PACK_DIR / f"{language}-vtuber-names.json"
        packs[language] = json.loads(path.read_text(encoding="utf-8"))
        hand[language] = [e for e in packs[language]["entries"] if not e.get("link")]
    import opencc
    converters = (opencc.OpenCC("t2s"), opencc.OpenCC("s2t"))
    inner_terms = {"ja": set(), "zh": set()}
    # A source another pack of the language already has keeps that pack's entry, in both scripts,
    # so Simplified and Traditional text get the same rendering.
    for path in sorted(PACK_DIR.glob("*.json")):
        other = json.loads(path.read_text(encoding="utf-8"))
        for language in ([other["language"]] if other["language"] in taken else list(taken)
                         if other["language"] == "any" else []):
            for e in other["entries"]:
                if not e.get("link"):
                    taken[language].update(_variants(e["source"], language, converters))
                if other["id"] != f"{language}-vtuber-names":
                    inner_terms[language].update(_variants(e["source"], language, converters))
    reference = {name: json.loads((cache_dir / f"{name}.json").read_text(encoding="utf-8"))
                 if (cache_dir / f"{name}.json").exists() else [] for name in ("official", "wikidata")}
    terms = [t.strip() for t in exclude.read_text(encoding="utf-8").splitlines()] if exclude else []
    terms = {v for t in terms if t and not t.startswith("#") for v in _variants(t, "zh", converters)}
    patterns = [re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.I) for t in sorted(terms)]

    def keep(record):
        return not any(p.search(json.dumps(record, ensure_ascii=False)) for p in patterns)

    # A left-out VTuber is dropped whole, nicknames and review lines included.
    reference = _excluded_reference(reference, [r for r in rows if not keep(r)], converters)
    rows = [r for r in rows if keep(r)]
    reference = {name: [r for r in records if keep(r)] for name, records in reference.items()}
    kept, held = filter_candidates(candidates(rows, reference["official"], reference["wikidata"]),
                                   _Words(), taken, inner_terms)
    left_out = 0
    for language in kept:
        before = len(kept[language])
        kept[language] = [e for e in kept[language] if not _excluded(e, patterns)]
        left_out += before - len(kept[language])
    held = [h for h in held if not any(p.search(" ".join(h)) for p in patterns)]
    texts = {}
    for language, pack in packs.items():
        fixes = overrides.get(language, {})
        generated = []
        for entry in sorted(kept[language], key=lambda e: e["link"]):
            fix = fixes.get(entry["source"])
            if fix is None:
                generated.append(entry)
            elif not fix.get("drop"):
                generated.append({**entry, **{k: v for k, v in fix.items() if k in ("en", "note")}})
        before = len(pack["entries"]) - len(hand[language])
        if not force and before >= GUARDED_FROM and len(generated) < before * MIN_KEPT_SHARE:
            raise SystemExit(f"{language}: {len(generated)} generated entries, down from {before}; "
                             "check the cache, or pass --force.")
        entries = hand[language] + generated
        if entries != pack["entries"]:
            pack["entries"] = entries
            pack["version"] += 1
        text = _pack_text(pack)
        # The app skips a pack that fails its checks, hand-written entries and all, with only a log
        # line, so a bad build must fail here instead.
        language_packs.validate_pack(json.loads(text), f"{language}-vtuber-names.json")
        if len(text.encode("utf-8")) > language_packs.MAX_PACK_BYTES:
            raise SystemExit(f"{language}-vtuber-names.json would be larger than the app loads.")
        texts[language] = text
        print(f"{language}: {len(hand[language])} hand-written + {len(generated)} generated", flush=True)
    # Both packs pass before either is written, so a failed build leaves the pair as it was.
    for language, text in texts.items():
        (PACK_DIR / f"{language}-vtuber-names.json").write_text(text, encoding="utf-8")
    if patterns:
        print(f"left out by --exclude: {left_out}", flush=True)
    if review:
        lines = ["| language | source | English | reason | profile |", "|---|---|---|---|---|"]
        lines += [f"| {lang} | {src} | {en} | {why} | {link} |" for lang, src, en, link, why in sorted(held)]
        review.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"held back: {len(held)} (listed in {review})", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--cache", type=Path, required=True)
    f.add_argument("--refresh-max", type=int, default=800)
    r = sub.add_parser("reference")
    r.add_argument("--cache", type=Path, required=True)
    b = sub.add_parser("build")
    b.add_argument("--cache", type=Path, required=True)
    b.add_argument("--review", type=Path)
    b.add_argument("--exclude", type=Path)
    b.add_argument("--force", action="store_true", help="write even when most generated entries are gone")
    args = parser.parse_args(argv)
    if args.command == "fetch":
        fetch(args.cache, args.refresh_max)
    elif args.command == "reference":
        fetch_reference(args.cache)
    else:
        build(args.cache, args.review, args.exclude, args.force)


if __name__ == "__main__":
    main()
