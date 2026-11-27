// FlatPaneContext: a page that shows one pane at a time draws the Sections inside as plain headings (Section.tsx).
import { createContext } from 'react'

/** `showing`: the pane is on screen, which a flat section's body reads in place of "the fold is open" (useDetailsOpen). */
export const FlatPaneContext = createContext<{ showing: boolean } | null>(null)
