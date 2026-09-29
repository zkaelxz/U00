import { useEffect, useState } from 'react'

import { deleteSeriesCharacter, listSeriesCharacters } from '../../../api/stageDeletes'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Section } from '../../../components/Section'
import type { SeriesCharacter } from '../../../types/autotuneGlossary'
import { ConfirmButton } from '../../../components/ConfirmButton'
import { PC_ONLY_DELETE_NOTE, usePcOnly } from '../../../hooks/usePcOnly'
import './seriesCast.css'

// Characters → "Series cast (n)": the series-level character list shared by
// every drama in the series; removing one is PC-only.
export function SeriesCast({ seriesId }: { seriesId: number }) {
  const [cast, setCast] = useState<SeriesCharacter[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [removeError, setRemoveError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const pc = usePcOnly()

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
    setRemoveError(null)
    setNotice(null)
    deleteSeriesCharacter(seriesId, c.id).then(
      () => {
        setError(null)
        setNotice(`Removed ${c.character_name} from the series.`)
        setCast((cur) => cur && cur.filter((x) => x.id !== c.id))
      },
      setRemoveError,
    )
  }

  // Nothing to show (not loaded, or no series characters): only a load error.
  if (!cast || cast.length === 0) return <ErrorBanner error={error} onDismiss={() => setError(null)} />

  return (
    <Section
      storageKey="translate.characters.series"
      title="Series cast"
      count={cast.length}
      summary="shared by every drama in the series"
    >
      <ul className="series-cast" data-testid="series-cast">
        {cast.map((c) => (
          <li key={c.id}>
            <span>
              <strong>{c.character_name}</strong>
              {c.pronouns && <span className="muted"> · {c.pronouns}</span>}
              {c.aliases && <span className="muted"> · also {c.aliases}</span>}
            </span>
            {pc === 'local' && (
              <ConfirmButton
                name={c.character_name}
                label="Remove…"
                confirmLabel={`Confirm remove ${c.character_name} from series`}
                onConfirm={() => remove(c)}
              />
            )}
          </li>
        ))}
      </ul>
      {pc === 'remote' && <p className="muted">{PC_ONLY_DELETE_NOTE}</p>}
      {notice && <p role="status">{notice}</p>}
      <ErrorBanner error={removeError} describe={{ pcOnly: true }} onDismiss={() => setRemoveError(null)} />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Section>
  )
}
