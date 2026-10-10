import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'

import { resetPcModeForTests } from '../../../api/pcOnly'
import { PC_ONLY_BODY } from '../../../hooks/usePcOnly'
import { SubtitleImport } from './SubtitleImport'

const render = () => renderToStaticMarkup(<SubtitleImport dramaId={1} onImported={() => {}} onRealignStarted={() => {}} />)

afterEach(() => resetPcModeForTests())

describe('SubtitleImport away from the PC', () => {
  it('shows the PC-only note instead of a file picker', () => {
    resetPcModeForTests('remote')
    const html = render()
    expect(html).toContain(PC_ONLY_BODY)
    expect(html).not.toContain('type="file"')
  })

  it('keeps the file picker on the PC', () => {
    resetPcModeForTests('local')
    const html = render()
    expect(html).toContain('type="file"')
    expect(html).not.toContain(PC_ONLY_BODY)
  })
})
