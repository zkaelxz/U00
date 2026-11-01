import { updateDramaMetadata } from '../../../api/library'
import { updateSourceConfig } from '../../../api/source'
import { attachNovelText } from '../../../api/workspace'

/**
 * Makes the title a novel that is translated first: optional text becomes the text to translate, then the
 * title takes the novel media type and narration mode, which is what Chunk and tag and the Translate
 * stage work from. Audio and dubbing come after. The mode is set before the type so a failure part-way
 * never leaves a novel title without the mode that gives it lines.
 */
export async function startAsNovel(dramaId: number, text?: string): Promise<void> {
  if (text?.trim()) await attachNovelText(dramaId, text, 'replace')
  await updateSourceConfig(dramaId, { content_mode: 'novel_narration' })
  await updateDramaMetadata(dramaId, { media_type: 'novel' })
}

// The narration job's progress message starts with "Resuming:" when it picks up an interrupted run
// (services/narration_service.py); the Job panel then says how to start over.
export const NARRATION_RESUME_NOTE = 'Resuming an interrupted run. Use Start over to tag everything again.'

export function narrationResumeNote(message: string | null | undefined): string | null {
  return message?.startsWith('Resuming:') ? NARRATION_RESUME_NOTE : null
}
