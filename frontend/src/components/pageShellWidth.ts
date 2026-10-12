// Class-name helper for PageShell (kept out of the component file so React fast refresh stays happy).
export type PageWidth = 'narrow' | 'standard' | 'wide'

export const PAGE_WIDTHS: readonly PageWidth[] = ['narrow', 'standard', 'wide']

// The one place a width preset becomes a class; the pixel caps live in index.css (.page-shell-*).
export function pageShellClass(width: PageWidth, extra?: string): string {
  return ['page-shell', `page-shell-${width}`, extra ?? ''].filter(Boolean).join(' ')
}
