import { createElement, type ComponentProps } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { Badge } from './Badge'
import { ButtonLink } from './Button'
import { Card } from './Card'
import { Field } from './Field'
import { humanize, statusTone, tidy } from './labels'
import { Toggle } from './Toggle'
import { badgeClass, buttonClass } from './uiClasses'

describe('Toggle', () => {
  it('renders a switch button with aria-checked', () => {
    const on = renderToStaticMarkup(createElement(Toggle, { checked: true, onChange: () => {} }))
    expect(on).toContain('role="switch"')
    expect(on).toContain('aria-checked="true"')
    expect(on).toContain('type="button"')
    const off = renderToStaticMarkup(createElement(Toggle, { checked: false, onChange: () => {} }))
    expect(off).toContain('aria-checked="false"')
  })

  it('click reports the flipped value', () => {
    const onChange = vi.fn()
    const el = Toggle({ checked: false, onChange }) as { props: { onClick: () => void } }
    el.props.onClick()
    expect(onChange).toHaveBeenCalledWith(true)
    const el2 = Toggle({ checked: true, onChange }) as { props: { onClick: () => void } }
    el2.props.onClick()
    expect(onChange).toHaveBeenLastCalledWith(false)
  })

  it('passes disabled and extra props through', () => {
    const html = renderToStaticMarkup(
      createElement(Toggle, { checked: false, onChange: () => {}, disabled: true, 'aria-label': 'Dark mode' }),
    )
    expect(html).toContain('disabled=""')
    expect(html).toContain('aria-label="Dark mode"')
  })

  it('takes its name from a Field label (id wired to htmlFor)', () => {
    const html = renderToStaticMarkup(
      createElement(
        Field,
        { label: 'Use the GPU', help: 'Faster.' } as ComponentProps<typeof Field>,
        createElement(Toggle, { checked: true, onChange: () => {} }),
      ),
    )
    const id = /<button[^>]*id="([^"]+)"[^>]*role="switch"|<button[^>]*role="switch"[^>]*id="([^"]+)"/.exec(html)
    const controlId = id?.[1] ?? id?.[2]
    expect(controlId).toBeTruthy()
    expect(html).toContain(`for="${controlId}"`)
    expect(html).toMatch(/role="switch"[^>]*aria-describedby=|aria-describedby=[^>]*role="switch"/)
  })
})

describe('buttonClass / ButtonLink', () => {
  it('builds variant and size classes', () => {
    expect(buttonClass()).toBe('btn btn-secondary')
    expect(buttonClass('primary')).toBe('btn btn-primary')
    expect(buttonClass('ghost', 'sm')).toBe('btn btn-ghost btn-sm')
    expect(buttonClass('danger', 'md', 'wide')).toBe('btn btn-danger wide')
  })

  it('ButtonLink is an anchor styled as a button', () => {
    const html = renderToStaticMarkup(
      createElement(ButtonLink, { href: '#/drama/1', variant: 'primary' }, 'Open workspace'),
    )
    expect(html).toBe('<a href="#/drama/1" class="btn btn-primary">Open workspace</a>')
  })
})

describe('labels', () => {
  it('humanizes raw values', () => {
    expect(humanize('mediaType', 'streamer_vod')).toBe('Streamer VOD')
    expect(humanize('mediaType', 'audio_drama')).toBe('Audio drama')
    expect(humanize('language', 'zh')).toBe('Chinese')
    expect(humanize('language', 'ZH')).toBe('Chinese')
    expect(humanize('engine', 'claude')).toBe('Claude')
    expect(humanize('engine', 'hf_token')).toBe('Hugging Face token')
    expect(humanize('status', 'not started')).toBe('Not started')
  })

  it('falls back to a tidy label and handles empty values', () => {
    expect(humanize('mediaType', 'brand_new_type')).toBe('Brand new type')
    expect(humanize('language', 'th')).toBe('Th')
    expect(humanize('status', null)).toBe('')
    expect(humanize('status', undefined)).toBe('')
    expect(tidy('  NOT--started ')).toBe('Not started')
    expect(tidy('')).toBe('')
  })

  it('maps statuses to tones', () => {
    expect(statusTone('exported')).toBe('ok')
    expect(statusTone('translated')).toBe('accent')
    expect(statusTone('failed')).toBe('bad')
    expect(statusTone('not_started')).toBe('neutral')
    expect(statusTone('something else')).toBe('neutral')
    expect(statusTone(null)).toBe('neutral')
  })
})

describe('Badge', () => {
  it('humanizes a kind + value and picks a status tone', () => {
    expect(renderToStaticMarkup(createElement(Badge, { kind: 'status', value: 'exported' }))).toBe(
      '<span class="pill pill-ok">Exported</span>',
    )
    expect(renderToStaticMarkup(createElement(Badge, { kind: 'language', value: 'ko' }))).toBe(
      '<span class="pill pill-neutral">Korean</span>',
    )
  })

  it('an explicit tone and children win', () => {
    expect(renderToStaticMarkup(createElement(Badge, { tone: 'warn' }, 'Beta'))).toBe(
      '<span class="pill pill-warn">Beta</span>',
    )
    expect(badgeClass()).toBe('pill pill-neutral')
  })
})

describe('Card', () => {
  it('renders header parts only when given', () => {
    const bare = renderToStaticMarkup(createElement(Card, null, 'Body'))
    expect(bare).toBe('<section class="card"><div class="card-body">Body</div></section>')
    const full = renderToStaticMarkup(
      createElement(Card, { title: 'Keys', meta: '2 of 5', actions: 'X', as: 'div', 'aria-label': 'Keys' }, 'B'),
    )
    expect(full).toContain('<div class="card" aria-label="Keys">')
    expect(full).toContain('<h3 class="card-title">Keys</h3>')
    expect(full).toContain('<p class="card-meta">2 of 5</p>')
    expect(full).toContain('<div class="card-actions">X</div>')
  })
})
