/** The cast the glossary scan found, offered for approval: pronouns are set only where none are, and only for the ticked people. */
import { useEffect, useState } from 'react'

import { applyCastProposal, getCastProposal, type CastPerson, type CastProposal, type Pronouns } from '../../../api/novelCast'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { buttonClass } from '../../../components/uiClasses'
import './novelCast.css'

const CHOICES: Pronouns[] = ['she/her', 'he/him', 'they/them']

const isChoice = (v: string): v is Pronouns => (CHOICES as string[]).includes(v)

/** What to show for a person: the scan's reading unless pronouns are already set. */
export function initialPick(p: CastPerson): { checked: boolean; pronouns: Pronouns | '' } {
  const settled = !!p.current_pronouns
  return { checked: !settled && isChoice(p.pronouns), pronouns: isChoice(p.pronouns) ? p.pronouns : '' }
}

export function reportText(r: { set: string[]; added: string[]; kept: string[]; not_applied: string[] }): string {
  const parts = []
  if (r.set.length + r.added.length) parts.push(`Set pronouns for ${[...r.set, ...r.added].join(', ')}.`)
  if (r.kept.length) parts.push(`Left as they were: ${r.kept.join(', ')} (already set).`)
  if (r.not_applied.length) {
    parts.push(`Not applied: ${r.not_applied.join(', ')} (no speaker of that name yet, and the title has no series to keep them in).`)
  }
  return parts.join(' ') || 'Nothing to apply.'
}

export function NovelCastCard({ dramaId, scanDone }: { dramaId: number; scanDone: boolean }) {
  const [proposal, setProposal] = useState<CastProposal | null>(null)
  const [picks, setPicks] = useState<Record<string, { checked: boolean; pronouns: Pronouns | '' }>>({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    if (!scanDone) return
    let cancelled = false
    getCastProposal(dramaId).then(
      (p) => {
        if (cancelled) return
        setProposal(p)
        setPicks(Object.fromEntries(p.people.map((x) => [x.term, initialPick(x)])))
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, scanDone, reload])

  // Nothing to offer once every person already has pronouns.
  if (!scanDone || !proposal || proposal.people.every((p) => !!p.current_pronouns)) return null
  const chosen = proposal.people.filter((p) => picks[p.term]?.checked && picks[p.term]?.pronouns)

  const apply = async () => {
    if (busy || chosen.length === 0) return
    setBusy(true)
    try {
      const report = await applyCastProposal(dramaId, chosen.map((p) => ({ name: p.name, pronouns: picks[p.term].pronouns as Pronouns })))
      setMessage(reportText(report))
      setReload((n) => n + 1)
    } catch (e) {
      setError(e)
    }
    setBusy(false)
  }

  return (
    <section className="panel novel-cast" aria-label="Cast proposal">
      <h2 className="source-subhead">Cast found in the text</h2>
      <p className="muted">
        Tick the people whose pronouns look right, change any that don't, then apply. Nothing is saved until you do, and pronouns that are already set are never replaced.
      </p>
      <ul className="novel-cast-list">
        {proposal.people.map((p) => {
          const pick = picks[p.term] ?? { checked: false, pronouns: '' }
          const set = (patch: Partial<typeof pick>) => setPicks((all) => ({ ...all, [p.term]: { ...pick, ...patch } }))
          return (
            <li key={p.term} className="novel-cast-item">
              <label className="novel-cast-name">
                <input
                  type="checkbox"
                  checked={pick.checked}
                  disabled={!!p.current_pronouns || busy}
                  aria-label={`Include ${p.name}`}
                  onChange={(e) => set({ checked: e.target.checked })}
                />
                <span>{p.name}</span>
                <span className="muted"> ({p.term})</span>
              </label>
              {p.current_pronouns ? (
                <span className="muted">Already set: {p.current_pronouns}</span>
              ) : (
                <select
                  aria-label={`Pronouns for ${p.name}`}
                  value={pick.pronouns}
                  disabled={busy}
                  onChange={(e) => set({ pronouns: isChoice(e.target.value) ? e.target.value : '', checked: isChoice(e.target.value) })}
                >
                  <option value="">Not sure</option>
                  {CHOICES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              )}
              {p.who && <span className="novel-cast-who muted">{p.who}</span>}
            </li>
          )
        })}
      </ul>
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={busy || chosen.length === 0} onClick={() => void apply()}>
          Apply to characters
        </button>
        <span className="muted" role="status" aria-live="polite">{message ?? `${chosen.length} chosen`}</span>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </section>
  )
}
