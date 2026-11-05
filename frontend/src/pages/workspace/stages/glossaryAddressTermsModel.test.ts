import { describe, expect, it } from 'vitest'

import { pickedChoices } from './glossaryAddressTermsModel'

describe('pickedChoices', () => {
  it('keeps a rendering or a keep-as-is pick and leaves out an empty one', () => {
    expect(pickedChoices({
      '姐姐': { text: ' big sister ', keep: false },
      'さん': { text: 'ignored', keep: true },
      '老师': { text: '  ', keep: false },
    })).toEqual([{ term: '姐姐', translation: 'big sister' }, { term: 'さん', keep_as_is: true }])
  })
})
