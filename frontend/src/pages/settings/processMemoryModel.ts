// Pure helpers for the "Memory by process" card (ProcessMemoryCard.tsx).
import type { ProcessMemory, ProcessRow } from '../../api/processMemory'
import { formatDecimalBytes } from '../libraryAdmin/libraryAdmin'

export const RELATION_LABEL: Record<ProcessRow['relation'], string> = {
  this_server: 'This server',
  child: 'Started by this server',
  orphan: 'Not started by this server; its parent has ended (left over?)',
  other: 'Not started by this server (another program started it)',
}

export const STATE_NOTE: Record<ProcessMemory['state'], string> = {
  ok: '',
  unsupported: "This operating system isn't supported here, so processes can't be listed.",
  unavailable: "Couldn't read the process list.",
}

export const vramCell = (r: ProcessRow) => (r.vram_bytes == null ? "Windows doesn't say" : formatDecimalBytes(r.vram_bytes))

export function summaryLine(d: ProcessMemory): string {
  const n = d.processes.length
  const ram = d.processes.reduce((sum, r) => sum + r.ram_bytes, 0)
  const left = d.processes.filter((r) => r.relation === 'orphan').length
  const base = `${n} ${n === 1 ? 'process' : 'processes'} · ${formatDecimalBytes(ram)} RAM`
  return left > 0 ? `${base} · ${left} not started by this server` : base
}

/** Graphics memory in use that no listed process accounts for, said plainly. */
export function gpuGapNote(d: ProcessMemory): string {
  const used = d.gpu.state === 'ok' ? d.gpu.used_bytes : null
  if (used == null) return ''
  if (d.vram_attributed_bytes == null) {
    return `The GPU has ${formatDecimalBytes(used)} in use, but this PC doesn't report it per process.`
  }
  const gap = used - d.vram_attributed_bytes
  return gap > 1_000_000_000
    ? `The GPU has ${formatDecimalBytes(used)} in use; ${formatDecimalBytes(gap)} of it is not tied to any listed process (other programs, or memory the driver doesn't attribute).`
    : ''
}
