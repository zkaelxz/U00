import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { DramaDetail } from '../../../../api/types'
import type { ReviewLine } from '../../../../types/review'
import { StageContext } from '../../StageContext'
import { MergeConfirm } from './MergeConfirm'

const line = (id: number, zh: string): ReviewLine => ({
  id, idx: id, start: id, end: id + 1, zh, en: '', speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null,
})

function preview(sourceLanguage: string | null) {
  const merge = <MergeConfirm run={[line(1, '今日'), line(2, '晴れ')]} busy={false} blocked={null} onMerge={() => {}} onCancel={() => {}} />
  const drama = { source_language: sourceLanguage } as DramaDetail
  return renderToStaticMarkup(
    <StageContext.Provider value={{ dramaId: 1, drama, refetchDrama: () => {}, onJobDone: () => {} }}>{merge}</StageContext.Provider>,
  )
}

describe("source text in Review's side panels", () => {
  it("takes the title's language, so Japanese kanji get Japanese glyph forms", () => {
    expect(preview('ja')).toContain('<div lang="ja">今日晴れ</div>')
  })

  it('falls back to Chinese when the title has no language saved', () => {
    expect(preview(null)).toContain('lang="zh"')
  })
})
