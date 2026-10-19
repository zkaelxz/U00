import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import type { SetupReadiness } from '../../types/setup'
import type { SourceSummary } from '../../types/sources'
import { SearchPanel } from './SearchPanel'

let readiness: Partial<SetupReadiness> | null = null
vi.mock('../../hooks/useReadiness', () => ({ useReadiness: () => ({ readiness, refresh: async () => {} }), refreshReadiness: async () => {} }))
vi.mock('../readiness/NeedsInstall', () => ({ NeedsInstall: ({ need }: { need: { id: string; reason: string } }) => <p data-testid={`needs-${need.id}`}>{need.reason}</p> }))

const src = (name: string, over: Partial<SourceSummary> = {}): SourceSummary => ({
  name, display_name: name.toUpperCase(), content_types: [], languages: [],
  supports: { search: true, get_series: true, get_chapters: true, get_pages: false, download_page: false, get_chapter_text: false, get_audio_url: false, login: false },
  import_supported: true, auth_supported: false, supports_adult_toggle: false, enabled: true, adult_enabled: false,
  health: 'green', has_saved_signin: false, pace: 'normal', fast_allowed: false, slowed_down: false, ...over,
})

const job = { status: 'idle', result: null, error: null, progress: null, startError: null, start: vi.fn(), cancel: vi.fn(), reset: vi.fn(), clearStartError: vi.fn() } as never

const show = (sources: SourceSummary[]) =>
  renderToStaticMarkup(<SearchPanel sources={sources} remote={false} job={job} resultsHidden={false} onOpen={() => {}} onUseLink={null} />)

describe('browser-only sources in Search', () => {
  it('lists them and shows no reason when the browser is ready', () => {
    readiness = { playwright: true, browser_found: true }
    const html = show([src('a'), src('b', { needs_browser: true })])
    expect(html).toContain('>B<')
    expect(html).not.toContain('needs-browser')
  })

  it('leaves them out and says why when playwright is missing', () => {
    readiness = { playwright: false, browser_found: false }
    const html = show([src('a'), src('b', { needs_browser: true })])
    expect(html).not.toContain('>B<')
    expect(html).toContain('needs-browser_package')
  })

  it('says no browser was found when only the program is missing', () => {
    readiness = { playwright: true, browser_found: false }
    expect(show([src('b', { needs_browser: true })])).toContain('needs-browser_program')
  })
})
