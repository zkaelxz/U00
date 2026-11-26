// "42 s", "7 min", "2 h 5 min": how long a finished job ran.
export function activityDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} h ${rest} min` : `${hours} h`
}

// GPU hours with one decimal, "0 h" for an idle day.
export function gpuHours(seconds: number): string {
  if (seconds <= 0) return '0 h'
  const hours = seconds / 3600
  return hours < 0.1 ? '<0.1 h' : `${hours.toFixed(1)} h`
}

// Bar heights as 0..1 of the busiest day; an all-idle range stays flat.
export function barFractions(seconds: number[]): number[] {
  const top = Math.max(0, ...seconds)
  return seconds.map((s) => (top > 0 ? s / top : 0))
}
