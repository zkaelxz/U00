import { useState, useSyncExternalStore } from 'react'

import { ApiError } from '../../api/client'
import { getInstallPresets, installDependency, setupGpuTorch } from '../../api/diagnostics'
import { refreshReadiness } from '../../hooks/useReadiness'
import { installBlockedReason, type AdminBusy } from '../diagnostics/diagnosticsAdmin'
import { missingTranscription } from '../diagnostics/installPresets'
import { runInstallJob } from '../diagnostics/installJob'
import { useInstallPlan } from '../diagnostics/useInstallPlan'
import type { Need } from './needs'

// One pip runs at a time on the PC, so while one install button works the others on the page wait.
let installing = 0
const listeners = new Set<() => void>()
const track = (delta: number) => {
  installing += delta
  listeners.forEach((l) => l())
}
const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => void listeners.delete(l)
}

/** What "Install all" runs: GPU PyTorch first, then every package the open needs name, each once. */
export function installAllPlan(needs: Need[], whisperKeys: string[]): { gpuTorch: boolean; keys: string[] } {
  const keys = new Set(whisperKeys)
  needs.filter((n) => n.action === 'diagnostics').forEach((n) => n.packages.forEach((k) => keys.add(k)))
  return { gpuTorch: needs.some((n) => n.action === 'install_gpu_torch'), keys: [...keys] }
}

type Result = Awaited<ReturnType<typeof runInstallJob>>

/**
 * The install buttons a missing need offers in place: Whisper and the engine packages through the
 * shared preview-then-install flow (useInstallPlan), GPU PyTorch through its own setup job. Nothing
 * here is a new installer; it calls the same routes Diagnostics does.
 */
export function useNeedInstall(onInstalled?: () => void) {
  const [busy, setBusy] = useState<AdminBusy>(null)
  const [failure, setFailure] = useState<unknown>(null)
  const [note, setNote] = useState<string | null>(null)
  const otherInstalling = useSyncExternalStore(subscribe, () => installing > 0, () => false)

  const run = async (label: string, start: (onJob: (job: NonNullable<AdminBusy>['job']) => void) => Promise<Result>): Promise<boolean> => {
    let ok = false
    setFailure(null)
    setNote(null)
    setBusy({ kind: 'install', name: label })
    track(1)
    try {
      const r = await start((job) => setBusy({ kind: 'install', name: label, job }))
      if (r.cancelled) setNote(`Cancelled installing ${label}.`)
      else if (!r.ok) setFailure(new ApiError(500, { code: 'dependency_unavailable', message: r.hint ?? '' }))
      else setNote(`Installed ${label}.`)
      await refreshReadiness()
      ok = r.ok
      if (r.ok) onInstalled?.()
    } catch (e) {
      setFailure(e)
    } finally {
      track(-1)
      setBusy(null)
    }
    return ok
  }

  const { planned, withPlan, view } = useInstallPlan({
    onChecking: (label) => {
      setFailure(null)
      setNote(null)
      setBusy({ kind: 'install', name: `Checking what ${label} will change` })
    },
    onChecked: () => setBusy(null),
    onQueued: (label) => {
      setBusy(null)
      setNote(`${label} is queued. It installs when you restart Baihe.`)
    },
    onQueueFailed: (_label, e) => {
      setBusy(null)
      setFailure(e)
    },
  })

  /** One package at a time (the server runs one pip); the first failure ends the run. */
  const installKeys = (label: string, keys: string[]) =>
    withPlan(label, keys, async (accepted) => { await run(label, async (onJob) => {
      let last = { ok: true } as Result
      for (const key of keys) {
        last = await runInstallJob(() => installDependency(key, undefined, accepted), onJob, key)
        if (!last.ok) break
      }
      return last
    }) })

  /** Transcription is a task: the packages it still needs come from the install presets. */
  const whisperKeys = async () => {
    try {
      const task = missingTranscription(await getInstallPresets())
      if (task?.to_install.length) return task.to_install
    } catch {
      // The presets are optional extras; the one package is the minimum.
    }
    return ['faster_whisper']
  }

  const installWhisper = async () => installKeys('transcription', await whisperKeys())

  const installGpuTorch = () => run('GPU PyTorch', (onJob) => runInstallJob(() => setupGpuTorch(), onJob, 'GPU PyTorch'))

  /**
   * GPU PyTorch must finish before any package that depends on torch, or pip pulls the CPU build from PyPI;
   * the rest then install in one preview-then-install run.
   */
  const installAll = async (needs: Need[]) => {
    const plan = installAllPlan(needs, needs.some((n) => n.action === 'install_whisper') ? await whisperKeys() : [])
    if (plan.gpuTorch && !(await installGpuTorch())) return
    if (plan.keys.length) await installKeys('everything', plan.keys)
  }

  const blocked = installBlockedReason(false, busy) ?? (otherInstalling && !busy ? 'Wait for the install to finish.' : null)
    ?? (planned ? 'Close the install preview below first.' : null)
  return { busy, failure, setFailure, note, blocked, installWhisper, installKeys, installGpuTorch, installAll, planView: view }
}
