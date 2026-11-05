import type { VoiceEntry } from '../../types/voices'

export const similarityText = (value: number | null | undefined) =>
  value == null ? 'no stored voice' : `${Math.round(value * 100)}% match`

/** One line under a person's name: how many samples built the print and how well the linked speakers match it. */
export function voiceSummary(v: VoiceEntry): string {
  const samples = `${v.sample_count} ${v.sample_count === 1 ? 'sample' : 'samples'}`
  if (v.average_similarity == null) return `${samples} · no linked speaker has a stored voice to compare`
  return `${samples} · ${Math.round(v.average_similarity * 100)}% average match, lowest ${Math.round((v.lowest_similarity ?? 0) * 100)}%`
}

/** Retraining only uses linked speakers that still have a stored voice; this says how many samples it would lose. */
export function retrainNote(v: VoiceEntry): string {
  if (v.rebuildable === 0) return 'Retrain is unavailable: none of the linked speakers has a stored voice. Clear the print instead.'
  const lost = v.sample_count - v.rebuildable
  return lost > 0
    ? `Retrain rebuilds the print from the ${v.rebuildable} linked ${v.rebuildable === 1 ? 'speaker' : 'speakers'} with a stored voice; ${lost} older ${lost === 1 ? 'sample' : 'samples'} can't be rebuilt and drop out.`
    : `Retrain rebuilds the print from the ${v.rebuildable} linked ${v.rebuildable === 1 ? 'speaker' : 'speakers'}.`
}

/** A match under this is worth a look; the same line suggestions use (voice_id.DEFAULT_THRESHOLD). */
export const LOW_MATCH = 0.75
