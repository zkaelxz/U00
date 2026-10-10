// Pure helpers for Packages > "GPU PyTorch" (GET /api/diagnostics/gpu-torch,
// POST /api/diagnostics/gpu-torch/setup). The versions and index shown come
// from the server's static table; nothing here builds a pip argument.
import type {
  DiagnosticsGpuTorchNvidia, DiagnosticsGpuTorchStatus, DiagnosticsTorchPackage, DiagnosticsTorchVariant,
  DiagnosticsTorchVerify,
} from '../../types/diagnostics'

/** One line for the GPU and its driver. */
export function driverText(n: DiagnosticsGpuTorchNvidia): string {
  if (!n.found) return 'No NVIDIA GPU found (nvidia-smi did not answer).'
  const gpu = `${n.gpu_name ?? 'NVIDIA GPU'}, driver ${n.driver_version ?? 'unknown'}`
  if (n.status === 'too_old') return `${gpu}: too old for CUDA 12.8 (needs ${n.minimum}+, recommended ${n.recommended}+).`
  if (n.status === 'old') return `${gpu}: works, but ${n.recommended} or newer is recommended for CUDA 12.8.`
  return gpu
}

/** "2.11.0+cu128 (CUDA build)", "2.11.0+cpu (CPU only)", "not installed". */
export function packageVersionText(p: DiagnosticsTorchPackage): string {
  if (!p.version) return 'Not installed'
  if (p.build === 'cuda') return `${p.version} (CUDA build)`
  if (p.build === 'cpu') return `${p.version} (CPU only)`
  return p.version
}

/** The headline for the current state. */
export function stateText(s: DiagnosticsGpuTorchStatus): string {
  switch (s.state) {
    case 'missing': return 'PyTorch is not installed.'
    case 'mismatched': return 'torch, torchvision and torchaudio don\'t match. Set them up again together.'
    case 'cpu_on_gpu': return 'This PC has an NVIDIA GPU, but the installed PyTorch is CPU only.'
    case 'recommended': return 'The recommended PyTorch is installed.'
    case 'different': return 'A different PyTorch than the recommended one is installed. It may work, but the app is tested with the recommended set.'
  }
}

/** True when the state is worth fixing (shown as a warning). */
export const stateIsProblem = (s: DiagnosticsGpuTorchStatus) =>
  s.state === 'mismatched' || s.state === 'cpu_on_gpu' || s.nvidia.status === 'too_old'

/** Why the setup can't run for this variant, or null. */
export function setupBlockedReason(s: DiagnosticsGpuTorchStatus, v: DiagnosticsTorchVariant): string | null {
  if (!s.python_supported) return 'This Python version has no PyTorch builds for the recommended set.'
  if (v.needs_nvidia && !s.nvidia.found) return 'No NVIDIA GPU found. Install the NVIDIA driver first, or use the CPU version.'
  if (v.needs_nvidia && s.nvidia.status === 'too_old') return 'Update the NVIDIA driver first (nvidia.com), then set up GPU PyTorch.'
  return null
}

/** Second-press label with the download size. */
export const setupConfirmLabel = (v: DiagnosticsTorchVariant) =>
  `Confirm install ${v.needs_nvidia ? 'GPU' : 'CPU'} PyTorch (${v.needs_nvidia ? 'about 2.5 GB' : 'about 300 MB'})`

/** One line for a CUDA check result. */
export function verifyText(v: DiagnosticsTorchVerify): string {
  if (!v.torch) return `PyTorch didn't import${v.error ? `: ${v.error}` : '.'}`
  const cuda = v.cuda_available
    ? `CUDA works${v.device ? ` on ${v.device}` : ''}`
    : v.cuda_build ? `CUDA ${v.cuda_build} build, but no GPU is available` : 'CPU-only build: CUDA not available'
  return `PyTorch ${v.torch}: ${cuda}.`
}

/** The short badge next to the "GPU PyTorch" heading. */
export function stateBadge(s: DiagnosticsGpuTorchStatus): { text: string; tone: 'ok' | 'warn' | 'neutral' } {
  // A too-old driver breaks CUDA whatever is installed, so it outranks the package state.
  if (s.nvidia.status === 'too_old' && s.state !== 'missing') return { text: 'Driver too old', tone: 'warn' }
  switch (s.state) {
    case 'missing': return { text: 'Not installed', tone: 'neutral' }
    case 'mismatched': return { text: 'Versions don\'t match', tone: 'warn' }
    case 'cpu_on_gpu': return { text: 'CPU only', tone: 'warn' }
    case 'recommended': return { text: 'Recommended set', tone: 'ok' }
    case 'different': return { text: 'Not the recommended set', tone: 'neutral' }
  }
}

/** The closed fold's one line: "PyTorch 2.11.0+cu128 · CUDA OK · RTX 3080 Ti". */
export function gpuTorchSummary(s: DiagnosticsGpuTorchStatus): string {
  const torch = s.installed.find((p) => p.name === 'torch')
  const parts = [torch?.version ? `PyTorch ${torch.version}` : 'PyTorch not installed']
  if (s.probe) parts.push(s.probe.cuda_available ? 'CUDA OK' : 'no CUDA')
  else if (torch?.build === 'cuda') parts.push('CUDA build')
  else if (torch?.build === 'cpu') parts.push('CPU only')
  if (s.nvidia.found && s.nvidia.gpu_name) parts.push(s.nvidia.gpu_name)
  else if (!s.nvidia.found) parts.push('no NVIDIA GPU')
  return parts.join(' · ')
}
