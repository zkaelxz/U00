import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import FollowAlongPage from './FollowAlong'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  api: { getDrama: vi.fn(async () => ({ id: 3, title_en: 'Demo', title_zh: '' })) },
}))
vi.mock('../reader/ReadAlong', () => ({ loadRows: vi.fn(async () => []) }))

describe('FollowAlongPage', () => {
  it('opens paused with the clock controls and a way back to the title', () => {
    const out = renderToStaticMarkup(<FollowAlongPage id={3} />)
    expect(out).toContain('Follow along')
    expect(out).toContain('>Start<')
    for (const label of ['−5 s', '−0.5 s', '+0.5 s', '+5 s']) expect(out).toContain(label)
    expect(out).toContain('0:00')
    expect(out).toContain('href="#/drama/3"')
  })
})
