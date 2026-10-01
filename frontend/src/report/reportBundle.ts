/*
 * Pure helpers for "Report a problem": build the report the server stores,
 * the client-side markdown (used when the server can't be reached), the
 * link-safe sanitizer for that fallback, and the pre-filled GitHub issue link.
 *
 * The GitHub link is public, so it is only ever built from the server's
 * scrubbed texts (BugReportSaved: issue_markdown, what_happened, expected,
 * title; never the server log) or, when saving failed, from the client data
 * passed through sanitizeText().
 */
import { SCREENSHOT_MAX_BYTES, SCREENSHOT_TYPES } from '../api/bugReports'
import type { PcMode } from '../api/pcOnly'
import type { MetaResponse } from '../api/types'
import type { BugReportClient, ReportMode } from '../types/bugReports'
import type { CaptureSnapshot } from './capture'

const ISSUE_BASE = 'https://github.com/zkaelxz/U00/issues/new'
const ISSUE_TEMPLATE = 'bug.yml'
export const ISSUE_URL_MAX = 6000
export const CUT_NOTE = '\n\n…(cut to fit the link: paste the full report from the app here)'

type ReportNotes = { whatHappened: string; expected: string; includeServerLog: boolean }

type ReportEnv = {
  userAgent: string
  viewport: { width: number; height: number; dpr?: number } | null
  hostname: string
  buildId: string
}

/** 'pc' at the main PC; otherwise LAN when the page host is a private address. */
export function reportMode(pc: PcMode, hostname: string): ReportMode {
  if (pc === 'local') return 'pc'
  if (pc === 'unknown') return 'unknown'
  return isPrivateHost(hostname) ? 'lan' : 'remote'
}

export function isPrivateHost(host: string): boolean {
  const h = host.toLowerCase()
  if (h === 'localhost' || h.endsWith('.local') || h.endsWith('.lan') || h.endsWith('.home.arpa')) return true
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(h)
  if (!m) return false
  const [a, b] = [Number(m[1]), Number(m[2])]
  return a === 10 || a === 127 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31) || (a === 169 && b === 254)
}

