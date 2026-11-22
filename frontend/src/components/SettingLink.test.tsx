import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { SettingsMentions } from './SettingLink'
import { splitSettingMentions } from './settingMentions'

describe('setting mentions', () => {
  it('links a card named after "Settings >"', () => {
    const parts = splitSettingMentions('set one in Settings > Spending.')
    expect(parts.find((p) => p.link)).toEqual({ text: 'Settings > Spending', link: true, card: 'spending' })
  })

  it('links a bare pointer to the Settings page and leaves other uses of the word', () => {
    expect(renderToStaticMarkup(<SettingsMentions text="Add it in Settings." />)).toBe('Add it in <a href="#/settings">Settings</a>.')
    expect(splitSettingMentions("Settings' default engine is now X. Settings sweep").some((p) => p.link)).toBe(false)
  })

  it('returns plain text unchanged', () => {
    expect(renderToStaticMarkup(<SettingsMentions text="Nothing to see." />)).toBe('Nothing to see.')
  })
})
