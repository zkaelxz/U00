import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { SeriesChapter } from '../../types/sources'
import { ImportBar } from './ChapterImport'
import type { ChapterImportState } from './useChapterImport'

const chapters = [{ chapter_id: 'c1', title: 'One' }] as unknown as SeriesChapter[]
const bar = (imp: Partial<ChapterImportState>) =>
  renderToStaticMarkup(
    <ImportBar imp={{ dramaId: 4, running: false, start: () => {}, ...imp } as unknown as ChapterImportState}
      chapters={chapters} selected={['c1']} phone={false} />,
  )

describe('ImportBar', () => {
  it('offers Translate after import, ticked when it is on', () => {
    const html = bar({ translateAfter: true })
    expect(html).toContain('Translate after import')
    expect(html).toContain('checked')
  })

  it('leaves the tick unchecked when it is off', () => {
    expect(bar({ translateAfter: false })).not.toContain('checked')
  })
})
