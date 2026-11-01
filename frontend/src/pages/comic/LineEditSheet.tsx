/* Reader sheet for one tapped line: the original with Copy, the English to fix, and Save page (the editor's write path plus its render job). */
import { useEffect, useMemo, useState } from 'react'

import { scanlateApi } from '../../api/scanlate'
import { copyText } from '../../components/clipboard'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Sheet } from '../../components/Sheet'
import { buttonClass } from '../../components/uiClasses'
import { MAX_REGION_TEXT_CHARS, type ScanlatePageDetail } from '../../types/scanlate'
import { applyEdit, type EditorApi } from './editorState'

export interface TappedLine {
  pageId: number
  number: number
  idx: number
}

interface Props {
  dramaId: number
  line: TappedLine
  onClose: () => void
  // The render job started by Save page, so the reader can reload when it finishes.
  onSaved: (jobId: string) => void
}

export function LineEditSheet({ dramaId, line: { pageId, number, idx }, onClose, onSaved }: Props) {
  const [detail, setDetail] = useState<ScanlatePageDetail | null>(null)
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const api = useMemo<EditorApi>(
    () => ({
      page: () => scanlateApi.page(dramaId, pageId),
      editRegion: (rid, edit) => scanlateApi.editRegion(dramaId, pageId, rid, edit),
      createRegion: (box, rev) => scanlateApi.createRegion(dramaId, pageId, { expected_rev: rev, region: box }),
      deleteRegion: (rid, rev) => scanlateApi.deleteRegion(dramaId, pageId, rid, rev),
      undo: (rev) => scanlateApi.undo(dramaId, pageId, rev),
    }),
    [dramaId, pageId],
  )

  useEffect(() => {
    let cancelled = false
    api.page().then(
      (p) => {
        if (!cancelled) setDetail(p)
      },
      (e) => {
        if (!cancelled) setError(e)
      },
    )
    return () => {
      cancelled = true
    }
  }, [api])

  // The reader's lines are keyed by idx; the editor's by region id.
  const region = detail?.regions.find((r) => r.idx === idx) ?? null
  const shown = text ?? region?.translated_text ?? ''

  const save = async () => {
    if (!detail || !region) return
    setBusy(true)
    setError(null)
    try {
      const next = await applyEdit({ page: detail, error: null }, api, region.id, { translated_text: shown })
      if (next.error) {
        setDetail(next.page)
        setText(null)
        setError(next.error)
        return
      }
      onSaved((await scanlateApi.render(dramaId, pageId)).job_id)
      onClose()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet open title={`Page ${number} text`} onClose={onClose}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {region && (
        <div className="comic-line-edit">
          <label className="ce-field">
            <span>Original</span>
            <textarea rows={2} readOnly value={region.source_text} />
          </label>
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => copyText(region.source_text)}>
            Copy
          </button>
          <label className="ce-field">
            <span>English</span>
            <textarea rows={3} maxLength={MAX_REGION_TEXT_CHARS} value={shown} onChange={(e) => setText(e.target.value)} />
          </label>
          <button type="button" className={buttonClass('primary')} disabled={busy} onClick={save}>
            Save page
          </button>
        </div>
      )}
    </Sheet>
  )
}
