import { renderToStaticMarkup } from 'react-dom/server'
import { createRef, type ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ReviewLine } from '../../../../types/review'
import { QueueMediaToggle } from './CheckQueue'
import type { PlayerHandle } from './Player'
import { REVIEW_TOOLS, type ReviewTool } from './reviewToolsTabs'
import { ReviewTools } from './ReviewTools'
import { aroundFrom } from './checkQueueModel'
import { LinesAround, LineWhy, NearLine, ThisLinePanel } from './ThisLinePanel'

const line = (n: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id: 100 + n, idx: n - 1, start: n * 2, end: n * 2 + 1.5, zh: `第${n}句`, en: `Line ${n}`, speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, mark: null, ...over,
})
const GLOSSARY = line(24, { flag: 'glossary_missing', flag_note: 'Glossary: 夷陵老祖 -> Yiling Patriarch missing' })

afterEach(() => vi.unstubAllGlobals())

function browser(hash: string, store: Record<string, string> = {}) {
  const data = new Map(Object.entries(store))
  const localStorage = { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v), removeItem: (k: string) => void data.delete(k) }
  vi.stubGlobal('window', { location: { hash }, localStorage, matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }) })
  return data
}

const tools = Object.fromEntries(REVIEW_TOOLS.map((t) => [t.id, <p key={t.id}>{t.id}</p>])) as Record<ReviewTool, ReactElement>
const render = (props: Partial<Parameters<typeof ReviewTools>[0]> = {}) =>
  renderToStaticMarkup(<ReviewTools dramaId={3} status={<p>status</p>} tools={tools} {...props} />)
const tabs = (html: string) => [...html.matchAll(/role="tab" aria-selected="(true|false)"[^>]*>([^<]+)</g)].map((m) => `${m[2]}${m[1] === 'true' ? '*' : ''}`)

describe('the This line tab', () => {
  it('leads the tab row and is the open tab when a dock is offered, with an empty panel for the dock', () => {
    browser('#/drama/3/review')
    const html = render({ lineDock: () => {} })
    expect(tabs(html)).toEqual(['This line*', 'Look for problems', 'Change many lines', 'Records', 'Finish'])
    expect(html).toMatch(/<div id="review-tools-panel-line" role="tabpanel" aria-labelledby="review-tools-tab-line" class="review-toolbox-panel review-line-dock"><\/div>/)
    expect(html).toMatch(/id="review-tools-panel-problems"[^>]*hidden=""/)
  })

  it('is not there without a dock, and the remembered group is open', () => {
    browser('#/drama/3/review', { 'baihe.pref.review.tools.tab.3': '"finish"' })
    const html = render()
    expect(tabs(html)).toEqual(['Look for problems', 'Change many lines', 'Records', 'Finish*'])
    expect(html).not.toContain('review-line-dock')
  })

  it('gives way to a group the address names', () => {
    browser('#/drama/3/review?tools=change')
    expect(tabs(render({ lineDock: () => {} }))).toEqual(['This line', 'Look for problems', 'Change many lines*', 'Records', 'Finish'])
  })
})

describe('the Episodes tab', () => {
  const episodes = <ul aria-label="Episodes"><li>Episode 1</li></ul>

  it('ends the tab row when the page gives the series\' episodes, its list mounted once it is picked', () => {
    browser('#/drama/3')
    const html = render({ episodes })
    expect(tabs(html)).toEqual(['Look for problems*', 'Change many lines', 'Records', 'Finish', 'Episodes'])
    expect(html).toMatch(/<div id="review-tools-panel-episodes" role="tabpanel" aria-labelledby="review-tools-tab-episodes" class="review-toolbox-panel" hidden=""><\/div>/)
  })

  it('is not there without a series', () => {
    browser('#/drama/3')
    expect(tabs(render())).not.toContain('Episodes')
  })

  it('is not opened by a remembered Episodes tab or a ?tools=episodes link (the address keeps only groups), and stays last beside This line', () => {
    browser('#/drama/3?tools=episodes', { 'baihe.pref.review.tools.tab.3': '"episodes"' })
    expect(tabs(render({ episodes }))).toEqual(['Look for problems*', 'Change many lines', 'Records', 'Finish', 'Episodes'])
    browser('#/drama/3')
    expect(tabs(render({ episodes, lineDock: () => {} }))).toEqual(['This line*', 'Look for problems', 'Change many lines', 'Records', 'Finish', 'Episodes'])
  })
})

describe('the tools fold', () => {
  it('folded, puts the tabs and panels behind Checks and tools, closed, with the job status above it, still mounted', () => {
    browser('#/drama/3/review')
    const html = render({ folded: true })
    expect(html.indexOf('<p>status</p>')).toBeLessThan(html.indexOf('>Checks and tools'))
    expect(html).toMatch(/<button type="button" class="[^"]*review-toolbox-fold[^"]*" aria-expanded="false" aria-controls="review-tools-body">Checks and tools/)
    expect(html).toMatch(/<div id="review-tools-body" class="review-toolbox-body" hidden="">/)
    expect(html).toContain('role="tablist"')
  })

  it('opens for a link to a group, and is not there unfolded', () => {
    browser('#/drama/3/review?tools=finish')
    expect(render({ folded: true })).toMatch(/aria-expanded="true"[^>]*>Checks and tools/)
    expect(render({ folded: true })).toContain('<div id="review-tools-body" class="review-toolbox-body">')
    browser('#/drama/3/review')
    expect(render()).not.toContain('Checks and tools <')
    expect(render()).toContain('<div id="review-tools-body" class="review-toolbox-body">')
  })
})

