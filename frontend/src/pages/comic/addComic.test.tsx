import { zipSync } from 'fflate'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { AddComicDialog } from './AddComicDialog'
import { addComic, canAdd, cbzPages, startPrefs, type AddComicDeps, type AddComicInput } from './addComicLogic'

const file = (name: string) => new File(['x'], name)
const input = (over: Partial<AddComicInput> = {}): AddComicInput => ({ title: 'Solo', way: 'link', link: '', files: [], ...over })

describe('canAdd', () => {
  it("needs a title and the chosen way's input", () => {
    expect(canAdd(input({ title: ' ', link: 'https://a.example/x' }))).toBe(false)
    expect(canAdd(input())).toBe(false)
    expect(canAdd(input({ link: 'not a link' }))).toBe(false)
    expect(canAdd(input({ link: ' https://a.example/x ' }))).toBe(true)
    expect(canAdd(input({ way: 'images' }))).toBe(false)
    expect(canAdd(input({ way: 'images', files: [file('1.png')] }))).toBe(true)
    expect(canAdd(input({ way: 'file', files: [file('a.cbz')] }))).toBe(true)
  })
})

describe('cbzPages', () => {
  it('keeps images in reading order and skips folders and metadata', () => {
    const bytes = zipSync({
      'ch/page10.jpg': new Uint8Array([1]),
      'ch/page9.png': new Uint8Array([2]),
      'ch/page2.webp': new Uint8Array([3]),
      'ch/ComicInfo.xml': new Uint8Array([4]),
      '__MACOSX/ch/page1.jpg': new Uint8Array([5]),
      'ch/.hidden.png': new Uint8Array([6]),
    })
    expect(cbzPages(bytes).map((f) => f.name)).toEqual(['page2.webp', 'page9.png', 'page10.jpg'])
  })
  it('is empty for an archive with no images', () => {
    expect(cbzPages(zipSync({ 'a.txt': new Uint8Array([1]) }))).toEqual([])
  })
})

describe('startPrefs', () => {
  it('reads in the chosen mode with the fit that goes with it', () => {
    expect(startPrefs('manga', 'vertical')).toMatchObject({ mode: 'vertical', fit: 'width', rtl: true })
    expect(startPrefs('manhwa', 'paged')).toMatchObject({ mode: 'paged', fit: 'height', rtl: false })
  })
})

describe('addComic', () => {
  const deps = () => ({
    create: vi.fn().mockResolvedValue({ id: 5 }),
    importLink: vi.fn().mockResolvedValue({ job_id: 'sourceimport_5' }),
    upload: vi.fn().mockResolvedValue({ added: 2 }),
  })
  const run = (i: AddComicInput, existing: number | null, d: ReturnType<typeof deps>) =>
    addComic(i, { source_language: 'ko', title_en: 'Solo', media_type: 'manhwa' }, existing, d as unknown as AddComicDeps)

  it('imports a link into the new title', async () => {
    const d = deps()
    expect(await run(input({ link: 'https://a.example/x' }), null, d)).toBe(5)
    expect(d.importLink).toHaveBeenCalledWith('https://a.example/x', 5)
    expect(d.upload).not.toHaveBeenCalled()
  })
  it('uploads images or CBZ pages into the new title', async () => {
    const d = deps()
    const files = [file('1.png')]
    await run(input({ way: 'images', files }), null, d)
    expect(d.upload).toHaveBeenCalledWith(5, files)
    expect(d.importLink).not.toHaveBeenCalled()
  })
  it('retries into the title an earlier try made', async () => {
    const d = deps()
    d.importLink.mockRejectedValueOnce(new Error('blocked'))
    await expect(run(input({ link: 'https://a.example/x' }), null, d)).rejects.toThrow('blocked')
    expect(await run(input({ link: 'https://a.example/x' }), 5, d)).toBe(5)
    expect(d.create).toHaveBeenCalledTimes(1)
  })
})

describe('AddComicDialog', () => {
  it('shows the three ways in, the reading mode and a disabled Add', () => {
    const html = renderToStaticMarkup(<AddComicDialog open onClose={() => {}} />)
    for (const t of ['Add a Comic', 'Link', 'Images', 'CBZ', 'Reading mode', 'Vertical scroll', 'One page at a time']) {
      expect(html).toContain(t)
    }
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Add<\/button>/)
  })
  it('draws nothing while closed', () => {
    expect(renderToStaticMarkup(<AddComicDialog open={false} onClose={() => {}} />)).not.toContain('Reading mode')
  })
})
