/** The engines, titles and series the Live form offers, read once on open. */
import { useEffect, useState } from 'react'

import { api } from '../../api/client'
import { getAllSeries } from '../../api/library'
import { translateApi } from '../../api/translate'
import type { TranslateEngine } from '../../types/translate'

export function useLiveChoices() {
  // Titles and series the server lets this user see; null until read. A failed read leaves the picker empty.
  const [titles, setTitles] = useState<{ id: number; name: string; series_id: number | null }[] | null>(null)
  const [seriesList, setSeriesList] = useState<{ id: number; name: string }[] | null>(null)
  const [engines, setEngines] = useState<TranslateEngine[] | null>(null)
  const [enginesFailed, setEnginesFailed] = useState(false)

  useEffect(() => {
    translateApi.engines().then(setEngines, () => {
      setEngines([])
      setEnginesFailed(true)
    })
    api.listDramas({ progress: 'false' }).then(
      (r) => setTitles(r.items.map((d) => ({ id: d.id, name: d.title_en || d.title_zh || `#${d.id}`, series_id: d.series_id ?? null }))),
      () => setTitles([]),
    )
    getAllSeries().then(
      (r) => setSeriesList(r.items.map((s) => ({ id: s.id, name: s.name }))),
      () => setSeriesList([]),
    )
  }, [])

  return { titles, seriesList, engines, enginesFailed }
}
