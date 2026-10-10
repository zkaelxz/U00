import { describe, expect, it } from 'vitest'

import type { BuildSetResult } from '../../api/benchmark'
import { buildProblems, buildRequest, describeBuild, emptyBuildForm, wholeNumber, type BuildForm } from './buildSetForm'

const form = (over: Partial<BuildForm> = {}): BuildForm => ({ ...emptyBuildForm(), dramaId: 7, setName: ' moon ', ...over })

const result = (over: Partial<BuildSetResult> = {}): BuildSetResult => ({
  set_name: 'moon', tier: 'application', include: 'reviewed', lines_in_range: 40, reviewed_lines: 12, lines_used: 12,
  case_count: 3, added: 3, skipped: 0, dry_run: false, ...over,
})

describe('build set form', () => {
  it('reads whole numbers of 1 or more only', () => {
    expect(wholeNumber(' 12 ')).toBe(12)
    for (const bad of ['', '0', '-3', '2.5', 'abc']) expect(wholeNumber(bad)).toBeNull()
  })

  it('needs a title and a name, and explains which is missing', () => {
    expect(buildProblems(emptyBuildForm())).toEqual(['Pick a title.', 'Give the set a name.'])
    expect(buildProblems(form())).toEqual([])
  })

  it('catches a bad range, scene count or case size', () => {
    expect(buildProblems(form({ lineStart: '9', lineEnd: '3' }))).toContain('The last line comes before the first.')
    expect(buildProblems(form({ sceneCount: 'x' }))).toContain('Scenes must be a whole number, 1 or more.')
    expect(buildProblems(form({ linesPerCase: '11' }))).toContain('Lines per case must be 1 to 10.')
    expect(buildProblems(form({ linesPerCase: '' }))).toContain('Lines per case must be 1 to 10.')
  })

  it('leaves blank range and scene fields out of the request and trims the name', () => {
    expect(buildRequest(form(), false)).toEqual({
      drama_id: 7, set_name: 'moon', include: 'reviewed', lines_per_case: 4, dry_run: false,
    })
    expect(buildRequest(form({ include: 'all', lineStart: '5', lineEnd: '90', sceneCount: '6', linesPerCase: '3' }), true)).toEqual({
      drama_id: 7, set_name: 'moon', include: 'all', line_start: 5, line_end: 90, scene_count: 6, lines_per_case: 3, dry_run: true,
    })
  })

  it('describes a preview and a build in plain words', () => {
    expect(describeBuild(result({ dry_run: true }))).toBe('12 reviewed lines of 40 lines in range. This would make 3 cases from 12 lines.')
    expect(describeBuild(result({ skipped: 1, added: 2 }))).toContain('1 case already in the set was skipped')
    expect(describeBuild(result({ include: 'all', reviewed_lines: 0, lines_used: 1, case_count: 1, added: 1 }))).toContain(
      '40 lines in range, reviewed or not',
    )
  })
})
