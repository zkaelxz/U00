import { beforeEach, describe, expect, it } from 'vitest'

import { currentUndoOffer, dropUndo, offerUndo, retireUndoOffer, undoOfferFor } from './undoOffer'

const shown = (owner: 'lines' | 'merge-short' | 'resplit', dramaId = 1) => undoOfferFor(currentUndoOffer(), owner, dramaId)

describe('the one Review Undo offer', () => {
  beforeEach(() => retireUndoOffer())

  it('a structural change in one panel retires the other panels’ offers', () => {
    offerUndo('lines', 1, 'split')
    expect(shown('lines')).toBe('split')
    offerUndo('merge-short', 1, 'merge')
    expect(shown('lines')).toBeNull()
    expect(shown('merge-short')).toBe('merge')
    offerUndo('resplit', 1, 'resplit')
    expect(shown('merge-short')).toBeNull()
    expect(shown('resplit')).toBe('resplit')
  })
  it('a panel clearing its own offer leaves another panel’s', () => {
    offerUndo('merge-short', 1, 'merge')
    dropUndo('lines')
    expect(shown('merge-short')).toBe('merge')
    dropUndo('merge-short')
    expect(shown('merge-short')).toBeNull()
  })
  it('an edit retires any offer, and an offer shows only for its own title', () => {
    offerUndo('lines', 1, 'split')
    expect(shown('lines', 2)).toBeNull()
    retireUndoOffer()
    expect(shown('lines')).toBeNull()
  })
})
