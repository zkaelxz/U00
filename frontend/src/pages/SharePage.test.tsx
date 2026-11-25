import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SharePage from './SharePage'

const holds = vi.hoisted(() => ({ importUrl: true }))
vi.mock('../hooks/useHolds', () => ({ useHolds: () => holds.importUrl }))

const LINK = 'https://site.example/watch?v=1'
const html = (url: string | null) => renderToStaticMarkup(<SharePage url={url} />)

afterEach(() => {
  holds.importUrl = true
  vi.unstubAllGlobals()
})

describe('SharePage', () => {
  it('offers subtitling, Live and Sources for a shared link', () => {
    const out = html(LINK)
    expect(out).toContain(`href="#/make-subtitles?url=${encodeURIComponent(LINK)}"`)
    expect(out).toContain(`href="#/live?url=${encodeURIComponent(LINK)}"`)
    expect(out).toContain(`href="#/sources?url=${encodeURIComponent(LINK)}"`)
    expect(out).not.toContain('baihegrab://')
  })

  it('leaves out what a member without Import from a link cannot do', () => {
    holds.importUrl = false
    const out = html(LINK)
    expect(out).not.toContain('#/make-subtitles')
    expect(out).not.toContain('#/live')
    expect(out).toContain('#/sources?url=')
  })

  it('offers Quick read only inside the phone app', () => {
    vi.stubGlobal('navigator', { userAgent: 'Mozilla/5.0 BaiheAndroid/1200 (2026-11-25)' })
    expect(html(LINK)).toContain(`href="baihegrab://quickread?url=${encodeURIComponent(LINK)}"`)
  })

  it('says so when no link came with the share', () => {
    expect(html(null)).toContain('No web link was found')
  })
})
