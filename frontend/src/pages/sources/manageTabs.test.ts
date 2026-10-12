import { describe, expect, it } from 'vitest'

import {
  MANAGE_CARDS, MANAGE_TABS, cardStartsOpen, cardStorageKey, emptyTabMessage, isManageTab, manageHref, tabFromParam,
  type ManageTab,
} from './manageTabs'

describe('Manage sources tabs', () => {
  it('lists the four tabs in order, Sites first', () => {
    expect(MANAGE_TABS.map((t) => t.label)).toEqual(['Sites', 'Timing & cache', 'Network', 'Activity'])
  })

  it('reads the tab from a link and falls back to Sites', () => {
    expect(tabFromParam('network')).toBe('network')
    expect(tabFromParam('timing')).toBe('timing')
    expect(tabFromParam(null)).toBe('sites')
    expect(tabFromParam('nope')).toBe('sites')
    expect(isManageTab('activity')).toBe(true)
    expect(isManageTab('Activity')).toBe(false)
  })

  it('writes a link the parser reads back, with no query for the default tab', () => {
    expect(manageHref()).toBe('#/sources/manage')
    expect(manageHref('sites')).toBe('#/sources/manage')
    for (const t of MANAGE_TABS.filter((x) => x.id !== 'sites')) {
      expect(manageHref(t.id)).toBe(`#/sources/manage?tab=${t.id}`)
    }
  })

  it('opens only the first card of each tab', () => {
    for (const t of MANAGE_TABS) {
      const cards = MANAGE_CARDS[t.id] as readonly string[]
      expect(cards.length).toBeGreaterThan(0)
      cards.forEach((c, i) => expect(cardStartsOpen(t.id, c as never)).toBe(i === 0))
    }
  })

  it('remembers each card under its own key', () => {
    const keys = MANAGE_TABS.flatMap((t) => (MANAGE_CARDS[t.id] as readonly string[]).map((c) => cardStorageKey(t.id, c as never)))
    expect(new Set(keys).size).toBe(keys.length)
    expect(cardStorageKey('network', 'proxy')).toBe('sources.manage.network.proxy')
  })

  it('has an empty-state message for every tab', () => {
    for (const t of MANAGE_TABS) expect(emptyTabMessage(t.id as ManageTab).length).toBeGreaterThan(10)
  })
})
