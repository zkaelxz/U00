import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { emptyAssForm } from '../exportForm'
import { ExportSubtitles } from './ExportSubtitles'

vi.mock('../StageContext', () => ({
  useStage: () => ({ dramaId: 7, drama: { title_en: 'Sample', title_zh: '' }, refetchDrama: () => {} }),
}))

const render = (soundLines: number | null) =>
  renderToStaticMarkup(
    <ExportSubtitles fmt="srt" setFmt={() => {}} form={emptyAssForm('')} setForm={() => {}} options={null} totalLines={5} soundLines={soundLines} />,
  )

describe('the Sound captions option', () => {
  it('shows with the count only when tagged lines would get a caption', () => {
    expect(render(3)).toContain('Start the English of 3 lines')
    expect(render(0)).not.toContain('Sound captions')
    expect(render(null)).not.toContain('Sound captions')
  })
})
