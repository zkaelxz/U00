/** The one-line version and release date shown at the top of Settings and in Diagnostics, on every device. */
import { useEffect, useState } from 'react'

import { getAppVersion } from '../api/update'
import type { AppVersionInfo } from '../types/update'

// A fixed month list rather than toLocaleDateString: browsers differ on "Sep" and "Sept".
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2026-11-22" as "22 Nov 2026"; null for anything else. */
export function shortDate(iso: string | null | undefined): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? '')
  const month = m ? MONTHS[Number(m[2]) - 1] : undefined
  return m && month ? `${Number(m[3])} ${month} ${m[1]}` : null
}

export function appVersionText(info: AppVersionInfo): string | null {
  if (!info.version) return null
  if (info.source_copy) {
    const when = shortDate(info.commit_date)
    return `Baihe ${info.version} · source copy${when ? `, ${when}` : ''}`
  }
  const released = shortDate(info.released)
  return `Baihe ${info.version}${released ? ` · released ${released}` : ''}`
}

/** The phone app adds "BaiheAndroid/<build> (<build date>)" to its WebView's user agent (android/app MainActivity). */
export function phoneAppText(userAgent: string): string | null {
  const m = /\bBaiheAndroid\/(\d+)(?: \((\d{4}-\d{2}-\d{2})\))?/.exec(userAgent)
  if (!m) return null
  const built = shortDate(m[2])
  return `Phone app build ${m[1]}${built ? ` · built ${built}` : ''}`
}

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
