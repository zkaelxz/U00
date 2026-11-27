import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { remainingSeconds, UploadProgress } from './UploadProgress'

describe('UploadProgress', () => {
  it('shows percent, bytes and that it is still sending', () => {
    const html = renderToStaticMarkup(<UploadProgress sent={{ loaded: 500_000_000, total: 2_000_000_000 }} />)
    expect(html).toContain('25%')
    expect(html).toContain('Still sending')
  })
  it('says the PC is saving once everything is sent, and waits quietly before the first update', () => {
    expect(renderToStaticMarkup(<UploadProgress sent={{ loaded: 10, total: 10 }} />)).toContain('saving the file')
    expect(renderToStaticMarkup(<UploadProgress sent={null} />)).toContain('Starting the upload')
  })
  it('estimates time left from the speed so far, and not before a few seconds of data', () => {
    const sent = { loaded: 100, total: 400 }
    expect(remainingSeconds(sent, 2000)).toBeNull()
    expect(remainingSeconds(sent, 10_000)).toBe(30)
    expect(remainingSeconds({ loaded: 400, total: 400 }, 10_000)).toBeNull()
  })
})
