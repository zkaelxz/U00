import { describe, expect, it } from 'vitest'

import type { ResourceAlertRule } from '../../types/resourceAlerts'
import { changeOf, draftOf, hasProblem, limitProblem, minutesProblem, ruleSummary } from './pcAlerts'

const ram: ResourceAlertRule = {
  id: 'ram', label: 'RAM use', unit: '%', has_limit: true, below: false, enabled: true, limit: 95, minutes: 10,
  limit_min: 50, limit_max: 100, minutes_min: 1, minutes_max: 1440,
  default_enabled: true, default_limit: 95, default_minutes: 10,
}
const throttle: ResourceAlertRule = {
  ...ram, id: 'gpu_throttle', label: 'GPU throttling', unit: '', has_limit: false, limit: null, limit_min: null,
  limit_max: null, default_limit: null,
}

describe('pc alerts rows', () => {
  it('sends nothing until something differs from what is saved', () => {
    expect(changeOf(ram, draftOf(ram))).toBeNull()
  })

  it('sends only the changed fields', () => {
    expect(changeOf(ram, { ...draftOf(ram), limit: '90' })).toEqual({ limit: 90 })
    expect(changeOf(ram, { enabled: false, limit: '95', minutes: '3' })).toEqual({ enabled: false, minutes: 3 })
  })

  it('never sends a limit for an alert that has none', () => {
    expect(changeOf(throttle, { ...draftOf(throttle), minutes: '5' })).toEqual({ minutes: 5 })
  })

  it('flags values outside the range or not numbers', () => {
    expect(limitProblem(ram, '20')).toBe('Use 50 to 100.')
    expect(limitProblem(ram, '')).not.toBeNull()
    expect(minutesProblem(ram, '0')).toBe('Use 1 to 1440.')
    expect(minutesProblem(ram, '2.5')).not.toBeNull()
    expect(hasProblem(ram, { ...draftOf(ram), minutes: 'x' })).toBe(true)
    expect(hasProblem(throttle, draftOf(throttle))).toBe(false)
  })

  it('describes the default in a line', () => {
    expect(ruleSummary(ram)).toBe('Alerts at or above 95% by default, after 10 min.')
  })
})
