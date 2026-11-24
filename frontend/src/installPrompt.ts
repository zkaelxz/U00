/** Owns keeping Chrome's automatic install pop-up away from first-time mobile visitors. */

/*
 * preventDefault on beforeinstallprompt stops Chrome's own mini-infobar; "Install app" in the browser
 * menu stays, so installing (and Share to Baihe) is still there for anyone who looks for it.
 */
export function suppressInstallPrompt(target: Pick<Window, 'addEventListener'> = window): void {
  target.addEventListener('beforeinstallprompt', (e) => e.preventDefault())
}
