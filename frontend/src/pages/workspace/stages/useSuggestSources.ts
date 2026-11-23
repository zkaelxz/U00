// What a title's glossary suggestions can be read from, and which source to start on.
import { mediaKind } from '../detailsForm'
import { useStage } from '../StageContext'
import { stageLabel } from '../stages'
import { titleKind } from '../titleKind'
import { defaultSuggestSource, suggestBlockers } from './glossaryExtract'
import { useHasLines, useHasNovel } from './useGlossaryRun'

// What the title can be read from and which source to start on, shared by
// the bar and the empty-state card so both name the same default.
export function useSuggestSources() {
  const { dramaId, drama } = useStage()
  const hasNovel = useHasNovel(dramaId, drama)
  const hasLines = useHasLines(dramaId)
  const blockers = suggestBlockers(dramaId, hasNovel, hasLines, stageLabel('source', titleKind(drama.media_type, drama.content_mode)))
  const defaultSource = defaultSuggestSource(blockers, mediaKind(drama.media_type) !== 'audio')
  return { blockers, defaultSource, ready: hasNovel !== null && hasLines !== null }
}
