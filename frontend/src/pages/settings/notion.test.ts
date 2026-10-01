import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { NotionConfig } from '../../types/notion'
import {
  configChanges, notionErrorMessage, notionSummary, partlySetUp, readyToExport, safeNotionUrl, testResultText,
} from './notion'
import { KEY_WRITES_REFUSED } from '../../components/errorMessages'

const ID = '0123456789abcdef0123456789abcdef'
const cfg: NotionConfig = { target_type: 'database', target_id: ID, token_configured: true }
const none: NotionConfig = { target_type: null, target_id: null, token_configured: false }

describe('notion helpers', () => {
  it('summarizes the setup and says when it is ready', () => {
    expect(notionSummary(none)).toBe('Not set up')
    expect(notionSummary({ ...cfg, token_configured: false })).toBe('No token')
    expect(notionSummary({ ...cfg, target_id: null })).toBe('No page or database')
    expect(notionSummary(cfg)).toBe('Ready, into a database')
    expect(notionSummary({ ...cfg, target_type: 'page' })).toBe('Ready, into a page')
    expect(readyToExport(cfg)).toBe(true)
    expect(readyToExport({ ...cfg, target_id: null })).toBe(false)
    expect(readyToExport({ ...cfg, token_configured: false })).toBe(false)
    expect(readyToExport(null)).toBe(false)
    expect(partlySetUp(none)).toBe(false)
    expect(partlySetUp(cfg)).toBe(false)
    expect(partlySetUp({ ...cfg, target_id: null })).toBe(true)
    expect(partlySetUp({ ...none, target_id: ID })).toBe(true)
    expect(partlySetUp(null)).toBe(false)
  })

  it('links only to notion.so pages', () => {
    expect(safeNotionUrl(`https://www.notion.so/${ID}`)).toBe(`https://www.notion.so/${ID}`)
    expect(safeNotionUrl('http://www.notion.so/x')).toBeNull()
    expect(safeNotionUrl('https://www.notion.so.evil.com/x')).toBeNull()
    expect(safeNotionUrl('https://evil.com/https://www.notion.so/')).toBeNull()
    expect(safeNotionUrl('javascript:alert(1)')).toBeNull()
    expect(safeNotionUrl(null)).toBeNull()
    expect(safeNotionUrl(undefined)).toBeNull()
  })

  it('sends only the changed config fields', () => {
    expect(configChanges(cfg, 'database', ID)).toEqual({})
    expect(configChanges(cfg, 'page', ID)).toEqual({ target_type: 'page' })
    expect(configChanges(cfg, 'database', '  https://www.notion.so/x  ')).toEqual({ target_id: 'https://www.notion.so/x' })
    expect(configChanges(cfg, 'database', '')).toEqual({ target_id: '' })
    // Nothing saved yet: the type is sent with the first target.
    expect(configChanges(none, 'database', ID)).toEqual({ target_type: 'database', target_id: ID })
    expect(configChanges(none, 'database', '')).toEqual({})
    expect(configChanges(none, 'page', '')).toEqual({})
  })

  it('reports a test and errors', () => {
    expect(testResultText({ ok: true, bot_name: 'Baihe', target_title: 'Dramas', target_type: 'database' }))
      .toBe('Connected as Baihe. Exports go into the database "Dramas".')
    expect(testResultText({ ok: true, bot_name: '', target_title: '', target_type: 'page' }))
      .toBe('Connected as your integration. Exports go into the page "Untitled".')
    expect(notionErrorMessage(new ApiError(403, { code: 'forbidden', message: 'x' }), true)).toBe(KEY_WRITES_REFUSED)
    expect(notionErrorMessage(new ApiError(403, { code: 'forbidden', message: 'x' }))).toMatch(/PC only/)
    expect(notionErrorMessage(new ApiError(409, { code: 'conflict', message: 'An export is already running.' })))
      .toBe('An export is already running.')
    expect(notionErrorMessage(new ApiError(503, { code: 'unavailable', message: 'Notion is unreachable.' })))
      .toBe('Notion is unreachable.')
    expect(notionErrorMessage(new ApiError(0, { code: 'network_error', message: 'x' }))).toMatch(/reach the Baihe API/)
    expect(notionErrorMessage(new ApiError(500, { code: 'internal', message: 'secret detail' })))
      .toBe('That did not work. Try again.')
    expect(notionErrorMessage(new Error('boom'))).toBe('That did not work. Try again.')
  })
})
