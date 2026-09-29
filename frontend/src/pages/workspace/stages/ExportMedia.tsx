import { useState } from 'react'

import { artifactUrl } from '../../../api/client'
import { getArtifactInfo, getEpub, startAudiobook, startBurnedVideo } from '../../../api/export'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { useJob, useJobRun } from '../../../hooks/useJob'
import type { ArtifactInfo, AssExportRequest, MediaExportStarted, MediaKind } from '../../../types/export'
import { formatBytes } from '../exportForm'
import { useStage } from '../StageContext'
import { JobPanel } from './JobPanel'

export function ExportEpub() {
  const { dramaId } = useStage()
  const [field, setField] = useState<'en' | 'zh'>('en')
  const [error, setError] = useState<unknown>(null)

  const download = () =>
    getEpub(dramaId, field).then((blob) => {
      setError(null)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `drama_${dramaId}_${field}.epub`
      a.click()
      URL.revokeObjectURL(url)
    }, setError)

  return (
    <section className="panel" aria-label="EPUB">
      <h3>EPUB (novel narration)</h3>
      <label>
        Text
        <select value={field} onChange={(e) => setField(e.target.value as 'en' | 'zh')}>
          <option value="en">English</option>
          <option value="zh">Source language</option>
        </select>
      </label>
      <button type="button" onClick={download}>Download EPUB</button>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </section>
  )
}

interface JobProps {
  title: string
  label: string
  kind: MediaKind
  // Starts the job, or returns a plain-language reason it cannot start yet.
  start: () => Promise<MediaExportStarted> | string
  note: string
}

function MediaJobSection({ title, label, kind, start, note }: JobProps) {
  const { dramaId } = useStage()
  const [jobId, setJobId, runKey] = useJobRun()
  const [error, setError] = useState<unknown>(null)
  const [artifact, setArtifact] = useState<ArtifactInfo | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      if (j.status !== 'done') return
      getArtifactInfo(dramaId, kind).then(setArtifact, setError)
    },
  })
  const busy = jobId !== null && !done && !pollError

  const run = () => {
    const p = start()
    setArtifact(null)
    if (typeof p === 'string') {
      setProblem(p)
      return
    }
    setProblem(null)
    p.then(
      (r) => {
        setError(null)
        setJobId(r.job_id)
      },
      setError,
    )
  }

  return (
    <section className="panel" aria-label={title}>
      <h3>{title}</h3>
      <p className="muted">{note}</p>
      <button type="button" disabled={busy} onClick={run}>{label}</button>
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {jobId && <JobPanel job={job} pollError={pollError} />}
      {artifact && job?.status === 'done' && (
        <p data-testid={`artifact-${kind}`}>
          <a href={artifactUrl(dramaId, kind)} download>Download {artifact.name}</a>{' '}
          <span className="muted">({formatBytes(artifact.size)})</span>
        </p>
      )}
    </section>
  )
}

export function ExportMediaJobs({ request }: { request: () => { request?: AssExportRequest; error?: string } }) {
  const { dramaId } = useStage()
  return (
    <>
      <MediaJobSection
        title="Audiobook"
        label="Start audiobook export"
        kind="audio"
        note="Encodes the narration audio to an .m4b file with chapter markers. Needs a finished narration track (Dub stage) and ffmpeg."
        start={() => startAudiobook(dramaId)}
      />
      <MediaJobSection
        title="Burned-in video"
        label="Start burned-in video export"
        kind="video"
        note="Burns the subtitles into the source video using the ASS style above. Needs an uploaded source video and ffmpeg. This can take a while."
        start={() => {
          const r = request()
          return r.request ? startBurnedVideo(dramaId, r.request) : (r.error ?? 'Fix the style settings above first.')
        }}
      />
    </>
  )
}
