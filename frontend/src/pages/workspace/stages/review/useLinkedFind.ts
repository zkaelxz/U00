import { useState } from 'react'

import { useRoute } from '../../../../router'

/**
 * The Review search box (typed text and the term sent to the server), started from `?find=` so a
 * Ctrl+K line hit opens Review already searched. A new link while Review is open replaces both
 * during render, which is how React syncs state to a changed prop without an extra effect pass.
 */
export function useLinkedFind(dramaId: number) {
  const route = useRoute()
  const linked = route.name === 'drama' && route.id === dramaId ? (route.find ?? '') : ''
  const [input, setInput] = useState(linked)
  const [term, setTerm] = useState(linked)
  const [seen, setSeen] = useState(linked)
  if (linked !== seen) {
    setSeen(linked)
    if (linked) {
      setInput(linked)
      setTerm(linked)
    }
  }
  return { input, setInput, term, setTerm }
}
