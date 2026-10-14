import { describe, expect, it } from 'vitest'

import { ASSISTANT_TABS, isAssistantTab, resolveAssistantTab } from './assistantTabs'

describe('assistant tabs', () => {
  it('lists Ask, Work list and GitHub and tools in that order', () => {
    expect(ASSISTANT_TABS.map((t) => t.label)).toEqual(['Ask', 'Work list', 'GitHub and tools'])
  })

  it('opens on the remembered tab, and on Ask when nothing valid is remembered', () => {
    expect(resolveAssistantTab('work')).toBe('work')
    expect(resolveAssistantTab('tools')).toBe('tools')
    expect(resolveAssistantTab('nope')).toBe('ask')
    expect(resolveAssistantTab(null)).toBe('ask')
    expect(isAssistantTab('ask')).toBe(true)
    expect(isAssistantTab('Ask')).toBe(false)
  })
})
