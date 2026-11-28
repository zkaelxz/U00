/** The Find & Add box: the one place to find or start a title, from a name, a pasted link, a chosen or dropped file, or by hand. */
import { useEffect, useMemo, useRef, useState } from 'react'

import { buttonClass } from '../../components/uiClasses'
import { useHolds } from '../../hooks/useHolds'
import { routeHref } from '../../router'
import { MAX_URL_LEN } from '../libraryForm'
import { UPLOAD_EXTENSIONS } from '../workspace/sourceForm'
import { handOff, linkFrom, linkTaker, type NewTitleStart } from './addBoxModel'
import { FindResults, type FindRequest } from './FindResults'
import { FilePlan, FileRunNotice, FileRunStatus } from './MakeSubtitles'
import { useFileRun } from './useFileRun'
import { QuickVideoLinkPlan, QuickVideoRuns } from './QuickVideoLink'

export const ADD_LINK_ID = 'add-box-link'

const go = (route: Parameters<typeof routeHref>[0]) => { window.location.hash = routeHref(route) }

/** A run happens in place; New title is the Library's, so that choice is handed over there. */
export function AddBox({ initialLink = null }: {
  // A link another app shared: the box starts with it, as if it had just been pasted.
  initialLink?: string | null
}) {
  // A member given only Import from a link can run a link but not make a title by hand.
  const canCreate = useHolds('library.create')
  const run = useFileRun()
  const [text, setText] = useState(initialLink ?? '')
  const [dragging, setDragging] = useState(false)
  const [runsKey, setRunsKey] = useState(0)
  const fileInput = useRef<HTMLInputElement>(null)
  // The link the person has finished giving; text still being typed is not one yet.
  const [link, setLink] = useState<string | null>(initialLink)
  const taker = useMemo(() => linkTaker(setLink), [])
  useEffect(() => taker.cancel, [taker])
  const pasted = useRef(false)
  // A name typed in the box is searched in Sources.
  const [find, setFind] = useState<FindRequest | null>(null)

  const clearLink = () => {
    setText('')
    taker.clear()
  }
  const submit = () => {
    if (text.trim() && !linkFrom(text)) setFind((f) => ({ query: text.trim(), n: (f?.n ?? 0) + 1 }))
    else taker.now(text)
  }
  const pickFile = (f: File | null | undefined) => {
    if (!f) return
    clearLink()
    setFind(null)
    run.setFile(f)
  }
  // Back to the empty box. The remembered language, translator and English variant stay as they were.
  const clearFile = () => {
    run.reset()
    // Lets the same file be chosen again: the input fires no change for the file it already holds.
    if (fileInput.current) fileInput.current.value = ''
  }
  const newTitle = (start: NewTitleStart) => {
    // New title keeps the link or the file's name, and a second press here would make a duplicate.
    if (start && 'link' in start) clearLink()
    else if (start) clearFile()
    handOff({ kind: 'new-title', start })
    go({ name: 'library' })
  }
  const linkStarted = () => {
    clearLink()
    setRunsKey((k) => k + 1)
  }
  const chooseFile = () => fileInput.current?.click()
  const drops = !run.showingRun

  return (
    <>
      <section
        className={`add-box add-box-page${dragging ? ' is-dragging' : ''}`}
        aria-label="Add"
        onDragOver={drops ? (e) => { e.preventDefault(); setDragging(true) } : undefined}
        onDragLeave={drops ? () => setDragging(false) : undefined}
        onDrop={drops ? (e) => { e.preventDefault(); setDragging(false); pickFile(e.dataTransfer.files[0]) } : undefined}
      >
        {run.showingRun ? <FileRunStatus run={run} /> : (
          <>
            {!link && <FileRunNotice run={run} />}
            <h2 className="card-title">What do you want to add?</h2>
            <div className="add-box-row">
              <label htmlFor={ADD_LINK_ID} className="visually-hidden">Paste a link, or type a name to search</label>
              <input
                id={ADD_LINK_ID}
                type="text"
                enterKeyHint="search"
                autoComplete="off"
                value={text}
                maxLength={MAX_URL_LEN}
                placeholder="A name, or a link"
                onPaste={() => { pasted.current = true }}
                onChange={(e) => {
                  setText(e.target.value)
                  if (pasted.current) taker.now(e.target.value)
                  else taker.typed(e.target.value)
                  pasted.current = false
                }}
                // Enter while an input method is composing only confirms the characters.
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); submit() } }}
              />
              <button type="button" className={buttonClass('primary')} disabled={!text.trim()} onClick={submit}>Search</button>
              <button type="button" className={buttonClass('secondary')} title="Or drop an audio or video file on this box" onClick={chooseFile}>
                Choose a file
              </button>
              <input ref={fileInput} type="file" className="visually-hidden" tabIndex={-1} aria-label="Audio or video file"
                accept={UPLOAD_EXTENSIONS.join(',')} onChange={(e) => pickFile(e.target.files?.[0])} />
            </div>
            {!link && !run.file && <p className="muted">A name searches your comic and novel sites. A link to a video or a page is read and added.</p>}
            {link ? (
              <QuickVideoLinkPlan key={link} url={link} onStarted={linkStarted} onOnlyAdd={canCreate ? () => newTitle({ link }) : undefined} onClear={clearLink} />
            ) : run.file || run.flow.phase === 'error' ? (
              <FilePlan run={run} onChooseFile={chooseFile} onClear={clearFile}
                onOnlyAdd={() => run.file && newTitle({ fileName: run.file.name })} />
            ) : (
              <p className="muted add-box-quiet">
                {canCreate && <><button type="button" className="link" onClick={() => newTitle(null)}>Add a title by hand</button><span className="add-box-quiet-sep" aria-hidden="true">·</span></>}
                <span>Just want to translate some pasted text? <a className="touch-link" href={routeHref({ name: 'translate' })}>Open Text</a></span>
              </p>
            )}
          </>
        )}
      </section>
      {!link && !run.file && !run.showingRun && <FindResults request={find} />}
      <QuickVideoRuns refreshKey={runsKey} />
    </>
  )
}
