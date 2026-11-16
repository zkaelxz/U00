import { startTypesetComics } from '../../api/libraryAdmin'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { ADMIN_JOB_IDS } from '../../types/libraryAdmin'
import { jobSucceeded } from '../../types/jobs'
import { AdminJobLine } from '../libraryAdmin/AdminJobLine'
import { useAdminJob } from '../libraryAdmin/useAdminJob'

/**
 * Library tools > "Typeset all comics again": redraws the pages of every comic that has
 * translations, the same as Redo > Typeset only on each title. It reads and translates nothing,
 * so it costs nothing and keeps the English and box edits already there.
 */
export function TypesetComicsCard() {
  const job = useAdminJob(ADMIN_JOB_IDS.typesetComics)
  const summary = job.done && job.job && jobSucceeded(job.job) ? job.job.message : null
  return (
    <div className="admin-block">
      <p className="muted">
        Redraws every comic page from its saved translations, for example after an update that changed how
        the English is placed in the balloons. Free: nothing is read or translated again, and your English
        and box edits are kept. It goes one title at a time; titles with no translations are skipped.
      </p>
      <div className="actions">
        <ConfirmButton
          name="all comics"
          label="Typeset all comics again…"
          verb="typeset"
          confirmLabel="Confirm: typeset all comics (free, keeps edits)"
          tone="primary"
          busy={job.active}
          onConfirm={() => void job.start(() => startTypesetComics())}
        />
      </div>
      <AdminJobLine job={job} busyText="Typesetting comics…" />
      {summary && <p role="status">{summary}</p>}
      <ErrorBanner error={job.startError} />
    </div>
  )
}
