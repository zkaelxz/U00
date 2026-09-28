import { useEffect, useState } from 'react'

import { dubApi } from '../../../api/dub'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { useJob } from '../../../hooks/useJob'
import type { DubConfig, DubPacing } from '../../../types/dub'
import { useStage } from '../StageContext'
import {
  buildDubRequest,
  dubBlocker,
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
  const [jobId, setJobId] = useState<string | null>(null)
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

  return (
    <div className="stage-dub">
      <section className="panel" aria-label="Dub">
        <h3>Dub</h3>
        <p className="muted" data-testid="dub-summary">
          {cfg.speakable_line_count} speakable lines ·{' '}
          {cfg.track_available ? 'A dub track exists' : 'No dub track yet'}
          {cfg.gpu_required ? ' · Uses the GPU' : ''}
        </p>
        <div className="dub-grid">
          <label>
            Voice engine
            <select value={form.engine} onChange={(e) => set({ engine: e.target.value })}>
              {cfg.tts_engines.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                  {t.requires_internet ? ' (online)' : ''}
                </option>
              ))}
            </select>
          </label>
          {cfg.is_narration ? (
            <label>
              Narration language
              <select value={form.language} onChange={(e) => set({ language: e.target.value })}>
                {cfg.narration_language_options.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <>
              <label>
                Max speed-up (1.0 to 2.0)
                <input
                  type="number"
                  step={0.05}
                  min={1}
                  max={2}
                  value={form.maxSpeedup}
                  onChange={(e) => set({ maxSpeedup: Number(e.target.value) })}
                />
              </label>
              <label>
                Max slow-down (0.5 to 1.0)
                <input
                  type="number"
                  step={0.05}
                  min={0.5}
                  max={1}
                  value={form.maxSlowdown}
                  onChange={(e) => set({ maxSlowdown: Number(e.target.value) })}
                />
              </label>
            </>
          )}
        </div>
        <label className="inline">
          <input
            type="checkbox"
            disabled={!cfg.can_keep_background}
            checked={cfg.can_keep_background && form.keepBackground}
            onChange={(e) => set({ keepBackground: e.target.checked })}
          />
          Keep the original background music (BGM-preserving; real audio has not been verified)
        </label>
        {!cfg.can_keep_background && (
          <p className="muted">
            Only available for video dubs that have source audio and the separation tools installed.
          </p>
        )}
        {blocker && <p className="muted">{blocker}</p>}
        <button type="button" disabled={busy || blocker !== null} onClick={start}>
          Generate dub
        </button>
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        <p className="muted">Downloading the finished dub track is not available yet.</p>
      </section>
      {cfg.is_narration && <NarrationPanel dramaId={dramaId} busy={busy} onJobStarted={setJobId} />}
      {pacing?.available && (
        <section className="panel" aria-label="Pacing">
          <h3>Pacing of the last run</h3>
          <p className="muted" data-testid="pacing-summary">
            {pacingSummary(pacing.counts)}
          </p>
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
        </section>
      )}
      {jobId && <JobPanel job={job} pollError={pollError} />}
    </div>
  )
}