/** The frontend build: the hashed entry asset ("index-AbC123.js"), or "dev". */
export function buildIdFrom(scriptSrcs: string[]): string {
  for (const src of scriptSrcs) {
    const m = /\/assets\/([^/?#]+\.js)(?:[?#].*)?$/.exec(src)
    if (m) return m[1]
  }
  return 'dev'
}

// Issue-form "Page / area" options (.github/ISSUE_TEMPLATE/bug.yml).
const STAGE_AREAS: Record<string, string> = {
  source: 'Workspace/Source', transcribe: 'Transcribe', translate: 'Translate',
  review: 'Review', export: 'Export', dub: 'Dub',
}
const PAGE_AREAS: Record<string, string> = {
  library: 'Library', translate: 'Translate', read: 'Reader', sources: 'Sources',
  settings: 'Settings', diagnostics: 'Diagnostics', comic: 'Comic', login: 'Sign-in', 'sign-in': 'Sign-in',
}

export function areaForRoute(route: string): string {
  const [head, , stage] = route.split('/').filter(Boolean)
  if (!head) return 'Library'
  if (head === 'drama') return STAGE_AREAS[stage ?? 'source'] ?? 'Workspace/Source'
  return PAGE_AREAS[head] ?? 'Other'
}

export function buildReport(notes: ReportNotes, snap: CaptureSnapshot, meta: MetaResponse | null,
  env: ReportEnv, pc: PcMode): BugReportClient {
  return {
    what_happened: notes.whatHappened.trim().slice(0, 5000),
    expected: notes.expected.trim().slice(0, 5000),
    include_server_log: notes.includeServerLog,
    route: snap.route,
    route_history: snap.route_history,
    console: snap.console,
    errors: snap.errors,
    failed_requests: snap.failed_requests,
    app_version: (meta?.app ?? '').slice(0, 60),
    api_version: (meta?.api_version ?? '').slice(0, 60),
    environment: (meta?.environment ?? '').slice(0, 60),
    build_id: env.buildId.slice(0, 120),
    user_agent: env.userAgent.slice(0, 500),
    viewport: env.viewport,
    mode: reportMode(pc, env.hostname),
  }
}

function fence(text: string): string {
  const longest = Math.max(0, ...(text.match(/`+/g) ?? []).map((m) => m.length))
  const f = '`'.repeat(Math.max(3, longest + 1))
  return `${f}text\n${text}\n${f}`
}

const cell = (v: unknown) => String(v ?? '').replace(/\|/g, '\\|').replace(/\n/g, ' ')

/** Markdown from the client data alone (the server could not be reached). */
export function clientMarkdown(r: BugReportClient): string {
  const vp = r.viewport ? `${r.viewport.width}x${r.viewport.height}${r.viewport.dpr ? ` @${r.viewport.dpr}x` : ''}` : 'unknown'
  const out = [
    `## What happened\n\n${r.what_happened || '(not given)'}\n`,
    `## What I expected\n\n${r.expected || '(not given)'}\n`,
    '## Context\n',
    '- Report: not saved (the server could not be reached)',
    `- Page: \`${r.route || '/'}\``,
    `- Mode: ${r.mode}`,
    `- App: ${r.app_version || '?'} · API ${r.api_version || '?'}${r.environment ? ` (${r.environment})` : ''}`,
    `- Frontend build: ${r.build_id || 'unknown'}`,
    `- Browser: ${r.user_agent || 'unknown'}`,
    `- Viewport: ${vp}`,
    '',
  ]
  if (r.route_history.length) {
    out.push('## Recent pages\n', ...r.route_history.map((v) => `- ${cell(v.at)} \`${v.route}\``), '')
  }
  if (r.failed_requests.length) {
    out.push('## Failed API calls\n', '| When | Method | Path | Status | Code |', '|---|---|---|---|---|',
      ...r.failed_requests.map((f) => `| ${cell(f.at)} | ${cell(f.method)} | \`${cell(f.path)}\` | ${cell(f.status)} | ${cell(f.code)} |`), '')
  }
  if (r.errors.length) {
    out.push('## Uncaught errors\n', fence(r.errors.map((e) => `${e.at} [${e.kind}] ${e.message}${e.source ? ` (${e.source})` : ''}`).join('\n')), '')
  }
  if (r.console.length) {
    out.push('## Console errors and warnings\n', fence(r.console.map((e) => `${e.at} [${e.level}] ${e.message}`).join('\n')), '')
  }
  return `${out.join('\n').trimEnd()}\n`
}

// Client-side masking for the save-failed path: the server can't scrub, so
// keys, tokens, cookies, URL userinfo, user folders and absolute paths are
// masked here before the text reaches the clipboard or the GitHub link.
const SECRET_PATTERNS: [RegExp, string][] = [
  [/((?:set-)?cookie["']?\s*[:=]\s*)[^\r\n]*/gi, '$1[REDACTED]'],
  [/(authorization["']?\s*[:=]\s*["']?(?:Bearer\s+)?)[A-Za-z0-9_\-.~+/=]{6,}/gi, '$1[REDACTED]'],
  [/(x-csrf-token["']?\s*[:=]\s*["']?)[^\s"',;]+/gi, '$1[REDACTED]'],
  [/(\/\/)[^/\s@"'<>]+@/g, '$1***@'],
  [/(^|[^\w.-])((?:[\w.-]*(?:token|sig|auth|session|secret|credential|passw|key|jwt|ticket|hmac|policy)[\w.-]*|x-amz-[\w.-]+)=)[^&#\s"'<>\\;,]*/gi, '$1$2[REDACTED]'],
  [/\b(?:sk-|AIza|hf_|gsk_|ghp_|gho_|ghs_|github_pat_|xox[abprs]-)[A-Za-z0-9_-]{10,}/g, '[REDACTED]'],
  [/(^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])/g, '$1[REDACTED]'],
]
// A user's home folder on any OS: C:\Users\<name>, /home/<name>, /Users/<name>.
const USER_DIR = /((?:[a-z]:)?[\\/]+(?:users|documents and settings|home)[\\/]+)[^\\/\s"'<>|:*?]+/gi
// An absolute filesystem path (drive, UNC or a POSIX system root), cut to its last segment.
const ABS_PATH = /(?:(?<!\w)[a-z]:[\\/]|\\\\|(?<![\w.:/])\/(?:home|users|root|mnt|media|tmp|var|etc|opt|srv|private|volumes)\/)(?:[^\s\\/:*?"<>|]+[\\/])*([^\s\\/:*?"<>|]+)/gi

export function sanitizeText(text: string): string {
  let out = text
  for (const [re, to] of SECRET_PATTERNS) out = out.replace(re, to)
  out = out.replace(USER_DIR, '$1[USER]')
  return out.replace(ABS_PATH, '.../$1')
}

/** The client-only report made safe for the clipboard and the GitHub link. */
export function sanitizeReport(r: BugReportClient): BugReportClient {
  return { ...r, what_happened: sanitizeText(r.what_happened), expected: sanitizeText(r.expected) }
}

export function issueTitle(whatHappened: string): string {
  const first = whatHappened.trim().split('\n', 1)[0] ?? ''
  const short = first.length > 80 ? `${first.slice(0, 79)}…` : first
  return `[Bug] ${short || 'Problem report'}`
}

function issueUrlWith(params: Record<string, string>): string {
  const qs = Object.entries(params).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
  return `${ISSUE_BASE}?${qs}`
}

/**
 * The issue-form link with the fields pre-filled by id (issue forms ignore
 * `body`). The report is cut, on a character boundary, so the whole link
 * stays within ISSUE_URL_MAX; `truncated` says it was cut.
 */
export type IssueFields = Pick<BugReportClient, 'what_happened' | 'expected' | 'route'> & { title?: string }

export function githubIssueUrl(r: IssueFields, markdown: string,
  max = ISSUE_URL_MAX): { url: string; truncated: boolean } {
  const base: Record<string, string> = {
    template: ISSUE_TEMPLATE,
    title: r.title ?? issueTitle(r.what_happened),
    area: areaForRoute(r.route),
    'what-happened': r.what_happened.slice(0, 1500),
    expected: r.expected.slice(0, 800),
  }
  const full = issueUrlWith({ ...base, report: markdown })
  if (full.length <= max) return { url: full, truncated: false }
  const chars = Array.from(markdown)
  let lo = 0
  let hi = chars.length
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2)
    if (issueUrlWith({ ...base, report: chars.slice(0, mid).join('') + CUT_NOTE }).length <= max) lo = mid
    else hi = mid - 1
  }
  const url = issueUrlWith({ ...base, report: chars.slice(0, lo).join('') + CUT_NOTE })
  return { url: url.length <= max ? url : issueUrlWith({ template: ISSUE_TEMPLATE, title: base.title }), truncated: true }
}

/** Why a chosen screenshot can't be sent, or null. */
export function screenshotProblem(file: Pick<File, 'type' | 'size'> | null): string | null {
  if (!file) return null
  if (!(SCREENSHOT_TYPES as readonly string[]).includes(file.type)) return 'Choose a PNG or JPEG image.'
  if (file.size > SCREENSHOT_MAX_BYTES) return 'The image is larger than 5 MB.'
  return null
}
