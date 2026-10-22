import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'

import type { AuthMe } from '../../../api/auth'
import { resetPcModeForTests } from '../../../api/pcOnly'
import { resetSessionForTests } from '../../../hooks/useSession'
import { SubtitleImport } from './SubtitleImport'

const render = () => renderToStaticMarkup(<SubtitleImport dramaId={1} onImported={() => {}} onRealignStarted={() => {}} />)

const signedIn = (permissions: string[]): AuthMe => ({
  auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet',
  user: { id: 1, email: null, display_name: 'Admin', is_admin: true, is_local_owner: false },
  permissions,
})

afterEach(() => {
  resetPcModeForTests()
  resetSessionForTests()
})

describe('SubtitleImport away from the PC', () => {
  it('renders nothing instead of a file picker', () => {
    resetPcModeForTests('remote')
    resetSessionForTests({ status: 'ready', me: signedIn(['library.read', 'lines.edit']) })
    expect(render()).toBe('')
  })

  it('shows the file picker while the PC allows uploads from away', () => {
    resetPcModeForTests('remote')
    resetSessionForTests({ status: 'ready', me: signedIn(['library.read', 'media.upload']) })
    expect(render()).toContain('type="file"')
  })

  it('keeps the file picker on the PC', () => {
    resetPcModeForTests('local')
    const html = render()
    expect(html).toContain('type="file"')
  })
})
