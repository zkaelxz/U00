import { describe, expect, it } from 'vitest'
import { ApiError } from '../../api/client'
import { defaultPrTitle, githubErrorText, githubNotReadyReason, githubReady } from './githubFormat'
import { KEY_WRITES_REFUSED } from '../../components/errorMessages'

const on = { enabled: true, repo: 'me/app', base_branch: 'baihe-subtitler', token_configured: true, branch_prefix: 'baihe-assistant/' }

describe('GitHub delivery helpers', () => {
  it('is ready only when on, with a token and a repo', () => {
    expect(githubReady(on)).toBe(true)
    expect(githubReady({ ...on, enabled: false })).toBe(false)
    expect(githubNotReadyReason({ ...on, token_configured: false })).toMatch(/token/)
    expect(githubNotReadyReason({ ...on, repo: null })).toMatch(/repository/)
    expect(githubNotReadyReason(on)).toBeNull()
  })
  it('builds a short title', () => {
    expect(defaultPrTitle('  why   dub skips ')).toBe('Fix: why dub skips')
    expect(defaultPrTitle('x'.repeat(300)).length).toBeLessThanOrEqual(120)
  })
  it('says key writes are off for a token 403', () => {
    const e = new ApiError(403, { code: 'forbidden', message: 'Not allowed' })
    expect(githubErrorText(e, true)).toBe(KEY_WRITES_REFUSED)
  })
})
