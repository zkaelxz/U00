/*
 * Settings > Advanced > Keys file and environment: read-only list of the settings that can only be
 * changed in the .env file or Windows' environment, each shown as set or not set. The server never
 * sends a value. PC only: the route is local_only, so the page leaves the card out elsewhere.
 */
import { useEffect, useState } from 'react'

import { getKeysFileStatus } from '../../api/settings'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { KEYS_FILE_NOTE, KEYS_FILE_TITLE, groupKeysFile, keysFileSummary, type KeysFileRow } from './keysFileModel'

export function KeysFileCard() {
  const [rows, setRows] = useState<KeysFileRow[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let live = true
    getKeysFileStatus().then(
      (r) => live && setRows(r),
      (e: unknown) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [])

  return (
    <Card title={KEYS_FILE_TITLE} aria-label={KEYS_FILE_TITLE} meta={rows ? keysFileSummary(rows) : undefined}>
      <p className="muted">{KEYS_FILE_NOTE}</p>
      {error != null && <ErrorBanner error={error} />}
      {rows === null && error == null && <p className="muted">Loading…</p>}
      {rows &&
        groupKeysFile(rows).map((g) => (
          <section key={g.group}>
            <p><strong>{g.group}</strong></p>
            <ul>
              {g.rows.map((r) => (
                <li key={r.name}>
                  <code>{r.name}</code> {r.label}
                  {!r.from_file && <span className="muted"> (Windows only)</span>}{' '}
                  <Badge tone={r.set ? 'ok' : 'neutral'}>{r.set ? 'Set' : 'Not set'}</Badge>
                </li>
              ))}
            </ul>
          </section>
        ))}
    </Card>
  )
}
