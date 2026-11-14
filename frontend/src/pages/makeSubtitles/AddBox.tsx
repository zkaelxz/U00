/** The Add box: the one place to start a title, from a pasted link, a chosen or dropped file, or by hand. */
import { useEffect, useMemo, useRef, useState } from 'react'

import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import { MAX_URL_LEN } from '../libraryForm'
import { UPLOAD_EXTENSIONS } from '../workspace/sourceForm'
import { handOff, linkTaker, type FileRunRequest, type NewTitleStart } from './addBoxModel'
import { FilePlan, FileRunNotice, FileRunStatus } from './MakeSubtitles'
import { useFileRun } from './useFileRun'
import { QuickVideoLinkPlan, QuickVideoRuns } from './QuickVideoLink'

export const ADD_LINK_ID = 'add-box-link'

const go = (route: Parameters<typeof routeHref>[0]) => { window.location.hash = routeHref(route) }

/**
 * On the Library (`place="library"`) the box only collects the input and the choices: a run goes on to
 * the Add page, which shows its progress there so the title list is not pushed down, and Only add it
 * opens the Library's New title sheet through `onNewTitle`. On the Add page the run happens in place,
 * and New title is the Library's, so the choice is handed over there.
 */
export function AddBox({ place, onNewTitle, request = null }: {
  place: 'library' | 'page'
  onNewTitle?: (start: NewTitleStart) => void
  request?: FileRunRequest | null
}) {
  const onPage = place === 'page'
  const run = useFileRun({ runs: onPage, request })
  const [text, setText] = useState('')
  const [dragging, setDragging] = useState(false)
  const [runsKey, setRunsKey] = useState(0)
  const fileInput = useRef<HTMLInputElement>(null)
  // The link the person has finished giving; text still being typed is not one yet.
  const [link, setLink] = useState<string | null>(null)
  const taker = useMemo(() => linkTaker(setLink), [])
  useEffect(() => taker.cancel, [taker])
  const pasted = useRef(false)

  const clearLink = () => {
    setText('')
    taker.clear()
  }
  const pickFile = (f: File | null | undefined) => {
    if (!f) return
    clearLink()
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
    if (onNewTitle) { onNewTitle(start); return }
    handOff({ kind: 'new-title', start })
    go({ name: 'library' })
  }
  const makeElsewhere = onPage ? undefined : (request: FileRunRequest) => {
    handOff({ kind: 'run-file', request })
    go({ name: 'make-subtitles' })
  }
  const linkStarted = () => {
    if (!onPage) { go({ name: 'make-subtitles' }); return }
    clearLink()
    setRunsKey((k) => k + 1)
  }
  const chooseFile = () => fileInput.current?.click()
  const drops = !run.showingRun

  return (
    <>
      <section
        className={`card add-box${dragging ? ' is-dragging' : ''}`}
        aria-label="Add"
        onDragOver={drops ? (e) => { e.preventDefault(); setDragging(true) } : undefined}
        onDragLeave={drops ? () => setDragging(false) : undefined}
        onDrop={drops ? (e) => { e.preventDefault(); setDragging(false); pickFile(e.dataTransfer.files[0]) } : undefined}
      >
        {run.showingRun ? <FileRunStatus run={run} /> : (
          <>
            {!link && <FileRunNotice run={run} />}
            <h2 className="card-title">Add something to translate</h2>
            <div className="add-box-row">
              <label htmlFor={ADD_LINK_ID} className="visually-hidden">Paste a link</label>
              <input
                id={ADD_LINK_ID}
                type="url"
                inputMode="url"
                value={text}
                maxLength={MAX_URL_LEN}
                placeholder="Paste a link"
                onPaste={() => { pasted.current = true }}
                onChange={(e) => {
                  setText(e.target.value)
                  if (pasted.current) taker.now(e.target.value)
                  else taker.typed(e.target.value)
                  pasted.current = false
                }}
                // Enter while an input method is composing only confirms the characters.
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); taker.now(text) } }}
              />
              <button type="button" className={buttonClass('secondary')} title="Or drop an audio or video file on this box" onClick={chooseFile}>
                Choose a file
              </button>
              <input ref={fileInput} type="file" className="visually-hidden" tabIndex={-1} aria-label="Audio or video file"
                accept={UPLOAD_EXTENSIONS.join(',')} onChange={(e) => pickFile(e.target.files?.[0])} />
            </div>
            {link ? (
              <QuickVideoLinkPlan key={link} url={link} onStarted={linkStarted} onOnlyAdd={() => newTitle({ link })} onClear={clearLink} />
            ) : run.file || run.flow.phase === 'error' ? (
              <FilePlan run={run} onChooseFile={chooseFile} onMake={makeElsewhere} onClear={clearFile}
                onOnlyAdd={() => run.file && newTitle({ fileName: run.file.name })} />
            ) : (
              <div className="add-box-quiet">
                <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => newTitle(null)}>Add a title by hand</button>
                <p className="muted">Just want to translate some pasted text? <a href={routeHref({ name: 'translate' })}>Open Text</a></p>
              </div>
            )}
          </>
        )}
      </section>
      {onPage && <QuickVideoRuns refreshKey={runsKey} />}
    </>
  )
}
