// The Benchmark Lab sections, listed down the left. "#/benchmark?tab=runs" opens one; a Model health
// compare link (?compare=) opens Run a benchmark, where the prefilled engines are.
import type { BenchSectionId } from './benchmarkHelp'

export type BenchmarkTab = BenchSectionId | 'quality' | 'add'

export const BENCHMARK_TABS: { id: BenchmarkTab; label: string; short?: string; pcOnly?: boolean }[] = [
  { id: 'quality', label: 'Quality' },
  { id: 'sets', label: 'Golden sets', short: 'Sets' },
  // Every part of it is a PC-only form, so another device would get an empty pane.
  { id: 'add', label: 'Add cases', pcOnly: true },
  { id: 'run', label: 'Run a benchmark', short: 'Run' },
  { id: 'sweep', label: 'Transcription sweep', short: 'Sweep' },
  { id: 'tsweep', label: 'Translation sweep' },
  { id: 'reeval', label: 'Model re-evaluation', short: 'Re-evaluate' },
  { id: 'runs', label: 'Recent runs', short: 'Recent' },
]

export const isBenchmarkTab = (v: unknown): v is BenchmarkTab => BENCHMARK_TABS.some((t) => t.id === v)
