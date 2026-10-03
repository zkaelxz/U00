// Pure helpers for Packages > "By task" (install presets from
// GET /api/diagnostics/install-presets). Sizes are the server's static
// estimates, always shown as "approx.".
import type { DiagnosticsInstallTask, DiagnosticsPackageInfo, TaskRole } from '../../types/diagnostics'

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

type TaskGroup = { group: string; tasks: DiagnosticsInstallTask[] }

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

/** Ready: every required and recommended package is there (optional extras don't count). */
export function taskReady(t: DiagnosticsInstallTask): boolean {
  if (!t.roles) return t.installed_count >= t.packages.length
  return !(t.required_missing ?? []).length && !t.to_install.length
}

/** "Ready", "Needs 2 required packages", "Works; 1 recommended to add" (or "2 of 3 installed" without roles). */
export function taskStatus(t: DiagnosticsInstallTask): string {
  if (taskReady(t)) return 'Ready'
  if (!t.roles) return `${t.installed_count} of ${t.packages.length} installed`
  const req = (t.required_missing ?? []).length
  if (req) return `Needs ${req} required ${req === 1 ? 'package' : 'packages'}`
  const rec = t.to_install.length
  return `Works; ${rec} recommended to add`
}

/** The status badge's tone: Ready green, a missing required package amber, anything else neutral. */
export function taskTone(t: DiagnosticsInstallTask): 'ok' | 'warn' | 'neutral' {
  if (taskReady(t)) return 'ok'
  return (t.required_missing ?? []).length ? 'warn' : 'neutral'
}

/** Ready tasks move last, so the ones that still need something come first. */
export function sortTasksNeedingInstall(tasks: DiagnosticsInstallTask[]): DiagnosticsInstallTask[] {
  return [...tasks.filter((t) => !taskReady(t)), ...tasks.filter(taskReady)]
}

/** The closed group's one-liner: how many of its tasks still need something installed. */
export function taskGroupSummary(tasks: DiagnosticsInstallTask[]): string {
  const need = tasks.filter((t) => !taskReady(t)).length
  return need ? `${need} still to set up` : 'All set up'
}

const ROLE_LABELS: Record<TaskRole, string> = { required: 'Required', recommended: 'Recommended', optional: 'Optional' }

/** "Required" / "Recommended" / "Optional", or null when the server sent no roles. */
export const roleLabel = (t: DiagnosticsInstallTask, name: string): string | null =>
  t.roles?.[name] ? ROLE_LABELS[t.roles[name]] : null

/** "≥ 0.42" when the app needs a minimum version. */
export const minVersionText = (p: DiagnosticsPackageInfo | undefined): string | null =>
  p?.min_version ? `≥ ${p.min_version}` : null

/** "jieba 0.40 is older than the 0.42 the app needs." for an installed package below the minimum. */
export const belowMinText = (p: DiagnosticsPackageInfo | undefined): string | null =>
  p?.installed && p.below_min && p.min_version
    ? `${p.name} ${p.installed_version ?? ''} is older than the ${p.min_version} the app needs.`.replace('  ', ' ')
    : null

/** Second-press label, with the approximate download. */
export function taskConfirmLabel(t: DiagnosticsInstallTask): string {
  const size = formatApproxMb(t.approx_mb)
  const n = t.to_install.length
  return `Confirm install ${n} ${n === 1 ? 'package' : 'packages'}${size ? ` (${size})` : ''}`
}

/** Warnings and not-offered reasons for a task's packages, as "name: text"; installed ones below the app's minimum. */
export function taskNotes(t: DiagnosticsInstallTask, packages: Record<string, DiagnosticsPackageInfo>): string[] {
  const notes: string[] = []
  for (const name of t.packages) {
    const p = packages[name]
    if (!p) continue
    if (p.installed) {
      const old = belowMinText(p)
      if (old) notes.push(old)
      continue
    }
    if (p.not_offered_reason) notes.push(`${name}: ${p.not_offered_reason}`)
    else if (p.warning && t.to_install.includes(name)) notes.push(`${name}: ${p.warning}`)
  }
  return notes
}

/** "Optional, not installed by this button: paddleocr" (install them one at a time under "Still to install"). */
export const optionalMissingText = (t: DiagnosticsInstallTask): string | null =>
  t.optional_missing?.length ? `Optional, not installed by this button: ${t.optional_missing.join(', ')}` : null

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
