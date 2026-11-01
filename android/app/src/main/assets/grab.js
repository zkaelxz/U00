// Runs inside the comic page. __NONCE__ is a fresh secret per grab: the page's own scripts
// cannot see it, so they cannot feed images to the app's bridge.
(async function () {
  var nonce = "__NONCE__";
  var MIN_SIDE = 200;
  var seen = {};
  var imgs = Array.prototype.slice.call(document.images).filter(function (img) {
    return img.naturalWidth >= MIN_SIDE && img.naturalHeight >= MIN_SIDE;
  });
  function first(selector, attr) {
    var el = document.querySelector(selector);
    return el ? (attr ? el.getAttribute(attr) : el.textContent) || "" : "";
  }
  var titles = [document.title, first('meta[property="og:title"]', "content"), first("h1")]
    .map(function (t) { return t.replace(/\s+/g, " ").trim(); })
    .filter(function (t) { return t; });
  Baihe.begin(nonce, JSON.stringify({ titles: titles, pageUrl: location.href, count: imgs.length }));

  function toBase64(blob) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result).split(",")[1] || ""); };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }
  async function bytesOf(img) {
    var src = img.currentSrc || img.src;
    try {
      var response = await fetch(src, { credentials: "include" });
      if (response.ok) {
        var blob = await response.blob();
        return { type: blob.type || "image/jpeg", data: await toBase64(blob) };
      }
    } catch (e) { /* cross-origin or blocked: fall back to the pixels already on screen */ }
    var canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext("2d").drawImage(img, 0, 0);
    var url = canvas.toDataURL("image/png");
    return { type: "image/png", data: url.split(",")[1] || "" };
  }

  for (var i = 0; i < imgs.length; i++) {
    var img = imgs[i];
    var src = img.currentSrc || img.src;
    if (seen[src]) continue;
    seen[src] = true;
    try {
      var got = await bytesOf(img);
      Baihe.image(nonce, "p" + i, src.indexOf("http") === 0 ? src : "", got.type, got.data);
    } catch (e) {
      Baihe.skipped(nonce, "p" + i);
    }
  }
  Baihe.done(nonce);
})();
