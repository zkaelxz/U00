import { describe, expect, it } from 'vitest'

import {
  MAX_EDGE,
  appendRead,
  cropPlan,
  firstImage,
  pickVisionEngine,
  rectBetween,
  visionEngines,
} from './readImage'
import type { TranslateEngine } from '../types/translate'

const engine = (name: string, key_configured = true): TranslateEngine => ({
  name,
  label: name,
  free: name === 'ollama',
  models: null,
  key_configured,
})
const ALL = ['deepseek', 'claude', 'ollama'].map((n) => engine(n))

describe('engines that read pictures', () => {
  it('leaves DeepSeek out', () => {
    expect(visionEngines(ALL).map((e) => e.name)).toEqual(['claude', 'ollama'])
  })

  it('keeps a remembered pick, then the page engine, then the first usable one', () => {
    expect(pickVisionEngine(ALL, 'ollama', 'claude')).toBe('ollama')
    expect(pickVisionEngine(ALL, 'deepseek', 'claude')).toBe('claude')
    expect(pickVisionEngine(ALL, '', 'deepseek')).toBe('claude')
    expect(pickVisionEngine([engine('claude', false), engine('ollama')], '', 'deepseek')).toBe('ollama')
    expect(pickVisionEngine([engine('deepseek')], '', 'deepseek')).toBe('')
  })
})

describe('crop and zoom', () => {
  it('sends the whole picture when nothing is selected', () => {
    expect(cropPlan(1280, 720, null, 1)).toEqual({ sx: 0, sy: 0, sw: 1280, sh: 720, outW: 1280, outH: 720 })
  })

  it('cuts the selection and enlarges it', () => {
    const plan = cropPlan(1000, 500, { x: 0.1, y: 0.8, w: 0.5, h: 0.1 }, 3)
    expect(plan).toEqual({ sx: 100, sy: 400, sw: 500, sh: 50, outW: 1500, outH: 150 })
  })

  it('never goes past the longest edge the server keeps', () => {
    const plan = cropPlan(1280, 720, { x: 0, y: 0, w: 1, h: 0.2 }, 4)
    expect(Math.max(plan.outW, plan.outH)).toBe(MAX_EDGE)
    expect(plan.outW / plan.outH).toBeCloseTo(1280 / 144, 1)
  })

  it('treats a stray click as no selection', () => {
    expect(cropPlan(1000, 500, { x: 0.5, y: 0.5, w: 0.001, h: 0.001 }, 1).sw).toBe(1000)
  })

  it('keeps a selection that touches the edge inside the picture', () => {
    const plan = cropPlan(1000, 500, { x: 0.9999, y: 0.5, w: 0.5, h: 0.5 }, 1)
    expect(plan.sx + plan.sw).toBeLessThanOrEqual(1000)
  })

  it('builds a box from any two corners, clamped to the picture', () => {
    const box = rectBetween({ x: 0.6, y: 0.9 }, { x: 0.2, y: 1.4 })
    expect([box.x, box.y, box.w, box.h]).toEqual([0.2, 0.9, expect.closeTo(0.4), expect.closeTo(0.1)])
    expect(rectBetween({ x: -1, y: 0 }, { x: 0.5, y: 0.5 }).x).toBe(0)
  })
})

describe('pasted and dropped items', () => {
  const file = new File(['x'], 'a.png', { type: 'image/png' })
  it('takes the first accepted picture', () => {
    const items = [
      { kind: 'string', type: 'text/plain' },
      { kind: 'file', type: 'image/gif', getAsFile: () => file },
      { kind: 'file', type: 'image/png', getAsFile: () => file },
    ]
    expect(firstImage(items)).toBe(file)
  })

  it('returns null when no item is a picture', () => {
    expect(firstImage([{ kind: 'string', type: 'text/plain' }])).toBeNull()
  })
})

describe('adding the read text', () => {
  it('starts the box with it, or adds it on a new line', () => {
    expect(appendRead('', ' 你好 ')).toBe('你好')
    expect(appendRead('第一行\n', '第二行')).toBe('第一行\n第二行')
  })

  it('leaves the box alone when nothing was read', () => {
    expect(appendRead('keep', '  ')).toBe('keep')
  })
})
