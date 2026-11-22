/** The one-line version and release date shown at the top of Settings and in Diagnostics, on every device. */
import { useEffect, useState } from 'react'

import { getAppVersion } from '../api/update'
import type { AppVersionInfo } from '../types/update'
import { appVersionText, phoneAppText } from './appVersionText'

export function AppVersionLine() {
  const [info, setInfo] = useState<AppVersionInfo | null>(null)
  useEffect(() => {
    let live = true
    // A missing line is no loss (an older server, a member without access): no error banner.
    getAppVersion().then((v) => { if (live) setInfo(v) }, () => undefined)
    return () => { live = false }
  }, [])
  const text = info ? appVersionText(info) : null
  const phone = typeof navigator === 'undefined' ? null : phoneAppText(navigator.userAgent)
  if (!text && !phone) return null
  // Two lines in the phone app: joined, the two versions wrapped into four lines on a 360px screen.
  return (
    <p className="page-meta" data-testid="app-version-line">
      {text}
      {text && phone ? <br /> : null}
      {phone}
    </p>
  )
}
