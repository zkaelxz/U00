/*
 * Activity log and daily GPU time, under the Jobs list. Both read the durable
 * activity_log, so they stay after finished jobs are deleted from the list.
 */
import { useEffect, useState } from 'react'

import { getActivity, getGpuDaily, type ActivityItem, type GpuDaily } from '../../api/activity'
import { ErrorBanner } from '../../components/ErrorBanner'
import { ChartReadout } from '../../components/ChartReadout'
import { useChartReadout } from '../../hooks/useChartReadout'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { activityDuration, barFractions, gpuHours } from './activityFormat'

const OUTCOME_TEXT = { done: 'Finished', error: 'Failed', cancelled: 'Cancelled' } as const

export function GpuChart({ data }: { data: GpuDaily }) {
  const fractions = barFractions(data.days.map((d) => d.gpu_seconds))
  const readout = useChartReadout()
  return (
    <figure className="activity-chart" data-testid="gpu-chart">
      <figcaption>
        GPU time per day, last {data.days.length} days: {gpuHours(data.total_gpu_seconds)} in total
      </figcaption>
      <div className="activity-bars" role="group" aria-label={`GPU hours per day, ${gpuHours(data.total_gpu_seconds)} in total`}>
        {data.days.map((d, i) => (
          <span
            key={d.day}
            className="activity-bar"
            {...readout.point(`${d.day}: ${gpuHours(d.gpu_seconds)} on the GPU, ${d.jobs} job${d.jobs === 1 ? '' : 's'}`)}
          >
            <i style={{ height: `${Math.max(2, Math.round(fractions[i] * 100))}%` }} />
          </span>
        ))}
      </div>
      <ChartReadout text={readout.text} />
      <div className="activity-axis muted"><span>{data.days[0]?.day}</span><span>{data.days[data.days.length - 1]?.day}</span></div>
    </figure>
  )
}

export function ActivityLog() {
  const [items, setItems] = useState<ActivityItem[] | null>(null)
  const [next, setNext] = useState<number | null>(null)
  const [gpu, setGpu] = useState<GpuDaily | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    getActivity().then((r) => { setItems(r.items); setNext(r.next_before_id) }, setError)
    getGpuDaily().then(setGpu, () => undefined)
  }, [])
  const more = () => getActivity(next).then((r) => { setItems((cur) => [...(cur ?? []), ...r.items]); setNext(r.next_before_id) }, setError)

  return (
    <div className="activity-log" data-testid="activity-log">
    <Section title="Activity log and GPU time" keepFold>
      {error != null && <ErrorBanner error={error} />}
      {gpu && gpu.total_gpu_seconds > 0 && <GpuChart data={gpu} />}
      {items !== null && items.length === 0 && <p className="muted">Nothing has finished yet. Finished jobs show up here and stay after you clear the list above.</p>}
      {items !== null && items.length > 0 && (
        <ul className="activity-list">
          {items.map((a) => (
            <li key={a.id}>
              <span className="activity-when muted">{new Date(a.ended_at * 1000).toLocaleString()}</span>
              <span className="activity-what">{a.label}</span>
              <span className="muted">{OUTCOME_TEXT[a.outcome]} · {activityDuration(a.seconds)}{a.gpu ? ' · GPU' : ''}</span>
            </li>
          ))}
        </ul>
      )}
      {next !== null && <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => void more()}>Show older</button>}
    </Section>
    </div>
  )
}
