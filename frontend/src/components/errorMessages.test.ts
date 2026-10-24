import { afterEach, describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { resetPcModeForTests } from '../api/pcOnly'
import { resetSessionForTests } from '../hooks/useSession'
import {
  ASK_PC_OWNER, couldNotReachBaihe, describeError, isForbidden, keyWritesRefused, summarizeEngineFailure,
} from './errorMessages'


describe('summarizeEngineFailure', () => {
  const ollamaRefused =
    "HTTPConnectionPool(host='localhost', port=11434): Max retries exceeded ... [WinError 10061] No connection could be made"
  it('says Ollama is not running and points to ollama.com', () => {
    const s = summarizeEngineFailure('ollama', ollamaRefused, 'Ollama')
    expect(s.kind).toBe('not_running')
    expect(s.summary).toMatch(/Ollama isn't running.*ollama\.com.*pull a model/)
  })
  it('detects a missing Ollama model', () => {
    expect(summarizeEngineFailure('ollama', "model 'x' not found, try pulling it first (404)").kind).toBe('no_model')
  })
  it('classifies keys, rate limits and timeouts', () => {
    expect(summarizeEngineFailure('claude', 'HTTPError: 401 Unauthorized', 'Claude').summary).toBe(
      'Claude rejected the key. Check its key under Engines and keys, then test again.',
    )
    expect(summarizeEngineFailure('gemini', '403 Forbidden').kind).toBe('key_rejected')
    expect(summarizeEngineFailure('gemini', '429 quota exceeded').kind).toBe('rate_limited')
    expect(summarizeEngineFailure('claude', 'No answer within 45 seconds.').kind).toBe('timed_out')
  })
  it('treats a refused cloud connection as unreachable and unknown text as generic', () => {
    expect(summarizeEngineFailure('claude', 'Max retries exceeded').kind).toBe('unreachable')
    expect(summarizeEngineFailure('claude', 'weird', 'Claude')).toEqual({ kind: 'other', summary: 'The Claude test failed.' })
    expect(summarizeEngineFailure('claude', null).kind).toBe('other')
  })
})

describe('describeError for Ollama failures', () => {
  it('shows the server sentence as the title instead of the generic text', () => {
    const msg = "Ollama isn't running. Start it, or pick another translator in Settings."
    const err = new ApiError(503, { code: 'dependency_unavailable', message: msg, details: { reason: 'ollama_unreachable' } })
    expect(describeError(err)).toEqual({ title: msg, detail: null })
  })
  it('keeps the generic text for other dependency errors', () => {
    const err = new ApiError(503, { code: 'dependency_unavailable', message: 'No claude key is configured.' })
    expect(describeError(err).title).toMatch(/not installed or not reachable/)
  })
})

describe('describeError for a missing key', () => {
  const noKey = (engine: unknown) =>
    new ApiError(503, {
      code: 'dependency_unavailable',
      message: 'No claude key is configured. Set one in Settings first.',
      details: { reason: 'no_key', engine },
    })
  it('has its own heading, not the missing-package one', () => {
    const { title, detail } = describeError(noKey('claude'))
    expect(title).toBe('No key is set for Claude. Add it in Settings.')
    expect(title).not.toMatch(/not installed/)
    expect(detail).toBeNull()
  })
  it('falls back to a neutral name for an odd engine value', () => {
    expect(describeError(noKey({ x: 1 })).title).toBe('No key is set for this engine. Add it in Settings.')
  })
  it('keeps the package heading for a real missing package', () => {
    const err = new ApiError(503, { code: 'dependency_unavailable', message: 'OmniVoice is not installed. Install it in Diagnostics.' })
    expect(describeError(err)).toEqual({
      title: 'A tool or package this needs is not installed or not reachable. See Diagnostics.',
      detail: 'OmniVoice is not installed. Install it in Diagnostics.',
    })
  })
})

describe('describeError for a refused export', () => {
  it('shows the server sentence when asked to', () => {
    const err = new ApiError(422, { code: 'validation_error', message: 'There is no narration yet. Create it in Dub first.' })
    // A 422 sentence is the heading by default; the options only matter for other statuses.
    expect(describeError(err)).toEqual({
      title: 'There is no narration yet. Create it in Dub first.',
      detail: null,
    })
    expect(describeError(err, { reasonAsTitle: true })).toEqual({
      title: 'There is no narration yet. Create it in Dub first.',
      detail: null,
    })
  })
  it('keeps the generic heading when the server text looks like it holds a path', () => {
    const err = new ApiError(422, { code: 'validation_error', message: 'Bad file /home/x/a.wav' })
    expect(describeError(err, { reasonAsTitle: true }).title).toMatch(/not valid/)
    expect(describeError(err).title).toMatch(/not valid/)
  })
})

describe('isForbidden', () => {
  it('is true only for a 403 refusal', () => {
    expect(isForbidden(new ApiError(403, { code: 'forbidden', message: 'Not allowed.' }))).toBe(true)
    expect(isForbidden(new ApiError(404, { code: 'not_found', message: '' }))).toBe(false)
    expect(isForbidden(new ApiError(500, { code: 'internal_error', message: '' }))).toBe(false)
    expect(isForbidden(null)).toBe(false)
  })

  it('is false for a model that is not downloaded, whose sentence says what to do', () => {
    expect(isForbidden(new ApiError(403, { code: 'model_not_downloaded', message: 'x' }))).toBe(false)
  })
})

describe('describeError for a model that is not downloaded', () => {
  const err = new ApiError(403, { code: 'model_not_downloaded', message: 'x' })

  it('shows the plain sentence, also where a 403 would read as PC-only', () => {
    const want = 'This model is not downloaded yet. Ask the owner of the main PC to download it first.'
    expect(describeError(err).title).toBe(want)
    expect(describeError(err, { pcOnly: true }).title).toBe(want)
  })
})

describe('describeError shows the real reason', () => {
  const api = (status: number, code: string, message: string, method?: string) =>
    new ApiError(status, { code, message }, method)

  it('422 shows the server sentence as the title', () => {
    for (const message of ['Invalid find pattern: unterminated group.', "Line 2 isn't valid JSON.", '2000 characters max']) {
      expect(describeError(api(422, 'validation_error', message, 'POST'))).toEqual({ title: message, detail: null })
    }
    expect(describeError(api(422, 'invalid_input', 'Pick a language.', 'PUT')).title).toBe('Pick a language.')
  })

  it('422 keeps the generic text when the sentence is FastAPI\'s own or is path- or key-like', () => {
    const generic = 'Some of the values entered are not valid. Check them and try again.'
    expect(describeError(api(422, 'validation_error', 'The request is invalid.', 'POST')).title).toBe(generic)
    expect(describeError(api(422, 'validation_error', 'Bad /home/kae/x.srt', 'POST')).title).toBe(generic)
    expect(describeError(api(422, 'validation_error', 'key sk-abcdef123456', 'POST')).title).toBe(generic)
  })

  it('429 uses the server wait text, with the generic text as the fallback', () => {
    expect(describeError(api(429, 'rate_limited', 'Wait a few seconds before testing again.', 'POST')).title).toBe(
      'Wait a few seconds before testing again.',
    )
    expect(describeError(api(429, 'rate_limited', 'at /var/log/x', 'POST')).title).toMatch(/^Too many requests/)
  })

  it('disk, file and database problems show the server sentence instead of the generic text', () => {
    const denied = 'Permission denied: Baihe is not allowed to open a file it needs. The app log names the file.'
    expect(describeError(api(503, 'permission_denied', denied, 'GET')).title).toBe(denied)
    expect(describeError(api(507, 'disk_full', 'The disk is full.', 'POST')).title).toBe('The disk is full.')
    expect(describeError(api(409, 'file_in_use', 'A file is open in another program.', 'POST')).detail).toBeNull()
    expect(describeError(api(503, 'permission_denied', 'at C:\\Users\\kae\\x', 'POST')).title).toMatch(/^The action failed/)
  })

  it('a failed read says it could not load; a failed write keeps the nothing-changed text', () => {
    expect(describeError(api(400, 'application_error', 'x', 'GET')).title).toMatch(/^Could not load/)
    expect(describeError(api(500, 'internal_error', 'Traceback /home/kae', 'GET')).title).toMatch(/^Could not load/)
    expect(describeError(api(500, 'internal_error', 'x', 'GET')).detail).toBeNull()
    expect(describeError(api(400, 'application_error', 'x', 'POST')).title).toMatch(/^The action failed\. Nothing was changed/)
    expect(describeError(api(500, 'internal_error', 'x', 'POST')).title).toMatch(/^Something went wrong inside Baihe/)
    // A caller that did not record the method is treated as a write, as before.
    expect(describeError(api(400, 'application_error', 'x')).title).toMatch(/^The action failed/)
  })

  it('a network failure and a timeout read differently for reads and writes', () => {
    expect(describeError(api(0, 'network_error', 'x', 'GET')).title).toMatch(/Could not reach Baihe\. Make sure it is still running/)
    expect(describeError(api(0, 'timeout', 'x', 'POST')).title).toMatch(/may not have been saved/)
    expect(describeError(api(0, 'timeout', 'x', 'GET')).title).toMatch(/^Could not load.*too long/)
  })

  it('409 shows the server text as detail', () => {
    expect(describeError(api(409, 'conflict', 'A translate job is already running.', 'POST'))).toMatchObject({
      detail: 'A translate job is already running.',
    })
  })
})

describe('error text by who is reading', () => {
  const me = (permissions: string[]) => ({
    status: 'ready' as const,
    me: { auth_enabled: true, signed_in: true, sign_in_configured: true, zone: null, user: null, permissions },
  })
  const missing = new ApiError(503, { code: 'dependency_unavailable', message: 'OmniVoice is not installed. Install it in Diagnostics.' }, 'POST')
  afterEach(() => {
    resetSessionForTests()
    resetPcModeForTests()
  })

  it('an admin is sent to Diagnostics and sees the server detail', () => {
    resetSessionForTests(me(['admin.diagnostics', 'admin.settings']))
    expect(describeError(missing)).toEqual({
      title: 'A tool or package this needs is not installed or not reachable. See Diagnostics.',
      detail: 'OmniVoice is not installed. Install it in Diagnostics.',
    })
    expect(keyWritesRefused()).toContain('BAIHE_API_ALLOW_KEY_WRITES=1')
  })

  it('a member is told to ask the person who runs Baihe, with no Diagnostics pointer or env var', () => {
    resetSessionForTests(me([]))
    const { title, detail } = describeError(missing)
    expect(title).toContain(ASK_PC_OWNER)
    expect(title).not.toMatch(/Diagnostics/)
    expect(detail).toBeNull()
    expect(describeError(new ApiError(503, { code: 'dependency_unavailable', message: 'x', details: { reason: 'missing_package' } }, 'POST')).title).toContain(ASK_PC_OWNER)
    expect(keyWritesRefused()).toBe(ASK_PC_OWNER)
  })

  it('away from the PC the unreachable text does not ask whether Baihe is running', () => {
    resetPcModeForTests('remote')
    expect(couldNotReachBaihe()).toMatch(/Check your connection/)
    expect(couldNotReachBaihe()).not.toMatch(/API/)
  })

  it('a refusal by permission keeps its generic text for everyone', () => {
    resetSessionForTests(me([]))
    expect(describeError(new ApiError(403, { code: 'forbidden', message: 'admin.diagnostics' }, 'GET')).title).toBe(
      'Not allowed from this device or account.',
    )
  })
})
