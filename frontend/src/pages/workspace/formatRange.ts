// Range and default hints for numeric fields. "0.1-2.0 s" reads clearly where "0.1-2" read as 0.1 to 0.2;
// both ends share the same number of decimals and always have a leading zero.
const places = (n: number) => {
  const s = String(n)
  const i = s.indexOf('.')
  return i < 0 ? 0 : s.length - i - 1
}

const withUnit = (text: string, unit?: string) => (unit ? `${text} ${unit}` : text)

/** "0.1-2.0 s", "100-3000 ms", "1-10". */
export function formatRange(min: number, max: number, unit?: string): string {
  const p = Math.max(places(min), places(max))
  return withUnit(`${min.toFixed(p)}-${max.toFixed(p)}`, unit)
}

/** "default 0.35 s", "default 5". */
export function formatDefault(value: number, unit?: string): string {
  return `default ${withUnit(value.toFixed(places(value)), unit)}`
}

/** "0.1-2.0 s, default 0.35 s." (the default is left out when there is none). */
export function rangeHint(min: number, max: number, opts: { unit?: string; default?: number } = {}): string {
  const base = formatRange(min, max, opts.unit)
  return opts.default === undefined ? `${base}.` : `${base}, ${formatDefault(opts.default, opts.unit)}.`
}
