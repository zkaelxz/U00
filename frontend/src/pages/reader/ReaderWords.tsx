/*
 * Reader Sections for words: "Words" (look up this page's words), and,
 * only when there is data, "Vocabulary" (sentence-card queue and exports)
 * and "Glossary" (the series glossary, read-only).
 */
import { useState } from 'react'

import { downloadRichDeck, readerApi, vocabApkgUrl, vocabCsvUrl } from '../../api/reader'
import { Section } from '../../components/Section'
import type { ReaderVocabList } from '../../types/reader'
import type { GlossaryTerm } from '../../types/translateStage'
import type { TranslateEngine } from '../../types/translate'
import { ActionError } from './ReaderAction'
import { useAction } from './useReaderAction'
import { EnginePicker } from './ReaderEngine'

export function WordsSection({ dramaId, page, chapterSize, engines, engine, onEngine, onLookedUp }: {
  dramaId: number
  page: number
  chapterSize: number
  engines: TranslateEngine[]
  engine: string
  onEngine: (name: string) => void
  onLookedUp: () => void
}) {
  const [useLlm, setUseLlm] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const paid = useLlm && !engines.find((e) => e.name === engine)?.free
  const lookup = useAction(async () => {
    setResult(null)
    const r = await readerApi.lookup(dramaId, {
      page,
      chapter_size: chapterSize,
      use_llm: useLlm,
      ...(useLlm && engine ? { engine } : {}),
    })
    setResult(r.saved === 1 ? 'Saved 1 definition.' : `Saved ${r.saved} definitions.`)
    onLookedUp()
  })
  return (
    <Section title="Words" storageKey="reader.words" summary="Look up this page's words">
      <p className="muted">Tap a word on the page to see its meaning. Words need looking up once per page.</p>
      <label className="reader-check">
        <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
        Use AI for words the dictionary doesn't have
      </label>
      {useLlm && <EnginePicker engines={engines} engine={engine} onChange={onEngine} />}
      <div className="actions">
        <button type="button" onClick={() => void lookup.run()} disabled={lookup.busy || (useLlm && !engine)}>
          {lookup.busy ? 'Looking up…' : 'Look up words on this page'}
        </button>
        {result && <span className="muted" role="status">{result}</span>}
      </div>
      <ActionError action={lookup} paidEngine={paid} />
    </Section>
  )
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export function VocabSection({ dramaId, vocab, onChanged }: {
  dramaId: number
  vocab: ReaderVocabList
  onChanged: () => void
}) {
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [notes, setNotes] = useState<string[]>([])
  const queued = vocab.words.filter((w) => w.export_rich)
  const open = vocab.words.filter((w) => !w.export_rich)

  const queue = useAction(async (words: string[], on: boolean) => {
    await readerApi.queueRich(dramaId, words, on)
    setPicked(new Set())
    onChanged()
  })
  const deck = useAction(async () => {
    setNotes([])
    const d = await downloadRichDeck(dramaId)
    saveBlob(d.blob, d.filename)
    const n: string[] = ['Sentence cards downloaded.']
    if (d.audioOmitted) n.push('Audio left out (needs media playback permission).')
    if (d.cardsCapped) n.push(`Only the first ${d.cardsCapped} cards were built.`)
    setNotes(n)
  })

  const toggle = (w: string) =>
    setPicked((cur) => {
      const next = new Set(cur)
      if (next.has(w)) next.delete(w)
      else next.add(w)
      return next
    })

  return (
    <Section
      title="Vocabulary"
      storageKey="reader.vocab"
      count={vocab.count}
      summary={`${vocab.count} words · ${queued.length} queued for sentence cards`}
    >
      {open.length > 0 && (
        <fieldset className="reader-vocab">
          <legend>Pick words for sentence cards</legend>
          <ul className="reader-vocab-list">
            {open.map((w) => (
              <li key={w.word}>
                <label className="reader-check">
                  <input type="checkbox" checked={picked.has(w.word)} onChange={() => toggle(w.word)} />
                  <span>
                    <strong>{w.word}</strong>
                    {w.reading && <span className="muted"> {w.reading}</span>}
                    {w.definitions[0] && <span className="muted"> · {w.definitions[0]}</span>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <div className="actions">
            <button type="button" disabled={picked.size === 0 || queue.busy} onClick={() => void queue.run([...picked], true)}>
              Queue {picked.size} for sentence cards
            </button>
          </div>
        </fieldset>
      )}
      {queued.length > 0 && (
        <div className="actions">
          <span className="muted">Queued: {queued.map((w) => w.word).join(', ')}</span>
          <button type="button" className="link" disabled={queue.busy} onClick={() => void queue.run(queued.map((w) => w.word), false)}>
            Clear queue
          </button>
        </div>
      )}
      <ActionError action={queue} />
      <div className="actions reader-downloads">
        <a href={vocabCsvUrl(dramaId)} download>Word list (.csv)</a>
        <a href={vocabApkgUrl(dramaId)} download>Anki deck (.apkg)</a>
        <button type="button" disabled={queued.length === 0 || deck.busy} onClick={() => void deck.run()}>
          {deck.busy ? 'Building…' : 'Sentence cards (.apkg)'}
        </button>
      </div>
      {queued.length === 0 && <p className="muted">Still needed for sentence cards: at least one queued word.</p>}
      {notes.length > 0 && <p className="muted" role="status">{notes.join(' ')}</p>}
      <ActionError action={deck} />
    </Section>
  )
}

export function GlossarySection({ terms }: { terms: GlossaryTerm[] }) {
  return (
    <Section title="Glossary" storageKey="reader.glossary" count={terms.length} summary="Series names and terms">
      <ul className="reader-glossary">
        {terms.map((t) => (
          <li key={t.id}>
            <strong>{t.term_original}</strong> — {t.term_translation}
            {t.notes && <span className="muted"> · {t.notes}</span>}
          </li>
        ))}
      </ul>
    </Section>
  )
}
