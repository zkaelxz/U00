import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DramaDetail } from '../../api/types'
import { JobsContext } from '../../hooks/useJobs'
import type { StepKey } from './stepModel'
import { kindBadgeText } from './titleKind'
import { TitleTop, type TitleProgressInput } from './TitleTop'

afterEach(() => vi.unstubAllGlobals())

function browser(phone: boolean) {
  const localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  vi.stubGlobal('window', { location: { hash: '#/drama/3' }, localStorage, matchMedia: () => ({ matches: phone, addEventListener() {}, removeEventListener() {} }) })
}

const drama = { id: 3, title_en: 'Signal', media_type: 'audio_drama', source_language: 'zh', source_url: null } as unknown as DramaDetail
const base = { audioReady: true, exported: false, riskCount: 3, flowOn: false, onFlowProgress: () => {} }
// One progress per current step; Export is never current on its own (Check is done only once the title is exported).
const AT: Record<Exclude<StepKey, 'export'>, TitleProgressInput> = {
  add: { ...base, audioReady: false, lineCount: 0, translatedCount: 0 },
  transcribe: { ...base, lineCount: 0, translatedCount: 0 },
  translate: { ...base, lineCount: 24, translatedCount: 23 },
  check: { ...base, lineCount: 24, translatedCount: 24 },
}

const withJobs = (el: ReactElement) =>
  renderToStaticMarkup(<JobsContext.Provider value={{ jobs: [], error: null, polling: false, reload: () => {} }}>{el}</JobsContext.Provider>)
const render = (progress: TitleProgressInput, page: { home?: boolean; openStep?: StepKey | null; badge?: boolean; drama?: DramaDetail; onCheck?: () => void; onSearch?: () => void } = {}) =>
  withJobs(
    <TitleTop id={3} drama={page.drama ?? drama} title="Signal" back={{ href: '#/library', label: 'Back to Library', text: 'Library' }}
      badge={page.badge} home={page.home} openStep={page.openStep ?? null} onCheck={page.onCheck} onSearch={page.onSearch} onJobFinished={() => {}} onView={() => {}} progress={progress} />,
  )

// Where the phone draws the main button: in the panel the steps button opens, or on the page under the header.
function phoneMain(html: string): 'panel' | 'page' {
  expect(html.match(/class="title-main-row"/g)).toHaveLength(1)
  const panel = html.slice(html.indexOf('class="steps-fold-body"'), html.indexOf('class="steps-fold-controls"'))
  return panel.includes('title-main-row') ? 'panel' : 'page'
}

describe('the phone\'s main button', () => {
  it('on the title page, sits in the steps panel on the Check step and under the header on every other step', () => {
    browser(true)
    expect(phoneMain(render(AT.add, { home: true }))).toBe('page')
    expect(phoneMain(render(AT.transcribe, { home: true }))).toBe('page')
    expect(phoneMain(render(AT.translate, { home: true }))).toBe('page')
    expect(phoneMain(render(AT.check, { home: true }))).toBe('panel')
  })

  it('on Review, sits in the steps panel whatever the current step', () => {
    browser(true)
    for (const progress of Object.values(AT)) expect(phoneMain(render(progress, { openStep: 'check' }))).toBe('panel')
  })

  it('on the other stage pages, sits under the header, even on the Check step', () => {
    browser(true)
    expect(phoneMain(render(AT.check, { openStep: 'translate' }))).toBe('page')
    expect(phoneMain(render(AT.check, { openStep: 'export' }))).toBe('page')
    expect(phoneMain(render(AT.translate, { openStep: 'translate' }))).toBe('page')
  })
})

