// Pure text helpers for the Notion export (Settings card and the Export
// stage's "Export to Notion"). Unit-tested.
import { ApiError } from '../../api/client'
import type { NotionConfig, NotionTargetType, NotionTestResult } from '../../types/notion'

export const TOKEN_WRITES_REFUSED =
  'The Notion token can only be set on the Baihe PC itself, with key writes turned on (start the API with BAIHE_API_ALLOW_KEY_WRITES=1).'

export function notionSummary(c: NotionConfig): string {
  if (!c.token_configured && !c.target_id) return 'Not set up'
  if (!c.token_configured) return 'No token'
  if (!c.target_id) return 'No page or database'
  return c.target_type === 'page' ? 'Ready, into a page' : 'Ready, into a database'
}

// Ready to export: a saved token and somewhere to put the pages.
export function readyToExport(c: NotionConfig | null): boolean {
  return !!c && c.token_configured && !!c.target_id
}

// Some setup done but not all: the Export stage says so instead of hiding.
export function partlySetUp(c: NotionConfig | null): boolean {
  return !!c && !readyToExport(c) && (c.token_configured || !!c.target_id)
}

// Only ever link to Notion itself, never to whatever URL came back.
export function safeNotionUrl(url: string | null | undefined): string | null {
  return typeof url === 'string' && url.startsWith('https://www.notion.so/') ? url : null
}

// The config fields the form changed, and nothing else. With no type saved
// yet, the shown default is sent along with the first target (an untouched
// empty form has nothing to save).
export function configChanges(
  c: NotionConfig,
  type: NotionTargetType,
  target: string,
): { target_type?: NotionTargetType; target_id?: string } {
  const body: { target_type?: NotionTargetType; target_id?: string } = {}
  const t = target.trim()
  if (type !== c.target_type && (c.target_type !== null || t !== '')) body.target_type = type
  if (t !== (c.target_id ?? '')) body.target_id = t
  return body
}

export function testResultText(r: NotionTestResult): string {
  const what = r.target_type === 'database' ? 'database' : 'page'
  const title = r.target_title || 'Untitled'
  return `Connected as ${r.bot_name || 'your integration'}. Exports go into the ${what} "${title}".`
}

// Server messages for these routes are fixed text (never the token or a
// path), so a 404/409/422/503 message is safe to show.
export function notionErrorMessage(err: unknown, tokenWrite = false): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return tokenWrite ? TOKEN_WRITES_REFUSED : 'Notion export is PC only. Run this on the main PC.'
    if (err.status === 0) return 'Could not reach the Baihe API. Is it running?'
    if ([404, 409, 422, 503].includes(err.status) && err.message) return err.message
  }
  return 'That did not work. Try again.'
}
