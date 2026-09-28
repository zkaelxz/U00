import { useEffect, useState } from 'react'

import { api, ApiError } from '../api/client'
import type { DramaDetail } from '../api/types'

function credit(name: string | null, romanized: string | null) {
  if (!name) return null
  return romanized && romanized !== name ? `${name} (${romanized})` : name
}

export function DramaDetailPanel({ dramaId }: { dramaId: number }) {
  const [drama, setDrama] = useState<DramaDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // App remounts this panel per drama (key={dramaId}), so state starts
    // fresh for each one instead of being reset here.
    let cancelled = false
    api
      .getDrama(dramaId)
      .then((d) => !cancelled && setDrama(d))
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : 'Unexpected error.')
      })
    return () => {
      cancelled = true
    }
  }, [dramaId])

  if (error) return <section className="panel"><p className="error" role="alert">{error}</p></section>
  if (!drama) return <section className="panel"><p className="muted">Loading…</p></section>

  const rows: [string, string | number | null][] = [
    ['Original title', drama.title_zh],
    ['Author', credit(drama.author, drama.author_romanized)],
    ['Studio', credit(drama.studio, drama.studio_romanized)],
    ['Voice actors', credit(drama.voice_actors, drama.voice_actors_romanized)],
    ['Status', drama.status],
    ['Type', drama.media_type],
    ['Language', drama.source_language],
    ['Genre', drama.genre],
    ['Chapters', drama.chapter_count],
    ['Translation engine', drama.translation_engine],
    ['Source media', drama.has_audio ? 'attached' : 'none'],
  ]

  return (
    <section className="panel" aria-labelledby="detail-heading">
      <h2 id="detail-heading">{drama.title_en || drama.title_zh || `Drama #${drama.id}`}</h2>
      {drama.summary && <p>{drama.summary}</p>}
      <dl>
        {rows
          .filter(([, v]) => v !== null && v !== '')
          .map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
      </dl>
    </section>
  )
}
