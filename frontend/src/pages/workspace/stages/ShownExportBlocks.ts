// Owns the context that tells an export block which blocks the title shows.
import { createContext } from 'react'

import type { ExportBlockId } from '../exportBlocks'

// The open choice is stored per kind, so a block the title hides must not count as remembered.
export const ShownExportBlocks = createContext<ExportBlockId[]>([])
