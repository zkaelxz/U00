import { describe, expect, it } from 'vitest'

import type { DiagnosticsHfCacheEntry } from '../../types/diagnostics'
import { groupModels, modelKind, sortModels } from './modelGroups'

const entry = (repo_id: string, size_bytes: number, revision = 'r'.repeat(40)): DiagnosticsHfCacheEntry => ({ repo_id, repo_type: 'model', revision, size_bytes })

describe('model groups', () => {
  it('sorts a repo into its kind by name and leaves unknown names in Other', () => {
    expect(modelKind('Systran/faster-whisper-large-v3')).toBe('whisper')
    expect(modelKind('pyannote/speaker-diarization-3.1')).toBe('speech')
    expect(modelKind('google/gemma-3-4b')).toBe('translation')
    expect(modelKind('someone/unknown-model')).toBe('other')
  })

  it('sorts by size (largest first) or name, with a stable tie-break', () => {
    const items = [entry('b/x', 5), entry('a/y', 9), entry('a/z', 5)]
    expect(sortModels(items, 'size').map((e) => e.repo_id)).toEqual(['a/y', 'a/z', 'b/x'])
    expect(sortModels(items, 'name').map((e) => e.repo_id)).toEqual(['a/y', 'a/z', 'b/x'])
  })

  it('groups 60 models, totals each group and keeps every revision in exactly one group', () => {
    const items = Array.from({ length: 60 }, (_, i) => entry(`org/${['whisper', 'asr', 'gemma', 'misc'][i % 4]}-${i}`, (i + 1) * 1000, String(i).padStart(40, '0')))
    const groups = groupModels(items, 'size')
    expect(groups.map((g) => g.id)).toEqual(['whisper', 'speech', 'translation', 'other'])
    expect(groups.reduce((n, g) => n + g.items.length, 0)).toBe(60)
    expect(groups.reduce((n, g) => n + g.totalBytes, 0)).toBe(items.reduce((n, e) => n + e.size_bytes, 0))
    expect(groups[0].items[0].size_bytes).toBeGreaterThan(groups[0].items[1].size_bytes)
  })

  it('leaves empty groups out', () => {
    expect(groupModels([entry('x/y', 1)], 'size').map((g) => g.id)).toEqual(['other'])
  })
})
