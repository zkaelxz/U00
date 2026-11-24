// The "already in your Library" warning under an add form's title and link fields.
import { useEffect, useState } from 'react'

import { sameTitles } from '../api/library'
import { routeHref } from '../router'
import type { SameTitle } from '../types/library'

// Long enough that typing a name asks once, not per letter.
const ASK_AFTER_MS = 400

interface Props {
  titleEn?: string
  titleZh?: string
  link?: string
  // Off while the form is reusing a title it already made, which would match itself.
  off?: boolean
}

export function SameTitleList({ found }: { found: SameTitle[] }) {
  if (found.length === 0) return null
  return (
    <div className="same-title-note" role="status" data-testid="same-title-note">
      <strong>Already in your Library:</strong>
      <ul>
        {found.map((t) => (
          <li key={t.id}>
            <a href={routeHref({ name: 'drama', id: t.id, stage: null })}>{t.title || 'Untitled'}</a>{' '}
            {t.same === 'link' ? 'has the same link' : 'has the same name'}.
          </li>
        ))}
      </ul>
      <span className="muted">You can still add it as a separate title.</span>
    </div>
  )
}

/** Warns without blocking: the add button still works, since two titles may share a name on purpose. */
export function SameTitleNote({ titleEn = '', titleZh = '', link = '', off = false }: Props) {
  // Kept with the question it answers, so an answer to an earlier spelling is never shown.
  const [answer, setAnswer] = useState<{ asked: string; items: SameTitle[] }>({ asked: '', items: [] })
  const title_en = titleEn.trim()
  const title_zh = titleZh.trim()
  const source_url = link.trim()
  const asked = off || !(title_en || title_zh || source_url) ? '' : JSON.stringify([title_en, title_zh, source_url])

  useEffect(() => {
    if (!asked) return
    let stale = false
    const timer = window.setTimeout(() => {
      // A failed check shows nothing: the warning is a courtesy and must not get in the way of adding.
      sameTitles({ title_en, title_zh, source_url }).then(
        (r) => { if (!stale) setAnswer({ asked, items: r.items }) },
        () => undefined,
      )
    }, ASK_AFTER_MS)
    return () => {
      stale = true
      window.clearTimeout(timer)
    }
  }, [asked, title_en, title_zh, source_url])

  return <SameTitleList found={asked && answer.asked === asked ? answer.items : []} />
}
