import { useEffect, useState } from 'react'

import { getHardSubReading } from '../../api/benchmark'
import { ApiError, api } from '../../api/client'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import type { PcMode } from '../../hooks/usePcOnly'
import { usePersistedState } from '../../hooks/usePersistedState'
import type { HardSubReadingResult } from '../../types/benchmark'
import { plainError } from './benchmarkForm'
import { clock, describeOverall, describeReading, groupRows } from './hardSubReadingForm'

type Title = { id: number; name: string }

/** The latest hard-sub reading of a title by speaker: lines per speaker group and the transcript comparison. PC only. */
export function HardSubReadingSection({ pc }: { pc: PcMode }) {
  const [titles, setTitles] = useState<Title[] | null>(null)
  const [title, setTitle] = usePersistedState<number | null>('benchmark.hardSubReadingTitle', null)
  const [loaded, setLoaded] = useState<{ id: number; reading: HardSubReadingResult | null } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (pc === 'remote') return
    api.listDramas().then(
      (r) => setTitles(r.items.map((d) => ({ id: d.id, name: d.title_en || d.title_zh || `#${d.id}` }))),
      () => setTitles([]),
    )
  }, [pc])

  const chosen = titles?.some((t) => t.id === title) ? title : null

  useEffect(() => {
    if (pc === 'remote' || chosen == null) return
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = () =>
      getHardSubReading(chosen).then(
        (reading) => {
          if (!live) return
          setLoaded({ id: chosen, reading })
          setError(null)
          if (reading.status === 'running') timer = setTimeout(poll, 3000)
        },
        (e) => {
          if (!live) return
          // 404 = nothing has been read for this title yet, which is a state, not a failure.
          if (e instanceof ApiError && e.status === 404) setLoaded({ id: chosen, reading: null })
          else setError(plainError(e, { pcOnly: true }))
        },
      )
    void poll()
    return () => {
      live = false
      if (timer) clearTimeout(timer)
    }
  }, [pc, chosen])

  if (pc === 'remote') return null

  const reading = loaded?.id === chosen ? loaded.reading : null
  const outcome = reading?.result ?? null

  return (
    <Section title="Hard-sub reading result" summary="Lines read per speaker from the picture" storageKey="benchmark.hardSubReading">
      <div className="field-row">
        <Field label="Title">
          <select value={chosen ?? ''} onChange={(e) => setTitle(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{titles ? 'Pick a title' : 'Loading…'}</option>
            {titles?.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </Field>
      </div>
      {chosen != null && loaded?.id === chosen && !reading && !error && (
        <p className="muted" data-testid="hard-sub-reading-none">No reading has been run for this title.</p>
      )}
      {reading && !outcome && (
        <p role="status" data-testid="hard-sub-reading-running">{reading.message || 'Reading…'}</p>
      )}
      {outcome && (
        <div data-testid="hard-sub-reading-result">
          <p role="status">{describeReading(outcome)}</p>
          <p>{describeOverall(outcome)}</p>
          <table>
            <thead>
              <tr><th>Speaker group</th><th>Lines</th><th>Characters</th><th>In transcript</th></tr>
            </thead>
            <tbody>
              {groupRows(outcome).map((row) => (
                <tr key={row.key}><td>{row.key}</td><td>{row.lines}</td><td>{row.characters}</td><td>{row.recall}</td></tr>
              ))}
            </tbody>
          </table>
          {outcome.groups.map((g) => (
            <details key={g.key}>
              <summary>{g.key}</summary>
              <ul>
                {g.lines.map((line, i) => (
                  <li key={i}>{clock(line.start)}–{clock(line.end)} {line.text}</li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}
