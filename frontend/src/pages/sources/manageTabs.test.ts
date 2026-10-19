import { describe, expect, it } from 'vitest'

import {
  MANAGE_CARDS, MANAGE_TABS, SOURCES_TABS, manageCardStartsOpen, cardStorageKey, emptyTabMessage, isManageTab, isSourcesTab, manageHref, sourcesHref, tabFromParam,
  type ManageTab,
} from './manageTabs'

describe('Sources tabs and Manage sources areas', () => {
  it('lists the four areas in order, Sites first', () => {
    expect(MANAGE_TABS.map((t) => t.label)).toEqual(['Sites', 'Timing & cache', 'Network', 'Activity'])
  })

  it('reads the area from a link and falls back to Sites', () => {
    expect(tabFromParam('network')).toBe('network')
    expect(tabFromParam('timing')).toBe('timing')
    expect(tabFromParam(null)).toBe('sites')
    expect(tabFromParam('nope')).toBe('sites')
    expect(isManageTab('activity')).toBe(true)
    expect(isManageTab('Activity')).toBe(false)
  })

  it('writes a link the parser reads back, with no query for the default area', () => {
    expect(manageHref()).toBe('#/sources/manage')
    expect(manageHref('sites')).toBe('#/sources/manage')
    for (const t of MANAGE_TABS.filter((x) => x.id !== 'sites')) {
      expect(manageHref(t.id)).toBe(`#/sources/manage?tab=${t.id}`)
    }
  })

  it('opens only the first card of the whole Manage sources tab', () => {
    const open = MANAGE_TABS.flatMap((t) =>
      (MANAGE_CARDS[t.id] as readonly string[]).filter((c) => manageCardStartsOpen(t.id, c as never)).map((c) => `${t.id}.${c}`),
    )
    expect(open).toEqual(['sites.sites'])
  })

  it('names the three Sources tabs and writes links the parser reads back', () => {
    expect(SOURCES_TABS.map((t) => t.label)).toEqual(['Find', 'New chapters', 'Manage sources'])
    expect(sourcesHref()).toBe('#/sources')
    expect(sourcesHref('find')).toBe('#/sources')
    expect(sourcesHref('new')).toBe('#/sources?tab=new')
    expect(sourcesHref('manage')).toBe('#/sources?tab=manage')
    expect(isSourcesTab('new')).toBe(true)
    expect(isSourcesTab('find')).toBe(false)
  })

  it('remembers each card under its own key', () => {
    const keys = MANAGE_TABS.flatMap((t) => (MANAGE_CARDS[t.id] as readonly string[]).map((c) => cardStorageKey(t.id, c as never)))
    expect(new Set(keys).size).toBe(keys.length)
    expect(cardStorageKey('network', 'proxy')).toBe('sources.manage.network.proxy')
  })

  it('has an empty-state message for every area', () => {
    for (const t of MANAGE_TABS) expect(emptyTabMessage(t.id as ManageTab).length).toBeGreaterThan(10)
  })
})
