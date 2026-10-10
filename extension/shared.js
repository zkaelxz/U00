// shared.js -- small helpers the popup and options pages both need, so their
// status lines and error wording cannot drift apart. Loaded by a script tag
// before the page's own script. It holds no token and makes no requests.

const GENERIC_FAILURE = "That didn't work.";

function pluralize(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

// Every worker reply is { ok, data } or { ok: false, error }; a missing reply
// (worker asleep, message dropped) counts as a failure too.
function isFailure(result) {
  return !result?.ok;
}

function failureMessage(result, fallback = GENERIC_FAILURE) {
  return result?.error || fallback;
}

function showStatus(el, message, bad = false) {
  el.textContent = message;
  el.classList.toggle("bad", Boolean(bad));
}
