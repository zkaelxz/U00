import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'

import { TvBar } from './TvBar'

it('offers the text sizes with the current one checked', () => {
  const html = renderToStaticMarkup(<TvBar shown hasPicture video="side" onVideo={() => {}} showOriginal onShowOriginal={() => {}}
    split="wide" onSplit={() => {}} textSize="xl" onTextSize={() => {}} onExit={() => {}} />)
  for (const name of ['Small', 'Medium', 'Large', 'Extra large']) expect(html).toContain(`aria-label="${name}"`)
  expect(html).toMatch(/role="radio" aria-checked="true" aria-label="Extra large"/)
})

it('offers the picture width only while the picture sits beside the lines', () => {
  const html = (video: 'side' | 'above') => renderToStaticMarkup(<TvBar shown hasPicture video={video} onVideo={() => {}} showOriginal onShowOriginal={() => {}}
    split="wide" onSplit={() => {}} textSize="l" onTextSize={() => {}} onExit={() => {}} />)
  expect(html('side')).toMatch(/aria-checked="true"[^>]*>Big picture/)
  expect(html('above')).not.toContain('Picture width')
})
