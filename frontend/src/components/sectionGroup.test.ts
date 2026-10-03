import { describe, expect, it, vi } from 'vitest'
import { announceOpen, joinGroup } from './sectionGroup'

describe('section groups', () => {
  it('closes the other members of the same group only', () => {
    const a = vi.fn(), b = vi.fn(), other = vi.fn()
    const offA = joinGroup('page', 'a', a)
    const offB = joinGroup('page', 'b', b)
    const offO = joinGroup('elsewhere', 'o', other)
    announceOpen('page', 'a')
    expect(a).not.toHaveBeenCalled()
    expect(b).toHaveBeenCalledTimes(1)
    expect(other).not.toHaveBeenCalled()
    offA(); offB(); offO()
  })

  it('stops notifying a section that left', () => {
    const b = vi.fn()
    const off = joinGroup('g', 'b', b)
    off()
    announceOpen('g', 'a')
    expect(b).not.toHaveBeenCalled()
  })
})
