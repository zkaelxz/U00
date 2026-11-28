// Owns the context that tells an export block which blocks the title shows.
import { createContext } from 'react'

import type { ExportBlockId } from '../exportBlocks'

// The open choice is stored per kind, so a block the title hides must not count as remembered.
export const ShownExportBlocks = createContext<ExportBlockId[]>([])

// Guided lays its exports side by side, each always open, with the one it leads with marked; All tools keeps folds.
export const ExportChoices = createContext<{ main: ExportBlockId | null } | null>(null)
