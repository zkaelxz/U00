import { afterEach, describe, expect, it, vi } from 'vitest'

import { downloadBlob, downloadText } from './downloadBlob'

function stubDocument() {
  const events: string[] = []
  const anchor = {
    href: '',
    download: '',
    click: () => events.push(`click ${anchor.href}`),
    remove: () => events.push('remove'),
  }
  vi.stubGlobal('document', {
    createElement: () => anchor,
    body: { appendChild: () => events.push('append') },
  })
  return { anchor, events }
}

describe('downloadBlob', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('keeps the object URL valid until after the click task', () => {
    vi.useFakeTimers()
    const { anchor, events } = stubDocument()
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => events.push('revoke'))
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x')
    downloadBlob(new Blob(['a']), 'a.txt')
    expect(anchor.download).toBe('a.txt')
    expect(events).toEqual(['append', 'click blob:x', 'remove'])
    expect(revoke).not.toHaveBeenCalled()
    vi.runAllTimers()
    expect(revoke).toHaveBeenCalledWith('blob:x')
  })

  it('saves text as UTF-8 with the given type', () => {
    stubDocument()
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:y')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    downloadText('你好', 'a.srt', 'text/plain')
    expect((create.mock.calls[0][0] as Blob).type).toBe('text/plain;charset=utf-8')
  })
})
