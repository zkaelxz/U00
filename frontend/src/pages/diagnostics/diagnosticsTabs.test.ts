import { describe, expect, it } from 'vitest'

import { CARD_TAB, DIAGNOSTICS_TABS, isDiagnosticsCard, isDiagnosticsTab } from './diagnosticsTabs'

describe('diagnostics tabs', () => {
  it('puts every card on a real tab and every tab holds a card', () => {
    const ids = DIAGNOSTICS_TABS.map((t) => t.id)
    for (const tab of Object.values(CARD_TAB)) expect(ids).toContain(tab)
    for (const id of ids) expect(Object.values(CARD_TAB)).toContain(id)
  })

  it('recognises only known tab and card names', () => {
    expect(isDiagnosticsTab('models')).toBe(true)
    expect(isDiagnosticsTab('admin')).toBe(false)
    expect(isDiagnosticsCard('reset')).toBe(true)
    expect(isDiagnosticsCard('toString')).toBe(false)
    expect(isDiagnosticsCard(null)).toBe(false)
  })
})
