import { describe, expect, it } from 'vitest'

import type { GlossaryTerm } from '../../../types/translateStage'
import { filterTerms, sortTerms } from './glossaryTermView'

const term = (id: number, o: string, t: string, aliases: string[] = []) =>
  ({ id, term_original: o, term_translation: t, aliases }) as GlossaryTerm

const terms = [term(1, '姑姑', 'Auntie', ['Gugu']), term(2, '畢藍', 'Bi Lan'), term(3, '駱棋', 'Luo Qi')]

describe('glossary term view', () => {
  it('finds a term by original, translation or alias, ignoring case', () => {
    expect(filterTerms(terms, 'auntie').map((t) => t.id)).toEqual([1])
    expect(filterTerms(terms, 'GUGU').map((t) => t.id)).toEqual([1])
    expect(filterTerms(terms, '駱').map((t) => t.id)).toEqual([3])
    expect(filterTerms(terms, '  ')).toBe(terms)
  })

  it('sorts without changing the list it was given', () => {
    expect(sortTerms(terms, 'newest').map((t) => t.id)).toEqual([3, 2, 1])
    expect(sortTerms(terms, 'listed')).toBe(terms)
    expect(sortTerms(terms, 'translation').map((t) => t.id)).toEqual([1, 2, 3])
    expect(terms.map((t) => t.id)).toEqual([1, 2, 3])
  })
})
