// Class-name helper for PageShell (kept out of the component file so React fast refresh stays happy).
export type PageShellWidth = 'narrow' | 'standard' | 'wide'

export const PAGE_WIDTHS: readonly PageShellWidth[] = ['narrow', 'standard', 'wide']

// The one place a width preset becomes a class; the pixel caps live in index.css (.page-shell-*).
export function pageShellClass(width: PageShellWidth, extra?: string): string {
  return ['page-shell', `page-shell-${width}`, extra ?? ''].filter(Boolean).join(' ')
}
