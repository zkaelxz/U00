// A small "Study" button on the Watch page's now card: the line's original text, then each word with its reading and
// meaning. It is a button, not a tap on the line, because tapping a line jumps the video there. Also the card's
// original text itself, with its Reading switch.
import { useEffect, useState, type RefObject } from 'react'

import { readerApi } from '../../api/reader'
import { chineseLang } from '../../hanScript'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { usePersistedState } from '../../hooks/usePersistedState'
import { heardUpTo } from './watchLogic'
import type { StudyLine as Study } from '../../types/reader'

export function StudyLine({ dramaId, lineId, language }: { dramaId: number; lineId: number; language: string }) {
  const [open, setOpen] = useState(false)
  const [study, setStudy] = useState<Study | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!open || study) return
    let cancelled = false
    readerApi.lineStudy(dramaId, lineId).then(
      (s) => !cancelled && setStudy(s),
      () => !cancelled && setFailed(true),
    )
    return () => {
      cancelled = true
    }
  }, [open, study, dramaId, lineId])

  return (
    <div className="watch-study">
      <button type="button" className={buttonClass()} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? 'Hide study' : 'Study'}
      </button>
      {open && failed && <p role="status">The breakdown could not be loaded.</p>}
      {open && !failed && !study && <p role="status">Loading…</p>}
      {open && study && <StudyWords study={study} language={language} />}
    </div>
  )
}

export function StudyWords({ study, language }: { study: Study; language: string }) {
  return (
    <div data-testid="watch-study">
      {study.needs_install && <p role="status">Breaking this into words needs {study.needs_install}, which is not installed (see Diagnostics).</p>}
      {study.words.length === 0 && !study.needs_install && <p role="status">Nothing to break down for this line.</p>}
      <ul className="watch-study-words" lang={study.language || language}>
        {study.words.map((w, i) => (
          <li key={i} className="watch-study-word">
            <span>{w.word}</span>
            {w.reading && <span className="watch-study-reading">{w.reading}</span>}
            {w.meanings && w.meanings.length > 0 && <span className="watch-study-meaning" lang="en">{w.meanings.join('; ')}</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}

const KANJI = /[㐀-䶿一-鿿豈-﫿々]/

type Word = Study['words'][number]

// The breakdown leaves out spaces and line breaks between words; they are put back from the line's own text. null when
// the words don't spell the text in order, and the text is shown plain.
function withGaps(text: string, words: Word[]): (string | { word: Word; index: number })[] | null {
  const out: (string | { word: Word; index: number })[] = []
  let pos = 0
  for (const [index, word] of words.entries()) {
    const at = text.indexOf(word.word, pos)
    if (at < 0) return null
    if (at > pos) out.push(text.slice(pos, at))
    out.push({ word, index })
    pos = at + word.word.length
  }
  if (pos < text.length) out.push(text.slice(pos))
  return out
}

/**
 * The now card's original text with a Reading switch: on, pinyin or furigana sits over each word and the words light up
 * as they are spoken (from the line's stored word times; a line without them just shows the readings).
 */
export function OriginalLine({ dramaId, lineId, text, language, mediaRef }: {
  dramaId: number
  lineId: number
  text: string
  language: string
  mediaRef?: RefObject<HTMLMediaElement | null>
}) {
  const canRead = language === 'zh' || language === 'ja'
  const [reading, setReading] = usePersistedState('watch.reading', false)
  const [study, setStudy] = useState<Study | null>(null)
  const [heard, setHeard] = useState(-1)
  const on = canRead && reading

  useEffect(() => {
    if (!on || study) return
    let cancelled = false
    readerApi.lineStudy(dramaId, lineId).then((s) => !cancelled && setStudy(s), () => undefined)
    return () => {
      cancelled = true
    }
  }, [on, study, dramaId, lineId])

  const words = on && study && !study.needs_install ? study.words : null
  const timed = !!words?.some((w) => w.start != null)
  useEffect(() => {
    const el = mediaRef?.current
    if (!timed || !el || !words) return
    // Read every frame while playing, so a word lights with the voice; state changes only when the word does.
    let raf = 0
    const tick = () => {
      const next = heardUpTo(words, el.currentTime)
      setHeard((h) => (h === next ? h : next))
      raf = el.paused ? 0 : requestAnimationFrame(tick)
    }
    const start = () => {
      cancelAnimationFrame(raf)
      tick()
    }
    tick()
    el.addEventListener('play', start)
    el.addEventListener('seeked', start)
    return () => {
      cancelAnimationFrame(raf)
      el.removeEventListener('play', start)
      el.removeEventListener('seeked', start)
    }
  }, [timed, words, mediaRef])

  const lang = chineseLang(study?.language || language, text, language)
  return (
    <div className="watch-now-original">
      <p className="watch-now-zh" lang={lang}>
        {words && words.length > 0 ? <ReadingWords text={text} words={words} lang={lang} heard={timed ? heard : -1} /> : text}
      </p>
      {on && study?.needs_install && <p role="status">Readings need {study.needs_install}, which is not installed (see Diagnostics).</p>}
      {canRead && (
        <Field label={language === 'ja' ? 'Furigana' : 'Pinyin'}>
          <Toggle checked={reading} onChange={setReading} />
        </Field>
      )}
    </div>
  )
}

/** A line's words with their readings over them; the words up to `heard` (an index, -1 for none) show as spoken. */
export function ReadingWords({ text, words, lang, heard }: { text: string; words: Word[]; lang: string; heard: number }) {
  const pieces = withGaps(text, words)
  if (!pieces) return <>{text}</>
  return (
    <>
      {pieces.map((p, i) => {
        if (typeof p === 'string') return p
        const { word: w, index } = p
        const cls = index <= heard ? 'watch-word watch-word-heard' : 'watch-word'
        // Katakana and kana need no reading over them.
        const show = w.reading && w.reading !== w.word && (lang !== 'ja' || KANJI.test(w.word))
        return show ? (
          <ruby key={i} className={cls}>
            {w.word}
            <rt>{w.reading}</rt>
          </ruby>
        ) : (
          <span key={i} className={cls}>{w.word}</span>
        )
      })}
    </>
  )
}
