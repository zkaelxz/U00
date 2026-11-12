// api/schemas/novel_chapters.py, from the generated schema. Titles, counts and text, plus each chapter's
// display-safe source page link; never a filename or path.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

// `url` stays optional: a chapter with no source page link has none to show, and the tests build rows without it.
export type NovelChapterRow = Omit<Schemas['NovelChapterRow'], 'url'> & { url?: string }
export type NovelChapterList = Omit<Schemas['NovelChapterList'], 'chapters'> & { chapters: NovelChapterRow[] }
export type NovelChapterText = Omit<Schemas['NovelChapterText'], 'url'> & { url?: string }

export type NovelLineChapterRow = Schemas['NovelLineChapterRow']
export type NovelLineChapters = Schemas['NovelLineChapters']

export type NovelChapterMoved = Schemas['NovelChapterMoved']
export type NovelChapterRenamed = Schemas['NovelChapterRenamed']
export type NovelChapterRetranslatePreview = components['schemas']['NovelChapterRetranslatePreview']
export type NovelChapterRetranslateStarted = components['schemas']['GlossaryAffectedRunStarted']
