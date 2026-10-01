import { useEffect, useState } from 'react'

import { listAdminUsers } from '../../api/adminUsers'
import { listJobs } from '../../api/jobs'
import {
  cleanStorage, restoreBackup, scanStorage, startBackup, startExport, startUserBackup,
} from '../../api/libraryAdmin'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { TypedConfirm } from '../../components/TypedConfirm'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, type PcMode } from '../../hooks/usePcOnly'
import type { AdminUser } from '../../types/adminUsers'
import {
  ADMIN_JOB_IDS, STORAGE_PRESETS, type LibraryStorageScan, type StoragePreset,
} from '../../types/libraryAdmin'
import { AdminJobLine } from './AdminJobLine'
import { SnapshotBlock } from './SnapshotBlock'
import { anyJobActive, describeClean, describeScan, formatBytes } from './libraryAdmin'
import { useAdminJob, type AdminJob } from './useAdminJob'

const SERVER = { pcOnly: true, serverText: true } as const
const JOB_POLL_MS = 3000

/** "Backup & storage": export, backup, restore and storage cleanup. PC only. */
export function AdminSection({ pc, exportable, exporter }: {
  pc: PcMode
  exportable: number
  // The page-level export job (shared with the selection bar).
  exporter: AdminJob
}) {
  if (pc === 'remote') {
    return (
      <Section title="Backup & storage" summary={PC_ONLY_SUMMARY} storageKey="library.admin">
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }
  return (
    <Section title="Backup & storage" summary="Export, back up, restore, free space" storageKey="library.admin">
      <ExportBlock exportable={exportable} job={exporter} />
      <BackupBlock />
      <RestoreBlock />
      <SnapshotBlock />
      <StorageBlock />
    </Section>
  )
}

function ExportBlock({ exportable, job }: { exportable: number; job: AdminJob }) {
  return (
    <div className="admin-block">
      <h3>Export</h3>
      <div className="actions">
        <button type="button" disabled={!exportable || job.active} onClick={() => void job.start(() => startExport())}>
          Export all translated ({exportable})
        </button>
      </div>
      {!exportable && (
        <p className="muted">Still needed: a translated drama. <a href="#/library">Pick one in the Library</a> and translate it.</p>
      )}
      <AdminJobLine job={job} busyText="Exporting…" artifact="export" />
      <ErrorBanner error={job.startError} describe={SERVER} />
    </div>
  )
}

function BackupBlock() {
  const full = useAdminJob(ADMIN_JOB_IDS.backup, 'backup')
  const database = useAdminJob(ADMIN_JOB_IDS.database, 'database')
  return (
    <div className="admin-block">
      <h3>Backup</h3>
      <div className="actions">
        <button type="button" disabled={full.active} onClick={() => void full.start(() => startBackup(false))}>
          Back up library
        </button>
        <button type="button" disabled={database.active} onClick={() => void database.start(() => startBackup(true))}>
          Database only
        </button>
      </div>
      <p className="muted">Database only is fast and small. Site sign-ins are never included.</p>
      <AdminJobLine job={full} busyText="Backing up…" artifact="backup" />
      <ErrorBanner error={full.startError} describe={SERVER} />
      <AdminJobLine job={database} busyText="Backing up the database…" artifact="database" />
      <ErrorBanner error={database.startError} describe={SERVER} />
      <UserBackup />
    </div>
  )
}

/** Backup of just one person's dramas and series, for moving to their own install. */
function UserBackup() {
  const job = useAdminJob(ADMIN_JOB_IDS.userBackup, 'user_backup')
  const [users, setUsers] = useState<AdminUser[]>([])
  const [owner, setOwner] = useState('')

  useEffect(() => {
    let cancelled = false
    // No user list (auth off, or not allowed): only the PC's own items.
    listAdminUsers().then((r) => !cancelled && setUsers(r.users), () => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <>
      <Field
        label="Backup of just my stuff"
        help="Only these dramas and series, with their media. No other person's items and no sign-in data. Restore it into a new install."
      >
        <select value={owner} onChange={(e) => setOwner(e.target.value)}>
          <option value="">Items owned at this PC</option>
          {users.map((u) => (
            <option key={u.id} value={String(u.id)}>{u.display_name || u.email}</option>
          ))}
        </select>
      </Field>
      <div className="actions">
        <button
          type="button"
          disabled={job.active}
          onClick={() => void job.start(() => startUserBackup(owner ? Number(owner) : null))}
        >
          Back up just these items
        </button>
      </div>
      <AdminJobLine job={job} busyText="Backing up…" artifact="user_backup" />
      <ErrorBanner error={job.startError} describe={SERVER} />
    </>
  )
}

function RestoreBlock() {
  const [file, setFile] = useState<File | null>(null)
  const [inputKey, setInputKey] = useState(0)
  const [jobsBusy, setJobsBusy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<unknown>(null)

  // While a file is picked (and until a restore succeeds), check every 3 s
  // whether any job is queued or running.
  useEffect(() => {
    if (!file || done) return
    let cancelled = false
    const check = () =>
      listJobs().then(
        (r) => !cancelled && setJobsBusy(anyJobActive(r.items)),
        () => undefined,
      )
    void check()
    const timer = setInterval(check, JOB_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [file, done])

  const restore = () => {
    if (!file) return
    setBusy(true)
    setError(null)
    restoreBackup(file).then(
      () => {
        setBusy(false)
        setDone(true)
        setFile(null)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  const cancel = () => {
    setFile(null)
    setError(null)
    setInputKey((k) => k + 1)
  }

  return (
    <div className="admin-block">
      <h3>Restore</h3>
      {done ? (
        <div className="actions" role="status">
          <span>Restored.</span>
          <button type="button" className="primary" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      ) : (
        <>
          <Field label="Backup file" help="A .zip made by Back up library.">
            <input
              key={inputKey}
              type="file"
              accept=".zip"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null)
                setError(null)
              }}
            />
          </Field>
          {file && (
            <TypedConfirm
              word="RESTORE"
              exact
              autoFocus
              action="Restore"
              busy={busy}
              blocked={jobsBusy && !busy ? 'Wait for the running job to finish.' : null}
              onConfirm={restore}
              onCancel={cancel}
            >
              <p>
                Replaces the whole library with this backup. Back up first if unsure. Everyone will
                need to sign in again.
              </p>
            </TypedConfirm>
          )}
          {busy && <p className="muted" role="status">Uploading and restoring… keep this tab open.</p>}
          <ErrorBanner error={error} describe={SERVER} />
        </>
      )}
    </div>
  )
}

function StorageBlock() {
  const [preset, setPreset] = useState<StoragePreset>('balanced')
  const [scan, setScan] = useState<LibraryStorageScan | null>(null)
  const [cleaning, setCleaning] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  // Scan is admin.library (default copy); Clean up is PC-only.
  const [scanError, setScanError] = useState<unknown>(null)
  const [error, setError] = useState<unknown>(null)

  const runScan = () => {
    setBusy(true)
    setScanError(null)
    setError(null)
    setResult(null)
    scanStorage(preset).then(
      (s) => {
        setBusy(false)
        setScan(s)
      },
      (e: unknown) => {
        setBusy(false)
        setScanError(e)
      },
    )
  }

  const clean = () => {
    setBusy(true)
    setError(null)
    cleanStorage(preset).then(
      (r) => {
        setBusy(false)
        setCleaning(false)
        setScan(null)
        setResult(describeClean(r))
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  return (
    <div className="admin-block">
      <h3>Storage</h3>
      <div className="field-row preset-row">
        <Field label="Cleanup preset" help="Scan is a dry run: nothing is removed until Clean up.">
          <select
            value={preset}
            onChange={(e) => {
              setPreset(e.target.value as StoragePreset)
              setScan(null)
              setCleaning(false)
            }}
          >
            {STORAGE_PRESETS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </Field>
      </div>
      <div className="actions">
        <button type="button" disabled={busy} onClick={runScan}>Scan</button>
        <button type="button" className="danger" disabled={busy || !scan} onClick={() => setCleaning(true)}>
          Clean up…
        </button>
      </div>
      {!scan && <p className="muted">Scan first.</p>}
      {scan && (
        <>
          <p data-testid="storage-scan">{describeScan(scan)}</p>
          <ul className="storage-cats">
            {scan.categories.map((c) => (
              <li key={c.key}>
                {c.label}: {formatBytes(c.bytes)}{' '}
                <span className="muted">{c.selected ? 'cleaned' : 'kept'}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {scan && cleaning && (
        <TypedConfirm word="CLEAN" exact autoFocus action="Clean up" busy={busy} onConfirm={clean} onCancel={() => setCleaning(false)}>
          <p>Removes the files this preset drops from every drama. Dramas with a running job are skipped.</p>
        </TypedConfirm>
      )}
      {result && <p role="status">{result}</p>}
      <ErrorBanner error={scanError} />
      <ErrorBanner error={error} describe={SERVER} />
    </div>
  )
}
