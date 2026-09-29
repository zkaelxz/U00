/*
 * Pure helpers for the Translate page (pages/Translate.tsx): engine picker
 * labels, restoring remembered choices, the language swap and the history
 * list. Kept here so they can be tested without a browser.
 */
import { NON_ENGLISH_LANGUAGES, engineShortName, usableEngines } from '../api/translate'
import { humanize } from '../components/labels'
import type { TranslateDirection, TranslateEngine, TranslateHistoryEntry } from '../types/translate'

/** History rows shown before "Show all". */
export const HISTORY_PREVIEW = 5

/** Picker text: the short name, plus "(no key)" when the engine can't run yet. */
export function engineOptionLabel(engine: Pick<TranslateEngine, 'name' | 'key_configured'>): string {
  const name = engineShortName(engine)
  return engine.key_configured ? name : `${name} (no key)`
}

/** Usable engines first (API order), then the ones missing a key. */
export function orderEngines(engines: TranslateEngine[]): TranslateEngine[] {
  return [...usableEngines(engines), ...engines.filter((e) => !e.key_configured)]
}

/**
 * The engine to show: the remembered one while the server still lists it
 * (a missing key then shows as a warning), else the first usable one, else ''.
 */
export function pickEngine(engines: TranslateEngine[], remembered: string): string {
  if (remembered && engines.some((e) => e.name === remembered)) return remembered
  return usableEngines(engines)[0]?.name ?? ''
}

/** The remembered model if the engine still offers it, else '' (the engine default). */
export function pickModel(engine: TranslateEngine | undefined, remembered: string): string {
  return remembered && engine?.models?.includes(remembered) ? remembered : ''
}

export function isDirection(value: string): value is TranslateDirection {
  return value === 'to_english' || value === 'from_english'
}

/** A remembered non-English language code, or Chinese when it is not offered. */
export function pickLanguage(remembered: string): string {
  return NON_ENGLISH_LANGUAGES.some((l) => l.code === remembered) ? remembered : 'zh'
}

export function swapDirection(direction: TranslateDirection): TranslateDirection {
  return direction === 'to_english' ? 'from_english' : 'to_english'
}

/** "Chinese → English · Claude" for a history row (never the raw codes). */
export function historyLabel(h: Pick<TranslateHistoryEntry, 'source_language' | 'target_language' | 'engine'>): string {
  const src = humanize('language', h.source_language) || 'Unknown'
  const tgt = humanize('language', h.target_language) || 'Unknown'
  return `${src} → ${tgt} · ${engineShortName({ name: h.engine })}`
}

/** "2026-09-29T18:31:05" or "2026-09-29 18:31:05" -> "2026-09-29 18:31". */
export function historyTime(createdAt: string): string {
  return createdAt.replace('T', ' ').slice(0, 16)
}

/** The rows to render: the first HISTORY_PREVIEW unless "Show all" is on. */
export function visibleHistory<T>(items: T[], showAll: boolean): T[] {
  return showAll ? items : items.slice(0, HISTORY_PREVIEW)
}
