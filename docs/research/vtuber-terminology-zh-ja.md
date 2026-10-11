# Ship four VTuber packs, hold the rest

About 380 Japanese and Chinese VTuber terms have a fetched source for their meaning, and Baihe can ship them now as four new language packs, all off by default: `ja-vtuber-core` (164 entries), `ja-vtuber-names` (42), `zh-vtuber-core` (139) and `zh-vtuber-names` (35). The sources agree on meanings far more than on English renderings. Fan-facing Japanese glossaries keep a small set of community-identity words romanised: oshi, teetee, otsu, seiso, akasupa, hako-oshi, gachikoi, mama/papa, kaigai niki. They translate format, platform and money words into plain English (stream, singing stream, clip, superchat, members-only). Proper names, fan names and catchphrases stay romanised. **No clipper, Holotranslate-style or Bilibili 字幕组 style guide was found**, so every English default below is a convention drawn from fan glossaries and the Netflix style guide, not a documented subtitling standard. The evidence is thinner than the entry count suggests. Moegirlpedia bodies, the fandom VTuber glossary (HTTP 402), pixiv百科 (403), the Hololive Fan Wiki (403), Baidu Baike bodies and Tieba were all blocked, and most Chinese English renderings rest on no English-language source at all. The biggest risks are mechanical, not lexical. Baihe matches by plain substring with no script, width or case normalisation and no CJK word boundaries, so one-character slang (典, 急, 推, 箱, 草) fires inside ordinary words. Simplified and Traditional forms must be listed separately. Only 60 entries reach a prompt, in pack-filename order.

## Fan glossaries agree on meanings, but no one publishes a subtitle style

