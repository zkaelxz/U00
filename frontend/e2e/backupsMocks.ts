import type { Page, Route } from '@playwright/test'

// Stateful page.route mocks for /api/backups/* (automatic backups, Step 43)
// and the backup job's /api/jobs poll. Every write is recorded in `posts`;
// settings changes are kept, so a reload shows what was saved.

export interface CopyBody {
  name: string
  created_at: string | null
  size: number
  kind: 'db-only' | 'full' | null
  drama_count: number | null
  readable: boolean
  kept_as: 'daily' | 'weekly' | null
  managed?: boolean
  sequence?: number | null
}

export interface SnapshotBody {
  exists: boolean
  readable?: boolean
  choose_copy?: boolean
  default_copy?: string | null
  created_at?: string
  kind?: 'db-only' | 'full'
  size?: number
  app_version?: string
  drama_count?: number
  copies?: CopyBody[]
}

export const COPIES: CopyBody[] = [
  { name: 'baihe_snapshot-20260928-093000.zip', created_at: '2026-09-28T09:30:00+00:00', size: 12_345_678, kind: 'db-only', drama_count: 3, readable: true, kept_as: 'daily', managed: true, sequence: 7 },
  { name: 'baihe_snapshot-20260927-093000.zip', created_at: '2026-09-27T09:30:00+00:00', size: 12_000_000, kind: 'db-only', drama_count: 3, readable: true, kept_as: 'daily', managed: true, sequence: 6 },
  { name: 'baihe_snapshot-20260921-093000.zip', created_at: '2026-09-21T09:30:00+00:00', size: 11_000_000, kind: 'full', drama_count: 2, readable: true, kept_as: 'weekly', managed: true, sequence: 3 },
]

export const SNAPSHOT: SnapshotBody = {
  exists: true, readable: true, choose_copy: false, default_copy: COPIES[0].name, created_at: '2026-09-28T09:30:00+00:00',
  kind: 'db-only', size: 12_345_678, app_version: '1.0', drama_count: 3, copies: COPIES,
}

// Another library's copy in a shared folder, newer than this library's: the
// server can't pick a default, so a restore must name a copy.
export const OTHER_COPY: CopyBody = {
  name: 'baihe_snapshot-20260929-120000.zip', created_at: '2026-09-29T12:00:00+00:00', size: 9_000_000, kind: 'db-only',
  drama_count: 2, readable: true, kept_as: null, managed: false, sequence: 4,
}

export const CHOOSE_SNAPSHOT: SnapshotBody = {
  exists: true, readable: true, choose_copy: true, default_copy: null, copies: [OTHER_COPY, ...COPIES],
}

export const SNAPSHOT_DRAMAS = [
  { id: 1, title: 'Grandmaster of Demonic Cultivation', media_type: 'audio_drama', line_count: 812, exists_now: true },
  { id: 2, title: "Heaven Official's Blessing", media_type: 'novel', line_count: 0, exists_now: false },
  { id: 3, title: 'Signal', media_type: 'video_drama', line_count: 1204, exists_now: false },
]

const NEW_COPY: CopyBody = {
  name: 'baihe_snapshot-20260930-080000.zip', created_at: '2026-09-30T08:00:00+00:00', size: 12_400_000,
  kind: 'db-only', drama_count: 3, readable: true, kept_as: 'daily', managed: true, sequence: 8,
}

/** The info for a list of copies (newest first), the way the server builds it
 * when the dates agree: the default is this library's highest sequence. */
function infoFor(copies: CopyBody[]): SnapshotBody {
  const own = copies.filter((c) => c.readable && c.managed !== false)
  const newest = own.sort((a, b) => (b.sequence ?? 0) - (a.sequence ?? 0))[0]
  if (!copies.length) return { exists: false, copies: [] }
  if (!copies.some((c) => c.readable)) return { exists: true, readable: false, copies }
  if (!newest) return { exists: true, readable: true, choose_copy: true, default_copy: null, copies }
  return {
    exists: true, readable: true, choose_copy: false, default_copy: newest.name, created_at: newest.created_at ?? undefined,
    kind: newest.kind ?? undefined, size: newest.size, app_version: '1.0', drama_count: newest.drama_count ?? undefined, copies,
  }
}

