/** Tells Traditional from Simplified Chinese text, so it can carry zh-Hant or zh-Hans and get that script's fonts and punctuation. */

// Common characters the two scripts write differently, Traditional then Simplified. Characters both scripts use
// (后, 里, 台, 只) are left out: they say nothing about which one a text is in.
const PAIRS = '這这們们個个來来說说會会對对國国時时麼么還还過过為为發发學学開开關关問问見见長长現现經经樣样點点題题'
  + '話话將将與与從从頭头車车東东書书無无認认義义實实當当應应變变電电聽听讓让顯显號号體体灣湾語语親亲愛爱戀恋'
  + '聲声劇剧讀读員员網网視视錄录歡欢給给嗎吗謝谢邊边錯错覺觉夢梦氣气歲岁寫写買买賣卖誰谁該该進进處处動动種种'
  + '間间隊队帶带師师媽妈樂乐臉脸'
const HANT = new Set<string>()
const HANS = new Set<string>()
for (let i = 0; i < PAIRS.length; i += 2) {
  HANT.add(PAIRS[i])
  HANS.add(PAIRS[i + 1])
}

const HAN = /[\u3400-\u4dbf\u4e00-\u9fff]/

/** zh-Hant or zh-Hans by which script's own characters the text uses more; null when it shows neither. */
export function hanScript(text: string): 'zh-Hant' | 'zh-Hans' | null {
  let hant = 0
  let hans = 0
  for (const ch of text) {
    if (HANT.has(ch)) hant++
    else if (HANS.has(ch)) hans++
  }
  return hant > hans ? 'zh-Hant' : hans > hant ? 'zh-Hans' : null
}

/** A Chinese `lang` narrowed to the text's own script; text that shows none keeps a lang that names one, and plain
 *  zh takes `fallback` (the title's) when that is Chinese. Other languages pass through. */
export function chineseLang(lang: string, text: string | null | undefined, fallback = 'zh'): string {
  if (lang !== 'zh' && !lang.startsWith('zh-')) return lang
  return hanScript(text ?? '') ?? (lang !== 'zh' ? lang : fallback.startsWith('zh') ? fallback : 'zh')
}

type TitleText = {
  source_language?: string | null
  title_zh?: string | null
  title_en?: string | null
  summary?: string | null
  author?: string | null
}

/** The `lang` of a title's source text: Chinese titles take the script their name, summary and author are written in. */
export function titleTextLang(d: TitleText): string {
  const lang = d.source_language || 'zh'
  return chineseLang(lang, [d.title_zh, d.title_en, d.author, d.summary?.slice(0, 2000)].filter(Boolean).join(' '))
}

/** The `lang` of a shown title name: kana or hangul name their language; none for a Latin name, so it keeps the page's English. */
export function nameLang(name: string, d: TitleText): string | undefined {
  if (/[\u3040-\u30ff]/.test(name)) return 'ja'
  if (/[\uac00-\ud7af]/.test(name)) return 'ko'
  if (!HAN.test(name)) return undefined
  return chineseLang(d.source_language || 'zh', name, titleTextLang(d))
}
