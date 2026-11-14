import { useEffect, useRef } from 'react'

import { ButtonLink } from '../../components/Button'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { humanize } from '../../components/labels'
import { RetryBanner } from '../../components/RetryBanner'
import { Section } from '../../components/Section'
import { usePcOnly } from '../../hooks/usePcOnly'
import { routeHref } from '../../router'
import { buttonClass } from '../../components/uiClasses'
import { engineLabel, languageLabel } from '../../labels'
import { cancelNeedsConfirm } from '../../types/jobs'
import { PreflightCard } from '../preflight/PreflightCard'
import { STAGE_LABELS, type StageId } from '../workspace/stages'
import { foundForFile, planForFile, type FileRunRequest } from './addBoxModel'
import { STEPS, STEP_LABEL, STEP_STAGE, optionsSummary, percentText, titleFor, type FlowStep } from './makeSubtitlesFlow'
import { LANGUAGES, type FileRun } from './useFileRun'
import './makeSubtitles.css'

const VARIANTS = ['en-US', 'en-GB', 'en-AU']

/** What went wrong, or why the box is back with a title already made. It comes before the form it asks to fix. */
export function FileRunNotice({ run }: { run: FileRun }) {
  const { flow, note } = run
  return (
    <>
      {flow.phase === 'error' && (
        <div className="stack">
          <ErrorBanner error={flow.error} />
          <p className="make-subtitles-failed">
            <strong>Failed at {STEP_LABEL[flow.step]}.</strong>{' '}
            {flow.dramaId !== null && (
              <ButtonLink size="sm" href={routeHref({ name: 'drama', id: flow.dramaId, stage: STEP_STAGE[flow.step] })}>
                Fix in {STAGE_LABELS[STEP_STAGE[flow.step] as StageId]}
              </ButtonLink>
            )}
          </p>
        </div>
      )}
      {note && (
        <p className="muted make-subtitles-note" role="status">
          {note.kind === 'cancelled'
            ? (note.dramaId !== null ? 'Cancelled. The title was kept.' : 'Cancelled.')
            : 'The last run stopped before the upload finished.'}{' '}
          {note.dramaId !== null && (
            <ButtonLink size="sm" variant="ghost" href={routeHref({ name: 'drama', id: note.dramaId, stage: null })}>Open title</ButtonLink>
          )}
        </p>
      )}
    </>
  )
}

