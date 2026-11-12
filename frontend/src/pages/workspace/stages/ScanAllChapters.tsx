// Suggest terms (From lines): scan every saved chapter in turn, adding the terms found without
// overwriting yours. The scan runs on the server, so it carries on if the page is left.
import { useEffect, useRef, useState } from 'react'

import { cancelChaptersGlossary, getChaptersGlossary, startChaptersGlossary } from '../../../api/autotuneGlossary'
import { getLineChapters } from '../../../api/novelChapters'
import { buttonClass } from '../../../components/uiClasses'
import { isActiveStatus, novelGlossaryStartErrorText } from './autotuneGlossary'
import { bumpGlossaryTerms } from './useGlossaryRun'
import { useRunStatus } from './useRunStatus'

const TIP = 'One scan per chapter, in order. Terms found are added to the glossary; ones you already have are kept. Uses the title’s translation engine (one paid call per chapter on a paid engine).'

export function ScanAllChapters({ dramaId }: { dramaId: number }) {
  const [chapters, setChapters] = useState(0)
  const [problem, setProblem] = useState<string | null>(null)
  const { status, refresh } = useRunStatus(dramaId, getChaptersGlossary)
  useEffect(() => {
    let live = true
    getLineChapters(dramaId).then((r) => live && setChapters(r.chapters.length), () => {})
    return () => { live = false }
  }, [dramaId])
  const active = isActiveStatus(status?.status)
  // The terms table re-reads once when a scan finishes, not on every poll.
  const wasActive = useRef(false)
  useEffect(() => {
    if (wasActive.current && !active) bumpGlossaryTerms()
    wasActive.current = active
  }, [active])
  if (chapters < 2) return null
  const start = () => {
    setProblem(null)
    startChaptersGlossary(dramaId).then(refresh, (e: unknown) => {
      setProblem(novelGlossaryStartErrorText(e) ?? 'Could not start the scan.')
      refresh()
    })
  }
  return (
    <>
      {!active && (
        <button type="button" className={buttonClass('secondary')} title={TIP} onClick={start}>Scan all chapters</button>
      )}
      {active && status && (
        <span className="actions" role="status">
          <span>{status.message || 'Scanning chapters…'}</span>
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={!status.run_id}
            onClick={() => status.run_id && cancelChaptersGlossary(dramaId, status.run_id).then(refresh, refresh)}>
            Cancel
          </button>
        </span>
      )}
      {!active && status?.status === 'done' && status.message && <span className="muted" role="status">{status.message}</span>}
      {!active && status?.status === 'error' && <span className="error" role="alert">{status.message}</span>}
      {problem && <span className="error" role="alert">{problem}</span>}
    </>
  )
}
