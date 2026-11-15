// Pure helpers for the Comic page's Translate panel (ScanlatePanel.tsx).

import { isTerminalStatus, type JobRecord } from '../../types/jobs'
import type { ScanlateConfig, ScanlateEngine, ScanlateUploadLimits } from '../../types/scanlate'

const MB = 1024 * 1024

// Engines that can run now (a key or local endpoint is configured).
export function usableEngines(config: ScanlateConfig | null): ScanlateEngine[] {
  return (config?.engines ?? []).filter((e) => e.key_configured)
}

// The engine picked by default: the Settings default when it can run,
// else the first engine that can.
export function defaultEngine(config: ScanlateConfig | null): string {
  const usable = usableEngines(config)
  if (usable.some((e) => e.name === config?.default_engine)) return config!.default_engine
  return usable[0]?.name ?? ''
}

// Why the run buttons are disabled (null when they can run).
export function runBlockedReason(config: ScanlateConfig | null, engine: string): string | null {
  if (!config) return null
  if (config.page_count === 0) return 'Upload pages first.'
  if (!engine) return 'No translation engine has a key yet. Add one in Settings.'
  return null
}

// One line about what will do the work on this PC.
export function toolsSummary(config: ScanlateConfig): string {
  const detector = config.ml_weights_cached ? 'ML detector' : 'OpenCV detector (ML model not downloaded)'
  const cleanup = config.lama_weights_cached ? 'LaMa cleanup' : 'OpenCV cleanup'
  const ocr = `OCR: ${config.ocr_backend}${config.ocr_backend_installed ? '' : ' (not installed)'}`
  return `${detector} · ${cleanup} · ${ocr}`
}

export function pagesText(n: number): string {
  return `${n} ${n === 1 ? 'page' : 'pages'}`
}

export function progressSummary(config: ScanlateConfig): string {
  if (config.page_count === 0) return 'No pages yet'
  return `${pagesText(config.page_count)} · ${config.pages_with_regions} with text · ${config.pages_rendered} typeset`
}

const IMAGE_EXT = /\.(png|jpe?g|webp)$/i
const PDF_EXT = /\.pdf$/i

// Checks a picked file list against the server's limits before sending it
// (the server checks again, including the pixel cap, which needs the bytes).
export function checkFiles(files: { name: string; size: number }[], limits: ScanlateUploadLimits): string | null {
  if (files.length === 0) return 'Choose at least one file.'
  if (files.length > limits.max_files) return `At most ${limits.max_files} files at once.`
  let total = 0
  for (const f of files) {
    total += f.size
    const pdf = PDF_EXT.test(f.name)
    if (!pdf && !IMAGE_EXT.test(f.name)) return `${f.name}: use PNG, JPEG, WebP${limits.pdf ? ' or PDF' : ''}.`
    if (pdf && !limits.pdf) return `${f.name}: PDF import needs the pypdf package. Install it in Diagnostics, or upload images.`
    if (f.size === 0) return `${f.name} is empty.`
    const cap = pdf ? limits.max_pdf_mb : limits.max_image_mb
    if (f.size > cap * MB) return `${f.name} is over ${cap} MB.`
  }
  if (total > limits.max_total_mb * MB) return `Over ${limits.max_total_mb} MB in total.`
  return null
}

export function uploadResultText(r: { added: number; pdf_pages_skipped: number; strips_sliced: number }): string {
  let s = `Added ${r.added} page${r.added === 1 ? '' : 's'}.`
  if (r.strips_sliced) s += ` ${r.strips_sliced} tall strip${r.strips_sliced === 1 ? ' was' : 's were'} sliced.`
  if (r.pdf_pages_skipped) s += ` ${r.pdf_pages_skipped} PDF page${r.pdf_pages_skipped === 1 ? '' : 's'} had no image and ${r.pdf_pages_skipped === 1 ? 'was' : 'were'} skipped.`
  return s
}

// Page 1 also loads the OCR and detector models, so it can sit for minutes with no other sign of life.
export function firstPageNote(kind: 'run' | 'export' | null, job: JobRecord | null): string | undefined {
  if (kind !== 'run' || !job || isTerminalStatus(job.status) || !/^Page 1 of /.test(job.message)) return undefined
  return 'The first page also loads the OCR and detector models, which can take a few minutes.'
}

