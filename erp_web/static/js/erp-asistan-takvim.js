/* ERP Asistan takvim. Ay / hafta / gün. Ek kütüphane yok. */
(function () {
  var kok = document.getElementById("erp-asistan-takvim");
  if (!kok) return;

  var RENK = {
    "Müşteri Görüşmesi": "#42a5f5",
    "Ödeme Takibi": "#ffb74d",
    "Sözleşme Yenileme": "#66bb6a",
    "Personel": "#ab47bc",
    "Genel Not": "#90a4ae"
  };
  var GUN = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
  var AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];

  var olcek = "ay";
  var odak = bugun();
  var notlar = [];

  function bugun() {
    var d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }

  function iso(d) {
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  function parse(s) {
    var p = String(s || "").split("-");
    return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  }

  function ekleGun(d, n) {
    var x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    x.setDate(x.getDate() + n);
    return x;
  }

  function haftaBasi(d) {
    var x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var gun = (x.getDay() + 6) % 7;
    x.setDate(x.getDate() - gun);
    return x;
  }

  function aralik() {
    if (olcek === "gun") return { bas: odak, bit: odak };
    if (olcek === "hafta") {
      var b = haftaBasi(odak);
      return { bas: b, bit: ekleGun(b, 6) };
    }
    var ilk = new Date(odak.getFullYear(), odak.getMonth(), 1);
    var son = new Date(odak.getFullYear(), odak.getMonth() + 1, 0);
    return { bas: haftaBasi(ilk), bit: ekleGun(haftaBasi(son), 6) };
  }

  function baslik() {
    if (olcek === "gun") return odak.getDate() + " " + AY[odak.getMonth()] + " " + odak.getFullYear();
    if (olcek === "hafta") {
      var b = haftaBasi(odak);
      var e = ekleGun(b, 6);
      return iso(b) + " – " + iso(e);
    }
    return AY[odak.getMonth()] + " " + odak.getFullYear();
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function renk(k) { return RENK[k] || "#4fc3f7"; }

  function yukle() {
    var a = aralik();
    var url = "/erp-notlar/api/liste?bas=" + iso(a.bas) + "&bitis=" + iso(a.bit);
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        notlar = (j && j.notlar) || [];
        ciz();
      })
      .catch(function () { ciz(); });
  }

  function grup() {
    var m = {};
    notlar.forEach(function (n) {
      var g = n.hatirlatma_gun;
      if (!g) return;
      if (!m[g]) m[g] = [];
      m[g].push(n);
    });
    return m;
  }

  function hucre(d, ayIcinde) {
    var g = iso(d);
    var liste = grup()[g] || [];
    var bugunMu = g === iso(bugun());
    var kat = {};
    liste.forEach(function (n) { kat[n.kategori || ""] = true; });
    var nokta = Object.keys(kat).slice(0, 5).map(function (k) {
      return '<i style="background:' + renk(k) + '"></i>';
    }).join("");
    var soluk = liste.length && liste.every(function (n) { return n.durum === "tamamlandi"; });
    return '<button type="button" class="ea-gun' +
      (ayIcinde ? "" : " ea-dis") +
      (bugunMu ? " ea-bugun" : "") +
      (soluk ? " ea-soluk" : "") +
      '" data-gun="' + g + '">' +
      '<span>' + d.getDate() + '</span>' +
      (liste.length ? '<b>' + liste.length + '</b>' : '<b></b>') +
      '<em>' + nokta + '</em></button>';
  }

  function cizIzgara(bas, adet, ayFiltresi) {
    var html = '<div class="ea-grid">';
    GUN.forEach(function (ad) { html += '<div class="ea-bas">' + ad + '</div>'; });
    for (var i = 0; i < adet; i++) {
      var d = ekleGun(bas, i);
      var icinde = !ayFiltresi || d.getMonth() === odak.getMonth();
      html += hucre(d, icinde);
    }
    html += '</div>';
    return html;
  }

  function gunListe(g) {
    var liste = grup()[g] || [];
    if (!liste.length) return '<p class="ea-bos">Bu günde not yok.</p>';
    var html = '<ul class="ea-liste">';
    liste.forEach(function (n) {
      var soluk = n.durum === "tamamlandi" ? " ea-soluk" : "";
      html += '<li class="' + soluk.trim() + '">' +
        '<i style="background:' + renk(n.kategori) + '"></i>' +
        '<span>Not tarihi: ' + esc(n.created_etiket) + ' · Hatırlatma: ' + esc(n.hatirlatma_etiket) + ' · ' + esc(n.kategori) + '</span>' +
        '<strong>' + esc(n.not_metni) + '</strong>' +
        (n.iliski_etiket ? '<em>' + esc(n.iliski_etiket) + '</em>' : '') +
        (n.detay_url ? ' <a href="' + esc(n.detay_url) + '">Detay</a>' : '') +
        '</li>';
    });
    return html + '</ul>';
  }

  function ciz() {
    var a = aralik();
    var govde = "";
    if (olcek === "ay") govde = cizIzgara(a.bas, 42, true);
    else if (olcek === "hafta") govde = cizIzgara(a.bas, 7, false);
    else govde = gunListe(iso(odak));
    var secim = olcek === "gun" ? "" : '<div id="ea-secim"></div>';
    kok.querySelector(".ea-govde").innerHTML = govde + secim;
    kok.querySelector(".ea-baslik").textContent = baslik();
  }

  kok.addEventListener("click", function (ev) {
    var ol = ev.target.closest("[data-olcek]");
    if (ol) {
      olcek = ol.getAttribute("data-olcek");
      kok.querySelectorAll("[data-olcek]").forEach(function (b) {
        b.classList.toggle("aktif", b === ol);
      });
      yukle();
      return;
    }
    if (ev.target.closest("[data-kaydir]")) {
      var yon = Number(ev.target.closest("[data-kaydir]").getAttribute("data-kaydir"));
      if (olcek === "ay") odak = new Date(odak.getFullYear(), odak.getMonth() + yon, 1);
      else if (olcek === "hafta") odak = ekleGun(odak, yon * 7);
      else odak = ekleGun(odak, yon);
      yukle();
      return;
    }
    var gun = ev.target.closest("[data-gun]");
    if (gun && olcek !== "gun") {
      var kutu = document.getElementById("ea-secim");
      if (kutu) kutu.innerHTML = '<h3>' + gun.getAttribute("data-gun") + '</h3>' + gunListe(gun.getAttribute("data-gun"));
    }
  });

  yukle();
})();