/** The run under way, then Download SRT. */
export function FileRunStatus({ run }: { run: FileRun }) {
  const { flow, job, jobId, stepNow } = run
  const busy = flow.phase === 'running'
  const pct = percentText(job?.progress)
  const status = useRef<HTMLDivElement>(null)
  useEffect(() => { if (busy) status.current?.focus() }, [busy])
  return (
    <div className="stack">
      <div ref={status} tabIndex={-1} role="status" aria-live="polite" className="make-subtitles-status">
        {busy ? (
          <>
            <ol className="make-subtitles-steps">
              {STEPS.map((s: FlowStep) => (
                <li key={s} aria-current={s === stepNow ? 'step' : undefined}
                  className={STEPS.indexOf(s) < STEPS.indexOf(stepNow!) ? 'is-done' : s === stepNow ? 'is-current' : undefined}>
                  {STEP_LABEL[s]}{s === stepNow && pct && ` ${pct}`}
                </li>
              ))}
            </ol>
            {job?.message && <p className="muted">{job.message}</p>}
          </>
        ) : (
          <p><strong>Subtitles are ready.</strong></p>
        )}
        {/* Inside the status, so a screen reader meets it with the run without focus moving to it. */}
        {run.waiting && (
          <p className="muted">
            “{run.waiting.file.name}” has not been started, because this run was already under way.{' '}
            {busy ? 'It will be here to start when this one is finished.' : 'Make another puts it in the box to start.'}
          </p>
        )}
      </div>
      <ErrorBanner error={run.cancelError ?? run.downloadError} />
      {busy ? (
        (jobId || stepNow === 'upload') && <div className="actions">
          {job && (stepNow === 'transcribe' || cancelNeedsConfirm(job)) ? (
            <ConfirmButton name="this run" label="Cancel…" verb="cancel" confirmLabel="Confirm cancel" onConfirm={run.cancel} />
          ) : (
            <button type="button" className={buttonClass('secondary')} onClick={run.cancel}>Cancel</button>
          )}
        </div>
      ) : (
        <div className="make-subtitles-go">
          <button type="button" className={buttonClass('primary')} onClick={() => void run.download()}>Download SRT</button>
          <div className="actions">
            {flow.phase === 'done' && (
              <>
                <ButtonLink variant="ghost" href={routeHref({ name: 'drama', id: flow.dramaId, stage: 'review' })}>Review lines</ButtonLink>
                <ButtonLink variant="ghost" href={routeHref({ name: 'drama', id: flow.dramaId, stage: null })}>Open title</ButtonLink>
              </>
            )}
            <button type="button" className={buttonClass('ghost')} onClick={run.reset}>Make another</button>
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * A chosen file in the Add box: what was found, what the main button will do, the run's choices under
 * Change options, and Only add it. `onMake` (the Library) hands the run on instead of running it.
 */
export function FilePlan({ run, onChooseFile, onOnlyAdd, onClear, onMake }: {
  run: FileRun
  onChooseFile: () => void
  onOnlyAdd: () => void
  onClear: () => void
  onMake?: (r: FileRunRequest) => void
}) {
  const pc = usePcOnly()
  const { file, engine, list, engines, language, title, variant, defaultLocale, blocker } = run
  const submit = () => {
    if (blocker || !file) return
    if (!onMake) { void run.start(run.choices(file)); return }
    if (!run.refused(file)) onMake(run.choices(file))
  }
  return (
    <form className="stack make-subtitles" onSubmit={(e) => { e.preventDefault(); submit() }}>
      {file && (
        <div className="add-box-found">
          <p data-testid="add-found"><strong>Found:</strong> {foundForFile(file)}</p>
          <p data-testid="add-plan">{planForFile({ pc, language: LANGUAGES.includes(language) ? language : 'zh', engine })}</p>
        </div>
      )}
      <RetryBanner error={engines.error} what="the translators" onRetry={run.retryEngines} />
      {engine && (
        <PreflightCard needs={['whisper', 'ffmpeg', 'key']} engine={engine}
          onReady={(_ok, rows) => run.setBlockers(rows)}
          onUseEngine={(name) => run.setPickedEngine(name)} />
      )}
      <div className="make-subtitles-go">
        <button type="submit" className={buttonClass('primary')} disabled={!!blocker} aria-describedby="make-subtitles-blocker">
          Make English subtitles
        </button>
        {blocker && (
          <p className="stage-blocker" id="make-subtitles-blocker">
            <span>{blocker}</span>
            {!file && <button type="button" className={buttonClass('ghost', 'sm')} onClick={onChooseFile}>Choose a file</button>}
          </p>
        )}
      </div>
      <Section title="Change options" summary={optionsSummary(file ? titleFor(file.name, title) : null, variant)}>
        <div className="make-subtitles-fields">
          <Field label="Translator">
            <select className="w-md" value={engine} disabled={!list.length} onChange={(e) => run.setPickedEngine(e.target.value)}>
              {!list.length && <option value="">{engines.error ? 'Translators unavailable' : 'Loading translators...'}</option>}
              {list.map((e) => <option key={e.name} value={e.name}>{engineLabel(e.name)}</option>)}
            </select>
          </Field>
          <Field label="Source language">
            <select className="w-md" value={LANGUAGES.includes(language) ? language : 'zh'} onChange={(e) => run.setLanguage(e.target.value)}>
              {LANGUAGES.map((l) => <option key={l} value={l}>{languageLabel(l)}</option>)}
            </select>
          </Field>
          <Field label="Title">
            <input value={title} placeholder={file ? titleFor(file.name, '') : ''} onChange={(e) => run.setTitle(e.target.value)} />
          </Field>
          <Field label="English variant">
            <select value={variant} onChange={(e) => run.setVariant(e.target.value)}>
              <option value="">{defaultLocale ? `Default (${humanize('locale', defaultLocale)})` : 'Settings default'}</option>
              {VARIANTS.map((v) => <option key={v} value={v}>{humanize('locale', v)}</option>)}
            </select>
          </Field>
        </div>
      </Section>
      {file && (
        <div className="actions">
          <button type="button" className={buttonClass('ghost')} onClick={onOnlyAdd}>Only add it, don&apos;t start</button>
          <button type="button" className={buttonClass('ghost')} onClick={onClear}>Clear</button>
        </div>
      )}
    </form>
  )
}
