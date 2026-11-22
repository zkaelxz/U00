import type { BadgeTone } from '../../components/labels'
import type { ResourceMetric, ResourceReadings } from '../../types/diagnostics'

function withUnit(m: ResourceMetric, n: number): string {
  if (m.unit === '%') return `${Math.round(n)}%`
  if (m.unit === 'GB') return `${n.toFixed(1)} GB`
  return m.unit ? `${Math.round(n)} ${m.unit}` : String(n)
}

function duration(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60))
  return minutes < 120 ? `${minutes} min` : `${Math.round(minutes / 60)} h`
}

export function readingText(m: ResourceMetric): string {
  if (m.value == null) return 'Not available'
  if (m.name === 'gpu_throttle') return m.value >= 1 ? 'Cutting clocks' : 'No'
  return withUnit(m, m.value)
}

export function ruleText(m: ResourceMetric): string {
  if (!m.enabled) return 'Alert off'
  if (m.limit == null) return `Alerts after ${m.minutes} min`
  return `Alerts ${m.low ? 'under' : 'at'} ${withUnit(m, m.limit)} for ${m.minutes} min`
}

/** Null when there is no reading: the line already says "Not available", so a second chip would only repeat it. */
export function resourceStatus(m: ResourceMetric): { tone: BadgeTone; label: string } | null {
  if (m.alerting) return { tone: 'bad', label: 'Alerting' }
  if (m.value == null) return null
  if (m.enabled && m.past_seconds != null) return { tone: 'warn', label: `Past limit ${duration(m.past_seconds)}` }
  return { tone: 'ok', label: 'OK' }
}

/**
 * The rows worth showing. Once a sample has been taken, a PC whose GPU rows all came back empty has no
 * NVIDIA GPU to read, so those rows would stay "Not available" forever.
 */
export function visibleMetrics(r: ResourceReadings): ResourceMetric[] {
  const gpu = r.metrics.filter((m) => m.name.startsWith('gpu_'))
  const noGpu = r.sampled_seconds_ago != null && gpu.every((m) => m.value == null)
  return noGpu ? r.metrics.filter((m) => !m.name.startsWith('gpu_')) : r.metrics
}

/** The closed Section's one-line summary. */
export function resourcesSummary(r: ResourceReadings): string {
  if (r.sampled_seconds_ago == null) return 'No reading yet'
  const alerting = r.metrics.filter((m) => m.alerting).length
  return alerting ? `${alerting} alerting` : 'All within limits'
}

export function sampledText(r: ResourceReadings): string {
  if (r.sampled_seconds_ago == null) {
    return `No reading yet. The monitor reads this PC every ${r.sample_seconds} s while Baihe is running.`
  }
  return `Read ${r.sampled_seconds_ago} s ago. The monitor reads this PC every ${r.sample_seconds} s.`
}
