import type { components } from '../../api/generated/schema'

export type KeysFileRow = components['schemas']['EnvSettingRow']

export const KEYS_FILE_TITLE = 'Keys file and environment'
export const KEYS_FILE_NOTE =
  'Where each setting is saved. Most are changed only in the .env file in the data folder (or in Windows, where marked); the ones in Connections and Remote access are saved there by their own cards. This list only says whether each one is set, never what it is set to.'

export interface KeysFileGroup {
  group: string
  rows: KeysFileRow[]
}

// Groups keep the server's order, which is the order the list is written in.
export function groupKeysFile(rows: KeysFileRow[]): KeysFileGroup[] {
  const groups: KeysFileGroup[] = []
  for (const row of rows) {
    const last = groups.find((g) => g.group === row.group)
    if (last) last.rows.push(row)
    else groups.push({ group: row.group, rows: [row] })
  }
  return groups
}

export const keysFileSummary = (rows: KeysFileRow[]) => `${rows.filter((r) => r.set).length} of ${rows.length} set`
