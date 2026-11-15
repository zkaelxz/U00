import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page } from '@playwright/test'

// Shared by the compare-selected specs: seed N lines on drama 3 and return
// their ids in order, plus the tick box and the selection-bar action. The
// Simple title specs seed the check queue's lines with seedCheckLines.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

export function seedLines(count: number): number[] {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i}句', en=f'Line {i}') for i in range(${count})])
print(','.join(str(l.id) for l in db.load_line_objects(3)))
`
  return execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot }).toString().trim().split(',').map(Number)
}

// 24 translated lines (or `count`) on drama 3 (or `drama`), none checked; returns their ids in order. With `warnings`, three of them need a look: #24 misses a glossary
// name, #12 is too fast to read (both flagged) and Whisper was unsure of #7 (unflagged); with `untranslated`, #3 has no English;
// with `audio`, drama 3 gets a 52-second WAV (the lines run to 49.5 s) so it plays like an audio drama. With `real`, every
// line has a speaker, #12 and #24 have a real line's length, and Whisper was also unsure of #12 (its Shaky badge).
export const GLOSSARY_NOTE = 'Glossary: 夷陵老祖 -> Yiling Patriarch missing'
// What the check queue's open card says for that note.
export const GLOSSARY_SENTENCE = 'The glossary writes 夷陵老祖 as “Yiling Patriarch”, but the English doesn’t use that name. ' +
  'Put it in the English, or change the glossary if the name is wrong.'
// A quiet tone on drama 3, long enough for every seeded line; any spec can paste it into its seed step.
export const WITH_AUDIO = `import os, wave, struct, math
p = os.path.join(db.drama_dir(3), 'e2e.wav')
with wave.open(p, 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(i / 6))) for i in range(8000 * 52)))
db.update_drama(3, audio_filename='e2e.wav')`
export function seedCheckLines({ warnings = true, untranslated = false, audio = false, real = false, drama = 3, count = 24 } = {}): number[] {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(${drama}, audio_filename=None, source_video_filename=None)
${audio && drama === 3 ? WITH_AUDIO : ''}
ls = [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i + 1}句', en=f'Line {i + 1}') for i in range(${count})]
if ${warnings ? 'True' : 'False'}:
    ls[23].flag, ls[23].flag_note = 'glossary_missing', ${JSON.stringify(GLOSSARY_NOTE)}
    ls[11].flag, ls[11].flag_note = 'reading_speed', '25.2 characters/second -- more than ~17/s is hard to read in time. The speech is this fast: shorten the text.'
    ls[6].confidence = 0.5
if ${untranslated ? 'True' : 'False'}:
    ls[2].en = ''
if ${real ? 'True' : 'False'}:
    for l in ls:
        l.speaker = 'Wei Wuxian'
    ls[11].zh, ls[11].en = '哈哈哈哈，蓝二公子，你可真会起名字！', 'Hahahaha, Second Young Master Lan, you really do have a way with names!'
    ls[23].zh, ls[23].en = '三百人？正好，让他们见识见识什么叫夷陵老祖。', 'Three hundred? Perfect. Let them see what the Old Ancestor of Yiling really is.'
    ls[11].confidence = 0.58
db.save_lines(${drama}, ls)
print(','.join(str(l.id) for l in db.load_line_objects(${drama})))
`
  return execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot }).toString().trim().split(',').map(Number)
}

// Takes another title's seeded lines away again, so later specs see it as seeded.
export function clearCheckLines(drama: number): void {
  inLibrary(`db.save_lines(${drama}, [])`)
}

// Titles 3 and 1 in one series, for the Simple title page's Episodes tab. leaveSeries deletes the series, which takes both
// titles out of it, so later specs see the library as it was seeded.
const SERIES = 'E2E episodes'
const inLibrary = (code: string) =>
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
export function joinSeries(): void {
  inLibrary(`sid = db.get_or_create_series(${JSON.stringify(SERIES)})\nfor d in (3, 1):\n    db.update_drama(d, series_id=sid)`)
}
export function leaveSeries(): void {
  inLibrary(`sid = db.get_series_id_by_name(${JSON.stringify(SERIES)})\nif sid:\n    db.delete_series(sid)`)
}

export const tickBox = (page: Page, n: number) => page.getByRole('checkbox', { name: `Select line #${n}`, exact: true })
export const compareAction = (page: Page) =>
  page.getByTestId('selection-bar').getByRole('button', { name: 'Compare transcription…' })
export const retimeAction = (page: Page) =>
  page.getByTestId('selection-bar').getByRole('button', { name: 'Re-time with Qwen3 aligner…' })

// Drama 3's loudness peaks without ffmpeg: a rising and falling shape over whatever span is asked for.
export async function mockPeaks(page: Page, fail = false): Promise<void> {
  await page.route('**/api/media/dramas/3/peaks*', (route) => {
    if (fail) return route.fulfill({ status: 500, json: { error: { code: 'internal', message: 'No peaks.' } } })
    const q = new URL(route.request().url()).searchParams
    const buckets = Number(q.get('buckets'))
    const peaks = Array.from({ length: buckets }, (_, i) => Math.round(40 + 200 * Math.abs(Math.sin(i / 5)) * Math.sin((Math.PI * i) / buckets)))
    return route.fulfill({ json: { start: Number(q.get('start')), end: Number(q.get('end')), buckets, peaks } })
  })
}

/** On a phone the selection bar lists its actions in a sheet: opens it and returns the named action there. */
export async function phoneBarAction(page: Page, name: string) {
  const bar = page.getByTestId('selection-bar')
  const count = (await bar.locator('.review-selbar-count').textContent()) ?? ''
  await bar.getByRole('button', { name: 'Actions…' }).tap()
  return page.getByRole('dialog', { name: count.trim() }).getByRole('button', { name })
}
