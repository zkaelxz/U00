import { describe, expect, it, vi } from 'vitest'

import { submitBugReport } from '../api/bugReports'
import type { CaptureSnapshot } from './capture'
import {
  CUT_NOTE, ISSUE_URL_MAX, areaForRoute, buildIdFrom, buildReport, clientMarkdown, githubIssueUrl, isPrivateHost,
  issueTitle, reportMode, sanitizeReport, sanitizeText, screenshotProblem,
} from './reportBundle'
import { closeReportDialog, isReportDialogOpen, openReportDialog, subscribeReportDialog } from './reportDialogStore'

const snap: CaptureSnapshot = {
  route: '/drama/3/review',
  route_history: [{ route: '/library', at: '12:00:00' }, { route: '/drama/3/review', at: '12:00:05' }],
  console: [{ level: 'error', message: 'x | ```y```', at: '12:00:06' }],
  errors: [],
  failed_requests: [{ method: 'GET', path: '/api/review/dramas/3/lines', status: 500, code: 'internal_error', at: '12:00:06' }],
}
const env = { userAgent: 'UA/1', viewport: { width: 390, height: 844, dpr: 3 }, hostname: '192.168.1.20', buildId: 'index-AbC.js' }
const meta = { app: 'Baihe Studio', api_version: '0.1', environment: 'development', local: false }