describe('the kind badge', () => {
  it('names the kind and the languages when the source language has a label', () => {
    expect(kindBadgeText('audio_drama', 'zh')).toBe('Audio drama · Chinese to English')
    expect(kindBadgeText('video_drama', 'KO')).toBe('Video drama · Korean to English')
    expect(kindBadgeText('anime', 'zh-hant')).toBe('Anime · Chinese (Traditional) to English')
  })

  it('is just the kind for a language without a label, for English, and with no language', () => {
    expect(kindBadgeText('audio_drama', 'th')).toBe('Audio drama')
    expect(kindBadgeText('audio_drama', 'en')).toBe('Audio drama')
    expect(kindBadgeText('audio_drama', '')).toBe('Audio drama')
    expect(kindBadgeText('audio_drama', null)).toBe('Audio drama')
    expect(kindBadgeText('audio_drama', undefined)).toBe('Audio drama')
  })

  it('is a chip after the title\'s name and before its buttons on a desktop, and not on a phone', () => {
    browser(false)
    const html = render(AT.check, { home: true, badge: true })
    expect(html).toContain('<h1 data-testid="drama-title" title="Signal">Signal</h1></div><span class="title-top-kind" data-testid="title-kind">Audio drama · Chinese to English</span>')
    expect(html.indexOf('data-testid="title-kind"')).toBeLessThan(html.indexOf('class="title-top-actions"'))
    expect(render(AT.check, { home: true, badge: true, drama: { ...drama, source_language: 'th' } })).toContain('data-testid="title-kind">Audio drama</span>')
    expect(render(AT.check, { openStep: 'translate' })).not.toContain('title-kind')
    browser(true)
    expect(render(AT.check, { home: true, badge: true })).not.toContain('title-kind')
  })
})

describe('the main button where the lines are', () => {
  it('opens the queue in place instead of linking to Review when the page gives it onCheck', () => {
    browser(false)
    const html = render(AT.check, { home: true, onCheck: () => {} })
    expect(html).toMatch(/<button type="button" class="[^"]*">Check the 3 lines<\/button>/)
    expect(render(AT.check, { home: true })).toMatch(/<a [^>]*href="#\/drama\/3\/review"[^>]*>Check the 3 lines<\/a>/)
  })

  it('keeps the one-button flow once it has started, after the lines arrive', () => {
    browser(false)
    expect(render({ ...AT.translate, flowOn: true }, { home: true })).toMatch(/<button type="button" class="[^"]*">Transcribe and translate<\/button>/)
    expect(render(AT.translate, { home: true })).toContain('>Translate 1 line</a>')
  })
})

describe('the header\'s buttons on a desktop', () => {
  const actions = (html: string) => html.slice(html.indexOf('class="title-top-actions"'), html.indexOf('class="title-top-more-row"'))
  const names = (html: string) => [...html.matchAll(/<(?:a|button)\b[^>]*>([^<]+)</g)].map((m) => m[1])

  it('are Add media, Redo transcription, Glossary, Search and More, in that order', () => {
    browser(false)
    expect(names(actions(render(AT.check, { home: true, onSearch: () => {} })))).toEqual(['Add media', 'Redo transcription', 'Glossary', 'Search', 'More'])
  })

  it('offer Search only for a title with lines, and only where something can answer it', () => {
    browser(false)
    expect(names(actions(render(AT.transcribe, { home: true, onSearch: () => {} })))).not.toContain('Search')
    expect(names(actions(render(AT.check, { home: true })))).not.toContain('Search')
  })

  it('leave the Simple | Full switch to More\'s row, which has a Full view button beside Open folder', () => {
    browser(false)
    const html = render(AT.check, { home: true, onSearch: () => {} })
    expect(actions(html)).not.toContain('Title view')
    const more = html.slice(html.indexOf('class="title-top-more-row"'), html.indexOf('class="title-steps"'))
    expect(more).not.toContain('Title view')
    expect(names(more)).toEqual(['Full view', 'Open folder', 'Add a source link'])
  })

  it('leave a slot in More\'s row where an audio title\'s Player button is drawn', () => {
    browser(false)
    expect(render(AT.check, { home: true })).toContain('class="title-top-player"')
  })
})
