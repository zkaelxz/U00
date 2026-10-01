import { describe, expect, it } from 'vitest'

import type { DiagnosticsInstallTask, DiagnosticsPackageInfo } from '../../types/diagnostics'
import {
  belowMinText, firstHint, formatApproxMb, groupTasks, minVersionText, optionalMissingText, packageSizeText, roleLabel,
  safeSourceUrl, sortTasksNeedingInstall, taskConfirmLabel, taskGroupSummary, taskNotes, taskOutput, taskReady, taskResultText, taskStatus, taskTone,
  type TaskRunResult,
} from './installPresets'

const task = (o: Partial<DiagnosticsInstallTask> = {}): DiagnosticsInstallTask => ({
  id: 't', group: 'Audio', label: 'Transcribe', help: '', packages: ['a', 'b', 'c'],
  installed_count: 1, to_install: ['a', 'b'], approx_mb: 82, ...o,
})

const pkg = (o: Partial<DiagnosticsPackageInfo> = {}): DiagnosticsPackageInfo => ({
  name: 'a', dist: 'a', installed: false, installable: true, powers: '', approx_mb: 5, pulls_torch: false,
  source_url: 'https://pypi.org/project/a/', not_offered_reason: null, warning: null, ...o,
})

describe('install presets helpers', () => {
  it('formats approximate sizes in MB and GB', () => {
    expect(formatApproxMb(45)).toBe('approx. 45 MB')
    expect(formatApproxMb(2500)).toBe('approx. 2.5 GB')
    expect(formatApproxMb(2000)).toBe('approx. 2 GB')
    expect(formatApproxMb(12000)).toBe('approx. 12 GB')
    expect(formatApproxMb(0.2)).toBe('under 1 MB')
    expect(formatApproxMb(null)).toBeNull()
    expect(formatApproxMb(-1)).toBeNull()
  })

  it('notes PyTorch only when it is not installed yet', () => {
    expect(packageSizeText({ approx_mb: 20, pulls_torch: true }, false)).toBe('approx. 20 MB + PyTorch')
    expect(packageSizeText({ approx_mb: 20, pulls_torch: true }, true)).toBe('approx. 20 MB')
    expect(packageSizeText({ approx_mb: null, pulls_torch: true }, false)).toBeNull()
  })

  it('groups tasks in server order', () => {
    const g = groupTasks([task({ id: '1' }), task({ id: '2', group: 'Dubbing' }), task({ id: '3' })])
    expect(g.map((x) => [x.group, x.tasks.map((t) => t.id)])).toEqual([['Audio', ['1', '3']], ['Dubbing', ['2']]])
  })

  it('shows status and moves ready tasks last', () => {
    const ready = task({ id: 'r', installed_count: 3, to_install: [] })
    expect(taskStatus(ready)).toBe('Ready')
    expect(taskStatus(task())).toBe('1 of 3 installed')
    expect(sortTasksNeedingInstall([ready, task({ id: 'n' })]).map((t) => t.id)).toEqual(['n', 'r'])
  })

  it('uses roles: required missing, recommended to add, optional never blocks Ready', () => {
    const roles = { a: 'required', b: 'recommended', c: 'optional' } as const
    const t = (o: Partial<DiagnosticsInstallTask>) => task({ roles, required_missing: [], optional_missing: [], ...o })
    expect(taskStatus(t({ required_missing: ['a'], to_install: ['a', 'b'] }))).toBe('Needs 1 required package')
    expect(taskStatus(t({ to_install: ['b'] }))).toBe('Works; 1 recommended to add')
    expect(taskTone(t({ required_missing: ['a'], to_install: ['a', 'b'] }))).toBe('warn')
    expect(taskTone(t({ to_install: ['b'] }))).toBe('neutral')
    expect(taskTone(t({ to_install: [] }))).toBe('ok')
    expect(taskTone(task())).toBe('neutral')
    const ready = t({ to_install: [], optional_missing: ['c'] })
    expect(taskReady(ready)).toBe(true)
    expect(taskStatus(ready)).toBe('Ready')
    expect(optionalMissingText(ready)).toBe('Optional, not installed by this button: c')
    expect(optionalMissingText(t({}))).toBeNull()
    expect(roleLabel(ready, 'b')).toBe('Recommended')
    expect(roleLabel(task(), 'b')).toBeNull()
  })

  it('shows the app\'s minimum version and flags an older install', () => {
    expect(minVersionText(pkg({ min_version: '0.42' }))).toBe('≥ 0.42')
    expect(minVersionText(pkg())).toBeNull()
    const old = pkg({ name: 'jieba', installed: true, installed_version: '0.40', min_version: '0.42', below_min: true })
    expect(belowMinText(old)).toBe('jieba 0.40 is older than the 0.42 the app needs.')
    expect(belowMinText({ ...old, below_min: false })).toBeNull()
    expect(taskNotes(task({ packages: ['jieba'], to_install: [] }), { jieba: old })).toEqual([
      'jieba 0.40 is older than the 0.42 the app needs.',
    ])
  })

  it('builds the confirm label with count and size', () => {
    expect(taskConfirmLabel(task())).toBe('Confirm install 2 packages (approx. 82 MB)')
    expect(taskConfirmLabel(task({ to_install: ['a'], approx_mb: 2500 }))).toBe('Confirm install 1 package (approx. 2.5 GB)')
  })

  it('lists not-offered reasons and warnings for missing packages only', () => {
    const packages = {
      a: pkg({ name: 'a', warning: 'would downgrade transformers' }),
      b: pkg({ name: 'b', installed: true, warning: 'ignored' }),
      c: pkg({ name: 'c', installable: false, not_offered_reason: 'not offered: broken' }),
    }
    expect(taskNotes(task({ to_install: ['a'] }), packages)).toEqual([
      'a: would downgrade transformers', 'c: not offered: broken',
    ])
  })

  it('summarises a task run', () => {
    const ok: TaskRunResult = { name: 'a', ok: true, output: ['done'] }
    const bad: TaskRunResult = { name: 'b', ok: false, output: [], hint: 'close other Python windows' }
    expect(taskResultText('Transcribe', [ok])).toBe('Installed everything for Transcribe.')
    expect(taskResultText('Transcribe', [ok, bad])).toBe('Transcribe: install failed for b (1 installed).')
    expect(taskResultText('Transcribe', [bad])).toBe('Transcribe: install failed for b.')
    expect(taskOutput([ok, bad])).toEqual(['── a ──', 'done', '── b ──', 'No output.'])
    expect(firstHint([ok, bad])).toBe('close other Python windows')
    expect(firstHint([ok])).toBeNull()
  })

  it('only accepts PyPI project links', () => {
    expect(safeSourceUrl('https://pypi.org/project/opencv-python/')).toBe('https://pypi.org/project/opencv-python/')
    expect(safeSourceUrl('javascript:alert(1)')).toBeNull()
    expect(safeSourceUrl('https://evil.example/project/x/')).toBeNull()
    expect(safeSourceUrl(null)).toBeNull()
  })
})

describe('task group summary', () => {
  it('counts the tasks that still need something, else says all set up', () => {
    const ready = task({ id: 'r', installed_count: 3, to_install: [] })
    expect(taskGroupSummary([task({ id: 'a' }), ready, task({ id: 'b' })])).toBe('2 still to set up')
    expect(taskGroupSummary([ready])).toBe('All set up')
  })
})