describe('report bundle', () => {
  it('buildReport bundles notes, buffers, versions and the device', () => {
    const r = buildReport({ whatHappened: '  Blank page  ', expected: '', includeServerLog: false }, snap, meta, env, 'remote')
    expect(r).toMatchObject({
      what_happened: 'Blank page', include_server_log: false, route: '/drama/3/review', api_version: '0.1',
      build_id: 'index-AbC.js', user_agent: 'UA/1', viewport: { width: 390, height: 844, dpr: 3 }, mode: 'lan',
    })
    expect(r.failed_requests).toHaveLength(1)
  })

  it('mode: pc, lan, remote, unknown', () => {
    expect(reportMode('local', 'example.com')).toBe('pc')
    expect(reportMode('remote', '10.0.0.4')).toBe('lan')
    expect(reportMode('remote', 'baihe.example.com')).toBe('remote')
    expect(reportMode('unknown', '10.0.0.4')).toBe('unknown')
    expect(isPrivateHost('172.20.1.1')).toBe(true)
    expect(isPrivateHost('172.40.1.1')).toBe(false)
  })

  it('build id from the hashed entry script, else dev', () => {
    expect(buildIdFrom(['http://h/assets/index-AbC123.js'])).toBe('index-AbC123.js')
    expect(buildIdFrom(['/src/main.tsx'])).toBe('dev')
  })

  it('area for the issue form', () => {
    expect(areaForRoute('/drama/3/review')).toBe('Review')
    expect(areaForRoute('/drama/3')).toBe('Workspace/Source')
    expect(areaForRoute('/read/2')).toBe('Reader')
    expect(areaForRoute('/diagnostics')).toBe('Diagnostics')
    expect(areaForRoute('/nowhere')).toBe('Other')
  })

  it('client markdown has the sections and a safe fence', () => {
    const md = clientMarkdown(buildReport({ whatHappened: 'Blank', expected: 'Lines', includeServerLog: true }, snap, meta, env, 'local'))
    expect(md).toContain('## What happened\n\nBlank')
    expect(md).toContain('not saved (the server could not be reached)')
    expect(md).toContain('| 12:00:06 | GET | `/api/review/dramas/3/lines` | 500 | internal_error |')
    expect(md).toContain('````text\n12:00:06 [error] x | ```y```\n````')
  })

  it('issue title', () => {
    expect(issueTitle('Line one\nmore')).toBe('[Bug] Line one')
    expect(issueTitle('')).toBe('[Bug] Problem report')
    expect(issueTitle('x'.repeat(200)).length).toBeLessThanOrEqual(86)
  })

  it('GitHub link pre-fills the form fields by id', () => {
    const { url, truncated } = githubIssueUrl({ what_happened: 'Blank', expected: 'Lines', route: '/drama/3/review' }, '## Report')
    expect(truncated).toBe(false)
    const u = new URL(url)
    expect(u.origin + u.pathname).toBe('https://github.com/zkaelxz/U00/issues/new')
    expect(u.searchParams.get('template')).toBe('bug.yml')
    expect(u.searchParams.get('title')).toBe('[Bug] Blank')
    expect(u.searchParams.get('area')).toBe('Review')
    expect(u.searchParams.get('report')).toBe('## Report')
  })

  it('GitHub link is cut on a character boundary to fit', () => {
    const md = '漢字😀'.repeat(3000)
    const { url, truncated } = githubIssueUrl({ what_happened: 'Blank', expected: '', route: '/' }, md)
    expect(truncated).toBe(true)
    expect(url.length).toBeLessThanOrEqual(ISSUE_URL_MAX)
    const report = new URL(url).searchParams.get('report')!
    expect(report.endsWith(CUT_NOTE)).toBe(true)
    expect(report.length).toBeGreaterThan(500)
    expect(report).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/)   // no lone high surrogate
  })

  it('the save-failed link has no planted key, token or Windows user path', () => {
    const key = 'sk-ant-api03-SECRETSECRETSECRET123456'
    const win = String.raw`C:\Users\kaewinuser\AppData\Local\Baihe\library\library.db`
    const notes = `Crashed with ${key} at ${win}; see https://bob:hunter2@cdn.example.com/a.png?X-Amz-Signature=zz9&token=abc and /home/someoneelse/x.txt Cookie: sid=cookieval`
    const r = sanitizeReport(buildReport({ whatHappened: notes, expected: `also ${win}`, includeServerLog: true },
      { ...snap, console: [{ level: 'error', message: `boom ${key} ${win}`, at: '12:00:06' }] }, meta, env, 'local'))
    const md = sanitizeText(clientMarkdown(r))
    const { url } = githubIssueUrl(r, md)
    const decoded = decodeURIComponent(url)
    for (const bad of [key, 'SECRETSECRET', 'kaewinuser', 'AppData', 'someoneelse', 'bob', 'hunter2', 'zz9', 'token=abc', 'cookieval']) {
      expect(decoded).not.toContain(bad)
      expect(md).not.toContain(bad)
    }
    expect(decoded).toContain('.../library.db')
    expect(decoded).toContain('[REDACTED]')
    expect(md).toContain('`/drama/3/review`')                     // app routes keep their shape
    expect(sanitizeText('/Users/alice/Desktop')).not.toContain('alice')
  })

  it('the saved-report link uses the server texts and title as given', () => {
    const { url } = githubIssueUrl({ what_happened: 'scrubbed', expected: '', route: '/', title: '[Bug] scrubbed' }, 'issue md')
    const u = new URL(url)
    expect(u.searchParams.get('title')).toBe('[Bug] scrubbed')
    expect(u.searchParams.get('report')).toBe('issue md')
  })

  it('screenshot checks', () => {
    expect(screenshotProblem(null)).toBeNull()
    expect(screenshotProblem({ type: 'image/png', size: 10 })).toBeNull()
    expect(screenshotProblem({ type: 'image/gif', size: 10 })).toMatch(/PNG or JPEG/)
    expect(screenshotProblem({ type: 'image/jpeg', size: 6 * 1024 * 1024 })).toMatch(/5 MB/)
  })
})

describe('submit and dialog store', () => {
  it('POSTs multipart with the report JSON and the screenshot', async () => {
    const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 4, markdown: 'm' }), { status: 200 }))
    const r = buildReport({ whatHappened: 'x', expected: '', includeServerLog: true }, snap, meta, env, 'local')
    const shot = new File([new Uint8Array([1, 2])], 's.png', { type: 'image/png' })
    const out = await submitBugReport(r, shot, mock as unknown as typeof fetch)
    expect(out.id).toBe(4)
    const [path, init] = mock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/diagnostics/bug-reports')
    expect(init.method).toBe('POST')
    const form = init.body as FormData
    expect(JSON.parse(form.get('report') as string).what_happened).toBe('x')
    expect((form.get('screenshot') as File).name).toBe('s.png')
    expect(new Headers(init.headers).get('X-Baihe-Local')).toBe('1')
  })

  it('openReportDialog notifies subscribers', () => {
    const l = vi.fn()
    const off = subscribeReportDialog(l)
    openReportDialog()
    expect(isReportDialogOpen()).toBe(true)
    closeReportDialog()
    expect(isReportDialogOpen()).toBe(false)
    expect(l).toHaveBeenCalledTimes(2)
    off()
  })
})
