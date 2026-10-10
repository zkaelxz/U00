import type { Page } from '@playwright/test'

// Stateful page.route mock for /api/data-usage (Library tools > Disk usage).
// Folders hold items; moving to Trash takes the item out of its folder into a
// trash list (restore puts it back, purge and empty delete it) and a move
// repoints the backup folder. Every POST body is recorded in `posts`.

type Kind = 'file' | 'folder'
interface Node {
  name: string
  kind: Kind
  size: number
  files: number
  protected?: string
  regen?: { label: string; note: string }
  irreplaceable?: boolean
  hasLink?: boolean
  movable?: boolean
  incomplete?: boolean
}

export const TREE: Record<string, Node[]> = {
  '': [
    { name: 'library', kind: 'folder', size: 48_000_000_000, files: 52000, protected: 'Holds database or key files. Open it and clear items inside instead.' },
    { name: 'model_cache', kind: 'folder', size: 6_200_000_000, files: 340, regen: { label: 'Downloaded models', note: 'Downloaded again when a feature needs them.' } },
    { name: '.env', kind: 'file', size: 412, files: 1, protected: 'Holds API keys or other secrets.' },
  ],
  library: [
    { name: 'dramas', kind: 'folder', size: 41_000_000_000, files: 9000, irreplaceable: true },
    { name: 'backups', kind: 'folder', size: 3_000_000_000, files: 12, irreplaceable: true },
    { name: 'tmp', kind: 'folder', size: 2_400_000_000, files: 80, regen: { label: 'Temporary job files', note: 'Working files of finished or interrupted jobs.' } },
    { name: 'library.db', kind: 'file', size: 1_600_000_000, files: 1, protected: 'The Baihe database.' },
  ],
  'library/dramas': [
    { name: '12', kind: 'folder', size: 21_000_000_000, files: 400, irreplaceable: true },
    { name: '7', kind: 'folder', size: 20_000_000_000, files: 120, irreplaceable: true },
  ],
  'library/backups': [
    { name: 'auto', kind: 'folder', size: 2_900_000_000, files: 4, movable: true, irreplaceable: true },
    { name: 'linked', kind: 'folder', size: 5_000, files: 2, hasLink: true },
    { name: 'exports', kind: 'folder', size: 100_000_000, files: 8, regen: { label: 'Exports', note: 'Export files; make them again from the Library.' } },
  ],
  'library/tmp': [],
}

const join = (a: string, b: string) => (a ? `${a}/${b}` : b)

export interface TrashEntry { id: string; parent: string; node: Node; trashed_at: string; restorable: boolean }

export interface MockClip { id: string; type: string; size: number; used?: boolean }

export interface DiskUsageMock {
  clips: { title: string; clips: MockClip[] }[]
  posts: { path: string; body: Record<string, unknown> }[]
  tree: Record<string, Node[]>
  trash: TrashEntry[]
  setBusy(reason: string | null): void
  setPartial(on: boolean): void
}

