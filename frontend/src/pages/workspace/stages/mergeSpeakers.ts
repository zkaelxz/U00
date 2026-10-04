import type { CharacterEntry } from '../../../types/translateStage'

export interface MergeChoice {
  label: string
  text: string
  /** A plain-English reason this speaker can't be picked, or null. */
  blocked: string | null
}

/** The speakers `source` can be merged into, in table order. Two speakers
 *  linked to different series characters can't be merged (the server refuses
 *  it too). */
export function mergeChoices(entries: CharacterEntry[], source: CharacterEntry): MergeChoice[] {
  return entries
    .filter((e) => e.speaker_label !== source.speaker_label)
    .map((e) => {
      const name = e.character_name.trim()
      const lines = `${e.line_count} ${e.line_count === 1 ? 'line' : 'lines'}`
      const clash = Boolean(source.series_character_id && e.series_character_id
        && source.series_character_id !== e.series_character_id)
      return {
        label: e.speaker_label,
        text: `${name && name !== e.speaker_label ? `${e.speaker_label} (${name})` : e.speaker_label}, ${lines}`,
        blocked: clash ? 'Linked to a different series character.' : null,
      }
    })
}

/** The confirm-step sentence: how many lines move and where. */
export function mergeSummary(source: CharacterEntry, targetLabel: string): string {
  const n = source.line_count
  const moved = `${n} ${n === 1 ? 'line' : 'lines'}`
  return `${moved} from ${source.speaker_label} will move to ${targetLabel}, and ${source.speaker_label} will be removed from this list. `
    + `Its name, voice and pronouns fill any blanks on ${targetLabel}; anything ${targetLabel} already has is kept.`
}
