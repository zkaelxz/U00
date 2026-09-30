// Pure helpers for the Comic page's Translate panel (ScanlatePanel.tsx).

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

export function progressSummary(config: ScanlateConfig): string {
  if (config.page_count === 0) return 'No pages yet'
  return `${config.page_count} pages · ${config.pages_with_regions} with text · ${config.pages_rendered} typeset`
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
    if (!pdf && !IMAGE_EXT.test(f.name)) return `${f.name}: use PNG, JPEG, WebP or PDF.`
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
