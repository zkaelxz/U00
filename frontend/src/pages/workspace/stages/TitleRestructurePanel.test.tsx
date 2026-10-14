import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'

import { resetPcModeForTests } from '../../../api/pcOnly'
import { PC_ONLY_BODY } from '../../../hooks/usePcOnly'
import { TitleRestructurePanel } from './TitleRestructurePanel'

afterEach(() => resetPcModeForTests())

describe('Merge or move chapters', () => {
  it('is a PC-only block away from the PC', () => {
    resetPcModeForTests('remote')
    const html = renderToStaticMarkup(<TitleRestructurePanel />)
    expect(html).toContain('Merge or move chapters')
    expect(html).toContain(PC_ONLY_BODY)
  })

  it('starts closed on the PC and loads nothing until opened', () => {
    resetPcModeForTests('local')
    const html = renderToStaticMarkup(<TitleRestructurePanel />)
    expect(html).toContain('Merge or move chapters')
    expect(html).not.toContain('title-restructure"')
    expect(html).not.toContain(PC_ONLY_BODY)
  })
})
