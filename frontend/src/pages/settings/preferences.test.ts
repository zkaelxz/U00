import { describe, expect, it } from 'vitest'

import type { SettingsPreferences } from '../../types/settings'
import {
  capSummary,
  changedPreferences,
  checkEndpointUrl,
  checkPath,
  cookiesSummary,
  parseCap,
  parseNumCtx,
  parseUploadMb,
} from './preferences'

const saved: SettingsPreferences = {
  default_engine: 'claude',
  default_locale: 'en-US',
  default_style_note: '',
  scene_aware_batches: true,
  episode_summary_engine: 'ollama',
  max_upload_mb: 20480,
  monthly_cap_usd: null,
  ollama_num_ctx_override: 0,
  whisper_model_path: '',
  ocr_backend: 'auto',
  ocr_prefer_paddle_vl_manga: false,
  tesseract_cmd: '',
  cookies_browser: null,
  cookies_file: '',
  lncrawl_cmd: '',
  whisper_model_path_configured: false,
  tesseract_cmd_configured: false,
  cookies_file_configured: false,
  lncrawl_cmd_configured: false,
}

describe('settings preferences', () => {
  it('builds a patch of only the changed fields', () => {
    expect(changedPreferences(saved, { default_engine: 'claude', default_locale: 'en-GB' })).toEqual({
      default_locale: 'en-GB',
    })
    expect(changedPreferences(saved, { monthly_cap_usd: null, cookies_browser: null })).toEqual({})
    expect(changedPreferences(saved, { monthly_cap_usd: 0 })).toEqual({ monthly_cap_usd: 0 })
  })

  it('parses the monthly cap: blank is .env, 0 is no cap', () => {
    expect(parseCap('')).toEqual({ ok: true, value: null })
    expect(parseCap(' 12.50 ')).toEqual({ ok: true, value: 12.5 })
    expect(parseCap('0')).toEqual({ ok: true, value: 0 })
    expect(parseCap('-1').ok).toBe(false)
    expect(parseCap('1e3').ok).toBe(false)
    expect(parseCap('2000000').ok).toBe(false)
  })

  it('parses num_ctx: blank is automatic, whole numbers only', () => {
    expect(parseNumCtx('')).toEqual({ ok: true, value: 0 })
    expect(parseNumCtx('16384')).toEqual({ ok: true, value: 16384 })
    expect(parseNumCtx('1.5').ok).toBe(false)
    expect(parseNumCtx('99999999').ok).toBe(false)
  })

  it('parses the upload limit: 100 to 1,048,576 MB, blank is the default', () => {
    expect(parseUploadMb('', 20480)).toEqual({ ok: true, value: 20480 })
    expect(parseUploadMb(' 4096 ', 20480)).toEqual({ ok: true, value: 4096 })
    expect(parseUploadMb('100', 20480).ok).toBe(true)
    expect(parseUploadMb('1048576', 20480).ok).toBe(true)
    for (const bad of ['99', '0', '-5', '1048577', '1.5', '2 GB', '1e3']) {
      expect(parseUploadMb(bad, 20480).ok).toBe(false)
    }
  })

  it('checks paths for control characters and length', () => {
    expect(checkPath('C:\\Program Files\\Tesseract-OCR\\tesseract.exe')).toBeNull()
    expect(checkPath('a\nb')).toMatch(/not allowed/)
    expect(checkPath('x'.repeat(1025))).toMatch(/too long/)
  })

  it('checks endpoint URLs like the server does', () => {
    expect(checkEndpointUrl('http://127.0.0.1:11434')).toBeNull()
    expect(checkEndpointUrl('https://lt.example.com/api')).toBeNull()
    expect(checkEndpointUrl('')).toMatch(/Enter a URL/)
    expect(checkEndpointUrl('ftp://h')).toMatch(/http/)
    expect(checkEndpointUrl('http://me:pw@h')).toMatch(/user name/)
    expect(checkEndpointUrl('http://h/?key=1')).toMatch(/query/)
    expect(checkEndpointUrl('http://h a')).toMatch(/not allowed/)
    expect(checkEndpointUrl('nonsense')).toMatch(/not valid|http/)
  })

  it('summarises the cap and cookies', () => {
    expect(capSummary(null, 0)).toBe('no cap')
    expect(capSummary(null, 20)).toBe('$20.00 (from .env)')
    expect(capSummary(5, 20)).toBe('$5.00 a month')
    expect(capSummary(0, 20)).toBe('no cap')
    expect(cookiesSummary(null, '')).toBe('none')
    expect(cookiesSummary('firefox', '')).toBe('from firefox')
    expect(cookiesSummary('firefox', '/c.txt')).toBe('cookies.txt file')
  })
})
