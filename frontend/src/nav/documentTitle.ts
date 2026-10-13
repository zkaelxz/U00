import { useEffect, type RefObject } from 'react'

export const APP_NAME = 'Baihe Studio'

/** "Settings · Baihe Studio": the page first, so a narrow tab strip still says which page it is. */
export function formatDocumentTitle(heading: string | null | undefined): string {
  const h = (heading ?? '').replace(/\s+/g, ' ').trim()
  return h && h !== APP_NAME ? `${h} · ${APP_NAME}` : APP_NAME
}

/**
 * Keeps document.title on the page's own h1. Read from the DOM rather than passed in because pages
 * load their title (a title's name, a series) after they mount, and each page already renders it.
 */
export function useDocumentTitle(mainRef: RefObject<HTMLElement | null>, routeKey: string) {
  useEffect(() => {
    const main = mainRef.current
    if (!main) return
    const apply = () => {
      const h1 = main.querySelector('h1')?.cloneNode(true) as HTMLElement | undefined
      // A page's help (i) tip sits inside its h1, and its text is not part of the page's name.
      h1?.querySelectorAll('.field-help').forEach((el) => el.remove())
      document.title = formatDocumentTitle(h1?.textContent)
    }
    apply()
    const observer = new MutationObserver(apply)
    observer.observe(main, { childList: true, subtree: true, characterData: true })
    return () => observer.disconnect()
  }, [mainRef, routeKey])
}
