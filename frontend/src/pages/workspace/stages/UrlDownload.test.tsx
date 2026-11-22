import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { UrlDownload } from './UrlDownload'

const render = (url: string, hasAudio = false) =>
  renderToStaticMarkup(<UrlDownload dramaId={1} hasAudio={hasAudio} url={url} onStarted={() => {}} />)

describe('UrlDownload Audio only default', () => {
  it('starts off for a video page or streamer VOD link', () => {
    expect(render('https://www.youtube.com/watch?v=abc')).toContain('aria-checked="false"')
    expect(render('https://www.twitch.tv/videos/123')).toContain('aria-checked="false"')
  })
  it('starts on for a direct audio file link', () => {
    expect(render('https://a.example/x/ep.mp3')).toContain('aria-checked="true"')
  })
})

describe('UrlDownload Video quality', () => {
  it('offers the quality choices for a video page, best available first', () => {
    const html = render('https://www.youtube.com/watch?v=abc')
    expect(html).toContain('Video quality')
    expect(html).toContain('Best available')
    expect(html).toContain('720p')
  })
  it('starts at 1080p, with Best available and 4K still offered', () => {
    const html = render('https://www.youtube.com/watch?v=abc')
    expect(html).toMatch(/<option value="1080" selected="">1080p<\/option>/)
    expect(html).toContain('4K (2160p)')
  })
  it('is not shown for a direct file link, which has one quality', () => {
    expect(render('https://a.example/x/ep.mp3')).not.toContain('Video quality')
    expect(render('https://a.example/x/ep.mp4')).not.toContain('Video quality')
  })
})

describe('UrlDownload download again', () => {
  it('offers downloading the same video again only when the title has media', () => {
    expect(render('https://www.youtube.com/watch?v=abc', true)).toContain('Download the same video again (keeps the lines)')
    expect(render('https://www.youtube.com/watch?v=abc', false)).not.toContain('Download the same video again')
  })
})
