/*
 * "Read from video" under the side video in Review: grab the picture on screen
 * (or at the active line's start), drag a box round the hard-sub caption, and a
 * vision engine returns the characters, which fill the active line's source
 * text as an unsaved edit. The read itself is Quick translate's
 * (pages/TranslateReadImage.tsx); this only supplies the frame and the target.
 */
import { useEffect, useState, type RefObject } from 'react'

import { languagePair, translateApi } from '../../../../api/translate'
import { buttonClass } from '../../../../components/uiClasses'
import { usePersistedState } from '../../../../hooks/usePersistedState'
import { useHolds } from '../../../../hooks/useHolds'
import { usePcOnly } from '../../../../hooks/usePcOnly'
import { patchLine } from '../../../../api/review'
import type { TranslateEngine } from '../../../../types/translate'
import { firstImage } from '../../../readImage'
import { TranslateReadImage } from '../../../TranslateReadImage'
import type { ReviewLine } from '../../../../types/review'
import { afterLinesWrite, beforeLinesWrite } from './linesController'
import { useLineSelectionContext } from './LineSelectionContext'
import type { PlayerHandle } from './Player'

interface Props {
  player: RefObject<PlayerHandle | null>
  sourceLanguage: string
  // The active line's start in seconds, or null when no line is selected.
  lineStart: number | null
  // Puts the characters into the active line's source box (an unsaved edit).
  onText: (text: string) => void
  // The title's own engine, the one it is translated with; empty uses the page's default.
  translationEngine: string | null
  // Puts the read and its translation into the active line's boxes (an unsaved edit).
  onReadTranslated: (source: string, english: string) => void
  dramaId: number
  // The lines shown; the ticked ones among them are the batch.
  lines: ReviewLine[]
  // Called once the batch has written, so the list shows the new source text.
  onBatchSaved: () => void
}

export function ReadFromVideo({ player, sourceLanguage, lineStart, onText, translationEngine, onReadTranslated, dramaId, lines, onBatchSaved }: Props) {
  const { selectedSet } = useLineSelectionContext()
  const selectedLines = lines.filter((l) => selectedSet.has(l.id))
  // Closed until opened; once opened on purpose it stays open on this browser until closed.
  const [open, setOpen] = usePersistedState('review.readVideo.open', false)
  const [engines, setEngines] = useState<TranslateEngine[]>([])
  const [pageEngine, setPageEngine] = useState('')
  const [frame, setFrame] = useState<{ blob: Blob; seq: number } | null>(null)
  const [seq, setSeq] = useState(0)
  const [message, setMessage] = useState<string | null>(null)
  const allowed = useHolds('engines.paid')
  // The read route refuses callers that are not on the PC, so offer nothing there.
  const pc = usePcOnly()

  useEffect(() => {
    if (!open || engines.length) return
    translateApi.engineList().then((r) => {
      setEngines(r.items)
      setPageEngine(r.default_engine ?? '')
    }, () => setMessage('The engine list could not be loaded.'))
  }, [open, engines.length])

  // Ctrl+V of a picture while a line is selected opens the panel with it, so a
  // screenshot never has to be saved first. Only a picture is taken over: any
  // other paste, in a text box or elsewhere, is left alone. Once open, the
  // panel's own paste handler takes over.
  const canPaste = pc !== 'remote' && !open && lineStart !== null
  useEffect(() => {
    if (!canPaste) return
    const onPaste = (e: ClipboardEvent) => {
      const file = e.clipboardData ? firstImage(Array.from(e.clipboardData.items)) : null
      if (!file) return
      e.preventDefault()
      setFrame({ blob: file, seq: seq + 1 })
      setSeq(seq + 1)
      setOpen(true)
    }
    document.addEventListener('paste', onPaste)
    return () => document.removeEventListener('paste', onPaste)
  }, [canPaste, seq, setOpen])

  const grab = async (at?: number) => {
    setMessage(null)
    const blob = await player.current?.captureFrame(at)
    if (!blob) {
      setMessage('This browser could not take the picture from the video. Paste a screenshot instead (Ctrl+V).')
      return
    }
    setFrame({ blob, seq: seq + 1 })
    setSeq(seq + 1)
  }

  // Each ticked line's frame at its start is read with the box, zoom and engine set below, and the
  // characters are saved as that line's source text. A failure stops the run: the lines already
  // read stay saved, and the rest are not paid for.
  const readSelected = async (readPicture: (picture: Blob) => Promise<string>): Promise<string> => {
    const ids = selectedLines.map((l) => l.id)
    const blocked = await beforeLinesWrite(dramaId, ids)
    if (blocked) return blocked
    let saved = 0
    try {
      for (const line of selectedLines) {
        const picture = await player.current?.captureFrame(line.start)
        if (!picture) throw new Error('This browser could not take the picture from the video. Paste a screenshot instead (Ctrl+V).')
        const text = (await readPicture(picture)).trim()
        if (!text) continue
        await patchLine(dramaId, line.id, { zh: text, expected: { zh: line.zh } })
        saved += 1
      }
    } finally {
      afterLinesWrite(dramaId, ids)
      if (saved) onBatchSaved()
    }
    return saved ? '' : 'No text was found in the pictures.'
  }

  if (pc === 'remote') return null
  if (!open) {
    return (
      <button
        type="button"
        className={buttonClass('secondary', 'sm', 'review-read-video-open')}
        aria-label="Read from video"
        title="Read the caption from a picture: paste a screenshot (Ctrl+V) or grab a frame"
        onClick={() => setOpen(true)}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <circle cx="9" cy="10" r="1.5" />
          <path d="M21 16l-5-5-8 9" />
        </svg>
      </button>
    )
  }
  return (
    <div className="review-read-video">
      <div className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => void grab()}>
          Use this frame
        </button>
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={lineStart === null} onClick={() => lineStart !== null && void grab(lineStart)}>
          Use frame at line start
        </button>
      </div>
      {message && <p className="muted" role="status">{message}</p>}
      {lineStart === null && <p className="muted">Select a line first; the characters go into its source text.</p>}
      <TranslateReadImage
        engines={engines}
        pageEngine={pageEngine}
        language={sourceLanguage}
        allowed={allowed}
        onText={onText}
        onClose={() => setOpen(false)}
        frame={frame}
        keepBox
        heading="Read from video"
        hint="Paste a screenshot (Ctrl+V) or grab a frame, then drag a box round the caption: it reads far better than the whole frame. The box stays for the next frame. Nothing is saved until you save the line."
        alsoTranslate={{
          label: 'Read & translate',
          run: async (read) => {
            const translated = await translateApi.translate({
              text: read,
              engine: translationEngine || pageEngine,
              ...languagePair('to_english', sourceLanguage),
            })
            onReadTranslated(read, translated)
          },
        }}
        readEach={selectedLines.length ? { label: 'Read all selected lines', run: readSelected } : undefined}
        readLabel="Read into this line"
        addedNotice="Put into the line's source text. Save the line to keep it."
      />
    </div>
  )
}
