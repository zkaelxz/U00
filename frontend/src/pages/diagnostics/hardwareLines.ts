// Hardware lines for the Recommended installs card.
import type { DiagnosticsGpuTorchStatus, DiagnosticsSetupChecks } from '../../types/diagnostics'

/** What was found on this PC, as short lines: the GPU and driver, FFmpeg with libass, a JavaScript runtime. */
export function hardwareLines(setup: DiagnosticsSetupChecks | null, gpu: DiagnosticsGpuTorchStatus | null): { label: string; value: string; ok: boolean }[] {
  const nvidia = gpu?.nvidia
  return [
    {
      label: 'GPU',
      ok: !!nvidia?.found,
      value: !gpu ? 'checking…' : nvidia?.found ? `${nvidia.gpu_name ?? 'NVIDIA GPU'}, driver ${nvidia.driver_version ?? 'unknown'}` : 'No NVIDIA GPU found (transcription runs on the CPU, which is slower)',
    },
    {
      label: 'FFmpeg',
      ok: !!setup?.ffmpeg.found && setup.ffmpeg.libass !== false,
      value: !setup ? 'checking…' : !setup.ffmpeg.found ? 'Not found' : setup.ffmpeg.libass === false ? 'Found, without libass (burned-in subtitles need it)' : 'Found, with libass',
    },
    {
      label: 'JavaScript runtime',
      ok: !!setup?.js_runtime.found,
      value: !setup ? 'checking…' : setup.js_runtime.found ? `Found (${setup.js_runtime.name})` : 'Not found (some video downloads need one)',
    },
  ]
}
