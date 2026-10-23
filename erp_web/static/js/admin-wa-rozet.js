(function () {
  var el = document.getElementById("wa-saglik-rozet");
  if (!el) return;
  var sinif = {
    yesil: "hub-wa-rozet yesil",
    sari: "hub-wa-rozet sari",
    kirmizi: "hub-wa-rozet kirmizi",
    bilinmiyor: "hub-wa-rozet gri"
  };
  var yazi = {
    yesil: "hazır",
    sari: "uyarı",
    kirmizi: "ulaşılamıyor",
    bilinmiyor: "bilinmiyor"
  };

  function boya(seviye) {
    var s = sinif[seviye] ? seviye : "bilinmiyor";
    el.className = sinif[s];
    el.textContent = yazi[s];
  }

  var ctrl = new AbortController();
  var zaman = setTimeout(function () { ctrl.abort(); }, 3000);
  fetch("/whatsapp/api/saglik-rozet", { credentials: "same-origin", signal: ctrl.signal })
    .then(function (r) {
      if (!r.ok) throw new Error("yanit");
      return r.json();
    })
    .then(function (j) { boya(j && j.seviye); })
    .catch(function () { boya("bilinmiyor"); })
    .then(function () { clearTimeout(zaman); });
})();
