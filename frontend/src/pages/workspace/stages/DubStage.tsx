import { useEffect, useState } from 'react'

import { dubApi, dubTrackUrl } from '../../../api/dub'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { useJob, useJobRun } from '../../../hooks/useJob'
import type { DubConfig, DubPacing } from '../../../types/dub'
import { useStage } from '../StageContext'
import {
  buildDubRequest,
  dubAdvancedSummary,
  dubBlocker,
  dubSettingsLine,
  formatFactor,
  formatMs,
  initialDubForm,
  pacingRows,
  pacingSummary,
  type DubForm,
} from './dubForm'
import { JobPanel } from './JobPanel'
import { NarrationPanel } from './NarrationPanel'
import './dub.css'

export default function DubStage() {
  const { dramaId, onJobDone } = useStage()
  const [cfg, setCfg] = useState<DubConfig | null>(null)
  const [pacing, setPacing] = useState<DubPacing | null>(null)
  const [form, setForm] = useState<DubForm | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [jobId, setJobId, runKey] = useJobRun()
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    let cancelled = false
    Promise.all([dubApi.config(dramaId), dubApi.pacing(dramaId)]).then(
      ([c, p]) => {
        if (cancelled) return
        setCfg(c)
        setPacing(p)
        setForm((f) => f ?? initialDubForm(c))
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: () => {
      onJobDone()
      setReloads((n) => n + 1)
    },
  })
  const busy = jobId !== null && !done && !pollError

  if (!cfg || !form) {
    return (
      <div className="stage-dub">
        {error ? <ErrorBanner error={error} /> : <p className="muted">Loading…</p>}
      </div>
    )
  }

  const set = (patch: Partial<DubForm>) => setForm({ ...form, ...patch })
  const blocker = dubBlocker(cfg, form)
  const start = () =>
    dubApi.run(dramaId, buildDubRequest(cfg, form)).then((r) => {
      setError(null)
      setJobId(r.job_id)
    }, setError)
  const trackReady = cfg.track_available || (done && job?.status === 'done')
  const showPacing =
    pacing?.available && (pacing.lines.length > 0 || Object.keys(pacing.counts).length > 0)

  return (
    <div className="stage-dub">
      <section className="panel" aria-label="Dub">
        <h3>
          Dub {cfg.gpu_required && <span className="badge">GPU</span>}
        </h3>
        <p className="muted" data-testid="dub-summary">
          {cfg.speakable_line_count} speakable lines · {trackReady ? 'Dub track ready' : 'No dub track yet'}
        </p>
        <div className="dub-grid">
          <Field label="Voice engine">
            <select value={form.engine} onChange={(e) => set({ engine: e.target.value })}>
              {cfg.tts_engines.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                  {t.requires_internet ? ' (online)' : ''}
                </option>
              ))}
            </select>
          </Field>
          {cfg.is_narration && (
            <Field label="Narration language">
              <select value={form.language} onChange={(e) => set({ language: e.target.value })}>
                {cfg.narration_language_options.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </Field>
          )}
        </div>
        <div className="dub-actions">
          <button type="button" className="primary" disabled={busy || blocker !== null} onClick={start}>
            Generate dub
          </button>
          <p className="muted" data-testid="dub-settings">
            {blocker ?? dubSettingsLine(cfg, form)}
          </p>
        </div>
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {trackReady && (
          <p className="dub-note">
            <a href={dubTrackUrl(dramaId)} download>Download dub track (WAV)</a>
          </p>
        )}
        <Section storageKey="dub.advanced" title="Advanced" summary={dubAdvancedSummary(cfg, form)}>
          {!cfg.is_narration && (
            <div className="dub-grid">
              <Field label="Max speed-up" unit="x" help="How much a line may be sped up to fit its slot (1.0 to 2.0).">
                <input
                  type="number"
                  step={0.05}
                  min={1}
                  max={2}
                  value={form.maxSpeedup}
                  onChange={(e) => set({ maxSpeedup: Number(e.target.value) })}
                />
              </Field>
              <Field label="Max slow-down" unit="x" help="How much a line may be slowed down to fill its slot (0.5 to 1.0).">
                <input
                  type="number"
                  step={0.05}
                  min={0.5}
                  max={1}
                  value={form.maxSlowdown}
                  onChange={(e) => set({ maxSlowdown: Number(e.target.value) })}
                />
              </Field>
            </div>
          )}
          <div className="dub-check">
            <Field
              label="Keep background music"
              help={
                cfg.can_keep_background
                  ? 'Keeps the original background music under the dub. Real audio has not been verified.'
                  : 'Only available for video dubs that have source audio and the separation tools installed.'
              }
            >
              <input
                type="checkbox"
                disabled={!cfg.can_keep_background}
                checked={cfg.can_keep_background && form.keepBackground}
                onChange={(e) => set({ keepBackground: e.target.checked })}
              />
            </Field>
          </div>
        </Section>
      </section>
      {cfg.is_narration && <NarrationPanel dramaId={dramaId} busy={busy} onJobStarted={setJobId} />}
      {pacing && showPacing && (
        <Section title="Pacing of the last run" summary={pacingSummary(pacing.counts)}>
          <div className="pacing-scroll">
            <table className="pacing-table">
              <thead>
                <tr>
                  <th>Line</th>
                  <th>Fit</th>
                  <th>Speed</th>
                  <th>Clip</th>
                  <th>Window</th>
                </tr>
              </thead>
              <tbody>
                {pacingRows(pacing.lines).map((l) => (
                  <tr key={l.idx}>
                    <td>{l.idx}</td>
                    <td>{l.status}</td>
                    <td>{formatFactor(l.factor)}</td>
                    <td>{formatMs(l.clip_ms)}</td>
                    <td>{formatMs(l.window_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
      {jobId && <JobPanel job={job} pollError={pollError} />}
    </div>
  )
}
