// Read and bookmark marks in the comic reader: lays saved marks over the loaded page list and
// marks a chapter read when its last page is reached.
import { useCallback, useEffect, useRef, type Dispatch, type SetStateAction } from 'react'

import { comicApi } from '../../api/comic'
import type { ComicChapterMarks, ComicPagesResponse } from '../../types/comic'
import { withMarks } from './chapterLogic'

export function useChapterMarks(
  dramaId: number,
  setData: Dispatch<SetStateAction<ComicPagesResponse | null>>,
  // The chapter whose last page is on screen and is not read yet (chapterLogic.finishedChapter).
  finished: string | null,
) {
  const applyMarks = useCallback(
    (marks: ComicChapterMarks) => setData((d) => (d ? { ...d, chapters: withMarks(d.chapters ?? [], marks.chapters) } : d)),
    [setData],
  )
  // Each chapter is marked by itself once per title opened: after a Mark unread on its last page
  // the chapter is "finished and unread" again, and marking it a second time would undo the press.
  const marked = useRef({ dramaId, ids: new Set<string>() })
  useEffect(() => {
    if (!finished) return
    if (marked.current.dramaId !== dramaId) marked.current = { dramaId, ids: new Set() }
    if (marked.current.ids.has(finished)) return
    marked.current.ids.add(finished)
    // A refusal (no lines.edit) is ignored, as it is for the resume page: reading still works.
    comicApi.markChapters(dramaId, { chapter_ids: [finished], read: true }).then(applyMarks, () => {})
  }, [dramaId, finished, applyMarks])
  return applyMarks
}
