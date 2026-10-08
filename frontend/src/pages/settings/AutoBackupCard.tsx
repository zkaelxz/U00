/*
 * Settings > Automatic backups : an opt-in scheduled backup
 * that keeps rotating copies (one a day for the last 2 days, plus the first
 * of each of the last 2 weeks), plus "Back up now", which adds a copy. The copies are listed newest first; restoring a
 * drama from one, or deleting one, is in Library tools (SnapshotBlock).
 * PC only: away from the PC the Card shows the "Run this on the main PC."
 * note, and nothing is fetched until /api/meta has answered.
 *
 * Each change sends only the field that changed.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react'

import { backUpNow, getBackupSettings, getSnapshot, updateBackupSettings } from '../../api/backups'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { safeDetail } from '../../components/errorMessages'
import { buttonClass } from '../../components/uiClasses'
import { useJob, useJobRun } from '../../hooks/useJob'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import {
  AUTO_BACKUP_JOB_ID, type AutoBackupSettings, type AutoBackupSettingsUpdate, type BackupFrequency,
  type SnapshotInfo,
} from '../../types/backups'
import { jobFailed, jobSucceeded } from '../../types/jobs'
import {
  DEFAULT_FOLDER_TEXT, FOLDER_RULES, FREQUENCY_OPTIONS, ROTATION_NOTE, UNMANAGED_LABEL, UNMANAGED_NOTE, changedSettings,
  describeCopy, describeSnapshot, folderChange, formatWhen, nextRunText, serverSentence, settingsSummary, splitCopies,
} from '../backupsFormat'
import { percent } from '../libraryAdmin/libraryAdmin'
import '../backups.css'

const TITLE = 'Automatic backups'
const SERVER = { pcOnly: true, serverText: true } as const

export function AutoBackupCard() {
  const pc = usePcOnly()
  if (pc === 'remote') {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  return <AutoBackupControls />
}

function AutoBackupControls() {
  const [settings, setSettings] = useState<AutoBackupSettings | null>(null)
  const [snapshot, setSnapshot] = useState<SnapshotInfo | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [folderDraft, setFolderDraft] = useState('')
  const [folderError, setFolderError] = useState<string | null>(null)
  const [folderNote, setFolderNote] = useState<string | null>(null)
  const savingFolder = useRef(false)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<unknown>(null)
  const [finished, setFinished] = useState<string | null>(null)
  const [runId, setRun, runKey] = useJobRun()
  const freqLabel = useId()

  const loadSnapshot = useCallback(
    () => getSnapshot().then((s) => { setSnapshot(s); return s }, () => { setSnapshot(null); return null }),
    [],
  )

  const loadSettings = useCallback(
    () =>
      getBackupSettings().then(
        (s) => {
          setSettings(s)
          setFolderDraft(s.folder)
          return s
        },
        (e: unknown) => {
          setError(e)
          return null
        },
      ),
    [],
  )

  // Wait for /api/meta first, so a viewer away from the PC makes no calls here.
  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      void loadSettings().then((s) => {
        // A scheduled run already going: follow it.
        if (live && s?.running) setRun(AUTO_BACKUP_JOB_ID)
      })
      void loadSnapshot()
    })
    return () => {
      live = false
    }
  }, [loadSettings, loadSnapshot, setRun])

  const { job, error: pollError, done } = useJob(runId, {
    runKey,
    onDone: (j) => {
      setFinished(jobSucceeded(j) ? 'Backup finished.' : null)
      void loadSettings()
      void loadSnapshot()
    },
  })
  const running = starting || (!!runId && !done && !pollError)

  // Optimistic: show the change at once, roll back if the server refuses.
  const save = (next: AutoBackupSettingsUpdate) => {
    if (!settings) return
    const changes = changedSettings(settings, next)
    if (!Object.keys(changes).length) return
    const previous = settings
    setError(null)
    setSettings({ ...settings, ...changes })
    updateBackupSettings(changes).then(setSettings, (e: unknown) => {
      setSettings(previous)
      setError(e)
    })
  }

  const saveFolder = () => {
    if (!settings || savingFolder.current) return
    const next = folderChange(folderDraft, settings.folder)
    if (next === null) return
    savingFolder.current = true
    setFolderError(null)
    setFolderNote(null)
    updateBackupSettings({ folder: next }).then(
      (s) => {
        savingFolder.current = false
        setSettings(s)
        setFolderDraft(s.folder)
        setFolderNote('Folder saved.')
      },
      (e: unknown) => {
        savingFolder.current = false
        setFolderError(serverSentence(e, FOLDER_RULES))
      },
    )
  }

  // A new copy never replaces one, so there is nothing to confirm.
  const start = () => {
    setStarting(true)
    setStartError(null)
    setFinished(null)
    backUpNow().then(
      () => {
        setStarting(false)
        setRun(AUTO_BACKUP_JOB_ID)
      },
      (e: unknown) => {
        setStarting(false)
        setStartError(e)
      },
    )
  }

  const folderDirty = !!settings && folderChange(folderDraft, settings.folder) !== null
  const lastRun = settings && formatWhen(settings.last_run_at)
  const lastAttempt = settings && formatWhen(settings.last_attempt_at)
  const next = settings && nextRunText(settings)
  const j = job
  const { managed, unmanaged } = splitCopies(snapshot?.copies ?? [])

  return (
    <Card
      title={TITLE}
      meta={settings ? settingsSummary(settings) : undefined}
      aria-label={TITLE}
      className="auto-backup"
    >
      <p className="settings-note">{ROTATION_NOTE}</p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={SERVER} />
      {!settings ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="setting-list">
            <Field label="Back up automatically" help="Runs while Baihe is running; it checks once an hour whether a backup is due.">
              <Toggle checked={settings.enabled} onChange={(v) => save({ enabled: v })} />
            </Field>
            <div className="field-item backup-frequency">
              <div className="field-label-row">
                <span id={freqLabel}>How often</span>
              </div>
              <div className="field-control">
                <div className="segmented" role="radiogroup" aria-labelledby={freqLabel}>
                  {FREQUENCY_OPTIONS.map(([value, label]) => (
                    <label key={value}>
                      <input
                        type="radio"
                        name={`${freqLabel}-frequency`}
                        value={value}
                        checked={settings.frequency === value}
                        onChange={() => save({ frequency: value as BackupFrequency })}
                      />
                      <span>{label}</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <Field
              label="Include media (audio, video, pages)"
              help="Off: text, translations and settings only (small, fast). On: also all audio, video and page images (can be large)."
            >
              <Toggle checked={settings.include_media} onChange={(v) => save({ include_media: v })} />
            </Field>
          </div>

          <form
            className="settings-form"
            onSubmit={(e) => {
              e.preventDefault()
              saveFolder()
            }}
          >
            <Field
              label="Backup folder"
              help="Empty uses the library's backups folder. Otherwise a full path to an existing folder outside the library."
              error={folderError}
            >
              <input
                value={folderDraft}
                placeholder={DEFAULT_FOLDER_TEXT}
                spellCheck={false}
                autoComplete="off"
                onChange={(e) => {
                  setFolderDraft(e.target.value)
                  setFolderError(null)
                  setFolderNote(null)
                }}
                onBlur={saveFolder}
              />
            </Field>
            {folderDirty && (
              <div className="settings-actions">
                <button type="submit" className={buttonClass('secondary')}>
                  Save folder
                </button>
              </div>
            )}
          </form>
          {folderNote && (
            <p className="muted" role="status">
              {folderNote}
            </p>
          )}

          <div className="backup-status" data-testid="auto-backup-status">
            <p>{lastRun ? `Last backup: ${lastRun}.` : 'No backup has run yet.'}</p>
            {next && <p>{next}</p>}
            {settings.last_error && (
              <p className="error">
                {lastAttempt ? `Last attempt (${lastAttempt}) failed: ` : 'Last attempt failed: '}
                {safeDetail(settings.last_error) ?? 'see the app log.'}
              </p>
            )}
            <p className="muted" data-testid="auto-backup-snapshot">
              Newest copy: {describeSnapshot(snapshot)}
            </p>
            {managed.length > 0 && (
              <ul className="backup-copies" aria-label="Backup copies, newest first" data-testid="auto-backup-copies">
                {managed.map((c) => (
                  <li key={c.name}>{describeCopy(c)}</li>
                ))}
              </ul>
            )}
            {unmanaged.length > 0 && (
              <>
                <p className="muted">
                  {UNMANAGED_LABEL}: {UNMANAGED_NOTE}
                </p>
                <ul className="backup-copies" aria-label={UNMANAGED_LABEL} data-testid="auto-backup-unmanaged">
                  {unmanaged.map((c) => (
                    <li key={c.name}>{describeCopy(c)}</li>
                  ))}
                </ul>
              </>
            )}
          </div>

          <div className="settings-actions">
            <button
              type="button"
              className={buttonClass('primary')}
              disabled={running || settings.running}
              onClick={start}
            >
              {starting ? 'Starting…' : 'Back up now'}
            </button>
          </div>
          <div aria-live="polite">
            {running && (
              <p className="muted" data-testid="auto-backup-job">
                {j?.status === 'queued' ? 'Waiting for another job…' : 'Backing up…'}
                {j && percent(j.progress) && ` ${percent(j.progress)}`}
              </p>
            )}
            {!running && finished && <p role="status">{finished}</p>}
            {j && done && jobFailed(j) && !settings.last_error && (
              <p className="error" role="alert">
                {(j.error && safeDetail(j.error)) || 'The backup failed. Details are in the app log.'}
              </p>
            )}
          </div>
          <ErrorBanner error={startError} onDismiss={() => setStartError(null)} describe={SERVER} />
          <ErrorBanner error={pollError} />
        </>
      )}
    </Card>
  )
}
