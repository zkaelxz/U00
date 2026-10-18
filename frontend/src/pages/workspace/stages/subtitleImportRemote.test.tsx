import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'

import { resetPcModeForTests } from '../../../api/pcOnly'
import { SubtitleImport } from './SubtitleImport'

const render = () => renderToStaticMarkup(<SubtitleImport dramaId={1} onImported={() => {}} onRealignStarted={() => {}} />)

afterEach(() => resetPcModeForTests())

describe('SubtitleImport away from the PC', () => {
  it('renders nothing instead of a file picker', () => {
    resetPcModeForTests('remote')
    expect(render()).toBe('')
  })

  it('keeps the file picker on the PC', () => {
    resetPcModeForTests('local')
    const html = render()
    expect(html).toContain('type="file"')
  })
})
