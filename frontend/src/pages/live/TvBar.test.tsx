import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'

import { TvBar } from './TvBar'

it('offers the text sizes with the current one checked', () => {
  const html = renderToStaticMarkup(<TvBar shown hasPicture={false} video="side" onVideo={() => {}} showOriginal onShowOriginal={() => {}}
    textSize="xl" onTextSize={() => {}} onExit={() => {}} />)
  for (const name of ['Small', 'Medium', 'Large', 'Extra large']) expect(html).toContain(`aria-label="${name}"`)
  expect(html).toMatch(/role="radio" aria-checked="true" aria-label="Extra large"/)
})
