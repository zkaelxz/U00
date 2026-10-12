import { describe, expect, it } from 'vitest'

import { ASSISTANT_TABS, cardStartsOpen, isAssistantTab, resolveAssistantTab, type AssistantCard } from './assistantTabs'

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

  it('starts only the first card of each tab open', () => {
    const open = (github: boolean) =>
      (['chat', 'engine', 'backlog', 'changelog', 'github', 'tools', 'review'] as AssistantCard[]).filter((c) => cardStartsOpen(c, github))
    expect(open(true)).toEqual(['chat', 'backlog', 'github'])
  })

  it('makes Tools the first card of its tab when GitHub is not configured', () => {
    expect(cardStartsOpen('tools', false)).toBe(true)
    expect(cardStartsOpen('github', false)).toBe(false)
    expect(cardStartsOpen('review', false)).toBe(false)
  })
})
