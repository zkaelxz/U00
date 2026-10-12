// The Benchmark Lab tabs: one per section. "#/benchmark?tab=runs" opens a tab; a Model health
// compare link (?compare=) opens Run a benchmark, where the prefilled engines are.
import type { BenchSectionId } from './benchmarkHelp'

export type BenchmarkTab = BenchSectionId

export const BENCHMARK_TABS: { id: BenchmarkTab; label: string }[] = [
  { id: 'sets', label: 'Golden sets' },
  { id: 'run', label: 'Run a benchmark' },
  { id: 'reeval', label: 'Model re-evaluation' },
  { id: 'runs', label: 'Recent runs' },
]

export const isBenchmarkTab = (v: unknown): v is BenchmarkTab => BENCHMARK_TABS.some((t) => t.id === v)
