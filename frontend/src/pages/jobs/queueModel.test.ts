import { describe, expect, it } from 'vitest'

import type { QueueItem, QueueSettingField, QueueState } from '../../types/queue'
import {
  canRemove, canRetry, changedEdits, finishedCount, itemName, runStateText, savedSettingValue, scheduleText, settingsStage, statusLabel, statusTone,
  translateCostNote,
} from './queueModel'

const item = (p: Partial<QueueItem> = {}): QueueItem => ({
  id: 1, drama_id: 3, title: 'Signal', step: 'translate', status: 'waiting', reason: null, job_id: null,
  engine: 'claude', paid: true, changed_settings: [], mine: true, added_at: 1, started_at: null, finished_at: null, ...p,
})
const queue = (p: Partial<QueueState> = {}): QueueState => ({
  version: 1, state: 'idle', stop_on_failure: false, schedule: null, notice: null, max_items: 200,
  can_control: true, monthly_cap_usd: 0, items: [], ...p,
})

describe('queueModel', () => {
  it('labels statuses in words with a tone', () => {
    expect(statusLabel('skipped')).toBe('Skipped')
    expect(statusTone('failed')).toBe('bad')
    expect(statusTone('interrupted')).toBe('warn')
    expect(itemName(item({ step: 'export' }))).toBe('Export subtitles Signal')
  })

  it('offers Retry only on a finished-but-not-done item of your own, and never removes a running one', () => {
    expect(canRetry(item({ status: 'failed' }))).toBe(true)
    expect(canRetry(item({ status: 'interrupted' }))).toBe(true)
    expect(canRetry(item({ status: 'done' }))).toBe(false)
    expect(canRetry(item({ status: 'failed', mine: false }))).toBe(false)
    expect(canRemove(item({ status: 'running' }))).toBe(false)
    expect(canRemove(item({ status: 'waiting' }))).toBe(true)
    expect(finishedCount([item({ status: 'done' }), item({ status: 'running' }), item({ status: 'skipped', mine: false })])).toBe(1)
  })

  it('says what the run state means', () => {
    expect(runStateText(queue({ state: 'paused' }))).toContain('A running one finishes')
    expect(runStateText(queue())).toBe('Nothing waiting.')
    expect(runStateText(queue({ items: [item()] }))).toBe('Not running.')
  })

  it('shows a missed schedule as a question, never as a run', () => {
    const now = new Date(2026, 9, 10, 20, 0)
    const later = new Date(2026, 9, 10, 23, 30).getTime() / 1000
    const tomorrow = new Date(2026, 9, 11, 2, 0).getTime() / 1000
    expect(scheduleText(null)).toBeNull()
    expect(scheduleText({ time: '23:30', start_at: later, missed: false }, now)).toBe('Starts once at 23:30 today, on this PC\'s clock.')
    expect(scheduleText({ time: '02:00', start_at: tomorrow, missed: false }, now)).toContain('tomorrow')
    expect(scheduleText({ time: '02:00', start_at: tomorrow, missed: true }, now)).toBe('Missed: the start at 02:00 passed while Baihe was off. Start now?')
  })

  it('says a paid Translate uses your key and costs money, and names the cap', () => {
    expect(translateCostNote('Claude', true, 5)).toBe('Uses Claude. Uses your API key and costs money. It stops at your $5.00 monthly spending cap.')
    expect(translateCostNote('Claude', true, 0)).toContain('No monthly spending cap is set, so the queue will skip it')
    expect(translateCostNote('Ollama', false, 5)).toBe('Uses Ollama, which is free.')
  })
})

describe('saved settings panel', () => {
  it('words the engine row and leaves other rows as the server sent them', () => {
    expect(savedSettingValue({ label: 'Engine and model', value: 'claude-sonnet-5-5', engine: 'claude' })).toBe('Claude, claude-sonnet-5-5')
    expect(savedSettingValue({ label: 'Engine and model', value: '', engine: 'deepseek' })).toBe('DeepSeek')
    expect(savedSettingValue({ label: 'Batch size', value: '20', engine: null })).toBe('20')
  })

  it('links each step to the stage that holds its options', () => {
    expect(settingsStage('translate')).toEqual({ stage: 'translate', label: 'Translate' })
    expect(settingsStage('transcribe')).toEqual({ stage: 'source', label: 'Media' })
    expect(settingsStage('export')).toEqual({ stage: 'export', label: 'Export' })
  })

  it('does not double the full stop when an engine description ends with one', () => {
    expect(translateCostNote('Paid, cloud; best tone and character voice.', true, 5)).toBe(
      'Uses Paid, cloud; best tone and character voice. Uses your API key and costs money. It stops at your $5.00 monthly spending cap.')
    expect(translateCostNote('Claude', false, 0)).toBe('Uses Claude, which is free.')
    expect(translateCostNote('Claude.', false, 0)).toBe('Uses Claude, which is free.')
  })
})

describe('changedEdits', () => {
  const field = (key: string, value: QueueSettingField['value'], locked: string | null = null): QueueSettingField => ({
    key, label: key, kind: 'number', value, options: [], min: null, max: null, locked,
  })
  const fields = [field('beam_size', 5), field('separate_vocals_first', false), field('use_groq', false, 'Needs paid-engine permission.')]

  it('sends only what differs from the title, so the rest keep following it', () => {
    expect(changedEdits(fields, { beam_size: 8, separate_vocals_first: false })).toEqual({ beam_size: 8 })
  })

  it('sends nothing when every edit matches the title', () => {
    expect(changedEdits(fields, { beam_size: 5 })).toBeUndefined()
    expect(changedEdits(fields, {})).toBeUndefined()
  })

  it('never sends a locked setting', () => {
    expect(changedEdits(fields, { use_groq: true })).toBeUndefined()
  })
})
