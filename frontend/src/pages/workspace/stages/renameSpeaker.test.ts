import { describe, expect, it } from 'vitest'

import type { RenameUndo } from '../../../types/characters'
import { readRenameUndo, renameProblem, saveRenameUndo, takenNames } from './renameSpeaker'

const undo: RenameUndo = {
  speaker_label: 'Mei', previous_label: 'Speaker 1', previous_character_name: null,
  previous: [{ id: 4, speaker: 'Speaker 1', speaker_manual: false }],
}

function fakeStore(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> {
  const m = new Map<string, string>()
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v), removeItem: (k) => void m.delete(k) }
}

describe('rename undo store', () => {
  it('survives a reload and clears', () => {
    const s = fakeStore()
    saveRenameUndo(3, undo, s)
    expect(readRenameUndo(3, s)).toEqual(undo)
    expect(readRenameUndo(4, s)).toBeNull()
    saveRenameUndo(3, null, s)
    expect(readRenameUndo(3, s)).toBeNull()
  })

  it('ignores junk', () => {
    const s = fakeStore()
    s.setItem('baihe.characters.renameUndo.3', '{"x":1}')
    expect(readRenameUndo(3, s)).toBeNull()
  })
})

describe('renameProblem', () => {
  it('rejects blank, unchanged and taken names', () => {
    expect(renameProblem('A', '  ', [])).toBe('Type a name.')
    expect(renameProblem('A', ' A ', [])).toBe('That is already its name.')
    expect(renameProblem('A', 'B', ['A', 'B'])).toBe('Another speaker already has that name.')
    expect(renameProblem('A', ' Mei ', ['A', 'B'])).toBeNull()
  })
})

describe('renameProblem rules', () => {
  it('compares normalised and checks characters', () => {
    expect(renameProblem('A', ' ａｎｎａ ', ['Anna'])).toBe('Another speaker already has that name.')
    expect(renameProblem('A', 'speaker   2', ['Speaker 2'])).toBe('Another speaker already has that name.')
    expect(renameProblem('A', 'a/b', [])).toMatch(/slashes/)
    expect(renameProblem('A', 'x..y', [])).toMatch(/slashes/)
    expect(renameProblem('A', 'n'.repeat(101), [])).toMatch(/100/)
  })

  it('takenNames lists other speakers labels and names', () => {
    const e = [{ speaker_label: 'A', character_name: 'Mei' }, { speaker_label: 'B', character_name: 'Lin' }, { speaker_label: 'C', character_name: '' }]
    expect(takenNames(e, 'A')).toEqual(['B', 'Lin', 'C'])
  })
})
