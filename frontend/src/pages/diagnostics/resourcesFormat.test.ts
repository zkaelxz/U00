import { describe, expect, it } from 'vitest'

import type { ResourceMetric, ResourceReadings } from '../../types/diagnostics'
import { readingText, resourcesSummary, resourceStatus, ruleText, sampledText, visibleMetrics } from './resourcesFormat'

const metric = (over: Partial<ResourceMetric>): ResourceMetric => ({
  name: 'gpu_temp', label: 'GPU temperature', unit: '°C', low: false, enabled: true, limit: 85, minutes: 5,
  value: 70, alerting: false, past_seconds: null, ...over,
})

describe('resourcesFormat', () => {
  it('shows each reading with its unit', () => {
    expect(readingText(metric({}))).toBe('70 °C')
    expect(readingText(metric({ name: 'ram', unit: '%', value: 95.4 }))).toBe('95%')
    expect(readingText(metric({ name: 'disk_free', unit: 'GB', value: 12.34 }))).toBe('12.3 GB')
    expect(readingText(metric({ name: 'gpu_throttle', unit: '', value: 1 }))).toBe('Cutting clocks')
    expect(readingText(metric({ value: null }))).toBe('Not available')
  })

  it('describes the rule, including a low limit and one that is off', () => {
    expect(ruleText(metric({}))).toBe('Alerts at 85 °C for 5 min')
    expect(ruleText(metric({ name: 'disk_free', unit: 'GB', low: true, limit: 10 }))).toBe('Alerts under 10.0 GB for 5 min')
    expect(ruleText(metric({ name: 'gpu_throttle', unit: '', limit: null, minutes: 10 }))).toBe('Alerts after 10 min')
    expect(ruleText(metric({ enabled: false }))).toBe('Alert off')
  })

  it('ranks alerting over past the limit over OK', () => {
    expect(resourceStatus(metric({ alerting: true, past_seconds: 600 }))).toEqual({ tone: 'bad', label: 'Alerting' })
    expect(resourceStatus(metric({ value: 90, past_seconds: 120 }))).toEqual({ tone: 'warn', label: 'Past limit 2 min' })
    expect(resourceStatus(metric({}))).toEqual({ tone: 'ok', label: 'OK' })
    expect(resourceStatus(metric({ value: null }))).toBeNull()
  })

  it('summarises and says when nothing has been read yet', () => {
    const none: ResourceReadings = { sampled_seconds_ago: null, sample_seconds: 30, metrics: [metric({})] }
    expect(resourcesSummary(none)).toBe('No reading yet')
    expect(sampledText(none)).toContain('while Baihe is running')
    const hot = { ...none, sampled_seconds_ago: 4, metrics: [metric({ alerting: true }), metric({})] }
    expect(resourcesSummary(hot)).toBe('1 alerting')
    expect(sampledText(hot)).toBe('Read 4 s ago. The monitor reads this PC every 30 s.')
  })

  it('hides GPU rows once a sample found no GPU, but not before the first sample', () => {
    const rows = [metric({ name: 'gpu_temp', value: null }), metric({ name: 'ram', unit: '%', value: 40 })]
    const base: ResourceReadings = { sampled_seconds_ago: null, sample_seconds: 30, metrics: rows }
    expect(visibleMetrics(base)).toHaveLength(2)
    expect(visibleMetrics({ ...base, sampled_seconds_ago: 3 }).map((m) => m.name)).toEqual(['ram'])
    const withGpu = { ...base, sampled_seconds_ago: 3, metrics: [metric({ name: 'gpu_temp', value: 60 }), ...rows.slice(1)] }
    expect(visibleMetrics(withGpu)).toHaveLength(2)
  })
})
