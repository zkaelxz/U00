import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import type { ReviewLine } from '../../../../types/review'

const state = { pc: 'local' as 'local' | 'remote' | 'unknown' }
vi.mock('../../../../hooks/usePcOnly', () => ({ usePcOnly: () => state.pc }))

import { HardSubCaseButton } from './HardSubCaseButton'

const line = { id: 7, idx: 0, zh: '你好吗', en: 'Hi' } as ReviewLine
const html = () => renderToStaticMarkup(<HardSubCaseButton dramaId={1} line={line} />)

describe('HardSubCaseButton', () => {
  it('is the one named action, enabled, on the PC', () => {
    const out = html()
    expect(out).toContain('Add a hard-sub test case')
    expect(out).not.toContain('disabled')
  })

  it('shows while the PC is not yet confirmed (the server enforces)', () => {
    state.pc = 'unknown'
    expect(html()).toContain('Add a hard-sub test case')
  })

  it('is not rendered at all on a remote page', () => {
    state.pc = 'remote'
    expect(html()).toBe('')
  })
})
