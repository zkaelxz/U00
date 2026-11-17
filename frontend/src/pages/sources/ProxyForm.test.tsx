import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { SourcesSettings } from '../../types/sources'
import { ProxyForm } from './ProxyForm'

describe('ProxyForm', () => {
  it('says the steps that open a page in the browser do not use the proxy', () => {
    const html = renderToStaticMarkup(<ProxyForm settings={{ proxy_configured: false } as SourcesSettings} onSaved={() => {}} />)
    expect(html).not.toMatch(/Every source request/)
    expect(html).toMatch(/browser/)
  })
})