export function mockBackups(page: Page, opts: { snapshot?: SnapshotBody; jobPollsBeforeDone?: number } = {}) {
  const state = {
    settings: {
      enabled: false, frequency: 'daily', include_media: false, folder: '', frequencies: ['daily', 'weekly', 'monthly'],
      last_run_at: null as string | null, last_attempt_at: null as string | null, last_error: null as string | null,
      next_run_at: null as string | null, running: false,
    },
    snapshot: opts.snapshot ?? SNAPSHOT,
    posts: [] as { path: string; body: Record<string, unknown> }[],
    jobStarted: false,
    polls: 0,
  }
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })
  const copies = () => state.snapshot.copies ?? []
  const notFound = (route: Route) =>
    json(route, { error: { code: 'not_found', message: "That backup copy doesn't exist (it may have been rotated out)." } }, 404)

  const handler = async (route: Route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname
    if (req.method() === 'GET') {
      if (path === '/api/backups/settings') return json(route, { ...state.settings, copies: copies() })
      if (path === '/api/backups/snapshot') return json(route, state.snapshot)
      if (path === '/api/backups/snapshot/dramas') {
        const wanted = url.searchParams.get('snapshot')
        if (!wanted && !state.snapshot.default_copy) {
          return json(route, { error: { code: 'conflict', message: 'Choose which backup copy to use.', details: { reason: 'choose_copy', candidates: [] } } }, 409)
        }
        const copy = copies().find((c) => c.name === (wanted ?? state.snapshot.default_copy))
        if (!copy) return notFound(route)
        const dramas = copy.drama_count === 2 ? SNAPSHOT_DRAMAS.slice(1) : SNAPSHOT_DRAMAS
        return json(route, { name: copy.name, created_at: copy.created_at, kind: copy.kind, dramas })
      }
      return route.abort()
    }
    const body = (req.postDataJSON() ?? {}) as Record<string, unknown>
    state.posts.push({ path, body })
    if (path === '/api/backups/settings') {
      if (typeof body.folder === 'string' && body.folder && !/^[A-Za-z]:\\/.test(body.folder)) {
        return json(route, { error: { code: 'invalid_input', message: 'The backup folder must be a full folder path.' } }, 422)
      }
      Object.assign(state.settings, body)
      state.settings.next_run_at = state.settings.enabled ? '2026-10-01T08:00:00+00:00' : null
      return json(route, { ...state.settings, copies: copies() })
    }
    if (path === '/api/backups/now') {
      state.jobStarted = true
      return json(route, { job_id: 'library_auto_backup' })
    }
    if (path === '/api/backups/snapshot/restore-drama') {
      if (body.snapshot !== undefined && !copies().some((c) => c.name === body.snapshot)) return notFound(route)
      const d = SNAPSHOT_DRAMAS.find((x) => x.id === body.drama_id)
      if (!d) return json(route, { error: { code: 'not_found', message: "That drama isn't in the snapshot." } }, 404)
      return json(route, {
        drama_id: d.exists_now ? 40 : d.id, restored_as_new: d.exists_now,
        title: d.exists_now ? `${d.title} (restored 2026-09-30)` : d.title, media_restored: false,
        snapshot: body.snapshot ?? state.snapshot.default_copy, snapshot_kind: state.snapshot.kind, series: 'none', counts: { lines: d.line_count }, skipped_tables: ['bulk_jobs', 'metadata_research_results', 'usage_log'],
      })
    }
    if (path === '/api/backups/snapshot/delete') {
      const unmanaged = (c: CopyBody) => c.managed === false
      if (body.all === true && body.snapshot === undefined) {
        const left = body.include_unmanaged === true ? [] : copies().filter(unmanaged)
        const count = copies().length - left.length
        state.snapshot = infoFor(left)
        return json(route, { deleted: true, count, kept_unmanaged: left.length })
      }
      if (body.all !== undefined || body.snapshot === undefined) {
        return json(route, { error: { code: 'validation_error', message: 'Name one backup copy to delete, or ask for all of them.' } }, 422)
      }
      const target = copies().find((c) => c.name === body.snapshot)
      if (!target) return notFound(route)
      if (unmanaged(target) && body.include_unmanaged !== true) {
        return json(route, { error: { code: 'conflict', message: "That copy isn't managed by this library.", details: { reason: 'unmanaged' } } }, 409)
      }
      state.snapshot = infoFor(copies().filter((c) => c.name !== body.snapshot))
      return json(route, { deleted: true, count: 1, kept_unmanaged: 0 })
    }
    return route.abort()
  }

  const job = (route: Route) => {
    if (!state.jobStarted) return json(route, { error: { code: 'not_found', message: 'No job.' } }, 404)
    state.polls += 1
    const done = state.polls > (opts.jobPollsBeforeDone ?? 1)
    if (done && copies()[0]?.name !== NEW_COPY.name) {
      // A new copy on top; the rotation keeps at most 4.
      state.snapshot = infoFor([NEW_COPY, ...copies()].slice(0, 4))
      state.settings.last_run_at = '2026-09-30T08:00:00+00:00'
    }
    return json(route, {
      job_id: 'library_auto_backup', status: done ? 'done' : 'running', progress: done ? 1 : 0.4, message: '',
      error: null, description: 'Automatic backup', gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1,
      outcome: done ? 'ok' : null,
    })
  }

  return Promise.all([page.route('**/api/backups/**', handler), page.route('**/api/jobs/library_auto_backup', job)])
    .then(() => state)
}
