import { useEffect, useState } from 'react'

import { deleteSeriesCharacter, listSeriesCharacters } from '../../../api/stageDeletes'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Section } from '../../../components/Section'
import type { SeriesCharacter } from '../../../types/autotuneGlossary'
import { ConfirmButton } from '../pcOnly/ConfirmButton'
import { PC_ONLY_NOTE, reportPcOnlyError, usePcOnly } from '../pcOnly/pcOnly'
import '../pcOnly/pcOnly.css'

// Characters → "Series cast (n)": the series-level character list shared by
// every drama in the series; removing one is PC-only.
export function SeriesCast({ seriesId }: { seriesId: number }) {
  const [cast, setCast] = useState<SeriesCharacter[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const isLocal = usePcOnly()

  useEffect(() => {
    let cancelled = false
    listSeriesCharacters(seriesId).then(
      (c) => !cancelled && setCast(c),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [seriesId])

  const remove = (c: SeriesCharacter) => {
    setProblem(null)
    setNotice(null)
    deleteSeriesCharacter(seriesId, c.id).then(
      () => {
        setError(null)
        setNotice(`Removed ${c.character_name} from the series.`)
        setCast((cur) => cur && cur.filter((x) => x.id !== c.id))
      },
      (e: unknown) => reportPcOnlyError(e, setProblem, setError),
    )
  }

  if (!cast) return <ErrorBanner error={error} onDismiss={() => setError(null)} />

  return (
    <Section
      storageKey="translate.characters.series"
      title="Series cast"
      count={cast.length}
      summary={cast.length ? 'shared by every drama in the series' : 'none yet'}
    >
      {cast.length === 0 ? (
        <p className="muted">No series characters yet.</p>
      ) : (
        <ul className="series-cast" data-testid="series-cast">
          {cast.map((c) => (
            <li key={c.id}>
              <span>
                <strong>{c.character_name}</strong>
                {c.pronouns && <span className="muted"> · {c.pronouns}</span>}
                {c.aliases && <span className="muted"> · also {c.aliases}</span>}
              </span>
              {isLocal && (
                <ConfirmButton
                  label="Remove"
                  ariaLabel={`Remove ${c.character_name} from series`}
                  confirmLabel={`Confirm remove ${c.character_name} from series`}
                  onConfirm={() => remove(c)}
                />
              )}
            </li>
          ))}
        </ul>
      )}
      {isLocal === false && cast.length > 0 && <p className="muted">{PC_ONLY_NOTE}</p>}
      {notice && <p role="status">{notice}</p>}
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Section>
  )
}
