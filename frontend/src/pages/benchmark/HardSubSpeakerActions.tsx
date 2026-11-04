import { useState } from 'react'

import { acceptHardSubCaptions, applyHardSubReading, getHardSubCaptions } from '../../api/benchmark'
import { Field } from '../../components/Field'
import type { HardSubCaptionSuggestion } from '../../types/benchmark'
import { plainError } from './benchmarkForm'
import { clock, describeAccept, describeApply, namesToSend } from './hardSubReadingForm'

/** Names the colour groups, puts them on the title's lines, and offers the burned-in captions where the
 * transcript differs. Nothing is written until a button is pressed. */
export function HardSubSpeakerActions({ dramaId, groupKeys, onChanged }: { dramaId: number; groupKeys: string[]; onChanged?: () => void }) {
  const [typed, setTyped] = useState<Record<string, string>>({})
  const [applied, setApplied] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<HardSubCaptionSuggestion[] | null>(null)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [accepted, setAccepted] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async (work: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await work()
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div data-testid="hard-sub-actions">
      <h4>Who is each colour?</h4>
      <p className="muted">Name a colour group once; the title's series remembers it for its other videos. Leave a box empty to keep the group name.</p>
      {groupKeys.map((key) => (
        <Field key={key} label={key}>
          <input
            value={typed[key] ?? ''}
            maxLength={60}
            onChange={(e) => setTyped({ ...typed, [key]: e.target.value })}
          />
        </Field>
      ))}
      <button
        type="button"
        disabled={busy}
        onClick={() =>
          run(async () => {
            const names = namesToSend(Object.fromEntries(Object.entries(typed).filter(([, v]) => v.trim() !== '')))
            setApplied(describeApply(await applyHardSubReading(dramaId, names)))
            onChanged?.()
          })
        }
      >
        Put speakers on lines
      </button>
      {applied && <p role="status" data-testid="hard-sub-applied">{applied}</p>}

      <h4>Captions that differ from the transcript</h4>
      <button
        type="button"
        disabled={busy}
        onClick={() =>
          run(async () => {
            const got = await getHardSubCaptions(dramaId)
            setSuggestions(got.suggestions)
            setPicked(new Set())
            setAccepted(null)
          })
        }
      >
        Find differences
      </button>
      {suggestions && suggestions.length === 0 && <p className="muted">The captions and the transcript agree where they overlap.</p>}
      {suggestions && suggestions.length > 0 && (
        <>
          <ul data-testid="hard-sub-suggestions">
            {suggestions.map((s) => (
              <li key={s.line_id}>
                <label>
                  <input
                    type="checkbox"
                    checked={picked.has(s.line_id)}
                    onChange={(e) => {
                      const next = new Set(picked)
                      if (e.target.checked) next.add(s.line_id)
                      else next.delete(s.line_id)
                      setPicked(next)
                    }}
                  />{' '}
                  {clock(s.start)} {s.person ?? s.group}: transcript "{s.current}", caption "{s.caption}"
                </label>
              </li>
            ))}
          </ul>
          <button
            type="button"
            disabled={busy || picked.size === 0}
            onClick={() =>
              run(async () => {
                setAccepted(describeAccept(await acceptHardSubCaptions(dramaId, [...picked])))
                setSuggestions(null)
                onChanged?.()
              })
            }
          >
            Use the caption for the ticked lines
          </button>
        </>
      )}
      {accepted && <p role="status" data-testid="hard-sub-accepted">{accepted}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}