The Japanese material is the stronger half. Five fetched fan or industry glossaries ([OAS](https://www.oas.ac.jp/contents/blogs/2025/01/25/1144), [unbereal](https://unbereal.co.jp/vtuberaudition/vtuber_terminology/), [vtuberquest](https://vtuberquest.com/8726), [mamecolog](https://mamecolog.com/vtuber_terminology/), [VTuber Dictionary](https://vtd.mdkj.dev/)) sit beside two English-facing translator resources. One is a fan translator's Pekora/hololive slang list ([pastebin](https://pastebin.com/bAXxd0q1)). The other is valshia's bilingual "Holo-Oshikatsu English Lesson" ([note.com](https://note.com/valshia/n/n28778f2b8502?hl=en)). Japanese Wikipedia and the official hololive talent pages add the rest. Those last two sources matter most for rendering. They are the only fetched evidence of what English-speaking fan translators actually write. valshia explicitly prefers "clip" over *kirinuki*, says 枠 means "stream" or "waiting room" rather than "frame", and recommends keeping *otsukare*, *akasupa*, *teetee*, *kusa* and *seiso* as they are ([valshia](https://note.com/valshia/n/n28778f2b8502?hl=en)). About 60 Japanese candidate rows rested only on the researcher's own knowledge. A gap round promoted 11 of them. The rest are marked **DROP for shipping**, and they stay out of this report's ship lists. They include high-frequency items: 888, 初見, 待機, 炎上, 切り抜き師 and the platform words 高評価, 概要欄 and 固定コメント.

The Chinese material attests Chinese meanings only. The richest single source is a Taiwanese NOWnews glossary ([NOWnews](https://www.nownews.com/news/6346908)), with Chinese Wikipedia's VTuber and Bilibili-live pages ([zh.wikipedia](https://zh.wikipedia.org/zh-cn/虚拟YouTuber)), a Bilibili column on 大航海 prices ([Bilibili](https://www.bilibili.com/opus/553548467581752502)), and, in the gap round, a hostile "exposé" wiki's V圈黑话 list ([esugou](https://esugou.wiki/index.php?title=%E6%A6%82%E5%BF%B5%2FV%E5%9C%88%E9%BB%91%E8%AF%9D)) and an insider Douban A-SOUL glossary ([Douban](https://www.douban.com/group/topic/228454620/)). None of them says how the terms are rendered in English subtitles. The renderings in the Chinese tables are the conventional VTuber-English equivalents chosen by the researcher, so treat them as proposals. **The Traditional forms and pinyin in the Chinese gap-round notes are the note author's own machine conversion.** Twins this report adds itself are converted the same way. Both are marked † in the appendix and need a native-reader check.

The professional baseline is the Netflix English timed-text guide. It allows 42 characters per line and 20 characters per second for adult programmes (17 for children's). It italicises unfamiliar foreign words, puts on-screen text in ALL CAPS forced narratives that never share a subtitle with dialogue, and does not translate proper names unless approved translations exist. Nicknames are translated only when they carry meaning ([Netflix TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide)). It says nothing about honorifics. The anime precedent is split. A 2012 report says Funimation, and often Crunchyroll, kept honorifics because subtitle viewers want them ([animeanime.jp](https://animeanime.jp/article/2012/10/19/11799.html)). A 2010 forum thread cites a poll with 56.8% against honorifics ([ANN forum](https://www.animenewsnetwork.com/bbs/phpBB2/viewtopic.php?p=3428755)). Baihe's existing `ja-address` pack already covers honorifics, so the VTuber packs should not repeat them.

Several sources that would most likely have settled open questions were unreachable: the fandom glossary (402), pixiv百科 (403), the Hololive Fan Wiki (403), the 名取さな wiki (403), Moegirlpedia bodies on every mirror, Baidu Baike bodies, Bilibili `/read/cv` columns, Tieba (403), vtbfan.com (503), goodvtubersubs (empty), the 2025 *Journal of Japanese Linguistics* VTuber paper (405) and the NTNU thesis on VTuber 字幕组 (503). Many Moegirl, Baidu and Zhihu definitions survive only as search snippets. This report counts a snippet as a lead, not as a fetched source.

## A three-tier keep-or-translate rule fits the evidence

The sources support one policy with three tiers. The **first tier** is proper nouns: talent names, agency and unit names, fan names, signature greetings and outros. These stay in the official romanisation or styling in every style, glossed at most once. This follows Netflix's proper-name rule. It also follows the hololive pages, which list fan names, catchphrases and hashtags as fixed identifiers: こんぺこ for Pekora, おつこーん for Fubuki, こんやっぴー/おつやっぴー for Towa ([hololive Pekora](https://hololive.hololivepro.com/talents/usada-pekora/); [Fubuki](https://hololive.hololivepro.com/talents/shirakami-fubuki/); [Towa](https://hololive.hololivepro.com/talents/tokoyami-towa/)). Fan translators render 野うさぎ as "Nousagi" and 兎田ふぁみりあ as "Pekora Familia" ([pastebin](https://pastebin.com/bAXxd0q1)).

The **second tier** is about a dozen community-identity words. Fan translators keep these romanised, and a general-audience subtitle translates them. Baihe's `styles` field carries exactly this choice, so each such entry gets `{"romanised": "oshi", "natural": "favorite"}`-style values. **The `romanised` style should be the default**, because the only fetched translator sources keep these words. The default can be flipped per title.

The **third tier** is everything else: stream formats, platform features, money, roles and most slang. It is translated in both styles. Two calques from idol culture are now standard English VTuber jargon: "graduation" for 卒業 and "the person inside" for 中の人 ([Wikipedia: VTuber](https://en.wikipedia.org/wiki/VTuber)). 卒業 must stay distinct from 契約解除/解约, "contract termination", because fans read termination as involuntary ([zh.wikipedia VirtuaReal](https://zh.wikipedia.org/zh-cn/VirtuaReal)).

Laughter tokens need a decision that the sources leave open. valshia keeps "kusa", but a subtitle renders the laugh, not the word. So 草/w/ｗｗｗ default to "lol" in both styles, with *kusa* reserved for lines that discuss the word itself. Reading speed reinforces the choice. At 17–20 cps a two-second line holds roughly 34–40 characters, which leaves no room for "original (gloss)" on every occurrence. Pack notes should therefore tell the model to gloss once or not at all.

Persona handling is the weakest area. No VTuber or fan-translation guide states a rule for 一人称 (first-person pronouns) or 語尾 (sentence endings). The role-language literature, seen only as snippets, says such markers are usually standardised away in translation, at the cost of character distinctiveness ([Yakuwarigo](https://en.wikipedia.org/wiki/Yakuwarigo); [Heerink thesis](https://openaccess.leidenuniv.nl/bitstream/handle/1887/63751/Thesis%20Version%202.pdf)). A handful of VTuber facts are attested. Pekora calls herself ぺこーら while peers say ぺこら ([pastebin](https://pastebin.com/bAXxd0q1)). Towa's official self-introduction uses 常闇トワ**様** ([hololive Towa](https://hololive.hololivepro.com/talents/tokoyami-towa/)). 月ノ美兎 uses わたくし ([ja.wikipedia](https://ja.wikipedia.org/wiki/月ノ美兎)). A Chinese article maps a Japanese VTuber's archaic わらわ to archaic 妾身, register for register ([ifanr](https://www.ifanr.com/1291785), snippet). The defensible pack rule follows from these. Name entries say "third-person self-reference: keep the name, never turn it into he/she". Ordinary pronouns are left to the model. Branded tics such as Pekora's ぺこ wait for a second source, because only a pixiv snippet attests them. Pronoun gender should follow the talent's official profile, not the voice, because バ美肉 (babiniku, usually a man with a cute-girl avatar) is a recognised and celebrated category ([Wikipedia: VTuber](https://en.wikipedia.org/wiki/VTuber); [unbereal](https://unbereal.co.jp/vtuberaudition/vtuber_terminology/)). A shared pack cannot know a title's talent, so persona and pronoun notes belong in the title glossary, which Baihe already ranks above every pack.

Machine-translation research explains why notes matter as much as targets. Detect-explain-translate prompting beats plain translation on slang ([SlangDIT](https://arxiv.org/abs/2505.14181)). Prompts that say "translate" push LLMs toward literal output that loses puns ([arXiv 2507.06506](https://arxiv.org/pdf/2507.06506)). Glossary compliance is also imperfect: generic LLMs inserted the supplied term only about 64% of the time on one legal test set ([TermTrends 2025](https://aclanthology.org/2025.termtrends-1.1)). Each entry should therefore carry a short meaning note, not just an English word. A post-check in code, rather than prompt wording, is the only way to know whether the term landed.

## Baihe's substring matcher turns short terms into hazards

The pack schema fits VTuber terms without code changes. The existing `streamer`, `interjection`, `phrase` and `role` categories cover slang, platform words, agencies and people, and the optional `styles` block carries the keep-versus-translate choice. The problems are in matching. Matching is plain, case-sensitive substring search over the whole run's source text. ASCII terms get alphanumeric word boundaries; CJK terms get none. Nothing is normalised for script, width, kana/kanji or case. So **every one- or two-character source fires inside ordinary words** unless a `not_in` list names the longer words: 草 in 草原/雑草/草莓/草稿, 箱 in ゴミ箱/箱根, 枠 in 枠組み, 魂 in 霊魂/灵魂, 耐久 in 耐久性, さすが in さすがに, アンチ in アンチエイジング, 工商 in 工商银行.

The longest match wins each character span, across all packs of the language (including switched-off ones) and the user's glossary. That gives a free second defence: 推し活, 箱推し, 歌枠, 初配信詐欺 and 虚拟主播 each shield their shorter cores.

Some terms cannot be made safe this way. Chinese debate slang (典 孝 急 乐 批 麻 蚌 赢) is sourced ([The Paper](https://www.thepaper.cn/newsDetail_forward_30773412); [China Digital Times](https://chinadigitaltimes.net/chinese/684444.html)). But these characters appear in thousands of ordinary words, and their slang sense only exists when the character stands alone as a whole chat message. Baihe has no whole-line match, so they stay out until a code change adds one. Bare 推, 箱 (zh), 皮, 车, 寄, 塔, 清楚 ("pure" persona, but ordinarily "clear") and 混沌 stay out for the same reason. Their longer forms (单推, 箱推, 皮套, 独轮车, 塔照) ship instead.

**Simplified and Traditional forms are separate entries.** The shipped `zh-address` and `zh-common` packs already pair 弹幕/彈幕 and 打赏/打賞, and the zh packs below follow the same convention. The twin test covers only those two packs and a small character map. The new zh packs need their own twin test.

Width and case are separate strings too. `w`, `www` and `ｗｗｗ` are three entries, because ASCII `w` will not match inside `www`, and fullwidth never matches ASCII. The same applies to XSWL/xswl and YYDS/yyds. Whisper writes laughter inconsistently and rarely writes chat tokens in speech at all. Add width or kana variants only after they appear in real transcripts.

Three ordering facts shape the design.

First, **only 60 entries reach a prompt**. They are taken in pack-filename order, then file order, not by frequency. `ja-address` and `ja-common` (31 entries) sort ahead of any `ja-vtuber-*` pack, so their hits come first. Inside each new pack, frequent stream words must come before rare slang.

Second, **filename sorting is not intuitive**. `ja-vtuber-names.json` sorts *before* `ja-vtuber.json`, because `-` (0x2D) sorts before `.` (0x2E). That is why the ids proposed here are `-core` and `-names`: c sorts before n, so the core pack always takes priority.

Third, **a source that appears in two packs is emitted only from the first pack by filename**. `zh-address` already maps 妈妈/媽媽 to "Mom", so a `zh-vtuber-core` entry for 妈妈 = "mama (character designer)" would never fire. Mama/papa ships only in Japanese, with a context note. The Chinese version needs an edit to `zh-address` or a title-glossary entry. Likewise, 推し, 配信, 視聴者 and 切り抜き are already in `ja-common`. 直播, 主播, 弹幕 and 打赏 are in `zh-common`. VTuber, ASMR, CP, BGM, OP and ED are in `shared-drama`. None of them is repeated.

Two pipeline facts affect Live use. Packs follow only the title's source language, which defaults to `zh` when unset. A Japanese Live session with no source language would therefore load the Chinese packs. The in-progress Live PR should call `entries_for_text` per chunk. That makes the 60-entry cap almost irrelevant, but `not_in` lists matter more on short chunks.

Shipping the packs breaks one pinned test: the Japanese pack-id set in `tests/test_language_packs.py`. Every description must contain "Starter pack, review it".

## Four packs, ordered core first and names last

The research supports four packs, all off by default like every existing pack. `ja-vtuber-core` and `zh-vtuber-core` hold stream, platform, money, role and chat vocabulary with the romanised/fan versus natural styles. `ja-vtuber-names` and `zh-vtuber-names` hold agencies, units, talents, fan names and greetings, with no styles, since names render the same either way. A fifth language-neutral pack is not worth it. A pack with language `any` also loads for Korean titles, and the few ASCII terms (DD, SC, RTA, FA, VUP) fit inside the language packs. Splitting fan slang into its own pack would add one more filename to order with no matching benefit.

Agency and unit spellings need the most caution, because the organisations keep changing. Japanese Wikipedia records that hololive's brand structure was **reorganised on 2026-09-07**, five weeks ago. hololive, hololive ID, hololive EN and DEV_IS are now marketed together as "hololive", so branch labels in older sources are already historical ([ja.wikipedia ホロライブプロダクション](https://ja.wikipedia.org/wiki/ホロライブプロダクション)). Unit stylings are mixed-case and should be copied exactly: ReGLOSS, FLOW GLOW, holoX, holoForce, NePoLaBo, holoro, holoh3ro, AREA 15, UPROAR!!, TEMPUS, ARMIS. Promise was formerly Project: Hope and Council ([en.wikipedia Hololive Production](https://en.wikipedia.org/wiki/Hololive_Production)). The English and Japanese Wikipedia HOLOSTARS member lists conflict, probably because one lists current members and the other all-time members.

On the NIJISANJI side, members are "Livers", a portmanteau of "live streamers". English wave names are inconsistently spelled even within one article (LazuLight/Lazulight, OBSYDIA/Obsidiya). VirtuaReal is a Bilibili joint venture, not a "NIJISANJI VR" branch ([en.wikipedia Nijisanji](https://en.wikipedia.org/wiki/Nijisanji)). VShojo shut down in July 2025 ([Wikipedia: VTuber](https://en.wikipedia.org/wiki/VTuber)).

The Chinese side has its own corrections. EOE belongs to 乐华 (Yuehua), not 乐元素 (Happy Elements). A-SOUL passed from ByteDance to 枝江娱乐 to Yuehua in 2024 ([zh.wikipedia A-SOUL](https://zh.wikipedia.org/zh-cn/A-SOUL)). "虚拟乐园" is not an attested name for VirtuaReal; its abbreviations are VR and 维阿 ([zh.wikipedia VirtuaReal](https://zh.wikipedia.org/zh-cn/VirtuaReal)). A-SOUL has official English member names (Ava, Bella, Carol, Diana, Eileen) ([Last.fm](https://www.last.fm/zh/music/a-soul/%E8%B6%85%E7%BA%A7%E6%95%8F%E6%84%9F+-+Single)), but some fan pages use pinyin such as "Jiaran". The pack uses the official English and notes the pinyin.

VSPO!, Neo-Porte, Aogiri High School and 774 inc. rest on snippets only. Spring Fish Studio for 春魚工作室 is media usage, not a confirmed official spelling. All of these wait. The names packs should carry a version bump policy. Every reorganisation, graduation or rename is a content change, and a stale unit label is worse than none.

## What this pack cannot do

A glossary pack is a list of words with suggested English. It cannot read context, so it cannot tell when a word is being used in its everyday sense. 卒業 is a VTuber's graduation in one sentence and a school graduation in the next. ママ is the character designer or the streamer's actual mother. 助かる is fan praise in chat and an ordinary "that helps" in conversation. 草 is laughter in chat and literal grass in a gardening game. 生肉 is an untranslated clip or raw meat on a cooking stream. 案件 is a sponsorship or just "a matter". The pack can warn the model with a note, but the model still decides, and it will sometimes decide wrongly. The words in ordinary use most often are kept out of the pack entirely.

It cannot translate puns. VTuber humour leans heavily on homophones, name puns (こん + name greetings, 嘉晚饭 ship names) and character-swap jokes (蚌 for 绷). Research on pun translation shows models flatten wordplay and that a direct "translate" instruction makes it worse. A glossary entry can say "this is a pun, adapt it or drop it", but it cannot supply the adaptation.

It cannot keep up with slang. Chat vocabulary turns over within months. Several of the Chinese terms here come from a fan post written around 2021, and hololive reorganised its brands five weeks before this report. Whatever ships today will be partly stale within a year, and the pack has no expiry date.

It cannot follow agencies as they reorganise, merge or rename. Units graduate members, branches are folded together, and talents move between agencies (Komori Met moved from 774 inc. to VSPO! in 2023, per a news snippet). The names packs are a snapshot that someone must re-check before each release.

It cannot make terms from a hostile source neutral. Much of the Chinese fandom vocabulary was attested only by esugou, a wiki written to mock VTuber fans. Its words for newcomers, rival fandoms and failed streamers (底边, 梁木, 杏奴, 虹太监, 管人) are insults. Even the neutral-sounding ones carry a sneer the dictionary meaning does not show. This report ships only esugou terms that are neutral and useful, flags the one pejorative it ships (MMR/萌萌人, which an insider glossary also attests), and holds the rest back.

It cannot know who is speaking. A pack cannot tell the streamer's own speech from a chat message or superchat they read aloud. It cannot tell which talent is on screen, or whether that talent refers to herself in the third person. That information belongs in the title glossary and in speaker labels.

## What could not be established

No published subtitle style guide from hololive or NIJISANJI clippers, Holotranslate-style groups or Bilibili 字幕组 was found in English or Chinese searches. The nearest thing, a Taiwanese master's thesis on VTuber fansub groups, could not be fetched beyond its abstract ([airiti](https://www.airitilibrary.com/Article/Detail/U0021-NTNU42950)). The format of LiveTL's translator tags could not be verified. No source compared "keep and gloss" with "translate" for catchphrases on real clips.

No source gave English renderings for Chinese guard tiers. 舰长/提督/总督 are rendered here as Captain/Admiral/Governor because those are the literal naval names, not because the English community is documented using them. Current (2026) 大航海 and 醒目留言 prices are unverified; the sources date from 2020–22.

Several meanings are attested only by snippets: 老板 for a big tipper, the Mainland 88/888 usage, 打call, 下次一定, 破防 and 营业. Persona self-reference forms (本喵, 朕, 咱, 人家; 余, 吾輩, 拙者) and their English treatment have no source at all. Fan names and greetings were found for 13 hololive talents, 月ノ美兎 and the A-SOUL members. Nothing was found for VSPO!, Neo-Porte, Aogiri, 星瞳 or any Taiwanese talent. Brand capitalisation (hololive vs Hololive, VSPO! vs Vspo!) was not checked against official sites.

There is also a conflict in the records about NOWnews. The Japanese notes saw it only as a snippet, while the Chinese notes cite it in detail as their richest source. This report counts it as fetched and marks its single-source rows NN\* so one re-fetch can confirm them.

## Legal note

hololive's fan-work terms require clips to link the source stream and its title at the start of the description. Clips may be uploaded only after the stream ends and its VOD is public. Members-only and paid content are excluded without permission, and registering clips in Content ID is forbidden. The page fetched did not mention translations or subtitles, and the circulating "25% of a video" limit is a third-party claim, not official text ([hololive terms](https://hololivepro.com/en/terms/); [goodvtubersubs](https://goodvtubersubs.com/guidelines)). NIJISANJI (ANYCOLOR) and Bilibili terms were not checked. For the packs themselves, talent names, fan names and catchphrases are short factual identifiers. The packs carry only those plus Baihe's own glosses, copy no wiki definitions verbatim (wiki text carries its own licence), and use no agency logos or art.

## Conclusion

The packs' quality now depends more on Baihe's matcher than on the lexicon. Most of the sourced vocabulary that cannot ship is held back by matching, not by evidence: single-character debate slang, bare 推 and 箱, and context words like 清楚. A small code change, a "whole chat line equals source" match mode plus optional width/case folding, would unlock more safe entries than another research round. A second change, ranking the 60-entry cap by frequency rather than filename, would make pack ordering a non-issue.

The second takeaway is that the English renderings are the least-sourced layer of this work, and only real output can settle them. Baihe is well placed to do that. A clipper-facing review pass over a few real subtitled VODs would show whether *oshi* versus "favorite" and Captain versus "guard" actually read well, which no published guide answers today.

---

## Appendix A — Ship now

Rules applied: confidence H or M in the notes, **and** at least one source the notes record as fetched. A source counts as fetched unless the notes label it a snippet, a search summary or blocked. Japanese "DROP for shipping" rows, own-knowledge [K] rows and unsourced L candidates are excluded. Entries already in `ja-common`, `zh-common`, `shared-drama` or `zh-address` are excluded. A few sourced terms are excluded for matching risk (Appendix C).

"EN default" shows a single string, or `romanised / natural` (ja) and `fan / natural` (zh) for styled entries. † marks a Traditional form or pinyin that was machine-converted (by the note author in the gap round, or in this report), and so needs a native check. NN\* marks a NOWnews-only row; see "What could not be established". Source keys are listed in Appendix D.

### A1. `ja-vtuber-core` — language `ja` — 164 entries

Title "Japanese VTuber stream terms". Description "Starter pack, review it. Words VTubers and their viewers use on stream and in chat." Styles: default `romanised`, with options `romanised` ("Keep fan terms romanised (oshi, teetee, hako)") and `natural` ("Translate fan terms into plain English"). File order = the order below.

| Source text (entries) | EN default | Category | Conf · Src | Note / `not_in` / context |
|---|---|---|---|---|
| 草 | lol | interjection | H · PB, VA (+HW snippet) | not_in 草原, 草稿, 雑草, 草刈り, 草むしり, 草食, 草履, 草木, 草花, 草野球. Literal grass only for plants |
| w · www · ｗｗｗ (3) | lol | interjection | H · PB, VA | Separate entries: ASCII boundary and width are not normalised. Mostly in chat readings |
| 乙 · おつ (2) | otsu / good work | phrase | H · VA (+HW) | 乙 not_in 乙女, 乙女ゲー, 乙種, 甲乙. おつ not_in おつり, おつまみ, おつかい, おつむ. ja-common's お疲れ様 stays as is |
| てぇてぇ | teetee / precious | streamer | H · VA, VTD, MC, UB, PB | Wholesome interaction between talents |
| 尊い | precious | streamer | H · VTD, MC | |
| 推し活 | oshikatsu / fan activities | streamer | H · VQ, VA | Shields ja-common's 推し |
| 箱推し | hako-oshi / fan of the whole agency | streamer | H · UB, MC, PB | |
| 最推し | top oshi / number-one favorite | streamer | M · UB, MC | |
| 単推し | single-oshi / fan of just one talent | streamer | M · UB, MC | |
| DD | DD / fan of everyone | streamer | H · PB, VTD | |
| 推しマーク · ファンマーク (2) | oshi mark / fan emoji | streamer | M · UB | |
| ガチ恋 · ガチ恋勢 (2) | gachikoi / fan in love with the streamer | streamer | H · PB, VTD, VQ | |
| ガチ恋距離 | gachikoi distance / close-up | streamer | M · PB | Very close camera or mic distance |
| リアコ | riako / in love with the real person | streamer | M · VQ | |
| 限界オタク | fan at their limit | streamer | M · VQ | |
| 海外ニキ · 海外兄貴 (2) | kaigai niki / overseas bros | streamer | H · PB, VA | |
| 清楚 | seiso / pure and proper | streamer | H · PB, VA | Often ironic |
| ポンコツ · PON (2) | pon / airhead | streamer | H · PB, VQ | |
| 助かる | much appreciated | phrase | H · PB, VQ | Context: fanservice praise. Ordinary "that helps" otherwise |
| さすが | sasuga / as expected | phrase | H · PB, VA | not_in さすがに |
| エモい | emotional | streamer | M · PB | |
| 台パン | daipan / desk slam | streamer | M · VQ | |
| プロレス | pro-wrestling (staged banter) / playful feud | streamer | M · PB | Literal wrestling if the topic is wrestling |
| 伝書鳩 | carrier pigeon | streamer | H · UB (+FDM) | Relaying another stream's chat. Bad etiquette |
| エゴサ | ego-searching | streamer | M · PB | |
| アンチ | antis | streamer | M · VTD | not_in アンチエイジング |
| やらかし | blunder | streamer | M · VQ | |
| 晒し | exposing (callout) | streamer | M · VQ | |
| 内輪ネタ | in-joke | streamer | M · VQ | |
| わちゃわちゃ | chaotic fun | streamer | M · VQ | |
| 陽キャ · 陰キャ · コミュ障 (3) | extrovert · introvert · socially awkward | streamer | M · PB | |
| フラグ回収 · フラグ (2) | called it (flag paid off) · flag | streamer | M · PB | |
| 萌え | moe | streamer | H · MC, WP | |
| 半年ROMれ | lurk more | phrase | M · VA | |
| バブみ | babumi / motherly comfort | streamer | M · PB | |
| エアプ · 舐めプ · 姫プ (3) | never actually played · playing condescendingly · babying a player | streamer | M · PB | |
| げぼかわ | sickeningly cute | streamer | M · VTD | |
| ガンギマリ | wild-eyed | streamer | M · PB | |
| 闇落ち · 病み営業 (2) | dark turn · performative gloom | streamer | M · VQ | |
| 魔王ムーブ | demon-king move | streamer | M · VQ | |
| 反応芸 · リアクション芸 · 泣き芸 · ワイプ芸 (4) | reaction bit · reaction bit · crying bit · picture-in-picture reaction bit | streamer | M · VQ | |
| ログボ | login bonus | streamer | M · VQ | |
| 新人潰し | bullying newcomers | streamer | M · VQ | |
| 営業妨害 | ruining someone's image | streamer | M · PB | Joking use |
| ブーメラン | boomerang (backfired criticism) | streamer | M · PB | |
| 全ロス | lost everything | streamer | M · PB | Game item loss |
| マイクラ | Minecraft | streamer | M · PB | |
| 弾幕 | comment barrage | streamer | M · VQ | |
| 配信枠 · 枠 (2) | stream | streamer | H · PB, VA, VQ | 枠 not_in 枠組み, 枠内, 枠外, 別枠, 窓枠, 黒枠, 大枠, 枠線. Never "frame" |
| 歌枠 | singing stream | streamer | H · PB, VQ | |
| ランダム歌枠 | random-song karaoke stream | streamer | M · VQ | |
| 雑談 | zatsudan / chatting stream | streamer | H · PB, JWP | Context: a stream format. Ordinary chit-chat otherwise |
| コラボ | collab | streamer | H · PB, OAS, VQ | |
| 凸待ち | totsumachi / call-in stream | streamer | H · PB, UB | |
| 逆凸 | reverse totsu / calling people up | streamer | H · UB (+DG, SS) | |
| 耐久配信 · 耐久 (2) | endurance stream | streamer | M · VQ | 耐久 not_in 耐久性, 耐久力, 耐久値, 耐久度 |
| 案件配信 · 案件 (2) | sponsored stream | streamer | H · PB, VQ | Context: 案件 alone can be "a matter" |
| 二回行動 | second stream of the day | streamer | M · PB | |
| RTA | RTA / speedrun | streamer | M · PB | |
| マルチ配信 | multistream | streamer | M · VQ | |
| ワンオペ配信 | solo-run stream (no staff) | streamer | M · VQ | |
| 無音枠 | silent stream | streamer | M · VQ | |
| 焼肉配信 | yakiniku stream (real-life meal footage) | streamer | M · VQ | |
| パリコレ | outfit showcase | streamer | M · VQ | |
| アーカイブ | archive (VOD) | streamer | H · OAS, VQ | |
| 配信事故 · 放送事故 (2) | stream mishap | streamer | M · VQ | |
| 畳配信 | tatami stream (no game on screen) | streamer | M · UB | |
| ミュート芸 | mic-mute bit | streamer | M · UB | |
| 初配信 | debut stream | streamer | H · VTD, VQ | |
| 初配信詐欺 | fake debut stream | streamer | M · VQ | |
| メン限 · メンバー限定 (2) | members-only | streamer | H · PB, VTD | |
| ゲーム実況 | gaming stream | streamer | M · JWP | |
| 同時視聴 | watchalong | streamer | H · JWP (+MV) | |
| 3Dお披露目 | 3D debut | streamer | M · JWP | |
| 歌ってみた | utattemita / song cover | streamer | M · JWP | |
| リアルライブ | in-person concert | streamer | M · JWP | |
| 大事なお知らせ | important announcement | phrase | M · JWP | Often precedes graduation or hiatus news |
| 重大発表 | major announcement | phrase | M · JWP | |
| 配信活動終了 | ended streaming activities | streamer | M · JWP-H | Softer than 卒業 |
| 生配信 | livestream | streamer | M · VQ | |
| スパチャ · スーパーチャット (2) | superchat | streamer | H · OAS, MC, PB | |
| 赤スパ | akasupa / red superchat | streamer | H · UB, VQ, VA | |
| ナイスパ | nice superchat | phrase | M · UB | |
| 投げ銭 | tips | streamer | H · MC, VQ | |
| 課金 | spending money | streamer | M · VQ | |
| 養分 | big spender (self-mocking) | streamer | M · VQ | |
| メンバーシップ | membership | streamer | H · OAS, VTD | |
| 同接 | concurrent viewers | streamer | H · UB, VQ | |
| 登録者数 | subscriber count | streamer | M · JWP | |
| 収益化 | monetization | streamer | M · JWP | |
| サムネ | thumbnail | streamer | H · OAS, PB | |
| マシュマロ | Marshmallow (anonymous questions) | streamer | M · VQ | |
| 配信タグ | stream hashtag | streamer | H · VQ, HO | |
| ファンアート · FA (2) | fan art | streamer | H/M · HO, JWP, VQ | |
| ファンネーム | fan name | streamer | H · HO | |
| 事務所 | agency | role | H · OAS, VQ | |
| 箱 | hako / agency | role | H · MC (+NN) | not_in ゴミ箱, 箱根, 箱庭, 段ボール箱, 筆箱, 宝箱, 重箱, 木箱, 箱入り |
| 企業勢 · 事務所勢 · 企業系VTuber (3) | corpo | role | H · MC, VQ, JWP | |
| 個人勢 · 個人系VTuber (2) | indie | role | H · MC, VQ, JWP | |
| ステルス企業勢 | stealth corpo | role | M · VQ | |
| ライバー · Vライバー · バーチャルライバー (3) | liver / streamer | role | H · OAS, MC, EWP-N | NIJISANJI calls its talents Livers |
| バーチャルYouTuber · Vの者 (2) | VTuber | role | H/M · WP, OAS, JWP | |
| 中の人 | nakanohito / the person inside | role | H · WP, VQ, MC | Never name or speculate about the real person |
| 魂 | soul (the performer) | role | H · MC (+NN) | not_in 魂胆, 霊魂, 商魂. Sensitive topic |
| ガワ | model (avatar) | role | H · MC, VQ | |
| 前世 | past life | role | H · UB, MC | Prior identity. Sensitive |
| 転生 | reincarnation | role | H · UB, MC | Relaunch as a new character |
| 卒業 | graduation | role | H · WP, JWP | Context: school graduation otherwise |
| 引退 | retirement | role | M · JWP | |
| 契約解除 | contract termination | role | M · JWP | Never "graduation" |
| 期生 | gen | role | H · PB | 3期生 → "3rd gen" |
| マネちゃん | mane-chan / manager | role | M · VQ | |
| ママ · パパ (2) | mama / character designer · papa / rigger | role | H · UB, MC | Context: the real parent otherwise |
| 絵師 | artist | role | M · UB | |
| 動画師 · MIX師 (2) | video editor · audio mixer | role | M · UB | |
| 立ち絵 | character art | streamer | M · UB, VQ | |
| バ美肉 | babiniku | streamer | H · WP, UB, MC | Follow the avatar's presented gender |
| 受肉 | getting a model ("incarnation") | streamer | M · UB | |
| 顔バレ · 身バレ (2) | face reveal · identity exposed | streamer | M · VQ | |
| エンジョイ勢 · ガチ勢 (2) | casual · tryhard | streamer | M · VQ | |
| VTuber準備中 | pre-debut VTuber | role | M · JWP | |
| バーチャルシンガー · Vシンガー (2) | virtual singer | role | M · JWP | |
| 兼業VTuber | part-time VTuber | role | M · JWP | |
| ご当地VTuber | regional VTuber | role | M · JWP | |
| 部位チューバー | body-part VTuber | role | M · JWP | Shows only hands or face over real footage |
| 超美麗3D | photorealistic 3D | streamer | M · JWP | |
| ニコ生主 | Niconico streamer | role | M · JWP | |
| 兼任 | also a member of | role | M · JWP-H | Two units at once |
| ロールプレイ | staying in character | streamer | M · VQ | |
| モーションキャプチャ | mocap | streamer | H · JWP, OAS | |

### A2. `ja-vtuber-names` — language `ja` — 42 entries, no styles

Title "Japanese VTuber names". Description "Starter pack, review it. Agency, unit and talent names, fan names and greetings, kept romanised. Re-check after any agency reorganisation."

Greeting and fan-name romanisations are Hepburn renderings of the official Japanese, which are kana only. They are not official English spellings unless marked. Every entry carries the note "keep, do not translate; third-person self-reference keeps the name".

| Source text (entries) | EN default | Category | Conf · Src | Note |
|---|---|---|---|---|
| ホロライブ · ホロライブプロダクション (2) | hololive · hololive production | streamer | H · WP, JWP-H, EWP-H | Brands merged 2026-09-07. Lowercase styling not verified on the post-merger site |
| ホロスターズ | HOLOSTARS | streamer | H · EWP-H, JWP-H | |
| ホロメン | Holomem | streamer | H · PB, JWP-H | |
| にじさんじ | NIJISANJI | streamer | H · WP, EWP-N | |
| ホロライブゲーマーズ | hololive GAMERS | streamer | H · EWP-H, JWP-H | |
| ホロライブファンタジー | hololive Fantasy (Gen 3) | streamer | H · EWP-H, JWP-H | |
| ホロふぉーす | holoForce (Gen 4) | streamer | H · EWP-H, JWP-H | |
| ねぽらぼ | NePoLaBo (Gen 5) | streamer | H · EWP-H, JWP-H | |
| 秘密結社holoX | Secret Society holoX | streamer | H · EWP-H, JWP-H | |
| 魔フィア | MaFia (HOLOSTARS Gen 3) | streamer | H · EWP-H, JWP-H | |
| 兎田ぺこら · ぺこら · ぺこーら (3) | Usada Pekora · Pekora · Pekora | streamer | H · HO, PB | ぺこーら is her self-name: keep as "Pekora", never "she" |
| 宝鐘マリン | Houshou Marine | streamer | H · HO, JWP-Ma | |
| 常闇トワ | Tokoyami Towa | streamer | H · HO | Self-styled 常闇トワ様 → "Towa-sama" |
| 月ノ美兎 | Tsukino Mito | streamer | H · JWP-M | Uses わたくし |
| 奏手イヅル | Kanade Izuru | streamer | H · EWP-H, JWP-H | |
| 野うさぎ同盟 | Nousagi Alliance | streamer | H · HO, PB | Pekora's fans. Do not add bare 野うさぎ (wild rabbit) |
| 兎田ふぁみりあ | Pekora Familia | streamer | M · PB | Membership name |
| 宝鐘の一味 | Houshou no Ichimi | streamer | M · HO | Marine's crew. Romanisation ours |
| 星詠み | Hoshiyomi | streamer | M · HO | Suisei's fans |
| ころねすきー | Koronesuki | streamer | M · HO | |
| すこん部 | Sukonbu | streamer | M · HO | |
| ミオファ | Miofa | streamer | M · HO | |
| おにぎりゃー | Onigiryaa | streamer | M · HO | |
| 常闇眷属 | Tokoyami Kenzoku | streamer | M · HO | |
| こよりの助手くん | Koyori's assistants (joshu-kun) | streamer | M · HO | |
| こんぺこ | Konpeko | phrase | H · HO, PB | |
| こんこんきーつね | Konkon kitsune | phrase | M · HO | Fubuki |
| おはこん · ひるこん · よるこん (3) | Ohakon · Hirukon · Yorukon | phrase | M · HO | Fubuki |
| おつこーん | Otsukon | phrase | M · HO | |
| こんばんみぉーん | Konbanmion | phrase | M · HO | Mio |
| こんまっする | Konmassuru | phrase | M · HO | Noel |
| こんやっぴー · おつやっぴー (2) | Konyappi · Otsuyappi | phrase | M · HO | Towa |
| こんこよ | Konkoyo | phrase | M · HO | Koyori |
| ぉぁよ · ゆびゆび (2) | Oayo · yubi yubi | phrase | M · HO | Korone |
| ヨーソロー | Yousoro | phrase | M · HO, JWP-Ma | Marine. Nautical "steady as she goes" |

### A3. `zh-vtuber-core` — language `zh` — 139 entries

Title "Chinese VTuber stream terms". Description "Starter pack, review it. Bilibili and Taiwan/HK YouTube VTuber vocabulary, Simplified and Traditional." Styles: default `fan`, with options `fan` ("Keep shared VTuber-English fan terms (oshi, teetee, nakanohito)") and `natural` ("Translate fan terms into plain English"). The renderings are proposals; the sources attest Chinese meanings only.

| Source text (entries) | EN default | Category | Conf · Src | Note / `not_in` |
|---|---|---|---|---|
| 草 | lol | interjection | M · NN\* | not_in 草莓, 草地, 草原, 草稿, 草案, 草率, 花草, 青草, 草坪, 草本, 稻草, 草根. Senses beyond "lol" not checked |
| DD | DD / follows many VTubers | streamer | H · NN\*, Moegirl snippet | |
| 单推 · 單推 (2) | single-oshi / supports only one VTuber | streamer | M · NN\* | |
| 箱推 | hako-oshi / fan of the whole agency | streamer | H · E, D, TK | |
| 贴贴 · 貼貼 (2) | teetee / so cute together | streamer | M · NN\* | |
| 白嫖 | freeloading | streamer | H · WOC (+Moegirl) | Vulgar literal sense. Self-mocking |
| 零充 | F2P | streamer | M · Moegirl snippet + WOC context | |
| XSWL · xswl (2) | LMAO | interjection | M · CD | Lowercase is a case variant |
| YYDS · yyds (2) | GOAT | interjection | M · CD | |
| WML | I can't even | interjection | M · CD | |
| NSDD | you're right (often sarcastic) | phrase | M · CD | |
| 栓Q | thank you (ironic) | phrase | M · CD | |
| 芭比Q了 | we're cooked | phrase | M · CD | |
| 整活 | doing a bit | streamer | H · E (+Moegirl) | |
| 毒唯 | toxic solo stan | streamer | H · D (+Zhihu) | |
| 唯粉 | solo stan | streamer | M · D (+Zhihu) | |
| 团偏 · 團偏† (2) | group fan with favorites | streamer | M · D | |
| 塌房 | fall from grace | streamer | H · W-AS (+hanspub) | Scandal collapses an image |
| 开盒 · 開盒† (2) | doxxing | streamer | H · W-AS (+Tencent, Baidu) | |
| 差不多得了 | OK, that's enough | phrase | M · W-AS | |
| 独轮车 · 獨輪車† (2) | spam raid | streamer | M · D | Literal unicycle otherwise |
| MMR · 萌萌人 (2) | kiddie stan | streamer | H · E, D | **Pejorative**: keep the sneer |
| v老嗨 | old-timer | streamer | M · E | Hostile-wiki source; neutral meaning |
| KY | can't read the room | phrase | M · D | |
| zqsg | genuinely | phrase | M · D | |
| 版本答案 | the meta | streamer | M · D | |
| Gachi | gachikoi | streamer | M · E | |
| 黑粉 | anti (hater) | streamer | M · E | |
| RP | staying in character | streamer | H · E, D | Uppercase only |
| 切片 | clip | streamer | H · NN\* (+Bilibili snippet) | not_in 切片机, 切片器 |
| 切片man | clipper | streamer | H · E (+Bilibili snippet) | |
| 烤肉Man | clip translator | streamer | M · NN\* | Bare 烤肉 (BBQ) excluded |
| 生肉 | raw (untranslated) | streamer | H · NN\* (+Moegirl) | Context: literal raw meat otherwise |
| 熟肉 | subbed version | streamer | H · NN\* (+Moegirl) | |
| 配信 | stream | streamer | M · NN\* | |
| 初配信 | debut stream | streamer | M · NN\* | |
| 杂谈 · 雜談 (2) | zatsudan / chat stream | streamer | M · NN\* | |
| 酒杂 · 酒雜 (2) | drinking chat stream | streamer | M · NN\* | |
| 歌回 · 歌枠 (2) | singing stream | streamer | M · NN\* | |
| 耐久 | endurance stream | streamer | M · NN\* | not_in 耐久性, 耐久力, 耐久度 |
| 同时视听 · 同時視聽 (2) | watchalong | streamer | M · NN\* | |
| 新装 · 新裝 (2) | new outfit (reveal) | streamer | M · NN\* | |
| 重大告知 | big announcement | phrase | M · NN\* | |
| 突待 · 凸待 (2) | totsumachi / call-in stream | streamer | M · NN\* | |
| 联动 · 聯動 (2) | collab | streamer | M · NN\* | Context: ordinary "linkage" in tech talk |
| 收益化 | monetization | streamer | M · NN\* | |
| 待机 · 待機 (2) | waiting for the stream | streamer | M · NN\* | Context: device standby otherwise |
| 初见 · 初見 (2) | first time here | phrase | M · NN\* | |
| 大航海 | guard membership | streamer | H · ZW, BC | Bilibili paid tiers |
| 舰长 · 艦長 (2) | Captain | streamer | H · BC (+Moegirl) | Tier 1. English label is a literal choice, not attested |
| 提督 | Admiral | streamer | H · BC (+Moegirl) | Tier 2 |
| 总督 · 總督 (2) | Governor | streamer | H · BC (+Moegirl) | Tier 3 |
| 上舰 · 上艦 (2) | bought a guard tier | streamer | M · BC | |
| 千舰 · 千艦 · 万舰 · 萬艦 (4) | 1,000 captains · 10,000 captains | streamer | M · ZW | |
| 粉丝勋章 · 粉絲勳章 · 粉丝牌 · 粉絲牌 (4) | fan medal | streamer | M · BC | |
| 电池 · 電池 (2) | batteries (Bilibili currency) | streamer | M · ZB | |
| 醒目留言 | superchat | streamer | H · E, D | |
| SC | superchat | streamer | H · NN\*, E | |
| 斗内 · 斗內 · 抖內 · 抖内† (4) | donation | streamer | H · uptogo, toy-people, mandarin-banana | Taiwan |
| 红地毯 · 紅地毯 (2) | red carpet (flood of red superchats) | streamer | M · NN\* | |
| 石油王 | oil baron | streamer | M · NN\* | Big spender |
| 虚拟主播 · 虛擬主播 (2) | VTuber | role | H · ZW | Shields zh-common's 主播 |
| 虚拟UP主 · 虛擬UP主 (2) | VTuber | role | H · ZW | |
| VUP · VTB (2) | VTuber | role | H · ZW | |
| 中之人 | nakanohito / the person behind the avatar | role | H · ZW, NN\* (+Moegirl) | Never name the real person |
| 魂 | soul (the performer) | role | H · ZW, NN\* | not_in 灵魂, 靈魂, 魂魄, 鬼魂 |
| 皮套 | avatar (model) | role | H · BP, NN\* | |
| 套皮 | avatar with no persona | role | H · ZW, NN\* | |
| 个人势 · 個人勢 (2) | indie | role | H · ZW, NN\* | |
| 企业势 · 企業勢 (2) | corpo | role | H · ZW, NN\* | |
| 社团势 · 社團勢 · 团体势 · 團體勢† (4) | circle-affiliated | role | H · ZW, NN\* | |
| 毕业 · 畢業 (2) | graduation | role | M · NN\*, ZW | Context: school otherwise |
| 前世 | past life | role | M · NN\* | Sensitive |
| 转生 · 轉生 (2) | reincarnation | role | M · NN\* | |
| 冲塔 · 衝塔 (2) | digging up their real identity | role | M · NN\* | Frowned on |
| 炎上 | flaming (controversy) | streamer | M · NN\* | |
| 独角兽 · 獨角獸 (2) | unicorn (possessive fan) | streamer | M · NN\* | |
| 解约 · 解約† (2) | contract terminated | role | H · W-VR, W-AS | Never "graduated" |
| 期生 | gen | role | M · W-VR | |
| 合约到期 · 合約到期† (2) | contract ended | role | M · W-VR | |
| 移籍 | transfer | role | M · W-VR | |
| 直播休眠 | streaming hiatus | role | M · W-AS | |
| 工商 | sponsored content | streamer | M · E | not_in 工商局, 工商银行, 工商銀行, 工商业 |
| 歌势 · 歌勢† · 游戏势 · 遊戲勢† · 杂谈势 · 雜談勢† (6) | singing VTuber · gaming VTuber · chatting VTuber | role | M · E | |
| 本音 | natural voice | streamer | M · D | |
| 超美丽3D · 超美麗3D† (2) | "ultra-beautiful 3D" (real-life camera) | streamer | M · E | Joke |
| 八美肉 | babiniku | streamer | M · E | 巴美肉 spelling unverified |
| 塔照 | IRL photo (of the performer) | streamer | M · E | Sensitive |

### A4. `zh-vtuber-names` — language `zh` — 35 entries, no styles

Title "Chinese VTuber names". Description "Starter pack, review it. A-SOUL, VirtuaReal and other names and fan names. Re-check after any agency change."

| Source text (entries) | EN default | Category | Conf · Src | Note |
|---|---|---|---|---|
| 向晚 | Ava | streamer | H · LF, W-AS | Also a poetic word ("toward evening") |
| 贝拉 · 貝拉† (2) | Bella | streamer | H · LF, W-AS | |
| 珈乐 · 珈樂† (2) | Carol | streamer | H · LF, W-AS | |
| 嘉然 | Diana | streamer | H · LF, W-AS | Pinyin "Jiaran" also seen |
| 乃琳 | Eileen | streamer | H · LF, W-AS | |
| 然然 · 晚晚 · 拉姐 (3) | Ranran† · Wanwan† · La-jie† (Bella) | streamer | M · D | Nicknames, pinyin machine-converted |
| 一个魂 · 一個魂† (2) | A-SOUL fans | streamer | M · D | |
| 嘉心糖 | Jiaxintang† (Diana's fans) | streamer | H · W-AS, D | |
| 贝极星 · 貝極星† (2) | Beijixing† (Bella's fans) | streamer | H · W-AS, D | |
| 奶淇琳 | Naiqilin† (Eileen's fans) | streamer | H · W-AS, D | |
| 顶碗人 · 頂碗人† (2) | Dingwanren† (Ava's fans) | streamer | H · W-AS, D | |
| 皇珈骑士 · 皇珈騎士† (2) | Huangjia Qishi† (Carol's fans) | streamer | H · W-AS, D | |
| 音乐珈 · 音樂珈† (2) | Yinyuejia† (Carol's fans, later badge) | streamer | H · W-AS, D | |
| 嘉晚饭 · 嘉晚飯† (2) | Diana × Ava (ship name) | streamer | M · D | |
| 乐华娱乐 · 樂華娛樂† (2) | Yuehua Entertainment | streamer | H · W-AS | |
| 枝江娱乐 · 枝江娛樂† (2) | Zhijiang Entertainment† | streamer | M · W-AS | A-SOUL operations team |
| 维阿 · 維阿† (2) | VirtuaReal | streamer | H · W-VR | |
| 彩虹社 · 2434 (2) | NIJISANJI | streamer | M · ZW | |
| 七海Nana7mi | Nana7mi | streamer | M · ZW | |
| 凑阿库娅 · 湊阿庫婭† (2) | Minato Aqua | streamer | M · E | |

**NOT shippable (own knowledge or unsourced) — kept out of every table above:** all Japanese "DROP for shipping" rows (待機, 初見, 朝活, 晩酌, お絵描き, 記念配信, 3Dライブ, 新衣装, 誕生日配信, 周年, 収益化記念, 公式番組, オフコラボ, 無期限活動休止, 同期, 公式, 高評価, コメ/チャット欄, 読み上げ, ギフト/メンギフ, 概要欄, 固定コメント, ネキ, 888 (ja), 炎上 (ja), 匂わせ, ナイス, 引用, 切り抜き師); おつぺこ; Sakamata Chloe and the other English names absent from the notes; the Chinese L candidate list (开播/下播, 录播, 同传, 歌切, 电台, 老板/谢谢老板, 爆金币, 狗叫, 舔狗, 老婆/老公, 事务所, 本家, 老东家, 同期, 前辈/后辈, 契约, 跑路, 洗白, 退圈, 挂人, 拉黑, 鸽/咕咕咕, 投喂, 灯牌, 小心心, 辣条, 恰饭, 本命, 团推, 路人, 绷不住了, 蚌埠住了, 名场面, 高能, 空耳, 鬼畜, 刷屏, 房管, 2333, awsl, 好耶, and the 自称 forms 本喵/朕/咱/人家/偶/本小姐); 巴美肉; 虚拟乐园.

## Appendix B — Needs a second source

| Term | Lang | Current evidence | What is missing |
|---|---|---|---|
| 待機所 ("waiting room") | ja | M, search summary only (SS) | Any fetched glossary |
| 活動休止 ("hiatus") | ja | M, MoguraVR snippet | Fetched source with the exact phrase |
| 社団勢 ("circle-affiliated") | ja | M, NOWnews snippet (JP notes) | Japanese source |
| 囁き ("whispering", ASMR) | ja | unverified | Any source |
| ぺこ (Pekora's sentence ending) | ja | pixiv snippet | Fetched source, and a subtitling practice for keeping it |
| 白銀聖騎士団 → "Shirogane Knights" | ja | official JP name; English is the researcher's rendering | Official or fan-standard English |
| 研修生 / mekPark, ASOBI★MAWARI-TAI! | ja | English Wikipedia only | Japanese attestation |
| 岸堂天真, 影山シエン, 水無世燐央, 花咲みやび, アルランディス, 律可 | ja | Japanese Wikipedia only; English names not in notes; member lists conflict | English romanisation and current status |
| ぶいすぽっ! / VSPO!, ネオポルテ / Neo-Porte, あおぎり高校 / Aogiri High School, ななしいんく / 774 inc. | ja | agency snippets only | Official sites for spelling and capitalisation |
| 888 (clapping) | zh | M, NOWnews | Mainland usage check (88 as "bye-bye" is unsourced own knowledge, so an ambiguity is suspected but not verified) |
| 老板 / 谢谢老板 (big tipper) | zh | search summaries only | Fetched source |
| 打call, 荧光棒, 应援 | zh | Moegirl snippets | Fetched source |
| 下次一定, 三连 | zh | Bilibili column snippet | Fetched source |
| 破防 | zh | H on two snippets (Baidu Baike, China Daily) | One fetched page |
| 摆烂, 抽象, 哈基米, 营业, 脱粉, 拉踩, 控评 | zh | snippets only (Zhihu, Moegirl, Baidu, People's Daily) | Fetched sources |
| 野生字幕君 | zh | Moegirl snippet | Fetched source |
| 易拉罐 | zh | esugou and Douban **conflict** ("newbie" vs "tone-deaf shill") | Third source to settle the meaning |
| flbk | zh | meaning only (Douban); expansion unknown | Expansion and second source |
| 杏人, 虹人 (hololive/NIJISANJI fans), crew (Aqua's fans), 天狗 (Mea's fans), 神乐Mea | zh | esugou only (hostile wiki) | Neutral source |
| 脆鲨 / 海子姐 (七海's fans / nickname) | zh | Wikipedia and Zhihu snippets | Fetched page |
| 星瞳 → "Xingtong", 春魚工作室 → "Spring Fish Studio", 子午計畫, 金V獎 | zh | name attested; English unverified or media-only | Official English names |
| EOE members (莞儿, 露早, 米诺, 虞莫, 柚恩) | zh | Moegirl snippets | Fetched source and statuses |

## Appendix C — Sourced but held back for matching or tone

These have H or M sources but would mis-fire under substring matching, or carry hostility the pack cannot convey. Ship them only after a whole-line match mode exists, or as title-glossary entries where the user knows the context. **Chinese single characters**: 典, 孝, 急, 乐, 批, 麻, 蚌, 赢, 推, 箱, 皮, 车, 寄, 烧, 塔. **Ordinary words in Chinese**: 彩虹, 烤肉, 充电, 礼物, 认知, 棉花糖, 混沌, 清楚, 发病, 发散, 指导, 钓鱼, 木马, 结晶, 出道, 聊天室. **Hostile-wiki pejoratives**: 底边, 梁木, 鲸落, 管人, 杏奴, 虹太监, 8u, 4v. **Ordinary words in Japanese**: 放送, 企画, 祭り, 鳴き声, 挨拶, 肉体, 親, 鯖, メンバー, アバター, 新人, ユニット, 委員長 (Mito's nickname), 起立/気をつけ (Mito's opening), bare 野うさぎ. **Duplicate collisions**: 妈妈/媽媽 and 爸爸 as "mama/papa" (`zh-address` wins as "Mom"/"Dad"); 先輩 (in `ja-address`).

## Appendix D — Source keys

Fetched (Japanese): WP [en.wikipedia VTuber](https://en.wikipedia.org/wiki/VTuber) · OAS [oas.ac.jp](https://www.oas.ac.jp/contents/blogs/2025/01/25/1144) · UB [unbereal](https://unbereal.co.jp/vtuberaudition/vtuber_terminology/) · VQ [vtuberquest](https://vtuberquest.com/8726) · MC [mamecolog](https://mamecolog.com/vtuber_terminology/) · PB [pastebin](https://pastebin.com/bAXxd0q1) · VA [valshia](https://note.com/valshia/n/n28778f2b8502?hl=en) · VTD [VTuber Dictionary](https://vtd.mdkj.dev/) · JWP [ja.wikipedia バーチャルYouTuber](https://ja.wikipedia.org/wiki/バーチャルYouTuber) · JWP-H [ja.wikipedia ホロライブプロダクション](https://ja.wikipedia.org/wiki/ホロライブプロダクション) · EWP-H [en.wikipedia Hololive Production](https://en.wikipedia.org/wiki/Hololive_Production) · EWP-N [en.wikipedia Nijisanji](https://en.wikipedia.org/wiki/Nijisanji) · HO `https://hololive.hololivepro.com/talents/<slug>/` (e.g. [Pekora](https://hololive.hololivepro.com/talents/usada-pekora/)) · JWP-M [月ノ美兎](https://ja.wikipedia.org/wiki/月ノ美兎) · JWP-Ma [宝鐘マリン](https://ja.wikipedia.org/wiki/宝鐘マリン).

Fetched or cited as fetched (Chinese): NN\* [NOWnews](https://www.nownews.com/news/6346908) · ZW [zh.wikipedia 虚拟YouTuber](https://zh.wikipedia.org/zh-cn/虚拟YouTuber) · ZB [zh.wikipedia 哔哩哔哩直播](https://zh.wikipedia.org/zh-cn/哔哩哔哩直播) · BC [Bilibili 大航海 column](https://www.bilibili.com/opus/553548467581752502) · BP [Bilibili 皮套 column](https://www.bilibili.com/opus/945440980414234656) · WOC [The World of Chinese](https://www.theworldofchinese.com/2021/11/going-for-broke-slang-for-the-cash-strapped/) · CD [China Daily](https://language.chinadaily.com.cn/a/202408/08/WS66b491a7a3104e74fddb91d7.html) · TK [ticket.co.jp](https://www.ticket.co.jp/entx/?p=10055) · LF [Last.fm A-SOUL](https://www.last.fm/zh/music/a-soul/%E8%B6%85%E7%BA%A7%E6%95%8F%E6%84%9F+-+Single) · E [esugou V圈黑话](https://esugou.wiki/index.php?title=%E6%A6%82%E5%BF%B5%2FV%E5%9C%88%E9%BB%91%E8%AF%9D) (hostile) · D [Douban A-SOUL glossary](https://www.douban.com/group/topic/228454620/) · W-AS [zh.wikipedia A-SOUL](https://zh.wikipedia.org/zh-cn/A-SOUL) · W-VR [zh.wikipedia VirtuaReal](https://zh.wikipedia.org/zh-cn/VirtuaReal) · 斗內 sources [uptogo](https://uptogo.com.tw/科技/斗內/), [toy-people](https://www.toy-people.com/?p=97254), [mandarin-banana](https://www.mandarin-banana.com/words/76956).

Snippet only (never sufficient alone): HW Hololive Fan Wiki, FDM fandom glossary, DG Dengeki, SS sas-vliver/名取さな wiki summary, MOG/MOG-G MoguraVR, AG agency snippets, MV MoguraVR hiatus piece, PXS pixiv, YK/SFP role-language papers, Moegirl, Baidu Baike, Zhihu, People's Daily, Tencent News, hanspub.
