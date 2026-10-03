/* ERP Asistan — genel hatırlatma kartı. randevu-hatirlatma.js'e bağlı değildir. */
(function () {
  var POLL_MS = 30000;
  var STORAGE_KEY = "bestoffice_erp_asistan_v1";
  var acik = false;
  var sonMusteri = null;
  var panelSekme = "acik";
  var panelNotlar = [];
  var KOLON_VARSAYILAN = ["musteri", "kategori", "gorusme", "hatirlatma", "gorusen", "gorusulen", "aciklama", "durum"];
  var KOLON_ETIKET = {
    musteri: "Müşteri",
    kategori: "Kategori",
    gorusme: "Görüşme Tarihi",
    hatirlatma: "Hatırlatma Tarihi",
    gorusen: "Görüşen Kişi",
    gorusulen: "Görüşülen Kişi",
    aciklama: "Açıklama",
    durum: "Durum"
  };
  var kolonSirasi = KOLON_VARSAYILAN.slice();
  var KOLON_OZET_VARSAYILAN = ["musteri", "gorusme", "hatirlatma", "aciklama", "durum", "gorusen", "gorusulen"];
  var KOLON_OZET_ETIKET = {
    musteri: "Müşteri",
    gorusme: "Son Görüşme Tarihi",
    hatirlatma: "Son Hatırlatma Tarihi",
    aciklama: "Son Açıklama",
    durum: "Durum",
    gorusen: "Görüşen Kişi",
    gorusulen: "Görüşülen Kişi"
  };
  var ozetKolonSirasi = KOLON_OZET_VARSAYILAN.slice();
  var ozetKapsam = "acik";
  var ozetSatirlar = [];
  var ozetKuruldu = false;
  var ozetArama = null;
  var ozetGecmisId = null;
  var ozetGecmisNotlar = [];
  var ozetGecmisSekme = "acik";

  function loadShown() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      var arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (e) {
      return [];
    }
  }

  function saveShown(arr) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(arr.slice(-200)));
    } catch (e) {}
  }

  function anahtar(n) {
    return String(n.id) + "|" + String(n.hatirlatma_zamani || "");
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function stilEkle() {
    if (document.getElementById("erp-asistan-stil")) return;
    var st = document.createElement("style");
    st.id = "erp-asistan-stil";
    st.textContent =
      ".erp-asistan-perde{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:12000;display:flex;align-items:center;justify-content:center;padding:16px;}" +
      ".erp-asistan-kart{background:#0f2537;color:#e0f7fa;border:1px solid #1e3a50;border-radius:10px;max-width:520px;width:100%;padding:16px;box-shadow:0 8px 28px rgba(0,0,0,.35);}" +
      ".erp-asistan-kart h3{margin:0 0 8px;font-size:16px;}" +
      ".erp-asistan-kart p{margin:0 0 8px;font-size:14px;line-height:1.4;}" +
      ".erp-asistan-aksiyon{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;}" +
      ".erp-asistan-aksiyon button{cursor:pointer;border-radius:6px;border:1px solid #1e3a50;background:#1565c0;color:#fff;padding:6px 10px;}" +
      ".erp-asistan-alan{display:flex;flex-direction:column;gap:4px;margin-bottom:8px;font-size:13px;}" +
      ".erp-asistan-alan input,.erp-asistan-alan select,.erp-asistan-alan textarea{background:#0a1929;color:#e0f7fa;border:1px solid #1e3a50;border-radius:6px;padding:6px;}" +
      ".erp-asistan-gecmis-kutu{margin-top:8px;font-size:13px;}" +
      "#erp-asistan-yeni,#erp-asistan-sekme-acik,#erp-asistan-sekme-gecmis{margin:0 6px 8px 0;cursor:pointer;border-radius:6px;border:1px solid #1e3a50;background:#1565c0;color:#fff;padding:6px 10px;}" +
      "#erp-asistan-sekme-acik,#erp-asistan-sekme-gecmis{background:#0a1929;}" +
      "#erp-asistan-sekme-acik.aktif,#erp-asistan-sekme-gecmis.aktif{background:#1565c0;}" +
      "#erp_asistan_gecmis{display:none;margin:8px 0 12px;}" +
      "#erp-asistan-ozet{padding:8px 4px 28px;}" +
      ".ea-ozet-arac{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 10px;}" +
      ".ea-ozet-arac input{background:#0a1929;color:#e0f7fa;border:1px solid #1e3a50;border-radius:6px;padding:6px 8px;min-width:220px;}" +
      ".ea-ozet-arac button{cursor:pointer;border-radius:6px;border:1px solid #1e3a50;background:#0a1929;color:#e0f7fa;padding:6px 10px;}" +
      ".ea-ozet-arac button.aktif{background:#1565c0;border-color:#1565c0;}" +
      "#erp-asistan-ozet-gecmis .erp-asistan-kart{max-width:980px;max-height:86vh;overflow:auto;}" +
      "#erp_asistan_gecmis.acik{display:block;}" +
      ".erp-asistan-rozet{display:inline-block;background:#8d2a2a;color:#fff;border-radius:8px;padding:1px 6px;margin-left:6px;font-size:11px;}" +
      ".ea-tablo-kaydir{overflow-x:auto;margin-top:8px;}" +
      ".ea-tablo{width:100%;min-width:920px;border-collapse:collapse;font-size:13px;}" +
      ".ea-tablo th,.ea-tablo td{padding:6px 8px;border-bottom:1px solid #1e3a50;text-align:left;vertical-align:top;}" +
      ".ea-tablo th{font-size:12px;white-space:nowrap;}" +
      ".ea-tablo td:nth-child(3),.ea-tablo td:nth-child(4){white-space:nowrap;}" +
      ".ea-tablo tbody tr{cursor:pointer;}" +
      ".ea-tablo tbody tr:hover{background:#13293b;}" +
      ".ea-tablo tr.ea-not-soluk{opacity:.55;}" +
      ".ea-tablo tr.ea-not-vurgu{background:#1b3a4b;}" +
      ".ea-tablo a{color:#80deea;}" +
      ".ea-tablo th[data-kolon]{cursor:grab;user-select:none;}" +
      ".ea-tablo th.ea-surukleniyor{opacity:.45;cursor:grabbing;}" +
      ".ea-tablo th.ea-birak-sol{box-shadow:inset 3px 0 0 #4fc3f7;}" +
      ".ea-tablo th.ea-birak-sag{box-shadow:inset -3px 0 0 #4fc3f7;}" +
      ".ea-durum-ac{background:transparent;border:0;color:#4fc3f7;cursor:pointer;padding:0;font:inherit;text-decoration:underline;}" +
      ".ea-durum-menu{position:fixed;z-index:13000;background:#0f2537;border:1px solid #1e3a50;border-radius:6px;padding:4px;min-width:128px;box-shadow:0 6px 18px rgba(0,0,0,.35);}" +
      ".ea-durum-sec{display:block;width:100%;text-align:left;background:transparent;color:#e0f7fa;border:0;cursor:pointer;padding:6px 8px;border-radius:4px;}" +
      ".ea-durum-sec:hover{background:#1565c0;}" +
      ".ea-not-meta{font-size:11px;opacity:.7;margin-bottom:4px;}" +
      ".erp-asistan-sabit{opacity:.85;margin:0 0 6px;font-size:13px;}";
    document.head.appendChild(st);
  }

  function detayUrl(n) {
    if (n && n.detay_url) return n.detay_url;
    if (n && n.iliski_tip === "musteri" && n.iliski_id) {
      return "/giris/?mid=" + encodeURIComponent(n.iliski_id) + "&tab=sozlesmeler";
    }
    if (n && n.id) return "/erp-notlar/?vurgu=" + encodeURIComponent(n.id);
    return "/erp-notlar/?durum=bekliyor";
  }

  var sesCtx = null;

  function sesHazirla() {
    if (sesCtx) return;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try { sesCtx = new AC(); } catch (e) { sesCtx = null; }
  }

  document.addEventListener("pointerdown", function () {
    sesHazirla();
    if (sesCtx && sesCtx.state === "suspended") {
      sesCtx.resume().catch(function () {});
    }
  });

  function sesCal() {
    try {
      sesHazirla();
      if (!sesCtx || sesCtx.state !== "running") return;
      var o = sesCtx.createOscillator();
      var g = sesCtx.createGain();
      o.type = "sine";
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, sesCtx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.05, sesCtx.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, sesCtx.currentTime + 0.16);
      o.connect(g);
      g.connect(sesCtx.destination);
      o.start();
      o.stop(sesCtx.currentTime + 0.18);
    } catch (e) {}
  }

  function rozetGuncelle(adet) {
    var el = document.getElementById("erp-asistan-zil-adet");
    if (!el) return;
    var n = parseInt(adet, 10) || 0;
    if (n > 0) {
      el.textContent = n > 99 ? "99+" : String(n);
      el.style.display = "inline-flex";
    } else {
      el.style.display = "none";
    }
  }

  function isaretle(n) {
    var arr = loadShown();
    var k = anahtar(n);
    if (arr.indexOf(k) < 0) arr.push(k);
    saveShown(arr);
  }

  function kapatKart() {
    var el = document.getElementById("erp-asistan-popup");
    if (el) el.remove();
    acik = false;
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().then(function (j) {
        j._status = r.status;
        return j;
      });
    });
  }

  function masaustuBildir(n) {
    try {
      if (!document.hidden) return;
      if (!window.Notification || Notification.permission !== "granted") return;
      var baslik = n.kategori || "ERP Asistan";
      var notif = new Notification(baslik, { body: n.not_metni || "" });
      notif.onclick = function () {
        try { window.focus(); } catch (e) {}
        try { notif.close(); } catch (e2) {}
      };
    } catch (e) {}
  }

  function bildirimIzniBagla() {
    var btn = document.getElementById("erp-asistan-bildirim-izin");
    if (!btn) return;
    if (!window.Notification || Notification.permission !== "default") {
      btn.style.display = "none";
      return;
    }
    btn.onclick = function () {
      try {
        var p = Notification.requestPermission();
        if (p && typeof p.then === "function") {
          p.then(function () {
            if (Notification.permission !== "default") btn.style.display = "none";
          }).catch(function () {});
        } else if (Notification.permission !== "default") {
          btn.style.display = "none";
        }
      } catch (e) {}
    };
  }

  function kartGoster(n) {
    if (acik) return;
    stilEkle();
    acik = true;
    isaretle(n);
    sesCal();
    masaustuBildir(n);
    var perde = document.createElement("div");
    perde.id = "erp-asistan-popup";
    perde.className = "erp-asistan-perde";
    var rozet = n.whatsapp_rozet === "gönderilemedi"
      ? '<span class="erp-asistan-rozet" title="' + esc(n.whatsapp_hata) + '">gönderilemedi</span>'
      : "";
    perde.innerHTML =
      '<div class="erp-asistan-kart" role="dialog" aria-label="ERP Asistan hatırlatma">' +
      "<h3>ERP Asistan " + rozet + "</h3>" +
      "<p><strong>" + esc(n.kategori) + "</strong> · " + esc(n.hatirlatma_etiket) + "</p>" +
      (n.iliski_etiket ? "<p>" + esc(n.iliski_etiket) + "</p>" : "") +
      "<p>" + esc(n.not_metni) + "</p>" +
      '<div class="erp-asistan-aksiyon">' +
      '<button type="button" data-act="tamam">Tamamlandı</button>' +
      '<button type="button" data-act="baska">Başka Ekle</button>' +
      '<button type="button" data-act="5">Ertele 5 dk</button>' +
      '<button type="button" data-act="15">Ertele 15 dk</button>' +
      '<button type="button" data-act="60">Ertele 1 sa</button>' +
      '<button type="button" data-act="yarin">Yarın</button>' +
      '<button type="button" data-act="detay">Detaya Git</button>' +
      '<button type="button" data-act="kapat">Kapat</button>' +
      "</div></div>";
    perde.addEventListener("click", function (ev) {
      var btn = ev.target.closest("button");
      if (!btn) return;
      var act = btn.getAttribute("data-act");
      if (!act) return;
      if (act === "kapat") {
        kapatKart();
        return;
      }
      if (act === "detay") {
        window.location.href = detayUrl(n);
        return;
      }
      if (act === "baska") {
        baskaFormGoster(n);
        return;
      }
      if (act === "baska-vazgec") {
        var eskiForm = document.getElementById("erp-asistan-baska");
        if (eskiForm) eskiForm.remove();
        return;
      }
      if (act === "tamam") {
        postJson("/erp-notlar/api/" + n.id + "/tamamla", {}).then(function () {
          kapatKart();
        });
        return;
      }
      postJson("/erp-notlar/api/" + n.id + "/ertele", { sure: act }).then(function () {
        kapatKart();
      });
    });
    document.body.appendChild(perde);
  }

  function tara() {
    fetch("/erp-notlar/api/bekleyenler", { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) return null;
        return r.json();
      })
      .then(function (j) {
        if (!j || !j.ok || !j.notlar) return;
        rozetGuncelle(j.notlar.length);
        if (acik) return;
        var shown = loadShown();
        for (var i = 0; i < j.notlar.length; i++) {
          var n = j.notlar[i];
          if (shown.indexOf(anahtar(n)) >= 0) continue;
          kartGoster(n);
          break;
        }
      })
      .catch(function () {});
  }

  function musteriId() {
    try {
      if (typeof cariEkstreMusteriIdSec === "function") {
        var id = parseInt(cariEkstreMusteriIdSec(), 10);
        if (!isNaN(id) && id > 0) return id;
      }
    } catch (e) {}
    return null;
  }

  function panelAcikMi() {
    var kutu = document.getElementById("erp_asistan_gecmis");
    return !!(kutu && kutu.classList.contains("acik"));
  }

  function kolonNormalize(raw, ekran) {
    var kume = ekran === "ozet" ? KOLON_OZET_VARSAYILAN : KOLON_VARSAYILAN;
    if (!Array.isArray(raw) || raw.length !== kume.length) return kume.slice();
    var kopya = raw.map(function (x) { return String(x); });
    var a = kopya.slice().sort().join("|");
    var b = kume.slice().sort().join("|");
    return a === b ? kopya : kume.slice();
  }

  if (window.ERP_ASISTAN_KOLONLAR) kolonSirasi = kolonNormalize(window.ERP_ASISTAN_KOLONLAR);

  function hucreHtml(n, key) {
    if (key === "musteri") {
      if (!n.iliski_etiket) return "-";
      return n.detay_url
        ? '<a href="' + esc(n.detay_url) + '">' + esc(n.iliski_etiket) + "</a>"
        : esc(n.iliski_etiket);
    }
    if (key === "kategori") return esc(n.kategori);
    if (key === "gorusme") return esc(n.created_etiket);
    if (key === "hatirlatma") return esc(n.hatirlatma_etiket);
    if (key === "gorusen") return esc(String(n.olusturan_ad || "").trim() || "-");
    if (key === "gorusulen") return esc(String(n.gorusulen_kisi || "").trim() || "-");
    if (key === "aciklama") return esc(n.not_metni);
    if (key === "durum") {
      if (n.durum === "bekliyor" || n.durum === "ertelendi") {
        return '<button type="button" class="ea-durum-ac" data-not-id="' + esc(n.id) + '">' + esc(n.durum) + "</button>";
      }
      return esc(n.durum);
    }
    return "";
  }

  function satirHtml(n, ekran) {
    var soluk = n.durum === "tamamlandi" ? " ea-not-soluk" : "";
    var sira = ekran === "ozet" ? ozetKolonSirasi : kolonSirasi;
    var ozet = ekran === "ozet"
      ? ' data-ozet="1" data-musteri-id="' + esc(n.musteri_id || n.iliski_id) + '"'
      : "";
    var html = '<tr class="ea-satir' + soluk + '" data-not-id="' + esc(n.id) + '"' + ozet + ">";
    sira.forEach(function (key) {
      html += '<td' + (key === "durum" ? ' class="ea-durum"' : "") + ">" + hucreHtml(n, key) + "</td>";
    });
    return html + "</tr>";
  }

  function tabloHtml(notlar, ekran) {
    var sira = ekran === "ozet" ? ozetKolonSirasi : kolonSirasi;
    var etiket = ekran === "ozet" ? KOLON_OZET_ETIKET : KOLON_ETIKET;
    var html = '<div class="ea-tablo-kaydir"><table class="ea-tablo" data-ekran="' + (ekran === "ozet" ? "ozet" : "not") + '"><thead><tr>';
    sira.forEach(function (key) {
      html += '<th data-kolon="' + esc(key) + '" draggable="true" title="Kolonu sürükleyerek taşı">' +
        esc(etiket[key] || key) + "</th>";
    });
    html += "</tr></thead><tbody>";
    (notlar || []).forEach(function (n) { html += satirHtml(n, ekran); });
    return html + "</tbody></table></div>";
  }

  var suruklenenKolon = "";
  var suruklenenEkran = "not";

  function kolonYeniSira(sira, tasinan, hedef, once) {
    var yeni = sira.slice();
    var from = yeni.indexOf(tasinan);
    var to = yeni.indexOf(hedef);
    if (from < 0 || to < 0 || tasinan === hedef) return yeni;
    yeni.splice(from, 1);
    if (from < to) to--;
    yeni.splice(once ? to : to + 1, 0, tasinan);
    return yeni;
  }

  function kolonBaslik(el) {
    var node = el && el.nodeType === 1 ? el : (el && el.parentElement);
    var th = node && node.closest && node.closest("th[data-kolon]");
    if (!th || !th.closest("table.ea-tablo")) return null;
    return th;
  }

  function kolonOpaklik(key, deger) {
    var tablolar = document.querySelectorAll("table.ea-tablo");
    for (var t = 0; t < tablolar.length; t++) {
      var head = tablolar[t].tHead && tablolar[t].tHead.rows[0];
      if (!head) continue;
      var idx = -1;
      for (var i = 0; i < head.cells.length; i++) {
        if (head.cells[i].getAttribute("data-kolon") === key) idx = i;
      }
      if (idx < 0) continue;
      for (var r = 0; r < tablolar[t].rows.length; r++) {
        var cell = tablolar[t].rows[r].cells[idx];
        if (cell) cell.style.opacity = deger;
      }
    }
  }

  function kolonVurguTemizle() {
    var isaret = document.querySelectorAll(".ea-surukleniyor,.ea-birak-sol,.ea-birak-sag");
    for (var i = 0; i < isaret.length; i++) {
      isaret[i].classList.remove("ea-surukleniyor", "ea-birak-sol", "ea-birak-sag");
    }
    kolonOpaklik(suruklenenKolon, "");
  }

  function tabloKolonSirala(table, yeni) {
    var head = table.tHead && table.tHead.rows[0];
    if (!head) return;
    var simdiki = [];
    for (var i = 0; i < head.cells.length; i++) simdiki.push(head.cells[i].getAttribute("data-kolon"));
    for (var r = 0; r < table.rows.length; r++) {
      var byKey = {};
      for (var c = 0; c < simdiki.length; c++) byKey[simdiki[c]] = table.rows[r].cells[c];
      yeni.forEach(function (key) {
        if (byKey[key]) table.rows[r].appendChild(byKey[key]);
      });
    }
  }

  function kolonKaydet(yeni, ekran) {
    var ozet = ekran === "ozet";
    var simdiki = ozet ? ozetKolonSirasi : kolonSirasi;
    if (yeni.join("|") === simdiki.join("|")) return;
    postJson("/erp-notlar/api/kolonlar", { siralama: yeni, ekran: ozet ? "ozet" : "not" }).then(function (res) {
      if (!res || !res.ok || !res.siralama) {
        alert((res && res.mesaj) || "Sıra kaydedilemedi");
        return;
      }
      var sira = kolonNormalize(res.siralama, ozet ? "ozet" : "not");
      if (ozet) {
        ozetKolonSirasi = sira;
        if (document.getElementById("erp-asistan-ozet-tablo")) ozetCiz();
        return;
      }
      kolonSirasi = sira;
      if (panelAcikMi()) {
        gecmisCiz(panelNotlar);
        return;
      }
      if (document.getElementById("erp-asistan-ozet-gecmis-liste")) ozetGecmisCiz();
      var tablolar = document.querySelectorAll('table.ea-tablo[data-ekran="not"], table.ea-tablo:not([data-ekran])');
      for (var t = 0; t < tablolar.length; t++) {
        if (tablolar[t].getAttribute("data-ekran") === "ozet") continue;
        if (tablolar[t].closest("#erp-asistan-ozet-gecmis")) continue;
        tabloKolonSirala(tablolar[t], kolonSirasi);
      }
    });
  }

  function yerelInput(iso) {
    var s = String(iso || "");
    return s.length >= 16 ? s.slice(0, 16) : "";
  }

  function sekmeNotlari(notlar) {
    return (notlar || []).filter(function (n) {
      if (panelSekme === "gecmis") return n.durum === "tamamlandi";
      return n.durum === "bekliyor" || n.durum === "ertelendi";
    });
  }

  function sekmeIsaretle() {
    var acikBtn = document.getElementById("erp-asistan-sekme-acik");
    var gecmisBtn = document.getElementById("erp-asistan-sekme-gecmis");
    if (acikBtn) acikBtn.classList.toggle("aktif", panelSekme !== "gecmis");
    if (gecmisBtn) gecmisBtn.classList.toggle("aktif", panelSekme === "gecmis");
  }

  function gecmisCiz(notlar) {
    var liste = document.getElementById("erp_asistan_gecmis_liste");
    if (!liste) return;
    sekmeIsaretle();
    var gorunen = sekmeNotlari(notlar);
    if (!gorunen.length) {
      liste.innerHTML = panelSekme === "gecmis"
        ? "<div>Tamamlanan not yok.</div>"
        : "<div>Açık not yok.</div>";
      return;
    }
    var baslik = panelSekme === "gecmis" ? "Geçmiş notlar" : "Açık notlar";
    var html = "<strong>" + baslik + "</strong>" + tabloHtml(gorunen);
    html += '<a href="/erp-notlar/" style="color:#80deea;">Tüm notlar</a>';
    liste.innerHTML = html;
  }

  function gecmisYukle() {
    if (!panelAcikMi()) return;
    var liste = document.getElementById("erp_asistan_gecmis_liste");
    if (!liste) return;
    var id = musteriId();
    if (!id) {
      liste.innerHTML = "<div>Müşteri seçilince notlar burada listelenir.</div>";
      return;
    }
    fetch("/erp-notlar/api/liste?iliski_tip=musteri&iliski_id=" + id, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (j && j.ok) {
          panelNotlar = j.notlar || [];
          gecmisCiz(panelNotlar);
        }
      })
      .catch(function () {});
  }

  function yerelSaat(date) {
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return date.getFullYear() + "-" + p(date.getMonth() + 1) + "-" + p(date.getDate()) +
      "T" + p(date.getHours()) + ":" + p(date.getMinutes());
  }

  function formAc() {
    var mid = musteriId();
    if (!mid) {
      alert("ERP Asistan için önce müşteri seçin.");
      return;
    }
    stilEkle();
    var eski = document.getElementById("erp-asistan-form");
    if (eski) eski.remove();
    var perde = document.createElement("div");
    perde.id = "erp-asistan-form";
    perde.className = "erp-asistan-perde";
    perde.innerHTML =
      '<form class="erp-asistan-kart" id="erp-asistan-form-ic">' +
      "<h3>ERP Asistan</h3>" +
      '<div class="erp-asistan-alan"><span id="erp-asistan-musteri">Müşteri yükleniyor…</span></div>' +
      '<label class="erp-asistan-alan">Kategori<select name="kategori" id="erp-asistan-kategori"></select></label>' +
      '<label class="erp-asistan-alan">Açıklama<textarea name="not_metni" required rows="3"></textarea></label>' +
      '<label class="erp-asistan-alan">Görüşülen Kişi<input name="gorusulen_kisi" maxlength="200"></label>' +
      '<label class="erp-asistan-alan">Hatırlatma Tarihi<input type="datetime-local" name="hatirlatma_zamani" required></label>' +
      '<label class="erp-asistan-alan">Görünürlük<select name="gorunurluk" id="erp-asistan-gorunurluk">' +
      '<option value="kisisel">Kişisel</option><option value="ekip">Ekip</option></select></label>' +
      '<div id="erp-asistan-ekip" style="display:none;">' +
      '<label class="erp-asistan-alan">Kullanıcılar<select name="kullanici_ids" id="erp-asistan-kisiler" multiple size="5"></select></label>' +
      '<label class="erp-asistan-alan">Departman<select name="departman" id="erp-asistan-departman"><option value="">—</option></select></label>' +
      "</div>" +
      '<label class="erp-asistan-alan"><span><input type="checkbox" id="erp-asistan-wa"> WhatsApp ile de gönder</span></label>' +
      '<div id="erp-asistan-wa-alan" style="display:none;">' +
      '<label class="erp-asistan-alan">Telefon<input name="whatsapp_telefon" id="erp-asistan-tel"></label>' +
      '<label class="erp-asistan-alan">Mesaj (boşsa not metni gider)<textarea name="whatsapp_mesaj" rows="2"></textarea></label>' +
      "</div>" +
      '<div class="erp-asistan-aksiyon">' +
      '<button type="submit">Kaydet</button>' +
      '<button type="button" id="erp-asistan-form-kapat">Vazgeç</button>' +
      '<a href="/erp-notlar/" style="color:#80deea;align-self:center;">Tüm notlar</a>' +
      "</div></form>";
    document.body.appendChild(perde);
    var form = document.getElementById("erp-asistan-form-ic");
    form.hatirlatma_zamani.value = yerelSaat(new Date(Date.now() + 60 * 60 * 1000));
    document.getElementById("erp-asistan-form-kapat").onclick = function () { perde.remove(); };
    document.getElementById("erp-asistan-gorunurluk").onchange = function () {
      document.getElementById("erp-asistan-ekip").style.display = this.value === "ekip" ? "block" : "none";
    };
    document.getElementById("erp-asistan-wa").onchange = function () {
      document.getElementById("erp-asistan-wa-alan").style.display = this.checked ? "block" : "none";
    };
    fetch("/erp-notlar/api/musteri/" + mid, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || !j.ok) return;
        document.getElementById("erp-asistan-musteri").textContent = "Müşteri: " + (j.ad || ("#" + mid));
        var tel = document.getElementById("erp-asistan-tel");
        if (tel && !tel.value) tel.value = j.telefon || "";
      })
      .catch(function () {});
    fetch("/erp-notlar/api/meta", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || !j.ok) return;
        var kat = document.getElementById("erp-asistan-kategori");
        (j.kategoriler || []).forEach(function (k) {
          var o = document.createElement("option");
          o.value = k;
          o.textContent = k;
          kat.appendChild(o);
        });
        var kis = document.getElementById("erp-asistan-kisiler");
        (j.kisiler || []).forEach(function (k) {
          var o = document.createElement("option");
          o.value = k.id;
          o.textContent = k.ad + (k.departman ? " (" + k.departman + ")" : "");
          kis.appendChild(o);
        });
        var dep = document.getElementById("erp-asistan-departman");
        (j.departmanlar || []).forEach(function (d) {
          var o = document.createElement("option");
          o.value = d;
          o.textContent = d;
          dep.appendChild(o);
        });
      })
      .catch(function () {});
    form.onsubmit = function (ev) {
      ev.preventDefault();
      var fd = new FormData(form);
      var ids = [];
      var sel = document.getElementById("erp-asistan-kisiler");
      for (var i = 0; i < sel.options.length; i++) {
        if (sel.options[i].selected) ids.push(parseInt(sel.options[i].value, 10));
      }
      var payload = {
        kategori: fd.get("kategori"),
        not_metni: fd.get("not_metni"),
        gorusulen_kisi: fd.get("gorusulen_kisi") || "",
        hatirlatma_zamani: fd.get("hatirlatma_zamani"),
        gorunurluk: fd.get("gorunurluk"),
        departman: fd.get("gorunurluk") === "ekip" ? fd.get("departman") : "",
        kullanici_ids: fd.get("gorunurluk") === "ekip" ? ids : [],
        iliski_tip: "musteri",
        iliski_id: mid,
        whatsapp_gonderilsin: document.getElementById("erp-asistan-wa").checked,
        whatsapp_telefon: fd.get("whatsapp_telefon") || "",
        whatsapp_mesaj: fd.get("whatsapp_mesaj") || "",
      };
      postJson("/erp-notlar/api/olustur", payload).then(function (j) {
        if (!j || !j.ok) {
          alert((j && j.mesaj) || "Kaydedilemedi");
          return;
        }
        perde.remove();
        gecmisYukle();
      });
    };
  }

  function panelIskelet() {
    var kutu = document.getElementById("erp_asistan_gecmis");
    if (!kutu || document.getElementById("erp-asistan-yeni")) return;
    kutu.innerHTML =
      '<div class="erp-asistan-gecmis-kutu">' +
      '<button type="button" id="erp-asistan-yeni">Yeni Ekle</button>' +
      '<button type="button" id="erp-asistan-sekme-acik" class="aktif">Açık</button>' +
      '<button type="button" id="erp-asistan-sekme-gecmis">Geçmiş</button>' +
      '<div id="erp_asistan_gecmis_liste"></div></div>';
    document.getElementById("erp-asistan-yeni").onclick = function () { formAc(); };
    document.getElementById("erp-asistan-sekme-acik").onclick = function () {
      panelSekme = "acik";
      gecmisCiz(panelNotlar);
    };
    document.getElementById("erp-asistan-sekme-gecmis").onclick = function () {
      panelSekme = "gecmis";
      gecmisCiz(panelNotlar);
    };
  }

  function panelToggle() {
    var kutu = document.getElementById("erp_asistan_gecmis");
    if (!kutu) return;
    if (panelAcikMi()) {
      kutu.classList.remove("acik");
      return;
    }
    if (!musteriId()) {
      alert("ERP Asistan için önce müşteri seçin.");
      return;
    }
    panelIskelet();
    kutu.classList.add("acik");
    gecmisYukle();
  }

  function baskaFormGoster(n) {
    var eski = document.getElementById("erp-asistan-baska");
    if (eski) {
      eski.remove();
      return;
    }
    var aksiyon = document.querySelector("#erp-asistan-popup .erp-asistan-aksiyon");
    if (!aksiyon) return;
    var form = document.createElement("form");
    form.id = "erp-asistan-baska";
    form.innerHTML =
      '<label class="erp-asistan-alan">Açıklama<textarea name="not_metni" required rows="2"></textarea></label>' +
      '<label class="erp-asistan-alan">Görüşülen Kişi<input name="gorusulen_kisi" maxlength="200"></label>' +
      '<label class="erp-asistan-alan">Hatırlatma Tarihi<input type="datetime-local" name="hatirlatma_zamani" required></label>' +
      '<div class="erp-asistan-aksiyon">' +
      '<button type="submit">Kaydet</button>' +
      '<button type="button" data-act="baska-vazgec">Vazgeç</button></div>' +
      '<p id="erp-asistan-baska-durum" style="display:none;"></p>';
    var saat = form.querySelector('input[name="hatirlatma_zamani"]');
    saat.value = yerelSaat(new Date(Date.now() + 60 * 60 * 1000));
    aksiyon.parentNode.insertBefore(form, aksiyon);
    form.onsubmit = function (ev) {
      ev.preventDefault();
      var metin = String(form.not_metni.value || "").trim();
      var zaman = form.hatirlatma_zamani.value;
      var gorusulen = String(form.gorusulen_kisi.value || "").trim();
      if (!metin || !zaman) return;
      if (new Date(zaman).getTime() <= Date.now()) {
        alert("İleri bir tarih ve saat seçin.");
        return;
      }
      postJson("/erp-notlar/api/olustur", {
        kategori: n.kategori || "Genel Not",
        not_metni: metin,
        gorusulen_kisi: gorusulen,
        hatirlatma_zamani: zaman,
        gorunurluk: "kisisel",
        iliski_tip: n.iliski_tip || "",
        iliski_id: n.iliski_id || null
      }).then(function (j) {
        if (!j || !j.ok) {
          alert((j && j.mesaj) || "Kaydedilemedi");
          return;
        }
        form.remove();
        var bilgi = document.createElement("p");
        bilgi.textContent = "Yeni hatırlatma kaydedildi. Bu not duruyor.";
        aksiyon.parentNode.insertBefore(bilgi, aksiyon);
        if (panelAcikMi()) gecmisYukle();
      });
    };
  }

  function detayKapat() {
    var eski = document.getElementById("erp-asistan-detay");
    if (eski) eski.remove();
  }

  function detayCiz(n) {
    detayKapat();
    stilEkle();
    var gor = n.gorunurluk === "ekip" ? "Ekip" : "Kişisel";
    if (n.departman) gor += " · " + n.departman;
    var wa = "";
    if (n.whatsapp_gonderilsin) {
      wa = '<p class="ea-not-meta">WhatsApp: ' + esc(n.whatsapp_rozet || "gönderilecek") +
        (n.whatsapp_telefon ? " · " + esc(n.whatsapp_telefon) : "") +
        (n.whatsapp_mesaj ? " · " + esc(n.whatsapp_mesaj) : "") +
        (n.whatsapp_hata ? " · " + esc(n.whatsapp_hata) : "") + "</p>";
    }
    var perde = document.createElement("div");
    perde.id = "erp-asistan-detay";
    perde.className = "erp-asistan-perde";
    perde.innerHTML =
      '<form class="erp-asistan-kart" id="erp-asistan-detay-form">' +
      "<h3>Not detayı</h3>" +
      '<p class="ea-not-meta">' + esc(n.kategori) + " · " + esc(n.durum) + " · " + esc(gor) + "</p>" +
      (n.iliski_etiket ? '<p class="erp-asistan-sabit">' + esc(n.iliski_etiket) + "</p>" : "") +
      '<p class="erp-asistan-sabit">Görüşme Tarihi: ' + esc(n.created_etiket || "-") + "</p>" +
      '<p class="erp-asistan-sabit">Görüşen Kişi: ' + esc(n.olusturan_ad || "-") + "</p>" +
      '<label class="erp-asistan-alan">Hatırlatma Tarihi<input type="datetime-local" name="hatirlatma_zamani" required></label>' +
      '<label class="erp-asistan-alan">Görüşülen Kişi<input name="gorusulen_kisi" maxlength="200"></label>' +
      '<label class="erp-asistan-alan">Açıklama<textarea name="not_metni" required rows="4"></textarea></label>' +
      wa +
      '<div class="erp-asistan-aksiyon">' +
      '<button type="submit">Kaydet</button>' +
      '<button type="button" id="erp-asistan-detay-tamam">Tamamlandı</button>' +
      '<button type="button" id="erp-asistan-detay-kapat">Kapat</button>' +
      "</div></form>";
    document.body.appendChild(perde);
    var form = document.getElementById("erp-asistan-detay-form");
    form.hatirlatma_zamani.value = yerelInput(n.hatirlatma_zamani);
    form.gorusulen_kisi.value = n.gorusulen_kisi || "";
    form.not_metni.value = n.not_metni || "";
    function detayYenile() {
      detayKapat();
      if (document.getElementById("erp-asistan-liste") || document.getElementById("erp-asistan-takvim")) {
        window.location.reload();
        return;
      }
      if (panelAcikMi()) gecmisYukle();
    }
    document.getElementById("erp-asistan-detay-kapat").onclick = function () { detayKapat(); };
    document.getElementById("erp-asistan-detay-tamam").onclick = function () {
      postJson("/erp-notlar/api/" + n.id + "/tamamla", {}).then(function (j) {
        if (!j || !j.ok) {
          alert((j && j.mesaj) || "Tamamlanamadı");
          return;
        }
        detayYenile();
      });
    };
    form.onsubmit = function (ev) {
      ev.preventDefault();
      var metin = String(form.not_metni.value || "").trim();
      var zaman = form.hatirlatma_zamani.value;
      if (!metin || !zaman) {
        alert("Açıklama ve hatırlatma tarihi gerekli.");
        return;
      }
      postJson("/erp-notlar/api/" + n.id + "/guncelle", {
        not_metni: metin,
        gorusulen_kisi: form.gorusulen_kisi.value || "",
        hatirlatma_zamani: zaman
      }).then(function (j) {
        if (!j || !j.ok) {
          alert((j && j.mesaj) || "Kaydedilemedi");
          return;
        }
        detayYenile();
      });
    };
  }

  function detayAc(id) {
    fetch("/erp-notlar/api/" + encodeURIComponent(id), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.ok || !j.not) return;
        detayCiz(j.not);
      })
      .catch(function () {});
  }

  function durumMenuKapat() {
    var menu = document.getElementById("ea-durum-menu");
    if (menu) menu.remove();
  }

  function durumMenuAc(btn) {
    durumMenuKapat();
    var id = btn.getAttribute("data-not-id");
    if (!id) return;
    var menu = document.createElement("div");
    menu.id = "ea-durum-menu";
    menu.className = "ea-durum-menu";
    menu.setAttribute("data-not-id", id);
    menu.innerHTML = '<button type="button" class="ea-durum-sec" data-not-id="' + esc(id) + '">Tamamlandı</button>';
    document.body.appendChild(menu);
    var rect = btn.getBoundingClientRect();
    menu.style.left = Math.max(8, rect.left) + "px";
    menu.style.top = (rect.bottom + 4) + "px";
  }

  function notHemenTamamla(id) {
    durumMenuKapat();
    postJson("/erp-notlar/api/" + encodeURIComponent(id) + "/tamamla", {}).then(function (j) {
      if (!j || !j.ok) {
        alert((j && j.mesaj) || "Tamamlanamadı");
        return;
      }
      for (var i = 0; i < panelNotlar.length; i++) {
        if (String(panelNotlar[i].id) === String(id)) panelNotlar[i].durum = "tamamlandi";
      }
      if (panelAcikMi()) gecmisCiz(panelNotlar);
      ozetDurumIsle(id);
      var hepsi = /(?:^|[?&])durum=hepsi(?:&|$)/.test(location.search);
      var satirlar = document.querySelectorAll("tr.ea-satir");
      for (var s = 0; s < satirlar.length; s++) {
        var tr = satirlar[s];
        if (tr.getAttribute("data-not-id") !== String(id)) continue;
        if (tr.closest("#erp_asistan_gecmis") || tr.closest("#erp-asistan-ozet") || tr.closest("#erp-asistan-ozet-gecmis")) continue;
        if (tr.closest("#erp-asistan-liste") && !hepsi) {
          tr.remove();
          continue;
        }
        tr.classList.add("ea-not-soluk");
        var btn = tr.querySelector(".ea-durum-ac");
        if (btn && btn.parentNode) btn.parentNode.replaceChild(document.createTextNode("tamamlandi"), btn);
      }
    });
  }

  function ozetGorunen() {
    var liste = (ozetSatirlar || []).filter(function (n) {
      if (ozetKapsam === "hepsi") return true;
      return n.durum === "bekliyor" || n.durum === "ertelendi";
    });
    liste.sort(function (a, b) {
      var aa = (a.durum === "bekliyor" || a.durum === "ertelendi") ? 0 : 1;
      var bb = (b.durum === "bekliyor" || b.durum === "ertelendi") ? 0 : 1;
      if (aa !== bb) return aa - bb;
      return String(a.iliski_etiket || "").localeCompare(String(b.iliski_etiket || ""), "tr");
    });
    return liste;
  }

  function ozetCiz() {
    var kutu = document.getElementById("erp-asistan-ozet-tablo");
    if (!kutu) return;
    var liste = ozetGorunen();
    var acikBtn = document.getElementById("erp-asistan-ozet-acik");
    var hepsiBtn = document.getElementById("erp-asistan-ozet-hepsi");
    if (acikBtn) acikBtn.classList.toggle("aktif", ozetKapsam !== "hepsi");
    if (hepsiBtn) hepsiBtn.classList.toggle("aktif", ozetKapsam === "hepsi");
    if (!liste.length) {
      kutu.innerHTML = "<p>Bu filtrede müşteri yok.</p>";
      return;
    }
    kutu.innerHTML = tabloHtml(liste, "ozet");
  }

  function ozetGetir() {
    var kutu = document.getElementById("erp-asistan-ozet-tablo");
    if (!kutu) return;
    var q = "";
    var ara = document.getElementById("erp-asistan-ozet-ara");
    if (ara) q = String(ara.value || "").trim();
    fetch("/erp-notlar/api/ozet?kapsam=hepsi&q=" + encodeURIComponent(q), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.ok) return;
        ozetSatirlar = j.notlar || [];
        ozetCiz();
      })
      .catch(function () {});
  }

  function ozetIskelet() {
    var kok = document.getElementById("erp-asistan-ozet");
    if (!kok || ozetKuruldu) return;
    ozetKuruldu = true;
    kok.innerHTML =
      '<div class="ea-ozet-arac">' +
      '<input id="erp-asistan-ozet-ara" type="search" placeholder="Müşteri ara">' +
      '<button type="button" id="erp-asistan-ozet-acik" class="aktif">İş bitmemiş</button>' +
      '<button type="button" id="erp-asistan-ozet-hepsi">Hepsi</button>' +
      "</div>" +
      '<div id="erp-asistan-ozet-tablo"></div>';
    document.getElementById("erp-asistan-ozet-acik").onclick = function () {
      ozetKapsam = "acik";
      ozetCiz();
    };
    document.getElementById("erp-asistan-ozet-hepsi").onclick = function () {
      ozetKapsam = "hepsi";
      ozetCiz();
    };
    document.getElementById("erp-asistan-ozet-ara").oninput = function () {
      if (ozetArama) clearTimeout(ozetArama);
      ozetArama = setTimeout(ozetGetir, 300);
    };
  }

  function ozetGecmisCiz() {
    var liste = document.getElementById("erp-asistan-ozet-gecmis-liste");
    if (!liste) return;
    var acikBtn = document.getElementById("erp-asistan-ozet-gecmis-acik");
    var gecmisBtn = document.getElementById("erp-asistan-ozet-gecmis-gecmis");
    if (acikBtn) acikBtn.classList.toggle("aktif", ozetGecmisSekme !== "gecmis");
    if (gecmisBtn) gecmisBtn.classList.toggle("aktif", ozetGecmisSekme === "gecmis");
    var gorunen = (ozetGecmisNotlar || []).filter(function (n) {
      if (ozetGecmisSekme === "gecmis") return n.durum === "tamamlandi";
      return n.durum === "bekliyor" || n.durum === "ertelendi";
    });
    if (!gorunen.length) {
      liste.innerHTML = ozetGecmisSekme === "gecmis" ? "<p>Tamamlanan not yok.</p>" : "<p>Açık not yok.</p>";
      return;
    }
    liste.innerHTML = tabloHtml(gorunen, "not");
  }

  function ozetGecmisAc(mid) {
    if (!mid) return;
    ozetGecmisId = String(mid);
    ozetGecmisSekme = "acik";
    stilEkle();
    var eski = document.getElementById("erp-asistan-ozet-gecmis");
    if (eski) eski.remove();
    var perde = document.createElement("div");
    perde.id = "erp-asistan-ozet-gecmis";
    perde.className = "erp-asistan-perde";
    perde.innerHTML =
      '<div class="erp-asistan-kart">' +
      "<h3>Müşteri notları</h3>" +
      '<div class="ea-ozet-arac">' +
      '<button type="button" id="erp-asistan-ozet-gecmis-acik" class="aktif">Açık</button>' +
      '<button type="button" id="erp-asistan-ozet-gecmis-gecmis">Geçmiş</button>' +
      '<button type="button" id="erp-asistan-ozet-gecmis-kapat">Kapat</button>' +
      "</div>" +
      '<div id="erp-asistan-ozet-gecmis-liste"><p>Yükleniyor…</p></div></div>';
    document.body.appendChild(perde);
    document.getElementById("erp-asistan-ozet-gecmis-kapat").onclick = function () { perde.remove(); };
    document.getElementById("erp-asistan-ozet-gecmis-acik").onclick = function () {
      ozetGecmisSekme = "acik";
      ozetGecmisCiz();
    };
    document.getElementById("erp-asistan-ozet-gecmis-gecmis").onclick = function () {
      ozetGecmisSekme = "gecmis";
      ozetGecmisCiz();
    };
    fetch("/erp-notlar/api/musteri/" + encodeURIComponent(mid) + "/notlar", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.ok) return;
        ozetGecmisNotlar = j.notlar || [];
        ozetGecmisCiz();
      })
      .catch(function () {});
  }

  function ozetDurumIsle(id) {
    var degisti = false;
    for (var i = 0; i < ozetSatirlar.length; i++) {
      if (String(ozetSatirlar[i].id) === String(id)) {
        ozetSatirlar[i].durum = "tamamlandi";
        degisti = true;
      }
    }
    if (degisti && document.getElementById("erp-asistan-ozet-tablo")) ozetCiz();
    for (var g = 0; g < ozetGecmisNotlar.length; g++) {
      if (String(ozetGecmisNotlar[g].id) === String(id)) ozetGecmisNotlar[g].durum = "tamamlandi";
    }
    if (document.getElementById("erp-asistan-ozet-gecmis-liste")) ozetGecmisCiz();
  }

  function erpAsistanOzetYukle() {
    ozetIskelet();
    ozetGetir();
  }

  window.erpAsistanAc = panelToggle;
  window.erpAsistanTabloHtml = tabloHtml;
  window.erpAsistanOzetYukle = erpAsistanOzetYukle;

  function basla() {
    stilEkle();
    bildirimIzniBagla();
    var kutu = document.getElementById("erp_asistan_gecmis");
    if (kutu) kutu.classList.remove("acik");
    if (!window.ERP_ASISTAN_KOLONLAR) {
      fetch("/erp-notlar/api/kolonlar", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!j || !j.ok) return;
          kolonSirasi = kolonNormalize(j.siralama, "not");
          if (panelAcikMi()) gecmisCiz(panelNotlar);
        })
        .catch(function () {});
    }
    fetch("/erp-notlar/api/kolonlar?ekran=ozet", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.ok) return;
        ozetKolonSirasi = kolonNormalize(j.siralama, "ozet");
        if (document.getElementById("erp-asistan-ozet-tablo")) ozetCiz();
      })
      .catch(function () {});
    document.addEventListener("dragstart", function (ev) {
      durumMenuKapat();
      var th = kolonBaslik(ev.target);
      if (!th) return;
      suruklenenKolon = th.getAttribute("data-kolon") || "";
      var tablo = th.closest("table.ea-tablo");
      suruklenenEkran = tablo && tablo.getAttribute("data-ekran") === "ozet" ? "ozet" : "not";
      th.classList.add("ea-surukleniyor");
      kolonOpaklik(suruklenenKolon, "0.45");
      try {
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", suruklenenKolon);
      } catch (e) {}
    });
    document.addEventListener("dragover", function (ev) {
      var th = kolonBaslik(ev.target);
      if (!th || !suruklenenKolon) return;
      ev.preventDefault();
      var hedefler = document.querySelectorAll(".ea-birak-sol,.ea-birak-sag");
      for (var i = 0; i < hedefler.length; i++) hedefler[i].classList.remove("ea-birak-sol", "ea-birak-sag");
      if (th.getAttribute("data-kolon") === suruklenenKolon) return;
      var rect = th.getBoundingClientRect();
      var once = (ev.clientX - rect.left) < rect.width / 2;
      th.classList.add(once ? "ea-birak-sol" : "ea-birak-sag");
    });
    document.addEventListener("drop", function (ev) {
      var th = kolonBaslik(ev.target);
      if (!th || !suruklenenKolon) return;
      ev.preventDefault();
      var hedef = th.getAttribute("data-kolon") || "";
      var rect = th.getBoundingClientRect();
      var once = (ev.clientX - rect.left) < rect.width / 2;
      var kaynak = suruklenenEkran === "ozet" ? ozetKolonSirasi : kolonSirasi;
      var yeni = kolonYeniSira(kaynak, suruklenenKolon, hedef, once);
      kolonVurguTemizle();
      suruklenenKolon = "";
      kolonKaydet(yeni, suruklenenEkran);
    });
    document.addEventListener("dragend", function () {
      kolonVurguTemizle();
      suruklenenKolon = "";
    });
    document.addEventListener("click", function (ev) {
      var secim = ev.target.closest && ev.target.closest(".ea-durum-sec");
      if (secim) {
        notHemenTamamla(secim.getAttribute("data-not-id"));
        return;
      }
      var durumBtn = ev.target.closest && ev.target.closest(".ea-durum-ac");
      if (durumBtn) {
        var acikMenu = document.getElementById("ea-durum-menu");
        if (acikMenu && acikMenu.getAttribute("data-not-id") === durumBtn.getAttribute("data-not-id")) durumMenuKapat();
        else durumMenuAc(durumBtn);
        return;
      }
      if (!(ev.target.closest && ev.target.closest(".ea-durum-menu"))) durumMenuKapat();
      if (ev.target.closest && ev.target.closest(".ea-durum")) return;
      var kart = ev.target.closest && ev.target.closest("tr.ea-satir");
      if (!kart || ev.target.closest("a,button,input,textarea,select,label")) return;
      if (kart.getAttribute("data-ozet") === "1") {
        ozetGecmisAc(kart.getAttribute("data-musteri-id"));
        return;
      }
      var id = kart.getAttribute("data-not-id");
      if (id) detayAc(id);
    });
    tara();
    setInterval(tara, POLL_MS);
    setInterval(function () {
      var id = musteriId();
      if (id !== sonMusteri) {
        sonMusteri = id;
        panelSekme = "acik";
        panelNotlar = [];
        gecmisYukle();
      }
    }, 4000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", basla);
  } else {
    basla();
  }
})();
