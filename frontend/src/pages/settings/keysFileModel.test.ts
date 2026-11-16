import { describe, expect, it } from 'vitest'

import { groupKeysFile, keysFileSummary, type KeysFileRow } from './keysFileModel'

const row = (name: string, group: string, set: boolean): KeysFileRow => ({ name, label: name, group, from_file: true, set })

describe('keys file list', () => {
  it('groups rows in the server order', () => {
    const groups = groupKeysFile([row('A', 'One', true), row('B', 'Two', false), row('C', 'One', false)])
    expect(groups.map((g) => [g.group, g.rows.map((r) => r.name)])).toEqual([['One', ['A', 'C']], ['Two', ['B']]])
  })

  it('counts the ones that are set', () => {
    expect(keysFileSummary([row('A', 'One', true), row('B', 'One', false)])).toBe('1 of 2 set')
  })
})
