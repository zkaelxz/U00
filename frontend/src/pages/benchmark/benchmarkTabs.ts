// The Benchmark Lab tabs: one per section. "#/benchmark?tab=runs" opens a tab; a Model health
// compare link (?compare=) opens Run a benchmark, where the prefilled engines are.
import type { BenchSectionId } from './benchmarkHelp'

export type BenchmarkTab = BenchSectionId

export const BENCHMARK_TABS: { id: BenchmarkTab; label: string; short?: string }[] = [
  { id: 'sets', label: 'Golden sets' },
  { id: 'run', label: 'Run a benchmark', short: 'Run' },
  { id: 'reeval', label: 'Model re-evaluation', short: 'Re-evaluate' },
  { id: 'runs', label: 'Recent runs', short: 'Recent' },
]

export const isBenchmarkTab = (v: unknown): v is BenchmarkTab => BENCHMARK_TABS.some((t) => t.id === v)
