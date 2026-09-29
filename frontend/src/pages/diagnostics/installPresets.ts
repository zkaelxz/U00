// Pure helpers for Packages > "By task" (install presets from
// GET /api/diagnostics/install-presets). Sizes are the server's static
// estimates, always shown as "approx.".
import type { DiagnosticsInstallTask, DiagnosticsPackageInfo } from '../../types/diagnostics'

/** "approx. 45 MB", "approx. 2.5 GB", "under 1 MB"; null when unknown. */
export function formatApproxMb(mb: number | null | undefined): string | null {
  if (mb == null || !Number.isFinite(mb) || mb < 0) return null
  if (mb < 1) return 'under 1 MB'
  if (mb >= 1000) {
    const gb = mb / 1000
    return `approx. ${gb >= 10 ? Math.round(gb) : Number(gb.toFixed(1))} GB`
  }
  return `approx. ${Math.round(mb)} MB`
}

/** One package's size line, noting PyTorch when it would also be pulled in. */
export function packageSizeText(p: Pick<DiagnosticsPackageInfo, 'approx_mb' | 'pulls_torch'>, torchInstalled: boolean): string | null {
  const size = formatApproxMb(p.approx_mb)
  if (!size) return null
  return p.pulls_torch && !torchInstalled ? `${size} + PyTorch` : size
}

export type TaskGroup = { group: string; tasks: DiagnosticsInstallTask[] }

/** Tasks grouped by `group`, groups and tasks in server order. */
export function groupTasks(tasks: DiagnosticsInstallTask[]): TaskGroup[] {
  const out: TaskGroup[] = []
  for (const t of tasks) {
    const g = out.find((x) => x.group === t.group)
    if (g) g.tasks.push(t)
    else out.push({ group: t.group, tasks: [t] })
  }
  return out
}

/** "Ready" or "2 of 3 installed". */
export function taskStatus(t: DiagnosticsInstallTask): string {
  if (t.installed_count >= t.packages.length) return 'Ready'
  return `${t.installed_count} of ${t.packages.length} installed`
}

/** Ready tasks move last, so the ones that still need something come first. */
export function sortTasksNeedingInstall(tasks: DiagnosticsInstallTask[]): DiagnosticsInstallTask[] {
  const ready = (t: DiagnosticsInstallTask) => t.installed_count >= t.packages.length
  return [...tasks.filter((t) => !ready(t)), ...tasks.filter(ready)]
}

/** Second-press label, with the approximate download. */
export function taskConfirmLabel(t: DiagnosticsInstallTask): string {
  const size = formatApproxMb(t.approx_mb)
  const n = t.to_install.length
  return `Confirm install ${n} ${n === 1 ? 'package' : 'packages'}${size ? ` (${size})` : ''}`
}

/** Warnings and not-offered reasons for a task's packages, as "name: text". */
export function taskNotes(t: DiagnosticsInstallTask, packages: Record<string, DiagnosticsPackageInfo>): string[] {
  const notes: string[] = []
  for (const name of t.packages) {
    const p = packages[name]
    if (!p || p.installed) continue
    if (p.not_offered_reason) notes.push(`${name}: ${p.not_offered_reason}`)
    else if (p.warning && t.to_install.includes(name)) notes.push(`${name}: ${p.warning}`)
  }
  return notes
}

export type TaskRunResult = { name: string; ok: boolean; output: string[]; hint?: string | null }

/** One sentence for a finished "Install for this task". */
export function taskResultText(label: string, results: TaskRunResult[]): string {
  const failed = results.filter((r) => !r.ok).map((r) => r.name)
  if (!failed.length) return `Installed everything for ${label}.`
  const done = results.length - failed.length
  return `${label}: install failed for ${failed.join(', ')}` + (done ? ` (${done} installed).` : '.')
}

/** The combined output, one block per package. */
export const taskOutput = (results: TaskRunResult[]): string[] =>
  results.flatMap((r) => [`── ${r.name} ──`, ...(r.output.length ? r.output : ['No output.'])])

/** The first hint any package's install returned. */
export const firstHint = (results: TaskRunResult[]): string | null =>
  results.find((r) => r.hint)?.hint ?? null

/** Only https PyPI project links from the server are rendered as links. */
export const safeSourceUrl = (url: string | null | undefined): string | null =>
  url && /^https:\/\/pypi\.org\/project\/[a-z0-9-]+\/$/.test(url) ? url : null
