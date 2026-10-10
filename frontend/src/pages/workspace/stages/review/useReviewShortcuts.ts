import type { Dispatch, RefObject, SetStateAction } from 'react'

import type { MediaKind } from '../../../../api/media'
import { useShortcut } from '../../../../hooks/useShortcut'
import type { ReviewLine } from '../../../../types/review'
import type { EditState, RowActions } from './LineRow'
import type { createLinesController } from './linesController'
import type { PlayerHandle } from './Player'
import type { PanelMode } from './reviewLogic'
import type { LineSelection } from './useLineSelection'

const ALL_LINES_ONLY = 'Merge and add work in the All lines view (no filter or search).'

export interface ReviewShortcutsDeps {
  ctl: ReturnType<typeof createLinesController>
  actions: RowActions
  selection: LineSelection
  player: RefObject<PlayerHandle | null>
  listRef: RefObject<HTMLUListElement | null>
  searchRef: RefObject<HTMLInputElement | null>
  mediaKind: MediaKind | null
  isPhone: boolean
  sheetOpen: boolean
  keysOpen: boolean
  page: number
  pages: number
  searching: boolean
  limited: boolean
  active: ReviewLine | null
  edit: EditState | null
  ai: { lineId: number; mode: PanelMode } | null
  setAi: Dispatch<SetStateAction<{ lineId: number; mode: PanelMode } | null>>
  setKeysOpen: Dispatch<SetStateAction<boolean>>
  setStatus: Dispatch<SetStateAction<string | null>>
}

// The Review editor's keyboard map (J/K, arrows, E, M, A, timing nudges, ...).
export function useReviewShortcuts(deps: ReviewShortcutsDeps) {
  const {
    ctl, actions, selection, player, listRef, searchRef, mediaKind, isPhone, sheetOpen, keysOpen,
    page, pages, searching, limited, active, edit, ai, setAi, setKeysOpen, setStatus,
  } = deps
  const toggleFocusedRow = (target: HTMLElement) => {
    const id = target.matches?.('.review-line') ? Number(target.getAttribute('data-line-id')) : NaN
    if (Number.isNaN(id)) return false
    selection.toggle(id)
    return true
  }
  useShortcut((combo, { inText, event }) => {
    if (sheetOpen || keysOpen) return false
    if (combo === 'alt+ ') {
      if (!mediaKind) return false
      player.current?.togglePlay()
      return true
    }
    if (inText) return false
    const target = event.target as HTMLElement
    const inList = target === document.body || !!listRef.current?.contains(target)
    const onRow = target === document.body || target.matches?.('.review-line')
    const isControl = !!target.closest?.('input, select')
    // Single-key shortcuts only while focus is in the list (or nowhere).
    if ((combo.length === 1 && combo !== '?' && combo !== '/') || combo === 'shift+delete') {
      if (!inList) return false
    }
    switch (combo) {
      case 'arrowdown':
      case 'arrowup':
        if (!inList || isControl) return false
        ctl.move(combo === 'arrowdown' ? 1 : -1)
        return true
      case 'j':
      case 'k':
        ctl.move(combo === 'j' ? 1 : -1)
        return true
      case 'alt+arrowdown':
      case 'alt+arrowup':
        void ctl.moveFlagged(combo === 'alt+arrowdown' ? 1 : -1)
        return true
      case ']':
      case '[': {
        const p = page + (combo === ']' ? 1 : -1)
        if (searching || p < 1 || p > pages) return false
        void ctl.goPage(p, 'first')
        return true
      }
      case '/':
        if (isPhone && !searchRef.current) return false
        searchRef.current?.focus()
        return true
      case '?':
        setKeysOpen(true)
        return true
    }
    if (!active) return false
    switch (combo) {
      case 'enter':
        if (!onRow) return false
        void ctl.openEdit(active.id)
        return true
      case 'e':
        void ctl.openEdit(active.id)
        return true
      case 'd':
        actions.toggleDetails(active.id)
        return true
      case ' ': {
        if (!onRow) return false
        // With media, Space on a row keeps playing the line (Shift+Space ticks it).
        if (mediaKind) {
          player.current?.toggleLine(active)
          return true
        }
        return toggleFocusedRow(target)
      }
      case 'shift+ ':
        return toggleFocusedRow(target)
      case 'l':
        if (!mediaKind) return false
        player.current?.toggleLoop()
        return true
      case 's':
      case 't': {
        if (!mediaKind || !player.current) return false
        const now = player.current.getCurrentTime()
        ctl.setTiming(active.id, combo === 's' ? 'start' : 'end', () => now)
        return true
      }
      case 'z':
      case 'x':
      case 'c':
      case 'v': {
        const step = event.shiftKey ? 0.5 : 0.1
        const delta = combo === 'z' || combo === 'c' ? -step : step
        const field = combo === 'z' || combo === 'x' ? 'start' : 'end'
        ctl.setTiming(active.id, field, (line) => line[field] + delta)
        return true
      }
      case 'm':
      case 'a':
        if (limited) setStatus(ALL_LINES_ONLY)
        else void ctl.openSheet(active.id, combo === 'm' ? 'merge' : 'add')
        return true
      case 'shift+delete':
        void ctl.openSheet(active.id, 'menu', { armDelete: true })
        return true
      case 'f':
        if (!active.flag) return false
        actions.dismissFlag(active.id)
        return true
      case 'i':
        if (!active.en) return false
        actions.setAi(active.id, 'improve')
        return true
      case 'w':
        actions.setAi(active.id, 'explain')
        return true
      case 'escape':
        if (ai) {
          setAi(null)
          return true
        }
        if (edit || selection.count === 0) return false
        selection.clear()
        return true
    }
    return false
  })
}
