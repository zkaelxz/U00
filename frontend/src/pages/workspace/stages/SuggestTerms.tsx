import { mediaKind } from '../detailsForm'
import { useStage } from '../StageContext'
import { GlossaryExtract } from './GlossaryExtractPanel'
import {
  SOURCE_TEXT,
  defaultSuggestSource,
  suggestBlockers,
  type GlossarySource,
} from './glossaryExtract'
import { useHasLines, useHasNovel } from './useGlossaryRun'

const SOURCES: GlossarySource[] = ['lines', 'novel']

// What the title can be read from and which source to start on, shared by
// the bar and the empty-state card so both name the same default.
export function useSuggestSources() {
  const { dramaId, drama } = useStage()
  const hasNovel = useHasNovel(dramaId, drama)
  const hasLines = useHasLines(dramaId)
  const blockers = suggestBlockers(dramaId, drama.series_id ?? null, hasNovel, hasLines)
  const defaultSource = defaultSuggestSource(blockers, mediaKind(drama.media_type) !== 'audio')
  return { blockers, defaultSource, ready: hasNovel !== null && hasLines !== null }
}

type Sources = ReturnType<typeof useSuggestSources>

// "Suggest terms from [Transcript | Novel] [Suggest terms]" at the top of the
// Glossary section; the run's progress and review table appear under it.
export function SuggestTerms({ sources, hasTerms, startRequested, onStartHandled, pick, onPick }: {
  sources: Sources
  hasTerms: boolean
  startRequested: boolean
  onStartHandled: () => void
  // The viewer's choice; null follows the default.
  pick: GlossarySource | null
  onPick: (s: GlossarySource) => void
}) {
  const { blockers, defaultSource, ready } = sources
  const source = pick ?? defaultSource
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
