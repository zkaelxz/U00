/*
 * Library tools > Automatic backup copies > "From a backup file…": import
 * chosen dramas from a backup .zip (an automatic copy or a manual "Back up
 * library" zip) or a database backup into the library, as NEW dramas. Steps:
 * choose the file (listed by the server, nothing changes), tick dramas, read
 * what will happen, type RESTORE. The file is sent again for the import.
 */
import { useState } from 'react'

import { importBackupFileDramas, listBackupFileDramas } from '../../api/backups'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { TypedConfirm } from '../../components/TypedConfirm'
import { buttonClass } from '../../components/uiClasses'
import { mediaTypeLabel } from '../../labels'
import { RESTORE_SNAPSHOT_WORD, type BackupFileDramaList, type ImportDramasDone } from '../../types/backups'
import { importNotes, toggleId } from '../backupFileImportModel'
import '../backups.css'

const SERVER = { pcOnly: true, serverText: true } as const

export function BackupFileImport({ onDone, onCancel }: {
  onDone: (r: ImportDramasDone) => void
  onCancel: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [list, setList] = useState<BackupFileDramaList | null>(null)
  const [chosen, setChosen] = useState<Set<number>>(new Set())
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const pick = (f: File | null) => {
    setFile(f)
    setList(null)
    setChosen(new Set())
    setConfirming(false)
    setError(null)
    if (!f) return
    setBusy(true)
    listBackupFileDramas(f).then(
      (l) => {
        setBusy(false)
        setList(l)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  const run = () => {
    if (!file) return
    setBusy(true)
    setError(null)
    importBackupFileDramas(file, [...chosen]).then(
      (r) => {
        setBusy(false)
        onDone(r)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  const picked = list ? list.dramas.filter((d) => chosen.has(d.id)) : []
  return (
    <div className="admin-block" data-testid="backup-file-import">
      <Field label="Backup file (.zip or .db)">
        <input
          type="file"
          accept=".zip,.db,application/zip"
          disabled={busy || confirming}
          onChange={(e) => pick(e.target.files?.[0] ?? null)}
        />
      </Field>
      {busy && !confirming && <p className="muted" role="status">Reading the file…</p>}
      {list && !confirming && (
        <>
          {!list.dramas.length ? (
            <p className="muted">This file has no titles.</p>
          ) : (
            <>
              <p className="muted">Tick the titles to import. Nothing changes until you confirm.</p>
              <ul className="snapshot-dramas import-dramas" aria-label="Titles in the file">
                {list.dramas.map((d) => (
                  <li key={d.id}>
                    <label>
                      <input
                        type="checkbox"
                        checked={chosen.has(d.id)}
                        onChange={() => setChosen((c) => toggleId(list.dramas, c, d.id))}
                      />
                      <span>
                        <span className="snapshot-drama-title">{d.title}</span>
                        <span className="snapshot-drama-meta">
                          {mediaTypeLabel(d.media_type)} · {d.line_count.toLocaleString('en-US')}{' '}
                          {d.line_count === 1 ? 'line' : 'lines'}
                          {d.has_media ? ' · with files' : ''}
                        </span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </>
          )}
          <div className="actions">
            <button type="button" disabled={!picked.length} onClick={() => setConfirming(true)}>
              Import {picked.length ? `${picked.length} selected` : 'selected'}…
            </button>
          </div>
        </>
      )}
      {list && confirming && (
        <TypedConfirm
          word={RESTORE_SNAPSHOT_WORD}
          exact
          action="Import titles"
          busy={busy}
          onConfirm={run}
          onCancel={() => setConfirming(false)}
        >
          <ul className="restore-notes" data-testid="import-notes">
            {importNotes(list, picked).map((n) => <li key={n}>{n}</li>)}
          </ul>
        </TypedConfirm>
      )}
      {busy && confirming && <p className="muted" role="status">Importing… keep this tab open.</p>}
      <ErrorBanner error={error} describe={SERVER} />
      <div className="actions">
        <button type="button" className={buttonClass('ghost')} disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}