/** "5-20, 30" -> [5..20, 30] (1-based, ascending, no repeats); null for empty text, a page
 *  outside 1..max, or anything that is not numbers, commas and ranges. */
export function parsePageRanges(text: string, max: number): number[] | null {
  const found = new Set<number>()
  for (const part of text.split(',')) {
    const m = /^\s*(\d+)\s*(?:[-–]\s*(\d+)\s*)?$/.exec(part)
    if (!m) return null
    const from = Number(m[1])
    const to = m[2] === undefined ? from : Number(m[2])
    if (from < 1 || to < from || to > max) return null
    for (let n = from; n <= to; n += 1) found.add(n)
  }
  return found.size ? [...found].sort((a, b) => a - b) : null
}

export type WarningKind = 'noText' | 'other' | 'untranslated' | 'noBubbles'

export interface WarningPage {
  pageId: number
  ordinal: number
  kind: WarningKind
  text: string
  level: 'error' | 'warning'
  // Numbers drawn on the boxes the warning is about (empty when it is about the whole page).
  boxes: number[]
}

export interface WarningGroup {
  kind: WarningKind
  title: string
  // Harmless groups start folded so they do not bury the pages that need a look.
  harmless: boolean
  pages: WarningPage[]
}

const GROUPS: { kind: WarningKind; title: string; harmless: boolean }[] = [
  { kind: 'noText', title: 'No text was read', harmless: false },
  { kind: 'other', title: 'Other warnings', harmless: false },
  { kind: 'untranslated', title: 'Text boxes left in the original language', harmless: true },
  { kind: 'noBubbles', title: 'No speech bubbles found (often covers or art)', harmless: true },
]

// The server sends plain sentences, so the kind is read from their wording; an unknown sentence lands in "Other warnings".
export function describeWarning(message: string): { kind: WarningKind; text: string; boxes: number[] } {
  const line = message.split('\n')[0]
  const region = /^Regions? (.+?) (?:had|have) no translation/.exec(line)
  if (region) {
    const nums = region[1].replace(/ and /g, ', ')
    const plural = /[, ]/.test(nums)
    // The numbers are the ones drawn on the text boxes in the page view.
    return {
      kind: 'untranslated',
      boxes: nums.split(', ').map(Number),
      text: `Text box${plural ? 'es' : ''} numbered ${nums} on the page ${plural ? 'have' : 'has'} no translation and still show${plural ? '' : 's'} the original.`,
    }
  }
  if (line.startsWith('OCR found no text')) return { kind: 'noText', text: line, boxes: [] }
  if (line.startsWith('No bubbles found')) {
    return { kind: 'noBubbles', boxes: [], text: 'No speech bubbles found. Normal for a cover or splash art; otherwise add boxes by hand.' }
  }
  return { kind: 'other', boxes: [], text: line.length > 140 ? `${line.slice(0, 139)}…` : line }
}

// Every page with a warning or error (current page included), in reading order, plus the same pages grouped by kind.
export function warningPages(
  notes: { page_id: number; ordinal: number; notes: { level: string; message: string }[] }[],
): { all: WarningPage[]; groups: WarningGroup[] } {
  const all: WarningPage[] = []
  for (const p of notes) {
    const worst = p.notes.find((x) => x.level === 'error') ?? p.notes.find((x) => x.level !== 'info')
    if (!worst) continue
    const d = describeWarning(worst.message)
    all.push({ pageId: p.page_id, ordinal: p.ordinal, level: worst.level === 'error' ? 'error' : 'warning', ...d })
  }
  all.sort((a, b) => a.ordinal - b.ordinal)
  const groups = GROUPS.map((g) => ({ ...g, pages: all.filter((w) => w.kind === g.kind) })).filter((g) => g.pages.length > 0)
  return { all, groups }
}

// The nearest warning page after (or before) the one on screen, or null at the end of the list.
export function neighbourWarning(all: WarningPage[], current: number | null, dir: 1 | -1): WarningPage | null {
  const from = current ?? (dir === 1 ? 0 : Infinity)
  const ahead = all.filter((w) => (dir === 1 ? w.ordinal > from : w.ordinal < from))
  return (dir === 1 ? ahead[0] : ahead[ahead.length - 1]) ?? null
}
