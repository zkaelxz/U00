import { describe, expect, it } from 'vitest'

import type { PortEntry } from '../../types/diagnostics'
import { portsSummary, portStatus, portText } from './portsFormat'

const e = (over: Partial<PortEntry>): PortEntry => ({
  key: 'api', label: 'Baihe', port: 8600, active: true, how_to_change: 'x', ...over,
})

describe('ports format', () => {
  it('shows an unset port as not set', () => {
    expect(portText(e({}))).toBe('8600')
    expect(portText(e({ port: null, active: false }))).toBe('not set')
  })

  it('labels the state', () => {
    expect(portStatus(e({}))).toBe('in use')
    expect(portStatus(e({ active: false }))).toBe('off')
  })

  it('summarises the ports in use', () => {
    expect(portsSummary([e({}), e({ key: 'household', port: null, active: false }), e({ key: 'https', port: 443 })]))
      .toBe('In use: 8600, 443')
    expect(portsSummary([e({ active: false })])).toBe('None in use')
  })
})
