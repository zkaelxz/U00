import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'

import { SameTitleList } from './SameTitleNote'

test('names each title that is already there, says why, and links to it', () => {
  const out = renderToStaticMarkup(
    <SameTitleList found={[{ id: 7, title: 'Night Rain', same: 'link' }, { id: 9, title: '', same: 'name' }]} />,
  )
  expect(out).toContain('Already in your Library')
  expect(out).toContain('<a href="#/drama/7">Night Rain</a> has the same link.')
  expect(out).toContain('<a href="#/drama/9">Untitled</a> has the same name.')
  expect(out).toContain('You can still add it')
})

test('renders nothing when no title matches', () => {
  expect(renderToStaticMarkup(<SameTitleList found={[]} />)).toBe('')
})
