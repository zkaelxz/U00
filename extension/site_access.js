// site_access.js -- the one place that decides which origins the extension may
// ask the person to grant. Loaded by background.js (importScripts) and by the
// popup (a script tag), so the two cannot drift apart.

// The page chooses these URLs, so it must not be able to aim the person's browser
// at their own machine or network (including the app on 8600 and the bridge).
// This is a name/literal-address check only: a public DNS name that resolves to a
// private address (rebinding) cannot be excluded from inside an extension, so the
// lists below are a cheap guard for the common cases. Single-label names ("nas",
// "camera") and the usual home/office suffixes resolve through the LAN's own DNS,
// so they are refused the same way as addresses.
const WILDCARD_DNS_SUFFIXES = [".nip.io", ".sslip.io", ".localtest.me"];
const LAN_SUFFIXES = [".lan", ".internal", ".intranet", ".corp", ".home.arpa", ".localdomain"];

function isPrivateHost(hostname) {
  // "localhost." is the same host as "localhost".
  const host = hostname.replace(/^\[|\]$/g, "").toLowerCase().replace(/\.$/, "");
  if (/^(?:.*\.)?local(?:host)?$/.test(host)) return true;
  if (WILDCARD_DNS_SUFFIXES.some((suffix) => host.endsWith(suffix))) return true;
  if (LAN_SUFFIXES.some((suffix) => host.endsWith(suffix))) return true;
  if (host.includes(":")) return true;     // IPv6 literals: loopback, link-local, ULA, ::ffff: mapped
  if (!host.includes(".")) return true;    // single-label LAN names: http://nas, http://router
  const m = host.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (!m) return false;
  const [a, b] = [Number(m[1]), Number(m[2])];
  return a === 0 || a === 10 || a === 127 || a >= 224 || (a === 169 && b === 254)
    || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)
    || (a === 100 && b >= 64 && b <= 127) || (a === 198 && (b === 18 || b === 19));
}

// The permission pattern is built only from a validated scheme, host and port:
// URL parsing accepts "*" in a host, which would turn the request into a
// wildcard grant. IP literals are refused so only named hosts can be granted.
// Returns null for anything that is not a public web origin.
function permissionTarget(parsed) {
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  if (isPrivateHost(parsed.hostname)) return null;
  const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
  if (!/^[a-z0-9.-]+$/.test(host) || /^\d+(\.\d+)*$/.test(host) || /(^|\.)\.|^\.|\.\./.test(host)) return null;
  const origin = `${parsed.protocol}//${host}${parsed.port ? `:${parsed.port}` : ""}`;
  return { origin, pattern: `${origin}/*` };
}

// Origins reach the popup from the page, so it validates them with the same rules.
function sitePattern(origin) {
  try {
    const target = permissionTarget(new URL(origin));
    return target && target.pattern;
  } catch (e) {
    return null;
  }
}
