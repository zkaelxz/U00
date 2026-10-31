// Runs before the first paint, from index.html's <head>, so the page never
// flashes the wrong theme, width, text size or spacing. A file rather than
// an inline script so the household listener's Content-Security-Policy can
// limit scripts to the app's own files (api/security_headers.py). Keys and
// values match src/theme.ts and src/display.ts; keep them in step.
try {
  var t = localStorage.getItem('baihe.theme')
  if (t === 'light' || t === 'dark' || t === 'sepia' || t === 'oled') document.documentElement.setAttribute('data-theme', t)
  var w = localStorage.getItem('baihe.width')
  if (w === 'wide' || w === 'full') document.documentElement.setAttribute('data-width', w)
  var z = localStorage.getItem('baihe.textSize')
  if (z === 'small' || z === 'large' || z === 'larger') document.documentElement.setAttribute('data-text', z)
  if (localStorage.getItem('baihe.density') === 'compact') document.documentElement.setAttribute('data-density', 'compact')
  if (localStorage.getItem('baihe.style') === 'soft') document.documentElement.setAttribute('data-style', 'soft')
} catch (e) {}
