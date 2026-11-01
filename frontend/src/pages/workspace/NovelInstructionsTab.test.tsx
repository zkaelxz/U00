import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { InstructionsForm, joinConventions, splitConventions } from './NovelInstructionsTab'

describe('term conventions in the instructions text', () => {
  it('round-trips prose and conventions through one text', () => {
    const text = joinConventions('Keep the narrator distant.', '- sects: translate\n- names: pinyin')
    expect(text).toBe('Keep the narrator distant.\n\nTerm conventions:\n- sects: translate\n- names: pinyin')
    expect(splitConventions(text)).toEqual({
      prose: 'Keep the narrator distant.',
      conventions: '- sects: translate\n- names: pinyin',
    })
  })

  it('leaves plain instructions alone and drops an empty conventions section', () => {
    expect(splitConventions('Just prose.')).toEqual({ prose: 'Just prose.', conventions: '' })
    expect(joinConventions('Just prose.', '  ')).toBe('Just prose.')
    expect(splitConventions(joinConventions('', '- a'))).toEqual({ prose: '', conventions: '- a' })
  })
})

describe('InstructionsForm', () => {
  it('shows the saved prose and conventions in their own fields', () => {
    const html = renderToStaticMarkup(
      <InstructionsForm id={1} saved={'Be formal.\n\nTerm conventions:\n- names: pinyin'} onSaved={() => undefined} />,
    )
    expect(html).toContain('Instructions')
    expect(html).toContain('Term conventions')
    expect(html).toContain('Be formal.')
    expect(html).toContain('- names: pinyin')
    expect(html).not.toContain('Term conventions:')
  })
})
