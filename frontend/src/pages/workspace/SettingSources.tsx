/*
 * "Where settings come from" on a title: each setting's value and whether it is the title's own, the series default's or the Settings default.
 */
import { useEffect, useState } from 'react'

import { getTitleSettingSources, type TitleSettingSources } from '../../api/series'
import { sourceRows } from './settingSources'

export function SettingSources({ id }: { id: number }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<TitleSettingSources | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!open) return
    let cancelled = false
    getTitleSettingSources(id).then((d) => !cancelled && setData(d), () => !cancelled && setFailed(true))
    return () => {
      cancelled = true
    }
  }, [open, id])
  return (
    <details className="setting-sources" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Where settings come from</summary>
      {failed && <p className="title-panel-note">The settings could not be read.</p>}
      {data && (
        <>
          <p className="title-panel-note">
            A series default or a profile is copied onto the title when it is applied. A run uses the title&apos;s own value, or the Settings value when the title has none.
          </p>
          <dl className="setting-sources-list">
            {sourceRows(data).map((r) => (
              <div key={r.key}><dt>{r.label}</dt><dd>{r.value} · {r.source}</dd></div>
            ))}
          </dl>
        </>
      )}
    </details>
  )
}
