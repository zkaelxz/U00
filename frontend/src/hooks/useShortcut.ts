import { useEffect, useRef } from 'react'

// Keyboard shortcuts for list-style screens. A combo is normalised to a short
// string: "j", "?", "enter", "shift+delete", "alt+arrowdown", "alt+ ". Ctrl and
// Meta combos are never list shortcuts (they belong to the browser and to
// "save" inside an editor), and nothing fires while an IME is composing.

export interface KeyLike {
  key: string
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
  isComposing?: boolean
}

const NAMED = new Set([
  'enter', 'escape', 'delete', 'backspace', 'tab',
  'arrowdown', 'arrowup', 'arrowleft', 'arrowright', 'home', 'end', 'pagedown', 'pageup',
])

/** Normalised combo, or null when the key is never a list shortcut. */
export function comboOf(e: KeyLike): string | null {
  if (e.isComposing || e.ctrlKey || e.metaKey) return null
  if (!e.key || e.key === 'Unidentified' || e.key === 'Process' || e.key === 'Dead') return null
  // macOS types a no-break space for Alt+Space.
  const key = e.key === ' ' ? ' ' : e.key.toLowerCase()
  if (['shift', 'alt', 'control', 'meta'].includes(key)) return null
  const parts: string[] = []
  if (e.altKey) parts.push('alt')
  // Shift is already part of printable keys ("?" is shift+/); only name it for keys like Delete.
  if (e.shiftKey && (NAMED.has(key) || key === ' ')) parts.push('shift')
  parts.push(key)
  return parts.join('+')
}

/** True when the key goes to a text field (input, textarea, select or contenteditable). */
export function isTextEntry(target: EventTarget | null): boolean {
  const el = target as { tagName?: string; isContentEditable?: boolean; type?: string } | null
  if (!el || !el.tagName) return false
  if (el.isContentEditable) return true
  const tag = el.tagName.toLowerCase()
  if (tag === 'textarea' || tag === 'select') return true
  if (tag !== 'input') return false
  return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file'].includes(el.type ?? 'text')
}

export interface ShortcutContext {
  // The key went to a text field: single letters must be left alone there.
  inText: boolean
  event: KeyboardEvent
}

// Calls `handler(combo, ctx)` for every document keydown; return true to mark
// it handled (the default browser action is then prevented). The handler may
// change every render; the listener is attached once.
export function useShortcut(
  handler: (combo: string, ctx: ShortcutContext) => boolean,
  enabled = true,
) {
  const ref = useRef(handler)
  useEffect(() => {
    ref.current = handler
  })
  useEffect(() => {
    if (!enabled) return
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      const combo = comboOf(event)
      if (!combo) return
      if (ref.current(combo, { inText: isTextEntry(event.target), event })) event.preventDefault()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [enabled])
}
