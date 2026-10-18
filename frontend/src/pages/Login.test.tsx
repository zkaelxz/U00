import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import LoginPage from './Login'

describe('full-page Login', () => {
  it('signs in in the same tab: a plain link with no new-tab target', () => {
    const html = renderToStaticMarkup(<LoginPage configured />)
    expect(html).toContain('Sign in with Google')
    expect(html).toContain('/api/auth/login')
    expect(html).not.toContain('target="_blank"')
    expect(html).not.toContain('(new tab)')
  })

  it('does not claim a failed recheck before one has run', () => {
    expect(renderToStaticMarkup(<LoginPage configured />)).not.toContain('Still not signed in')
  })
})
