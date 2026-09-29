import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  artifactDownloadUrl, bulkDelete, bulkSetStatus, bulkSetTag, bulkTranslate, cleanStorage, deletePreset,
  deleteVoiceBankEntry, getArtifactInfo, restoreBackup, scanStorage, startBackup, startExport,
} from './libraryAdmin'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('library admin api', () => {
  it('bulk status and tags send the ids and the change, with the local header', async () => {
    const { mock, f } = reply(200, { results: [], updated: 0 })
    await bulkSetStatus([1, 2], 'translated', f)
    await bulkSetTag([3], 'On Hold', false, f)
    const [[u1, i1], [u2, i2]] = mock.mock.calls
    expect(u1).toBe('/api/library/admin/bulk/status')
    expect(JSON.parse(i1.body)).toEqual({ drama_ids: [1, 2], status: 'translated' })
    expect(u2).toBe('/api/library/admin/bulk/tags')
    expect(JSON.parse(i2.body)).toEqual({ drama_ids: [3], tag: 'On Hold', present: false })
    expect(localHeader(i1)).toBe('1')
  })

  it('bulk translate posts the ids', async () => {
    const { mock, f } = reply(200, { job_id: 'bulk_series_translate', queued: [1], skipped: [] })
    await bulkTranslate([1], f)
    expect(JSON.parse(mock.mock.calls[0][1].body)).toEqual({ drama_ids: [1] })
  })

  it('bulk delete confirms with DELETE and the PC-only header', async () => {
    const { mock, f } = reply(200, { results: [], deleted: 0 })
    await bulkDelete([4], f)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/library/admin/bulk/delete')
    expect(JSON.parse(init.body)).toEqual({ drama_ids: [4], confirm: true, confirm_text: 'DELETE' })
    expect(localHeader(init)).toBe('1')
  })

  it('export, backup and cleanup are PC-only JSON posts', async () => {
    const { mock, f } = reply(200, { job_id: 'x', drama_ids: [] })
    await startExport(undefined, f)
    await startExport([2], f)
    await startBackup(true, f)
    await cleanStorage('minimal', f)
    const bodies = mock.mock.calls.map(([, i]) => JSON.parse(i.body))
    expect(bodies).toEqual([
      {}, { drama_ids: [2] }, { database_only: true },
      { preset: 'minimal', confirm: true, confirm_text: 'CLEAN' },
    ])
    for (const [, init] of mock.mock.calls) expect(localHeader(init)).toBe('1')
  })

  it('restore is multipart with confirm fields and the PC-only header', async () => {
    const { mock, f } = reply(200, { restored: true, sessions_revoked: 0 })
    await restoreBackup(new Blob(['zip']), f)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/library/admin/restore')
    const form = init.body as FormData
    expect(form.get('confirm')).toBe('true')
    expect(form.get('confirm_text')).toBe('RESTORE')
    expect(form.get('file')).toBeInstanceOf(Blob)
    expect(localHeader(init)).toBe('1')
    expect(new Headers(init.headers).get('Content-Type')).toBeNull()
  })

  it('scan, artifact info and download link', async () => {
    const { mock, f } = reply(200, {})
    await scanStorage('balanced', f)
    await getArtifactInfo('backup', f)
    expect(mock.mock.calls.map(([u]) => u)).toEqual([
      '/api/library/admin/storage?preset=balanced', '/api/library/admin/artifacts/backup/info',
    ])
    expect(artifactDownloadUrl('database')).toBe('/api/library/admin/artifacts/database')
  })

  it('preset and voice bank deletes send {confirm: true}', async () => {
    const { mock, f } = reply(200, { deleted: true })
    await deletePreset(5, f)
    await deleteVoiceBankEntry(6, f)
    expect(mock.mock.calls.map(([u]) => u)).toEqual([
      '/api/library/presets/5/delete', '/api/library/voice-bank/6/delete',
    ])
    for (const [, init] of mock.mock.calls) {
      expect(JSON.parse(init.body)).toEqual({ confirm: true })
      expect(localHeader(init)).toBe('1')
    }
  })

  it('a 403 from a PC-only call marks the tab remote', async () => {
    const { f } = reply(403, { error: { code: 'forbidden', message: 'Not allowed.' } })
    expect(getPcMode()).toBe('unknown')
    await expect(deletePreset(1, f)).rejects.toMatchObject({ status: 403, code: 'forbidden' })
    expect(getPcMode()).toBe('remote')
  })
})
