import { useEffect, useState } from 'react'

import {
  DUPLICATE_SCAN_JOB_ID, getDuplicateTitles, startDuplicateScan, type DuplicateGroups, type DuplicateKind,
} from '../../api/duplicates'
import { ErrorBanner } from '../../components/ErrorBanner'
import { workspaceHref } from '../../components/libraryView'
import { AdminJobLine } from '../libraryAdmin/AdminJobLine'
import { useAdminJob } from '../libraryAdmin/useAdminJob'

const WHY: Record<DuplicateKind, string> = {
  file: 'Same file',
  link: 'Same link',
  name: 'Same name',
}

/**
 * Library tools > "Find duplicate titles": titles that share a file, a link or a name. Read-only;
 * "Check files" hashes titles added before files were compared so those show up as "Same file" too.
 */
export function DuplicatesCard({ canScan }: { canScan: boolean }) {
  const job = useAdminJob(DUPLICATE_SCAN_JOB_ID)
  const [found, setFound] = useState<DuplicateGroups | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    if (job.active) return
    // A failed read just shows no list: the card sits on a busy page and a banner here would hide the page's own errors.
    getDuplicateTitles().then(setFound, () => setFound(null))
  }, [job.active, job.done])

  return (
    <div className="admin-block" data-testid="duplicates-card">
      <p className="muted">Nothing is changed or deleted here. Open a title to decide what to keep.</p>
      {canScan && (
        <div className="actions">
          <button type="button" disabled={job.active} onClick={() => void job.start(() => startDuplicateScan())}>
            Check files
          </button>
        </div>
      )}
      <AdminJobLine job={job} busyText="Comparing files…" />
      <ErrorBanner error={job.startError ?? error} onDismiss={() => { job.clearError(); setError(null) }} />
      {found && found.unhashed > 0 && canScan && (
        <p className="muted">{found.unhashed} older titles have not been compared by file yet. Press Check files.</p>
      )}
      {found && !found.groups.length && <p className="muted">No duplicates found.</p>}
      {found && found.groups.length > 0 && (
        <ul className="tool-list scroll-box" tabIndex={0} aria-label="Duplicate titles">
          {found.groups.map((g) => (
            <li key={`${g.same}-${g.items.map((i) => i.id).join('-')}`}>
              <strong>{WHY[g.same]}:</strong>
              <ul className="tool-list">
                {g.items.map((i) => (
                  <li key={i.id}>
                    <a href={workspaceHref(i.id)}>{i.title || `Title ${i.id}`}</a>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
