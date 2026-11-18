import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { MisheardNote } from './MisheardNote'

vi.mock('../StageContext', () => ({ useStage: () => ({ dramaId: 7 }) }))
vi.mock('../../../api/misheard', () => ({ listMisheard: vi.fn(() => new Promise(() => {})) }))

describe('the mis-heard note on Transcribe', () => {
  it('shows nothing until the check has found words', () => {
    expect(renderToStaticMarkup(<MisheardNote busy={false} />)).toBe('')
  })
})
