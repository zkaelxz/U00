import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { clearEngineKey, setEngineKey } from '../api/settings'
import {
  initialKeyForm,
  keyErrorMessage,
  keyFormReducer,
  keyRows,
  type KeyFormAction,
  type KeyFormState,
} from './settingsKeys'
import { KEY_WRITES_REFUSED } from '../components/errorMessages'

const run = (actions: KeyFormAction[], s: KeyFormState = initialKeyForm) =>
  actions.reduce(keyFormReducer, s)
const SECRET = 'sk-test-SECRET123'

describe('key form reducer', () => {
  it('never keeps the key once a save is sent, succeeds or fails', () => {
    const sent = run([{ type: 'edit', value: SECRET }, { type: 'askSave' }, { type: 'send' }])
    expect(sent.draft).toBe('')
    const ok = keyFormReducer(sent, { type: 'done', notice: 'Saved.' })
    const bad = keyFormReducer(sent, {
      type: 'failed',
      error: new ApiError(403, { code: 'forbidden', message: SECRET }),
    })
    for (const st of [sent, ok, bad]) expect(JSON.stringify(st)).not.toContain(SECRET)
    expect(bad.error).toBe(KEY_WRITES_REFUSED)
  })

  it('needs an explicit confirm before save, never for an empty draft', () => {
    expect(run([{ type: 'askSave' }]).confirm).toBe('none')
    expect(run([{ type: 'edit', value: '   ' }, { type: 'askSave' }]).confirm).toBe('none')
    const asked = run([{ type: 'edit', value: SECRET }, { type: 'askSave' }])
    expect(asked.confirm).toBe('save')
    expect(keyFormReducer(asked, { type: 'edit', value: 'x' }).confirm).toBe('none')
  })

  it('clear is two-step and can be cancelled', () => {
    const asked = run([{ type: 'askClear' }])
    expect(asked.confirm).toBe('clear')
    expect(keyFormReducer(asked, { type: 'cancel' }).confirm).toBe('none')
    expect(keyFormReducer(asked, { type: 'send' }).busy).toBe(true)
  })
})

describe('refusal message mapping', () => {
  it('403 gives the one plain line naming the env var', () => {
    const m = keyErrorMessage(
      new ApiError(403, { code: 'forbidden', message: 'Not allowed from this connection.' }),
    )
    expect(m).toBe(KEY_WRITES_REFUSED)
    expect(m).toContain('BAIHE_API_ALLOW_KEY_WRITES=1')
  })

  it('other errors are plain and never echo the server message', () => {
    const raw = 'raw server detail'
    for (const st of [0, 400, 422, 500]) {
      expect(keyErrorMessage(new ApiError(st, { code: 'x', message: raw }))).not.toContain(raw)
    }
    expect(keyErrorMessage(new Error(raw))).not.toContain(raw)
  })
})

describe('key api calls', () => {
  const f = () =>
    vi.fn(
      async () => new Response(JSON.stringify({ engine: 'gemini', configured: true })),
    ) as unknown as typeof fetch

  it('sends the key in the body only, with confirm', async () => {
    const fx = f()
    expect(await setEngineKey('gemini', SECRET, fx)).toEqual({ engine: 'gemini', configured: true })
    const [url, init] = (fx as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/settings/keys/gemini')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ value: SECRET, confirm: true })
  })

  it('clear posts confirm only', async () => {
    const fx = f()
    await clearEngineKey('gemini', fx)
    const [url, init] = (fx as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/settings/keys/gemini/clear')
    expect(JSON.parse(init.body)).toEqual({ confirm: true })
  })
})

describe('keyRows', () => {
  it('lists write-only secrets first, then other keys, never the server addresses', () => {
    const rows = keyRows({ ollama_url: true, groq: false, claude: true, openai: false, mistral: false, gpt_sovits_url: false })
    expect(rows).toEqual([
      { engine: 'claude', label: 'Claude', writable: true },
      { engine: 'openai', label: 'OpenAI', writable: true },
      { engine: 'groq', label: 'Groq', writable: true },
      { engine: 'mistral', label: 'Mistral', writable: false },
    ])
  })
})