export async function mockDiskUsage(page: Page, opts: { notShown?: number; slow?: boolean; slowScan?: number; trash?: { path: string; size: number; files: number; restorable?: boolean }[]; clips?: { title: string; clips: MockClip[] }[]; clipsInUse?: number; clipsFailPost?: number } = {}): Promise<DiskUsageMock> {
  const tree: Record<string, Node[]> = JSON.parse(JSON.stringify(TREE))
  const state = { busy: null as string | null, partial: false }
  const trash: TrashEntry[] = (opts.trash ?? []).map((t, i) => {
    const parent = t.path.includes('/') ? t.path.slice(0, t.path.lastIndexOf('/')) : ''
    const name = t.path.split('/').pop()!
    return { id: `20261001-10000${i}-abcdef0${i}`, parent, trashed_at: '2026-10-01T10:00:00+00:00', restorable: t.restorable ?? true,
      node: { name, kind: 'folder', size: t.size, files: t.files } }
  })
  const mock: DiskUsageMock = {
    posts: [], tree, trash, clips: JSON.parse(JSON.stringify(opts.clips ?? [])),
    setBusy: (r) => { state.busy = r },
    setPartial: (on) => { state.partial = on },
  }
  const trashBytes = () => trash.reduce((s, t) => s + t.node.size, 0)
  const conflict = (message: string, reason = 'changed') =>
    ({ status: 409, json: { error: { code: 'conflict', message, details: { reason } } } })
  const items = (path: string) => {
    const nodes = (tree[path] ?? []).slice().sort((a, b) => b.size - a.size)
    const total = nodes.reduce((s, n) => s + n.size, 0)
    return { nodes, total }
  }
  await page.route('**/api/data-usage**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    if (opts.slow) await new Promise((r) => setTimeout(r, 1500))
    const isScan = url.pathname === '/api/data-usage' && req.method() === 'GET'
    if (opts.slowScan && isScan) await new Promise((r) => setTimeout(r, opts.slowScan))
    if (url.pathname === '/api/data-usage/unused-voice-clips' && req.method() === 'GET') {
      const titles = mock.clips.filter((t) => t.clips.length).map((t) => ({
        title: t.title, size_bytes: t.clips.reduce((s, c) => s + c.size, 0),
        clips: t.clips.map((c) => ({ id: c.id, file_type: c.type, size_bytes: c.size, modified_at: '2026-09-30T08:00:00+00:00' })),
      }))
      return route.fulfill({ json: {
        titles, total_bytes: titles.reduce((s, t) => s + t.size_bytes, 0),
        total_count: titles.reduce((s, t) => s + t.clips.length, 0), titles_in_use: opts.clipsInUse ?? 0, busy_reason: state.busy,
      } })
    }
    if (url.pathname === '/api/data-usage/unused-voice-clips/to-trash') {
      const body = req.postDataJSON()
      mock.posts.push({ path: 'clips-to-trash', body })
      if (state.busy) return route.fulfill(conflict(state.busy, 'busy'))
      if (body.clips.length > 500) return route.fulfill({ status: 422, json: { error: { code: 'validation_error', message: 'Too many clips.' } } })
      // The nth request stops part-way: two clips moved, then the library got busy.
      if (opts.clipsFailPost === mock.posts.filter((p) => p.path === 'clips-to-trash').length) {
        return route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'A job, restore or other library task is running.', details: { reason: 'busy', moved_count: 2, moved_bytes: 2, skipped: [] } } } })
      }
      let moved = 0
      let bytes = 0
      const skipped: { id: string; reason: string }[] = []
      for (const want of body.clips) {
        const owner = mock.clips.find((t) => t.clips.some((c) => c.id === want.id))
        const clip = owner?.clips.find((c) => c.id === want.id)
        if (!owner || !clip || clip.used) { skipped.push({ id: want.id, reason: 'no_longer_unused' }); continue }
        if (clip.size !== want.expected_size_bytes) { skipped.push({ id: want.id, reason: 'changed' }); continue }
        owner.clips = owner.clips.filter((c) => c !== clip)
        moved += 1
        bytes += clip.size
      }
      return route.fulfill({ json: { moved_count: moved, moved_bytes: bytes, skipped } })
    }
    if (url.pathname === '/api/data-usage/to-trash') {
      const body = req.postDataJSON()
      mock.posts.push({ path: 'to-trash', body })
      const parent = body.path.includes('/') ? body.path.slice(0, body.path.lastIndexOf('/')) : ''
      const name = body.path.split('/').pop()
      const node = (tree[parent] ?? []).find((n) => n.name === name)
      if (!node || node.size !== body.expected_size_bytes) {
        return route.fulfill(conflict('This item changed since you looked. Rescan and check again.'))
      }
      tree[parent] = tree[parent].filter((n) => n !== node)
      const id = `20261002-1000${trash.length}0-abcdef1${trash.length}`
      trash.unshift({ id, parent, node, trashed_at: '2026-10-02T10:00:00+00:00', restorable: true })
      return route.fulfill({ json: { moved_bytes: node.size, file_count: node.files, kind: node.kind, name, trash_id: id } })
    }
    if (url.pathname === '/api/data-usage/trash' && req.method() === 'GET') {
      return route.fulfill({ json: {
        items: trash.map((t) => ({
          id: t.id, original_path_relative: join(t.parent, t.node.name), kind: t.node.kind, size_bytes: t.node.size,
          file_count: t.node.files, trashed_at: t.trashed_at, restorable: t.restorable,
        })),
        size_bytes: trashBytes(), item_count: trash.length, partial: false, busy_reason: state.busy,
      } })
    }
    if (url.pathname === '/api/data-usage/clean-temp') {
      mock.posts.push({ path: 'clean-temp', body: req.postDataJSON() })
      if (state.busy) return route.fulfill(conflict(state.busy, 'busy'))
      return route.fulfill({ json: { removed: 3, freed_mb: 12.5 } })
    }
    if (url.pathname === '/api/data-usage/trash/restore') {
      const body = req.postDataJSON()
      mock.posts.push({ path: 'restore', body })
      const t = trash.find((x) => x.id === body.id)
      if (!t) return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'That item is no longer in Trash.' } } })
      if (!t.restorable) return route.fulfill(conflict('Something with the same name is already in its old place.', 'cannot_restore'))
      trash.splice(trash.indexOf(t), 1)
      tree[t.parent] = [...(tree[t.parent] ?? []), t.node]
      return route.fulfill({ json: { name: t.node.name, kind: t.node.kind, size_bytes: t.node.size, file_count: t.node.files } })
    }
    if (url.pathname === '/api/data-usage/trash/purge') {
      const body = req.postDataJSON()
      mock.posts.push({ path: 'purge', body })
      const t = trash.find((x) => x.id === body.id)
      if (!t) return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'That item is no longer in Trash.' } } })
      if (body.confirm_text !== 'DELETE') return route.fulfill({ status: 422, json: { error: { code: 'invalid_input', message: 'Type DELETE in capital letters to confirm.' } } })
      if (body.expected_size_bytes !== t.node.size) return route.fulfill(conflict('This Trash item changed since you looked. Reload the list and check again.'))
      trash.splice(trash.indexOf(t), 1)
      return route.fulfill({ json: { freed_bytes: t.node.size, file_count: t.node.files } })
    }
    if (url.pathname === '/api/data-usage/trash/empty') {
      const body = req.postDataJSON()
      mock.posts.push({ path: 'empty', body })
      if (body.expected_item_count !== trash.length || body.expected_size_bytes !== trashBytes()) {
        return route.fulfill(conflict('The Trash changed since you looked. Reload the list and check again.'))
      }
      const freed = trashBytes()
      const removed = trash.length
      trash.length = 0
      return route.fulfill({ json: { freed_bytes: freed, removed, failed: 0 } })
    }
    if (url.pathname === '/api/data-usage/move') {
      const body = req.postDataJSON()
      mock.posts.push({ path: 'move', body })
      const node = tree['library/backups'].find((n) => n.name === 'auto')!
      node.movable = false
      node.size = 0
      return route.fulfill({ json: { moved_bytes: 2_900_000_000, remaining_bytes: 0, what: 'backups', name: 'auto' } })
    }
    const path = url.searchParams.get('path') ?? ''
    if (!(path in tree)) {
      return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'That item is no longer there.' } } })
    }
    const { nodes, total } = items(path)
    return route.fulfill({ json: {
      path,
      parent: path ? (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '') : null,
      total_bytes: total,
      file_count: nodes.reduce((s, n) => s + n.files, 0),
      items: nodes.map((n) => ({
        name: n.name, path: join(path, n.name), kind: n.kind, size_bytes: n.size, file_count: n.files,
        percent_of_parent: total ? Math.round((1000 * n.size) / total) / 10 : 0,
        modified_at: '2026-10-01T10:00:00+00:00', is_link: false, contains_link: !!n.hasLink, complete: !n.incomplete,
        protected: !!n.protected, protected_reason: n.protected ?? null,
        regenerable: n.regen ?? null,
        irreplaceable: !!n.irreplaceable,
        irreplaceable_note: n.irreplaceable
          ? (join(path, n.name).startsWith('library/backups') ? "Your backups. Once cleared they can't be recreated from inside Baihe." : "Source audio and your work for this title. It can't be recreated from inside Baihe.")
          : null,
        movable: n.movable
          ? { supported: true, reason: null, what: 'backups' }
          : { supported: false, reason: n.protected ? 'Protected items can\'t be moved.' : 'Baihe has no setting for this location, so it can\'t be moved safely.', what: null },
      })),
      partial: state.partial, partial_reason: state.partial ? 'time' : null, not_shown: opts.notShown ?? 0, scanned_entries: 1234,
      busy_reason: state.busy,
      trash: { size_bytes: trashBytes(), item_count: trash.length, partial: false },
      disk_total_bytes: 252_000_000_000, disk_free_bytes: 2_800_000_000,
    } })
  })
  return mock
}
