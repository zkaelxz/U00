import { afterEach, describe, expect, it, vi } from 'vitest'

import { emptyTrash, listTrash, moveItem, moveToTrash, purgeTrashItem, restoreTrashItem, scanDiskUsage } from './diskUsage'
import { resetPcModeForTests } from './pcOnly'

function reply(body: unknown = {}) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }))
  return { mock, f: mock as unknown as typeof fetch }
}
const header = (init: RequestInit, name: string) => new Headers(init.headers).get(name)

afterEach(() => resetPcModeForTests())

describe('disk usage api', () => {
  it('scans the root and a folder, encoding the path, with the PC header', async () => {
    const { mock, f } = reply()
    await scanDiskUsage('', undefined, f)
    await scanDiskUsage('library/dramas/1', undefined, f)
    await scanDiskUsage('library/a b&c', undefined, f)
    expect(mock.mock.calls.map(([u]) => u)).toEqual([
      '/api/data-usage', '/api/data-usage?path=library%2Fdramas%2F1', '/api/data-usage?path=library%2Fa%20b%26c',
    ])
    expect(header(mock.mock.calls[0][1], 'X-Baihe-Local')).toBe('1')
  })

  it('a scan stops when its signal aborts', async () => {
    const ctl = new AbortController()
    const f = vi.fn((_u: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_res, rej) => {
      init?.signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')))
    })) as unknown as typeof fetch
    const p = scanDiskUsage('', ctl.signal, f)
    ctl.abort()
    await expect(p).rejects.toThrow()
  })

  it('move to Trash sends the sizes the person saw and the extra confirm only for media', async () => {
    const { mock, f } = reply()
    await moveToTrash({ path: 'library/tmp', size_bytes: 10, file_count: 2 }, f)
    await moveToTrash({ path: 'library/dramas/1', size_bytes: 5, file_count: 1, irreplaceable: true }, f)
    expect(JSON.parse(mock.mock.calls[0][1].body)).toEqual({
      path: 'library/tmp', confirm: true, expected_size_bytes: 10, expected_file_count: 2,
    })
    expect(JSON.parse(mock.mock.calls[1][1].body).confirm_irreplaceable).toBe(true)
    expect(mock.mock.calls[0][0]).toBe('/api/data-usage/to-trash')
  })

  it('move posts the path, destination and confirm', async () => {
    const { mock, f } = reply()
    await moveItem('library/backups/auto', 'D:\\Backups', f)
    expect(mock.mock.calls[0][0]).toBe('/api/data-usage/move')
    expect(JSON.parse(mock.mock.calls[0][1].body)).toEqual({
      path: 'library/backups/auto', destination: 'D:\\Backups', confirm: true,
    })
  })

  it('lists, restores, purges and empties Trash with the PC header and the typed word', async () => {
    const { mock, f } = reply()
    await listTrash(f)
    await restoreTrashItem('20260101-000000-abcdef12', f)
    await purgeTrashItem({ id: '20260101-000000-abcdef12', size_bytes: 400 }, f)
    await emptyTrash({ item_count: 2, size_bytes: 400 }, f)
    expect(mock.mock.calls.map(([u]) => u)).toEqual([
      '/api/data-usage/trash', '/api/data-usage/trash/restore', '/api/data-usage/trash/purge', '/api/data-usage/trash/empty',
    ])
    expect(header(mock.mock.calls[0][1], 'X-Baihe-Local')).toBe('1')
    expect(JSON.parse(mock.mock.calls[1][1].body)).toEqual({ id: '20260101-000000-abcdef12', confirm: true })
    expect(JSON.parse(mock.mock.calls[2][1].body)).toEqual({
      id: '20260101-000000-abcdef12', confirm_text: 'DELETE', expected_size_bytes: 400,
    })
    expect(JSON.parse(mock.mock.calls[3][1].body)).toEqual({
      confirm_text: 'DELETE', expected_item_count: 2, expected_size_bytes: 400,
    })
  })
})
