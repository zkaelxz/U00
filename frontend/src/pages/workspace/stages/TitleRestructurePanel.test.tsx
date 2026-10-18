import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'

import { resetPcModeForTests } from '../../../api/pcOnly'
import { TitleRestructurePanel } from './TitleRestructurePanel'

afterEach(() => resetPcModeForTests())

describe('Merge or move chapters', () => {
  it('is left out away from the PC', () => {
    resetPcModeForTests('remote')
    expect(renderToStaticMarkup(<TitleRestructurePanel />)).toBe('')
  })

  it('starts closed on the PC and loads nothing until opened', () => {
    resetPcModeForTests('local')
    const html = renderToStaticMarkup(<TitleRestructurePanel />)
    expect(html).toContain('Merge or move chapters')
    expect(html).not.toContain('title-restructure"')
  })
})
