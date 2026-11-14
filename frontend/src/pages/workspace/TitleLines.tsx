/*
 * The Simple page once a title has lines: a progress bar and the first few lines, so the title opens on its text.
 */
import { useEffect, useState } from 'react'

import { listLines } from '../../api/review'
import type { ReviewLine } from '../../types/review'
import { ButtonLink } from '../../components/Button'
import { routeHref } from '../../router'
import './titleLines.css'

const PREVIEW_LINES = 8

export function TitleProgress({ total, translated }: { total: number; translated: number }) {
  const pct = total > 0 ? Math.round((translated / total) * 100) : 0
  return (
    <div className="title-progress">
      <div className="title-progress-bar" role="progressbar" aria-label="Lines translated" aria-valuemin={0} aria-valuemax={total} aria-valuenow={translated}>
        <span style={{ width: `${pct}%` }} />
      </div>
      <p className="title-progress-text">{translated} of {total} lines translated</p>
    </div>
  )
}

export function TitleLines({ id, total }: { id: number; total: number }) {
  const [lines, setLines] = useState<ReviewLine[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    listLines(id, 1, PREVIEW_LINES, 'all').then(
      (r) => !cancelled && setLines(r.lines),
      () => !cancelled && setFailed(true),
    )
    return () => {
      cancelled = true
    }
  }, [id, total])

  return (
    <section className="panel title-lines" aria-label="First lines" aria-busy={lines === null && !failed}>
      <h2 className="source-subhead">First lines</h2>
      {failed && <p className="muted">The lines could not be loaded. Open Review to see them.</p>}
      <ol className="title-lines-list">
        {(lines ?? []).map((l) => (
          <li key={l.id} className="title-line">
            <span className="title-line-orig" lang="zh">{l.zh}</span>
            <span className="title-line-en">{l.en || <span className="muted">Not translated yet</span>}</span>
          </li>
        ))}
      </ol>
      <div className="actions">
        <ButtonLink href={routeHref({ name: 'drama', id, stage: 'review', focus: 'all' })}>{`Open all ${total} lines`}</ButtonLink>
      </div>
    </section>
  )
}