describe('the This line panel', () => {
  const player = createRef<PlayerHandle>()

  it('says why the line is listed, with Glossary for a glossary flag', () => {
    const html = renderToStaticMarkup(<LineWhy dramaId={3} line={GLOSSARY} />)
    expect(html).toContain('Why it is listed')
    expect(html).toContain('The glossary writes 夷陵老祖 as “Yiling Patriarch”')
    expect(html).toContain('<a class="this-line-link" href="#/drama/3/translate?tab=glossary">Glossary</a>')
    expect(renderToStaticMarkup(<LineWhy dramaId={3} line={line(7, { confidence: 0.5 })} />)).not.toContain('Glossary')
  })

  it('adds that Whisper was also unsure of a flagged line', () => {
    const html = renderToStaticMarkup(<LineWhy dramaId={3} line={{ ...GLOSSARY, confidence: 0.58 }} />)
    expect(html).toMatch(/that name\. Put it in the English, or change the glossary if the name is wrong\. Whisper was also unsure of this line \(58% sure\)\.</)
    expect(renderToStaticMarkup(<LineWhy dramaId={3} line={GLOSSARY} />)).not.toContain('also unsure')
  })

  it('lists the line before, the open line and the line after, in order, the open one marked', () => {
    const html = renderToStaticMarkup(<LinesAround line={line(24)} around={{ before: line(23), after: line(25, { en: '' }) }} sourceLanguage="zh" onShowLine={() => {}} />)
    expect([...html.matchAll(/>#(\d+)</g)].map((m) => m[1])).toEqual(['23', '24', '25'])
    expect(html).toMatch(/<div class="this-line-near is-open" aria-current="true">.*#24/)
    expect(html.match(/title="Open in full list"/g)).toHaveLength(2)
    expect(html).toContain('<span class="muted review-untranslated">Not translated</span>')
  })

  it('shows only the lines that exist at the start and end of a title', () => {
    const first = renderToStaticMarkup(<LinesAround line={line(1)} around={{ before: null, after: line(2) }} sourceLanguage="zh" onShowLine={() => {}} />)
    expect([...first.matchAll(/>#(\d+)</g)].map((m) => m[1])).toEqual(['1', '2'])
    const last = renderToStaticMarkup(<LinesAround line={line(24)} around={{ before: line(23), after: null }} sourceLanguage="zh" onShowLine={() => {}} />)
    expect([...last.matchAll(/>#(\d+)</g)].map((m) => m[1])).toEqual(['23', '24'])
  })

  it('opens a neighbour on its page in the full list when it is pressed', () => {
    const onShowLine = vi.fn()
    const button = NearLine({ line: line(23), sourceLanguage: 'zh', onShowLine }) as ReactElement<{ onClick: () => void }>
    button.props.onClick()
    expect(onShowLine).toHaveBeenCalledWith(123)
  })

  it('finds the neighbours on the page, or at its edge on the pages either side', () => {
    const page = [line(2), line(3), line(4)]
    expect(aroundFrom(page, 103, null, null)).toEqual({ before: line(2), after: line(4) })
    expect(aroundFrom(page, 102, line(1), null)).toEqual({ before: line(1), after: line(3) })
    expect(aroundFrom(page, 104, null, line(5))).toEqual({ before: line(3), after: line(5) })
    expect(aroundFrom(page, 104, null, null)).toEqual({ before: line(3), after: null })
    expect(aroundFrom(page, 999, null, null)).toBeNull()
  })

  it('has a sound card only for an audio title, with Play and the times, and no picture until peaks arrive', () => {
    const audio = renderToStaticMarkup(<ThisLinePanel dramaId={3} line={GLOSSARY} sound player={player} reloads={0} sourceLanguage="zh" onShowLine={() => {}} />)
    expect(audio).toContain('Line 24 sound')
    expect(audio).toMatch(/title="Key: Space">Play</)
    expect(audio).toContain('<span class="sr-only">Start </span>0:48.0</span>')
    expect(audio).toContain('<span class="sr-only">End </span>0:49.5</span>')
    expect(audio).not.toContain('this-line-wave')
    const video = renderToStaticMarkup(<ThisLinePanel dramaId={3} line={GLOSSARY} sound={false} player={player} reloads={0} sourceLanguage="zh" onShowLine={() => {}} />)
    expect(video).not.toContain('sound')
    expect(video).toContain('Why it is listed')
    expect(video).toContain('Around it')
  })
})

describe('the Player toggle', () => {
  it('says whether the player is shown and what it brings back', () => {
    const closed = renderToStaticMarkup(<QueueMediaToggle open={false} controls="player-1" onToggle={() => {}} />)
    expect(closed).toContain('aria-expanded="false"')
    expect(closed).toContain('aria-controls="player-1"')
    expect(closed).toMatch(/>Player <span aria-hidden="true">▾<\/span><\/button>/)
    expect(renderToStaticMarkup(<QueueMediaToggle open controls="player-1" onToggle={() => {}} />)).toContain('aria-expanded="true"')
  })
})
