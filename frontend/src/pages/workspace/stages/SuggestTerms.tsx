import { GlossaryExtract } from './GlossaryExtractPanel'
import { type GlossarySource, SOURCE_TEXT } from './glossaryExtract'
import type { useSuggestSources } from './useSuggestSources'

const SOURCES: GlossarySource[] = ['lines', 'novel']

type Sources = ReturnType<typeof useSuggestSources>

// "Suggest terms from [Transcript | Novel] [Suggest terms]" at the top of the
// Find terms tab; the run's progress and review table appear under it. The source
// is chosen by the Glossary panel (a held scan, the remembered choice or the default).
export function SuggestTerms({ sources, source, hasTerms, startRequested, onStartHandled, onPick }: {
  sources: Sources
  source: GlossarySource
  hasTerms: boolean
  startRequested: boolean
  onStartHandled: () => void
  onPick: (s: GlossarySource) => void
}) {
  const { blockers, ready } = sources
  const picker = (
    <select aria-label="Suggest terms from" value={source} onChange={(e) => onPick(e.target.value as GlossarySource)}>
      {SOURCES.map((s) => (
        <option key={s} value={s} disabled={!!blockers[s] && s !== source}>
          {SOURCE_TEXT[s].label}
        </option>
      ))}
    </select>
  )
  const blocked = SOURCES.filter((s) => blockers[s])
  const reasons = blocked.length > 0 && (
    <>
      {blocked.map((s) => {
        const b = blockers[s]!
        return (
          <p key={s} className="muted" data-testid={`suggest-reason-${s}`}>
            {SOURCE_TEXT[s].label}: {b.text} (<a href={b.href}>{b.link}</a>).
          </p>
        )
      })}
    </>
  )
  return (
    <GlossaryExtract
      key={source}
      source={source}
      blocker={blockers[source]}
      ready={ready}
      picker={picker}
      reasons={reasons}
      hasTerms={hasTerms}
      startRequested={startRequested}
      onStartHandled={onStartHandled}
    />
  )
}
