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
  var KOLON_OZET_VARSAYILAN = ["musteri", "toplam_borc", "geciken_ay", "gorusme", "hatirlatma", "aciklama", "durum", "whatsapp", "gorusen", "gorusulen"];
  var KOLON_OZET_ETIKET = {
    musteri: "Müşteri",
    toplam_borc: "Toplam borç",
    geciken_ay: "Geciken ay",
    gorusme: "Son Görüşme Tarihi",
    hatirlatma: "Son Hatırlatma Tarihi",
    aciklama: "Son Açıklama",
    durum: "Durum",
    whatsapp: "WhatsApp",
    gorusen: "Görüşen Kişi",
    gorusulen: "Görüşülen Kişi"
  };
  var KOLON_OZET_IPUCU = {
    toplam_borc: "Toplam borç: aylık gridde vadesi gelmiş ödenmemiş kira aylarının toplamı. Ekstredeki ek fatura/tediye/devir dahil değildir.",
    geciken_ay: "Geciken ay: vadesi geçmiş ödenmemiş ay sayısı (dondurulan ve ödenmiş aylar hariç)."
  };
  var KOLON_OZET_GIZLI_VARSAYILAN = ["gorusen", "gorusulen"];
  var ozetKolonSirasi = KOLON_OZET_VARSAYILAN.slice();
  var ozetGizli = KOLON_OZET_GIZLI_VARSAYILAN.slice();
  var ozetTercihElle = false;
  var ozetSiraKey = "";
  var ozetSiraYon = -1;
  var ozetSuruklendi = false;
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
      ".ea-ozet-arac input,.ea-ozet-arac select{background:#0a1929;color:#e0f7fa;border:1px solid #1e3a50;border-radius:6px;padding:6px 8px;}" +
      ".ea-ozet-arac input[type=search]{min-width:220px;}" +
      ".ea-ozet-arac label{display:inline-flex;align-items:center;gap:6px;font-size:13px;}" +
      "#erp-asistan-ozet-ozel{display:inline-flex;gap:6px;align-items:center;}" +
      "#erp-asistan-ozet-ozel[hidden]{display:none;}" +
      ".ea-ozet-uyari{color:#ffab91;font-size:12px;margin:0 0 8px;}" +
      ".ea-ozet-bilgi{font-size:12px;margin:0 0 8px;}" +
      ".ea-ozet-bilgi a{color:#80deea;}" +
      ".ea-ozet-arac button{cursor:pointer;border-radius:6px;border:1px solid #1e3a50;background:#0a1929;color:#e0f7fa;padding:6px 10px;}" +
      ".ea-ozet-arac button.aktif{background:#1565c0;border-color:#1565c0;}" +
      "#erp-asistan-ozet-gecmis .erp-asistan-kart{max-width:980px;max-height:86vh;overflow:auto;}" +
      ".ea-tablo td.ea-gecmis-ac{cursor:pointer;text-decoration:underline;text-underline-offset:2px;}" +
      ".ea-gecmis-kart{max-width:760px;width:100%;max-height:86vh;overflow:auto;box-sizing:border-box;}" +
      ".ea-gecmis-baslik{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;}" +
      ".ea-gecmis-baslik h3{margin:0;word-break:break-word;}" +
      ".ea-gecmis-x{cursor:pointer;border:0;background:transparent;color:#e0f7fa;font-size:22px;line-height:1;padding:0 4px;}" +
      ".ea-gecmis-kayit{border-top:1px solid #1e3a50;padding:8px 0;}" +
      ".ea-gecmis-ust,.ea-gecmis-kisi{display:flex;flex-wrap:wrap;gap:8px 14px;font-size:12px;}" +
      ".ea-gecmis-metin{white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;margin-top:4px;font-size:13px;}" +
      ".ea-gecmis-daha{margin-top:8px;cursor:pointer;border-radius:6px;border:1px solid #1e3a50;background:#0a1929;color:#e0f7fa;padding:6px 10px;}" +
      ".ea-wa{border:0;background:transparent;padding:0;cursor:pointer;line-height:0;}" +
      ".ea-wa[disabled]{cursor:not-allowed;opacity:.35;}" +
      ".ea-wa-kart{max-width:920px;width:min(920px,100%);}" +
      ".ea-wa-metin{width:100%;min-height:120px;box-sizing:border-box;white-space:pre-wrap;}" +
      ".ea-wa-durum{font-size:13px;margin:0 0 8px;color:#80deea;}" +
      ".ea-wa-web{margin:0 0 4px;font-size:13px;}" +
      ".ea-wa-web a{color:#80deea;}" +
      ".ea-wa-web-ipucu{margin:0 0 8px;font-size:12px;color:#b0bec5;}" +
      ".ea-wa-uyandir{margin:6px 0 0;font-size:12px;color:#ffcc80;}" +
      "#erp-asistan-wa-qr-kutu{margin:8px 0;}" +
      "#erp-asistan-wa-qr-kutu button{margin-right:6px;}" +
      "#erp-asistan-wa-qr-yedek{color:#80deea;}" +
      "#erp-asistan-wa-qr-not{margin-left:6px;color:#ffcc80;}" +
      "#erp-asistan-wa-yenile,#erp-asistan-wa-yine{background:#0a1929;color:#e0f7fa;}" +
      "#erp-asistan-wa-gonder:disabled{opacity:.45;cursor:not-allowed;}" +
      ".ea-wa-uyari{color:#ffab91;font-size:12px;}" +
      ".ea-wa-sonuc{font-size:13px;margin-top:8px;}" +
      "#erp-asistan-wa-link-kutu{margin-top:8px;padding:8px;border:1px solid #1e3a50;border-radius:8px;}" +
      "#erp-asistan-wa-link-kutu label{display:flex;gap:6px;align-items:center;margin:4px 0;font-size:13px;flex-wrap:wrap;}" +
      "#erp-asistan-wa-link-uyari,#erp-asistan-wa-link-onay{color:#ffcc80;font-size:13px;}" +
      "#erp-asistan-wa-ozel{max-width:120px;background:#0a1929;color:#e0f7fa;border:1px solid #1e3a50;border-radius:6px;padding:4px;}" +
      ".ea-wa-govde{display:flex;gap:12px;align-items:flex-start;}" +
      ".ea-wa-sol{flex:1;min-width:0;}" +
      ".ea-wa-sag{width:280px;max-height:420px;overflow:auto;border-left:1px solid #1e3a50;padding-left:8px;box-sizing:border-box;}" +
      ".ea-wa-ara{width:100%;box-sizing:border-box;margin:0 0 6px;}" +
      ".ea-wa-grup{font-size:11px;color:#80cbc4;margin:8px 0 4px;}" +
      ".ea-wa-oge{display:block;width:100%;text-align:left;background:transparent;color:#e0f7fa;border:0;border-radius:4px;padding:4px;cursor:pointer;}" +
      ".ea-wa-oge[disabled]{opacity:.45;cursor:not-allowed;}" +
      ".ea-wa-oge.secili{background:#1e3a50;}" +
      ".ea-wa-on{display:block;font-size:11px;color:#b0bec5;white-space:pre-wrap;}" +
      ".ea-wa-rozet{display:inline-block;font-size:10px;color:#ffcc80;margin-left:4px;}" +
      "@media (max-width:720px){.ea-wa-kart{max-width:100%;}.ea-wa-govde{flex-direction:column;}.ea-wa-sag{width:100%;max-height:220px;border-left:0;padding-left:0;border-top:1px solid #1e3a50;}}" +
      "@media (max-width:640px){.erp-asistan-perde{padding:8px;}.ea-gecmis-kart{max-width:100%;padding:10px;}}" +
      "#erp_asistan_gecmis.acik{display:block;}" +
      ".erp-asistan-rozet{display:inline-block;background:#8d2a2a;color:#fff;border-radius:8px;padding:1px 6px;margin-left:6px;font-size:11px;}" +
      ".ea-tablo-kaydir{overflow:auto;max-height:68vh;margin-top:8px;}" +
      ".ea-tablo{width:max-content;min-width:100%;border-collapse:separate;border-spacing:0;font-size:12px;line-height:1.2;}" +
      ".ea-tablo th,.ea-tablo td{padding:2px 6px;border-bottom:1px solid #1e3a50;text-align:left;vertical-align:middle;}" +
      ".ea-tablo th{font-size:11px;white-space:nowrap;position:sticky;top:0;z-index:2;background:#0f2537;}" +
      ".ea-tablo th[data-kolon=musteri],.ea-tablo td[data-kolon=musteri]{position:sticky;left:0;z-index:1;background:#0f2537;}" +
      ".ea-tablo thead th[data-kolon=musteri]{z-index:3;}" +
      ".ea-tablo td[data-kolon=gorusme],.ea-tablo td[data-kolon=hatirlatma],.ea-tablo td[data-kolon=toplam_borc],.ea-tablo td[data-kolon=geciken_ay]{white-space:nowrap;}" +
      ".ea-tablo td[data-kolon=toplam_borc],.ea-tablo td[data-kolon=geciken_ay]{text-align:right;font-variant-numeric:tabular-nums;}" +
      ".ea-kirp{max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" +
      ".ea-sayi-bos{opacity:.35;}" +
      ".ea-sutun-menu{position:absolute;z-index:20;background:#0f2537;border:1px solid #1e3a50;border-radius:6px;padding:6px;min-width:220px;box-shadow:0 6px 18px rgba(0,0,0,.35);}" +
      ".ea-sutun-satir{display:flex;align-items:center;gap:6px;padding:2px 0;font-size:12px;}" +
      ".ea-sutun-satir button{cursor:pointer;border:1px solid #1e3a50;background:#0a1929;color:#e0f7fa;border-radius:4px;padding:0 6px;line-height:1.4;}" +
      "#erp-asistan-ozet{position:relative;}" +
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
    var rozet = n.whatsapp_durum_etiket === "Gönderilemedi"
      ? '<span class="erp-asistan-rozet" title="' + esc(n.whatsapp_uyari || "") + '">Gönderilemedi</span>'
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
    if (document.getElementById("erp-asistan-ozet-tablo")) ozetGetir();
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

  function tercihAnahtar() {
    var id = parseInt(window.ERP_ASISTAN_UID, 10);
    if (!id || id < 0) id = 0;
    return "asistan_sutunlar_" + id;
  }

  function tercihBirlestir(kayit) {
    var bilinen = {};
    KOLON_OZET_VARSAYILAN.forEach(function (k) { bilinen[k] = true; });
    var sira = [];
    var gelen = kayit && Array.isArray(kayit.sira) ? kayit.sira : [];
    gelen.forEach(function (k) {
      k = String(k);
      if (!bilinen[k] || sira.indexOf(k) >= 0) return;
      sira.push(k);
    });
    KOLON_OZET_VARSAYILAN.forEach(function (k, i) {
      if (sira.indexOf(k) >= 0) return;
      var yer = sira.length;
      var bulundu = false;
      var p;
      for (p = i - 1; p >= 0; p--) {
        var once = sira.indexOf(KOLON_OZET_VARSAYILAN[p]);
        if (once >= 0) {
          yer = once + 1;
          bulundu = true;
          break;
        }
      }
      if (!bulundu) {
        var n;
        for (n = i + 1; n < KOLON_OZET_VARSAYILAN.length; n++) {
          var sonra = sira.indexOf(KOLON_OZET_VARSAYILAN[n]);
          if (sonra >= 0) {
            yer = sonra;
            break;
          }
        }
      }
      sira.splice(yer, 0, k);
    });
    var gizli = [];
    if (kayit && Array.isArray(kayit.gizli)) {
      kayit.gizli.forEach(function (k) {
        k = String(k);
        if (bilinen[k] && gizli.indexOf(k) < 0) gizli.push(k);
      });
    } else {
      gizli = KOLON_OZET_GIZLI_VARSAYILAN.slice();
    }
    var gorunen = sira.filter(function (k) { return gizli.indexOf(k) < 0; });
    if (!gorunen.length) gizli = gizli.filter(function (k) { return k !== "musteri"; });
    return { sira: sira, gizli: gizli };
  }

  function ozetTercihOku() {
    try {
      var raw = localStorage.getItem(tercihAnahtar());
      if (!raw) return null;
      var j = JSON.parse(raw);
      if (!j || !Array.isArray(j.sira)) return null;
      return j;
    } catch (e) {
      return null;
    }
  }

  function ozetTercihUygula(kayit) {
    var b = tercihBirlestir(kayit);
    ozetKolonSirasi = b.sira;
    ozetGizli = b.gizli;
  }

  function ozetTercihYaz() {
    ozetTercihElle = true;
    try {
      localStorage.setItem(tercihAnahtar(), JSON.stringify({ sira: ozetKolonSirasi, gizli: ozetGizli }));
    } catch (e) {}
  }

  function ozetGorunenKolonlar() {
    return ozetKolonSirasi.filter(function (k) { return ozetGizli.indexOf(k) < 0; });
  }

  function ozetVarsayilan() {
    ozetKolonSirasi = KOLON_OZET_VARSAYILAN.slice();
    ozetGizli = KOLON_OZET_GIZLI_VARSAYILAN.slice();
    ozetTercihYaz();
    if (document.getElementById("erp-asistan-ozet-tablo")) ozetCiz();
    ozetSutunMenuCiz();
  }

  function ozetSutunTasi(key, yon) {
    var i = ozetKolonSirasi.indexOf(key);
    var j = i + yon;
    if (i < 0 || j < 0 || j >= ozetKolonSirasi.length) return;
    var k = ozetKolonSirasi[j];
    ozetKolonSirasi[j] = ozetKolonSirasi[i];
    ozetKolonSirasi[i] = k;
    ozetTercihYaz();
    if (document.getElementById("erp-asistan-ozet-tablo")) ozetCiz();
    ozetSutunMenuCiz();
  }

  function ozetSutunGizle(key, gizli) {
    var i = ozetGizli.indexOf(key);
    if (gizli) {
      if (i < 0) ozetGizli.push(key);
    } else if (i >= 0) {
      ozetGizli.splice(i, 1);
    }
    if (!ozetGorunenKolonlar().length && key) {
      ozetGizli = ozetGizli.filter(function (k) { return k !== key; });
    }
    ozetTercihYaz();
    if (document.getElementById("erp-asistan-ozet-tablo")) ozetCiz();
  }

  function ozetSutunMenuCiz() {
    var menu = document.getElementById("erp-asistan-sutun-menu");
    if (!menu) return;
    var html = "";
    ozetKolonSirasi.forEach(function (key) {
      var acikKutu = ozetGizli.indexOf(key) < 0;
      html += '<div class="ea-sutun-satir">' +
        '<button type="button" data-sutun-yon="-1" data-sutun="' + esc(key) + '" aria-label="Yukarı">↑</button>' +
        '<button type="button" data-sutun-yon="1" data-sutun="' + esc(key) + '" aria-label="Aşağı">↓</button>' +
        '<label><input type="checkbox" data-sutun-goster="' + esc(key) + '"' + (acikKutu ? " checked" : "") + "> " +
        esc(KOLON_OZET_ETIKET[key] || key) + "</label></div>";
    });
    html += '<div class="ea-sutun-satir"><button type="button" id="erp-asistan-sutun-varsayilan">Varsayılana dön</button></div>';
    menu.innerHTML = html;
  }

  function ozetSutunMenuAc() {
    var menu = document.getElementById("erp-asistan-sutun-menu");
    if (!menu) return;
    menu.hidden = !menu.hidden;
    if (!menu.hidden) ozetSutunMenuCiz();
  }

  function paraMetin(v) {
    var x = Number(v);
    if (!isFinite(x) || x < 0.005) return "";
    return x.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function ayMetin(v) {
    var x = parseInt(v, 10);
    if (!x || x < 0) return "";
    return String(x);
  }

  function gridHesaplanmadi(n) {
    return !!(n && n.grid_yok);
  }

  function hucreHtml(n, key) {
    if (key === "toplam_borc" || key === "geciken_ay") {
      if (gridHesaplanmadi(n)) return "—";
      return esc(key === "toplam_borc" ? paraMetin(n.toplam_borc) : ayMetin(n.geciken_ay));
    }
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
    if (key === "whatsapp") {
      var ikon = '<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path fill="#25D366" d="M12 2a10 10 0 0 0-8.7 15l-1.2 4.3 4.4-1.2A10 10 0 1 0 12 2z"/></svg>';
      if (!n.wa_var) return '<button type="button" class="ea-wa" disabled title="Telefon yok">' + ikon + "</button>";
      return '<button type="button" class="ea-wa" data-wa="1" title="WhatsApp">' + ikon + "</button>";
    }
    if (key === "durum") {
      var temel = (n.durum === "bekliyor" || n.durum === "ertelendi")
        ? '<button type="button" class="ea-durum-ac" data-not-id="' + esc(n.id) + '">' + esc(n.durum) + "</button>"
        : esc(n.durum);
      if (n.whatsapp_gonderilsin && n.whatsapp_durum_etiket) {
        temel += '<div class="ea-not-meta">' + esc(n.whatsapp_durum_etiket) + "</div>";
        if (n.whatsapp_uyari) temel += '<div class="ea-not-meta">' + esc(n.whatsapp_uyari) + "</div>";
      }
      return temel;
    }
    return "";
  }

  function hucreBaslik(n, key) {
    if ((key === "toplam_borc" || key === "geciken_ay") && gridHesaplanmadi(n)) return "hesaplanmadı";
    if (key === "musteri") return String(n.iliski_etiket || "");
    if (key === "aciklama") return String(n.not_metni || "");
    if (key === "gorusen") return String(n.olusturan_ad || "");
    if (key === "gorusulen") return String(n.gorusulen_kisi || "");
    return "";
  }

  function satirHtml(n, ekran) {
    var soluk = n.durum === "tamamlandi" ? " ea-not-soluk" : "";
    var sira = ekran === "ozet" ? ozetGorunenKolonlar() : kolonSirasi;
    var ozet = ekran === "ozet"
      ? ' data-ozet="1" data-musteri-id="' + esc(n.musteri_id || n.iliski_id) + '"'
      : "";
    var html = '<tr class="ea-satir' + soluk + '" data-not-id="' + esc(n.id) + '"' + ozet + ">";
    sira.forEach(function (key) {
      var cls = [];
      if (key === "durum") cls.push("ea-durum");
      if (key === "musteri" || key === "aciklama" || key === "gorusen" || key === "gorusulen") cls.push("ea-kirp");
      if ((key === "toplam_borc" || key === "geciken_ay") && !hucreHtml(n, key)) cls.push("ea-sayi-bos");
      if (ekran === "ozet" && key === "aciklama") cls.push("ea-gecmis-ac");
      var ham = (ekran === "ozet" && key === "aciklama") ? "Tüm geçmişi gör" : hucreBaslik(n, key);
      var title = ham ? ' title="' + esc(ham) + '"' : "";
      html += "<td data-kolon=\"" + esc(key) + "\"" + (cls.length ? ' class="' + cls.join(" ") + '"' : "") + title + ">" + hucreHtml(n, key) + "</td>";
    });
    return html + "</tr>";
  }

  function tabloHtml(notlar, ekran) {
    var ozet = ekran === "ozet";
    var sira = ozet ? ozetGorunenKolonlar() : kolonSirasi;
    var etiket = ozet ? KOLON_OZET_ETIKET : KOLON_ETIKET;
    var html = '<div class="ea-tablo-kaydir"><table class="ea-tablo" data-ekran="' + (ozet ? "ozet" : "not") + '"><thead><tr>';
    sira.forEach(function (key) {
      var ipucu = ozet && KOLON_OZET_IPUCU[key] ? KOLON_OZET_IPUCU[key] : "Kolonu sürükleyerek taşı";
      var ok = "";
      if (ozet && key === ozetSiraKey) ok = ozetSiraYon < 0 ? " ▼" : " ▲";
      html += '<th data-kolon="' + esc(key) + '" draggable="true" title="' + esc(ipucu) + '">' +
        esc(etiket[key] || key) + ok + "</th>";
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
      '<p style="margin:4px 0;font-size:12px;color:#546e7a;">Müşteri numarasına gider. Ekip yalnızca notun kimlere görüneceğini belirler.</p>' +
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
        if (window.WaAlici && j.alicilar) {
          var telEtiket = tel && tel.closest("label");
          if (telEtiket) telEtiket.style.display = "none";
          if (tel) tel.value = "";
          var kutuA = document.createElement("div");
          kutuA.id = "erp-asistan-not-alicilar";
          if (telEtiket && telEtiket.parentNode) telEtiket.parentNode.insertBefore(kutuA, telEtiket);
          window.WaAlici.yaz(kutuA, {
            kartlar: (j.alicilar || []).map(function (a) {
              return { ham: a.gorunen, etiket: a.etiket || "", secili: !!a.secili };
            }),
            ek: true,
            ekId: "erp-asistan-not-ek",
            mod: "secim"
          });
        }
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
      var secimNot = notAliciSec();
      if (secimNot && secimNot.hata) {
        alert(secimNot.hata);
        return;
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
        whatsapp_telefon: (secimNot && secimNot.bir) ? secimNot.bir : (fd.get("whatsapp_telefon") || ""),
        whatsapp_mesaj: fd.get("whatsapp_mesaj") || "",
        whatsapp_serbest: !!(secimNot && secimNot.serbestBir),
      };
      postJson("/erp-notlar/api/olustur", payload).then(function (j) {
        if (!j || !j.ok) {
          alert((j && j.mesaj) || "Kaydedilemedi");
          return;
        }
        function kapat() {
          perde.remove();
          gecmisYukle();
        }
        if (!payload.whatsapp_gonderilsin) {
          kapat();
          return;
        }
        function durumSor() {
        fetch("/erp-notlar/api/whatsapp-durum", { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (st) {
            kapat();
            if (st && st.ok && (st.durum === "bagli_degil" || st.durum === "baglaniyor")) {
              alert("Not kaydedildi; WhatsApp bağlı olmadığı için mesaj henüz gitmedi.");
            }
          })
          .catch(function () { kapat(); });
        }
        if (secimNot && secimNot.ekler && secimNot.ekler.length) {
          notEkGonder(mid, payload.whatsapp_mesaj || payload.not_metni, secimNot.ekler, durumSor);
          return;
        }
        durumSor();
      });
    };
  }

  function notAliciSec() {
    var kutu = document.getElementById("erp-asistan-not-alicilar");
    var wa = document.getElementById("erp-asistan-wa");
    if (!kutu || !window.WaAlici || !wa || !wa.checked) return null;
    var sec = window.WaAlici.secilen(kutu);
    var ekInp = document.getElementById("erp-asistan-not-ek");
    var ek = "";
    if (ekInp && String(ekInp.value || "").trim()) {
      ek = window.WaAlici.norm(ekInp.value);
      if (!ek) return { hata: "Telefon numarası geçersiz" };
    }
    var liste = [];
    sec.forEach(function (s) {
      if (s.tel && liste.indexOf(s.tel) < 0) liste.push(s.tel);
    });
    if (ek && liste.indexOf(ek) < 0) liste.push(ek);
    if (!liste.length) return { hata: "En az bir numara seçin" };
    if (liste.length > 3) return { hata: "En fazla 3 alıcı" };
    return {
      bir: liste[0],
      serbestBir: !!(ek && liste[0] === ek),
      ekler: liste.slice(1).map(function (t) {
        return { tel: t, serbest: t === ek, sira: t === ek ? 3 : 2 };
      })
    };
  }

  function notEkGonder(mid, metin, ekler, bitti) {
    var i = 0;
    var kutu = document.getElementById("erp-asistan-not-alicilar");
    function adim() {
      if (i >= ekler.length) {
        if (bitti) bitti();
        return;
      }
      var oge = ekler[i];
      i += 1;
      var govde = {
        musteri_id: mid,
        telefon: oge.tel,
        mesaj: metin || "",
        buton: "asistan",
        onay: false,
        deneme: waDeneme()
      };
      if (oge.serbest) govde.serbest = true;
      fetch("/giris/api/sozlesme-whatsapp/gonder", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(govde)
      }).then(function (r) {
        return r.json().then(function (j) { return j || {}; }).catch(function () { return {}; });
      }).then(function (j) {
        if (window.WaAlici) window.WaAlici.rozet(kutu, oge.sira, (j && j.durum) || "hata");
        adim();
      }).catch(function () {
        if (window.WaAlici) window.WaAlici.rozet(kutu, oge.sira, "hata");
        adim();
      });
    }
    adim();
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
      wa = '<p class="ea-not-meta">WhatsApp: ' + esc(n.whatsapp_rozet || n.whatsapp_durum_etiket || "") +
        (n.whatsapp_telefon ? " · " + esc(n.whatsapp_telefon) : "") +
        (n.whatsapp_mesaj ? " · " + esc(n.whatsapp_mesaj) : "") + "</p>";
      if (n.whatsapp_uyari) wa += '<p class="ea-not-meta">' + esc(n.whatsapp_uyari) + "</p>";
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
      if (ozetSiraKey === "toplam_borc" || ozetSiraKey === "geciken_ay") {
        var ayok = gridHesaplanmadi(a);
        var byok = gridHesaplanmadi(b);
        if (ayok !== byok) return ayok ? 1 : -1;
        var av = Number(a[ozetSiraKey]) || 0;
        var bv = Number(b[ozetSiraKey]) || 0;
        if (av !== bv) return (av - bv) * ozetSiraYon;
      }
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
    ozetBilgi(liste.length);
    if (!liste.length) {
      kutu.innerHTML = "<p>Bu filtrede müşteri yok.</p>";
      return;
    }
    kutu.innerHTML = tabloHtml(liste, "ozet");
  }

  var TARIH_GORUSME = [
    { v: "hepsi", t: "Hepsi" },
    { v: "bugun", t: "Bugün" },
    { v: "dun", t: "Dün" },
    { v: "bu_hafta", t: "Bu hafta" },
    { v: "bu_ay", t: "Bu ay" },
    { v: "son_7", t: "Son 7 gün" },
    { v: "son_30", t: "Son 30 gün" },
    { v: "ozel", t: "Özel aralık" }
  ];
  var TARIH_HATIRLATMA = [
    { v: "hepsi", t: "Hepsi", ip: "Tümü; hatırlatması olmayan notlar da görünür" },
    { v: "bugun", t: "Bugün", ip: "Bugünün tamamı" },
    { v: "yarin", t: "Yarın", ip: "Yarının tamamı" },
    { v: "sonraki_hafta", t: "Önümüzdeki hafta", ip: "Bir sonraki takvim haftası (gelecek Pazartesi–Pazar)" },
    { v: "sonraki_ay", t: "Önümüzdeki ay", ip: "Bir sonraki takvim ayının tamamı" },
    { v: "sonraki_30", t: "Önümüzdeki 30 gün", ip: "Bugünden itibaren bugün dahil 30 gün" },
    { v: "ozel", t: "Özel aralık", ip: "Başlangıç ve bitiş tarihi seçimi, sınırlar dahil" }
  ];

  function ozetTarihAnahtar() {
    return "asistan_ozet_tarih_" + String(window.ERP_ASISTAN_UID || "0");
  }

  function ozetTarihListe(turu) {
    return turu === "hatirlatma" ? TARIH_HATIRLATMA : TARIH_GORUSME;
  }

  function ozetTarihGecerliSec(turu, sec) {
    var liste = ozetTarihListe(turu);
    for (var i = 0; i < liste.length; i++) {
      if (liste[i].v === sec) return sec;
    }
    return "hepsi";
  }

  function ozetTarihSecenekleri(secim) {
    var tur = document.getElementById("erp-asistan-ozet-tur");
    var tarih = document.getElementById("erp-asistan-ozet-tarih");
    if (!tarih) return;
    var turu = tur && tur.value === "hatirlatma" ? "hatirlatma" : "gorusme";
    var liste = ozetTarihListe(turu);
    var sec = ozetTarihGecerliSec(turu, secim || "hepsi");
    var html = "";
    liste.forEach(function (o) {
      html += '<option value="' + esc(o.v) + '"' + (o.ip ? ' title="' + esc(o.ip) + '"' : "") + ">" + esc(o.t) + "</option>";
    });
    tarih.innerHTML = html;
    tarih.value = sec;
  }

  function ozetTarihKaydet() {
    try {
      sessionStorage.setItem(ozetTarihAnahtar(), JSON.stringify(ozetTarihOku()));
    } catch (e) {}
  }

  function ozetTarihYukle() {
    var tur = document.getElementById("erp-asistan-ozet-tur");
    var bas = document.getElementById("erp-asistan-ozet-bas");
    var bit = document.getElementById("erp-asistan-ozet-bit");
    var kayit = null;
    try {
      kayit = JSON.parse(sessionStorage.getItem(ozetTarihAnahtar()) || "null");
    } catch (e) {
      kayit = null;
    }
    var turu = kayit && kayit.turu === "hatirlatma" ? "hatirlatma" : "gorusme";
    if (tur) tur.value = turu;
    var istenen = kayit ? kayit.sec : "hepsi";
    var sec = ozetTarihGecerliSec(turu, istenen);
    ozetTarihSecenekleri(sec);
    if (sec === "ozel") {
      if (bas) bas.value = (kayit && kayit.bas) || "";
      if (bit) bit.value = (kayit && kayit.bit) || "";
    } else {
      if (bas) bas.value = "";
      if (bit) bit.value = "";
    }
    ozetOzelGoster();
  }

  function ozetTarihOku() {
    var tur = document.getElementById("erp-asistan-ozet-tur");
    var tarih = document.getElementById("erp-asistan-ozet-tarih");
    var bas = document.getElementById("erp-asistan-ozet-bas");
    var bit = document.getElementById("erp-asistan-ozet-bit");
    return {
      turu: tur ? tur.value : "gorusme",
      sec: tarih ? tarih.value : "hepsi",
      bas: bas ? bas.value : "",
      bit: bit ? bit.value : ""
    };
  }

  function ozetOzelGoster() {
    var tarih = document.getElementById("erp-asistan-ozet-tarih");
    var ozel = document.getElementById("erp-asistan-ozet-ozel");
    if (ozel) ozel.hidden = !tarih || tarih.value !== "ozel";
  }

  function ozetTarihGecerli() {
    var p = ozetTarihOku();
    var uyari = document.getElementById("erp-asistan-ozet-uyari");
    var bozuk = p.sec === "ozel" && p.bas && p.bit && p.bas > p.bit;
    if (uyari) {
      uyari.style.display = bozuk ? "" : "none";
      uyari.textContent = bozuk ? "Başlangıç tarihi bitişten sonra olamaz." : "";
    }
    if (bozuk) {
      var bilgi = document.getElementById("erp-asistan-ozet-bilgi");
      if (bilgi) bilgi.innerHTML = "";
      return false;
    }
    return true;
  }

  function ozetTarihSifirla() {
    var tur = document.getElementById("erp-asistan-ozet-tur");
    var tarih = document.getElementById("erp-asistan-ozet-tarih");
    var bas = document.getElementById("erp-asistan-ozet-bas");
    var bit = document.getElementById("erp-asistan-ozet-bit");
    if (tur) tur.value = "gorusme";
    if (tarih) tarih.value = "hepsi";
    if (bas) bas.value = "";
    if (bit) bit.value = "";
    ozetOzelGoster();
    ozetGetir();
  }

  function ozetBilgi(adet) {
    var bilgi = document.getElementById("erp-asistan-ozet-bilgi");
    if (!bilgi) return;
    var p = ozetTarihOku();
    if (p.sec === "hepsi") {
      bilgi.innerHTML = "";
      return;
    }
    bilgi.innerHTML = esc(adet) + ' müşteri listeleniyor · <a href="#" id="erp-asistan-ozet-temizle">Filtreyi temizle</a>';
    var link = document.getElementById("erp-asistan-ozet-temizle");
    if (link) link.onclick = function (ev) {
      ev.preventDefault();
      ozetTarihSifirla();
    };
  }

  function ozetGetir() {
    var kutu = document.getElementById("erp-asistan-ozet-tablo");
    if (!kutu) return;
    if (!ozetTarihGecerli()) return;
    var q = "";
    var ara = document.getElementById("erp-asistan-ozet-ara");
    if (ara) q = String(ara.value || "").trim();
    var p = ozetTarihOku();
    var url = "/erp-notlar/api/ozet?kapsam=hepsi&q=" + encodeURIComponent(q) +
      "&tarih_turu=" + encodeURIComponent(p.turu) +
      "&tarih=" + encodeURIComponent(p.sec);
    if (p.sec === "ozel") {
      url += "&bas=" + encodeURIComponent(p.bas) + "&bitis=" + encodeURIComponent(p.bit);
    }
    ozetTarihKaydet();
    fetch(url, { credentials: "same-origin" })
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
      '<button type="button" id="erp-asistan-sutunlar">Sütunlar</button>' +
      '<label>Tarih türü <select id="erp-asistan-ozet-tur">' +
      '<option value="gorusme">Görüşme Tarihi</option>' +
      '<option value="hatirlatma">Hatırlatma Tarihi</option></select></label>' +
      '<label>Tarih <select id="erp-asistan-ozet-tarih">' +
      '<option value="hepsi">Hepsi</option>' +
      '<option value="bugun">Bugün</option>' +
      '<option value="dun">Dün</option>' +
      '<option value="bu_hafta">Bu hafta</option>' +
      '<option value="bu_ay">Bu ay</option>' +
      '<option value="son_7">Son 7 gün</option>' +
      '<option value="son_30">Son 30 gün</option>' +
      '<option value="ozel">Özel aralık</option></select></label>' +
      '<span id="erp-asistan-ozet-ozel" hidden>' +
      '<input id="erp-asistan-ozet-bas" type="date" aria-label="Başlangıç">' +
      "<span>–</span>" +
      '<input id="erp-asistan-ozet-bit" type="date" aria-label="Bitiş">' +
      "</span></div>" +
      '<p id="erp-asistan-ozet-uyari" class="ea-ozet-uyari" style="display:none"></p>' +
      '<p id="erp-asistan-ozet-bilgi" class="ea-ozet-bilgi"></p>' +
      '<div id="erp-asistan-sutun-menu" class="ea-sutun-menu" hidden></div>' +
      '<div id="erp-asistan-ozet-tablo"></div>';
    document.getElementById("erp-asistan-ozet-acik").onclick = function () {
      ozetKapsam = "acik";
      ozetCiz();
    };
    document.getElementById("erp-asistan-ozet-hepsi").onclick = function () {
      ozetKapsam = "hepsi";
      ozetCiz();
    };
    document.getElementById("erp-asistan-sutunlar").onclick = function (ev) {
      ev.stopPropagation();
      ozetSutunMenuAc();
    };
    document.getElementById("erp-asistan-sutun-menu").onclick = function (ev) {
      ev.stopPropagation();
      var hedef = ev.target;
      if (hedef && hedef.id === "erp-asistan-sutun-varsayilan") {
        ozetVarsayilan();
        return;
      }
      var yonBtn = hedef && hedef.closest && hedef.closest("[data-sutun-yon]");
      if (yonBtn) {
        ozetSutunTasi(yonBtn.getAttribute("data-sutun"), parseInt(yonBtn.getAttribute("data-sutun-yon"), 10) || 0);
        return;
      }
      var kutu = hedef && hedef.getAttribute && hedef.getAttribute("data-sutun-goster");
      if (kutu) ozetSutunGizle(kutu, !hedef.checked);
    };
    document.getElementById("erp-asistan-ozet-ara").oninput = function () {
      if (ozetArama) clearTimeout(ozetArama);
      ozetArama = setTimeout(ozetGetir, 300);
    };
    document.getElementById("erp-asistan-ozet-tur").onchange = function () {
      var tarih = document.getElementById("erp-asistan-ozet-tarih");
      var bas = document.getElementById("erp-asistan-ozet-bas");
      var bit = document.getElementById("erp-asistan-ozet-bit");
      if (tarih) tarih.value = "hepsi";
      if (bas) bas.value = "";
      if (bit) bit.value = "";
      ozetTarihSecenekleri("hepsi");
      ozetOzelGoster();
      ozetGetir();
    };
    document.getElementById("erp-asistan-ozet-tarih").onchange = function () {
      if (document.getElementById("erp-asistan-ozet-tarih").value !== "ozel") {
        var bas = document.getElementById("erp-asistan-ozet-bas");
        var bit = document.getElementById("erp-asistan-ozet-bit");
        if (bas) bas.value = "";
        if (bit) bit.value = "";
      }
      ozetOzelGoster();
      ozetGetir();
    };
    ozetTarihYukle();
    document.getElementById("erp-asistan-ozet-bas").onchange = ozetGetir;
    document.getElementById("erp-asistan-ozet-bit").onchange = ozetGetir;
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

  var waKilit = false;
  var waPoll = null;

  function waPollDur() {
    if (waPoll) {
      clearInterval(waPoll);
      waPoll = null;
    }
  }

  function waDeneme() {
    if (window.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  }

  var waDurumZaman = null;

  function waDurumDur() {
    if (waDurumZaman) {
      clearTimeout(waDurumZaman);
      waDurumZaman = null;
    }
  }

  function waPopKapat() {
    waDurumDur();
    waPollDur();
    var p = document.getElementById("erp-asistan-wa-pop");
    if (p) p.remove();
    waKilit = false;
  }

  function waPopAc(mid, unvan) {
    if (!mid) return;
    waPopKapat();
    stilEkle();
    var perde = document.createElement("div");
    perde.id = "erp-asistan-wa-pop";
    perde.className = "erp-asistan-perde";
    perde.innerHTML =
      '<div class="erp-asistan-kart ea-wa-kart" role="dialog" aria-modal="true">' +
      '<div class="ea-gecmis-baslik"><h3>' + esc(unvan || "Müşteri") + "</h3>" +
      '<button type="button" class="ea-gecmis-x" aria-label="Kapat">×</button></div>' +
      '<div class="ea-wa-govde"><div class="ea-wa-sol">' +
      '<p class="ea-wa-maske"></p>' +
      '<div id="erp-asistan-wa-numaralar"></div>' +
      '<p id="erp-asistan-wa-durum" class="ea-wa-durum">Durum alınıyor…</p>' +
      '<p id="erp-asistan-wa-web" class="ea-wa-web"><a id="erp-asistan-wa-web-a" href="https://web.whatsapp.com/" target="_blank" rel="noopener noreferrer">Kendi WhatsApp Web\'imde aç (ofis hattı olmayabilir)</a></p>' +
      '<p id="erp-asistan-wa-web-ipucu" class="ea-wa-web-ipucu">ERP üzerinden ofis hattıyla göndermek için Giriş → WhatsApp ekranından QR\'ı ofis telefonuyla okutun.</p>' +
      '<label class="erp-asistan-alan">Hazır mesaj <select id="erp-asistan-wa-sec"></select></label>' +
      '<textarea id="erp-asistan-wa-metin" class="ea-wa-metin" maxlength="1000"></textarea>' +
      '<p class="ea-wa-uyari" hidden></p>' +
      '<p id="erp-asistan-wa-gecis" hidden>Düzenlemeniz silinecek ' +
      '<button type="button" id="erp-asistan-wa-gecis-evet">Geç</button> ' +
      '<button type="button" id="erp-asistan-wa-gecis-hayir">Vazgeç</button></p>' +
      '<p class="ea-wa-sonuc"></p>' +
      '<div class="erp-asistan-aksiyon">' +
      '<button type="button" id="erp-asistan-wa-gonder" disabled>Gönder</button>' +
      '<button type="button" id="erp-asistan-wa-yenile" hidden>Durumu yenile</button>' +
      '<button type="button" id="erp-asistan-wa-yine" hidden>Yine de dene</button>' +
      '<button type="button" id="erp-asistan-wa-link">Ödeme linki</button>' +
      '<button type="button" id="erp-asistan-wa-onay" hidden>Onayla</button>' +
      '<button type="button" id="erp-asistan-wa-tekrar" hidden>Evet, yine de gönder</button>' +
      '<button type="button" id="erp-asistan-wa-belirsiz" hidden>Evet, gönder</button>' +
      "</div>" +
      '<p id="erp-asistan-wa-uyandir" class="ea-wa-uyandir" hidden>Oturum uyandırılmaya çalışılır, ~20 sn sürebilir.</p>' +
      '<p id="erp-asistan-wa-qr-kutu" hidden><button type="button" id="erp-asistan-wa-qr-okut">QR Okut</button> ' +
      '<button type="button" id="erp-asistan-wa-qr-kapat">QR Kapat</button> ' +
      '<a id="erp-asistan-wa-qr-yedek" href="/whatsapp/qr-ac" hidden>Açılamadı. QR sayfasını bu sekmede aç</a>' +
      '<span id="erp-asistan-wa-qr-not" hidden>bu sekmeyi kapatabilirsiniz</span></p>' +
      '<div id="erp-asistan-wa-link-kutu" hidden>' +
      '<p>Tutar</p>' +
      '<label><input type="radio" name="ea-wa-tutar" value="toplam"> Toplam borç <span id="erp-asistan-wa-toplam"></span></label>' +
      '<label><input type="radio" name="ea-wa-tutar" value="geciken"> Geciken tutar <span id="erp-asistan-wa-geciken"></span></label>' +
      '<label><input type="radio" name="ea-wa-tutar" value="ozel"> Özel tutar <input id="erp-asistan-wa-ozel" inputmode="decimal"></label>' +
      '<p id="erp-asistan-wa-link-uyari" hidden></p>' +
      '<p id="erp-asistan-wa-link-onay" hidden></p>' +
      '<button type="button" id="erp-asistan-wa-link-ekle">Mesaja ekle</button> ' +
      '<button type="button" id="erp-asistan-wa-link-degistir">Ödeme linki şablonuyla değiştir</button>' +
      '<p id="erp-asistan-wa-link-sonuc"></p></div></div>' +
      '<aside class="ea-wa-sag"><p class="ea-wa-grup">Tüm mesajlar</p>' +
      '<input id="erp-asistan-wa-ara" class="ea-wa-ara" type="search" placeholder="Ara">' +
      '<div id="erp-asistan-wa-liste"></div></aside></div></div>';
    document.body.appendChild(perde);
    perde.querySelector(".ea-gecmis-x").onclick = waPopKapat;
    perde.addEventListener("click", function (ev) { if (ev.target === perde) waPopKapat(); });
    var sec = perde.querySelector("#erp-asistan-wa-sec");
    var kutu = perde.querySelector("#erp-asistan-wa-metin");
    var uyari = perde.querySelector(".ea-wa-uyari");
    var sonuc = perde.querySelector(".ea-wa-sonuc");
    var gonder = perde.querySelector("#erp-asistan-wa-gonder");
    var onay = perde.querySelector("#erp-asistan-wa-onay");
    var liste = perde.querySelector("#erp-asistan-wa-liste");
    var ara = perde.querySelector("#erp-asistan-wa-ara");
    var gecis = perde.querySelector("#erp-asistan-wa-gecis");
    var sablonlar = [];
    var temiz = "";
    var bekleyen = null;
    var deneme = waDeneme();
    var dugmeTut = false;
    var tekrarBtn = perde.querySelector("#erp-asistan-wa-tekrar");
    var belirsizBtn = perde.querySelector("#erp-asistan-wa-belirsiz");
    function secili() {
      for (var i = 0; i < sablonlar.length; i++) if (sablonlar[i].anahtar === sec.value) return sablonlar[i];
      return null;
    }
    function kirli() {
      return String(kutu.value || "") !== String(temiz || "");
    }
    function panelCiz() {
      var q = String(ara.value || "").toLocaleLowerCase("tr");
      var html = "";
      var grup = "";
      sablonlar.forEach(function (s) {
        if (s.anahtar === "ozel") return;
        var ad = (s.etiket || "") + " " + (s.onizleme || "") + " " + (s.ipucu || "") + (s.anahtar === "link" ? "" : (" " + (s.metin || "")));
        if (q && ad.toLocaleLowerCase("tr").indexOf(q) < 0) return;
        if (s.grup !== grup) {
          grup = s.grup;
          html += '<p class="ea-wa-grup">' + esc(s.baslik || "") + "</p>";
        }
        var seciliSinif = sec.value === s.anahtar ? " secili" : "";
        var baslik = s.anahtar === "link"
          ? [s.hover || s.onizleme || "", s.ipucu || ""].filter(Boolean).join(" ")
          : (s.engel ? (s.ipucu || "") : (s.metin || ""));
        html += '<button type="button" class="ea-wa-oge' + seciliSinif + '" data-wa-anahtar="' + esc(s.anahtar) + '"' +
          (s.engel ? " disabled" : "") + ' title="' + esc(baslik) + '">' +
          esc(s.etiket || "") + (s.onerilen ? " (önerilen)" : "") +
          (s.rozet ? '<span class="ea-wa-rozet">' + esc(s.rozet) + "</span>" : "") +
          '<span class="ea-wa-on">' + esc(s.onizleme || "") + "</span></button>";
      });
      liste.innerHTML = html;
    }
    var durumEngel = false;
    var durumBas = Date.now();
    var durumKutu = perde.querySelector("#erp-asistan-wa-durum");
    var webKutu = perde.querySelector("#erp-asistan-wa-web");
    var webIpucu = perde.querySelector("#erp-asistan-wa-web-ipucu");
    var yenile = perde.querySelector("#erp-asistan-wa-yenile");
    var yine = perde.querySelector("#erp-asistan-wa-yine");
    var uyandirNot = perde.querySelector("#erp-asistan-wa-uyandir");
    var qrKutu = perde.querySelector("#erp-asistan-wa-qr-kutu");
    var qrYedek = perde.querySelector("#erp-asistan-wa-qr-yedek");
    var qrNot = perde.querySelector("#erp-asistan-wa-qr-not");
    function qrSayfaAc() {
      var w = null;
      try { w = window.open("/whatsapp/qr-ac", "BestOfficeERP_QR"); } catch (e) { w = null; }
      if (!w) {
        window.__erpQrPencere = null;
        if (qrYedek) qrYedek.hidden = false;
        return false;
      }
      window.__erpQrPencere = w;
      if (qrYedek) qrYedek.hidden = true;
      return true;
    }
    function qrSayfaKapat() {
      var w = window.__erpQrPencere;
      var kapandi = false;
      try {
        if (w && !w.closed) {
          w.close();
          kapandi = !!w.closed;
        }
      } catch (e) { kapandi = false; }
      if (qrNot) qrNot.hidden = kapandi;
      return kapandi;
    }
    if (qrKutu) {
      var qrOkut = qrKutu.querySelector("#erp-asistan-wa-qr-okut");
      var qrKapat = qrKutu.querySelector("#erp-asistan-wa-qr-kapat");
      if (qrOkut) qrOkut.onclick = function () { qrSayfaAc(); };
      if (qrKapat) qrKapat.onclick = function () { qrSayfaKapat(); };
    }
    function durumYaz(j) {
      var d = (j && j.durum) || "bilinmiyor";
      durumEngel = d === "bagli_degil" || d === "qr";
      if (d === "bagli_degil") {
        durumKutu.textContent = "Bağlı değil. WhatsApp bağlı değil, birkaç dakika sonra tekrar dene";
      } else if (d === "qr") {
        durumKutu.textContent = "QR bekliyor. Giriş → WhatsApp ekranından QR'ı ofis telefonuyla okutun.";
      } else {
        durumKutu.textContent = (j && j.etiket) || "Durum alınamadı";
      }
      var gizle = d === "bagli";
      if (webKutu) webKutu.hidden = gizle;
      if (webIpucu) webIpucu.hidden = gizle;
      var yardim = d === "bagli_degil" || d === "qr";
      if (yenile) yenile.hidden = !yardim;
      if (yine) yine.hidden = !yardim;
      if (uyandirNot) uyandirNot.hidden = !yardim;
      if (qrKutu) qrKutu.hidden = !yardim;
      uyar();
    }
    function durumPlanla(d) {
      waDurumDur();
      if ((d === "baglaniyor" || d === "qr") && (Date.now() - durumBas) < 120000) {
        waDurumZaman = setTimeout(durumCek, 10000);
      }
    }
    function durumCek() {
      fetch("/erp-notlar/api/whatsapp-durum", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!document.getElementById("erp-asistan-wa-pop")) return;
          if (!j || j.ok === false || !j.durum) {
            durumYaz({ durum: "bilinmiyor", etiket: "Durum alınamadı" });
            return;
          }
          durumYaz(j);
          durumPlanla(j.durum);
        })
        .catch(function () {
          if (!document.getElementById("erp-asistan-wa-pop")) return;
          durumYaz({ durum: "bilinmiyor", etiket: "Durum alınamadı" });
        });
    }
    function uyar() {
      var s = secili();
      var engel = !!(s && s.engel);
      uyari.hidden = !engel;
      uyari.textContent = engel ? (s.ipucu || "Borç hesaplanmadı") : "";
      var ekDolu = (function () {
        var el = document.getElementById("erp-asistan-wa-ek");
        return !!(el && String(el.value || "").trim());
      })();
      var secimBos = (function () {
        var secim = secimOku();
        return !!(secim && !secim.length && !ekDolu);
      })();
      if (secimBos && !engel) {
        uyari.hidden = false;
        uyari.textContent = "En az bir numara seçin";
      }
      gonder.disabled = dugmeTut || durumEngel || engel || secimBos || !String(kutu.value || "").trim();
      panelCiz();
    }
    function uygula(s) {
      if (!s || s.engel) return;
      sec.value = s.anahtar;
      aktif = s.anahtar;
      kutu.value = s.metin || "";
      temiz = kutu.value;
      bekleyen = null;
      gecis.hidden = true;
      uyar();
    }
    var aktif = "";
    function iste(s) {
      if (!s || s.engel) return;
      if (s.anahtar === aktif && !kirli()) return;
      if (kirli()) {
        bekleyen = s;
        gecis.hidden = false;
        if (sec.value !== aktif) sec.value = aktif;
        return;
      }
      uygula(s);
    }
    sec.onchange = function () { iste(secili()); };
    liste.onclick = function (ev) {
      var btn = ev.target.closest && ev.target.closest(".ea-wa-oge");
      if (!btn || btn.disabled) return;
      var anahtar = btn.getAttribute("data-wa-anahtar");
      var s = null;
      for (var i = 0; i < sablonlar.length; i++) if (sablonlar[i].anahtar === anahtar) s = sablonlar[i];
      iste(s);
    };
    perde.querySelector("#erp-asistan-wa-gecis-evet").onclick = function () {
      if (bekleyen) uygula(bekleyen);
    };
    perde.querySelector("#erp-asistan-wa-gecis-hayir").onclick = function () {
      bekleyen = null;
      gecis.hidden = true;
    };
    ara.oninput = panelCiz;
    kutu.oninput = uyar;
    var linkKutu = perde.querySelector("#erp-asistan-wa-link-kutu");
    var linkUyari = perde.querySelector("#erp-asistan-wa-link-uyari");
    var linkOnay = perde.querySelector("#erp-asistan-wa-link-onay");
    var linkSonuc = perde.querySelector("#erp-asistan-wa-link-sonuc");
    var linkBtn = perde.querySelector("#erp-asistan-wa-link");
    var gridYok = true;
    var toplamBorc = null;
    var gecikenTutar = null;
    var linkKilit = false;
    var linkBekleyen = null;
    function paraYazi(n) {
      if (n == null || n === "") return "—";
      return String(n).replace(".", ",");
    }
    function linkSecim() {
      var seciliTutar = perde.querySelector("input[name=ea-wa-tutar]:checked");
      return seciliTutar ? seciliTutar.value : "";
    }
    function linkPanel() {
      linkKutu.hidden = false;
      perde.querySelector("#erp-asistan-wa-toplam").textContent = gridYok ? "—" : paraYazi(toplamBorc);
      var gecikenVar = !gridYok && Number(gecikenTutar) > 0;
      perde.querySelector("#erp-asistan-wa-geciken").textContent = gecikenVar ? paraYazi(gecikenTutar) : "—";
      perde.querySelectorAll("input[name=ea-wa-tutar]").forEach(function (el) {
        var kilitli = gridYok && el.value !== "ozel";
        if (!gecikenVar && el.value === "geciken") kilitli = true;
        el.disabled = kilitli;
        if (kilitli) el.checked = false;
      });
      if (gridYok) {
        var ozel = perde.querySelector("input[name=ea-wa-tutar][value=ozel]");
        if (ozel) ozel.checked = true;
      }
      linkUyari.hidden = !gridYok;
      linkUyari.textContent = gridYok ? "Borç hesaplanmadı" : "";
    }
    function linkMetniKoy(metin, mod) {
      var ek = String(kutu.value || "").replace(/\s+$/, "");
      var sonraki = mod === "degistir" ? metin : (ek ? (ek + "\n" + metin) : metin);
      if (String(sonraki || "").length > 1000) {
        linkSonuc.textContent = "Mesaj çok uzun";
        return;
      }
      kutu.value = sonraki;
      if (mod === "degistir") {
        sablonlar.forEach(function (s) {
          if (s.anahtar === "link") {
            s.metin = metin;
            s.engel = false;
            s.onizleme = "";
            s.hover = "";
          }
        });
        sec.value = "link";
        aktif = "link";
      }
      temiz = kutu.value;
      uyar();
    }
    function linkGonder(mod, onay, ust) {
      if (linkKilit) return;
      var secim = linkSecim();
      if (!secim) {
        linkUyari.hidden = false;
        linkUyari.textContent = "Tutar seçilmedi";
        return;
      }
      var ozelDeger = String(perde.querySelector("#erp-asistan-wa-ozel").value || "").trim();
      if (secim === "ozel" && !(Number(ozelDeger.replace(",", ".")) > 0)) {
        linkUyari.hidden = false;
        linkUyari.textContent = "Tutar pozitif olmalı";
        return;
      }
      linkKilit = true;
      linkBtn.disabled = true;
      fetch("/erp-notlar/api/musteri/" + encodeURIComponent(mid) + "/odeme-linki", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ secim: secim, tutar: ozelDeger, onay: !!onay, ust_onay: !!ust })
      }).then(function (r) { return r.json().then(function (j) { return { kod: r.status, j: j || {} }; }).catch(function () { return { kod: r.status, j: {} }; }); })
        .then(function (p) {
          linkKilit = false;
          linkBtn.disabled = false;
          var j = p.j || {};
          if (j.onay_gerekli || j.ust_onay) {
            linkOnay.hidden = false;
            linkOnay.textContent = j.mesaj || "";
            linkBekleyen = { mod: mod, onay: j.onay_gerekli ? true : !!onay, ust: !!j.ust_onay };
            return;
          }
          linkOnay.hidden = true;
          linkBekleyen = null;
          if (!j.ok || !j.metin) {
            linkSonuc.textContent = j.mesaj || "Ödeme linki oluşturulamadı";
            return;
          }
          linkMetniKoy(j.metin, mod);
          linkSonuc.textContent = j.mesaj || "";
        })
        .catch(function () {
          linkKilit = false;
          linkBtn.disabled = false;
          linkSonuc.textContent = "Ödeme linki oluşturulamadı";
        });
    }
    linkBtn.onclick = linkPanel;
    perde.querySelector("#erp-asistan-wa-link-ekle").onclick = function () {
      if (linkBekleyen && linkBekleyen.mod === "ekle") {
        var b = linkBekleyen;
        linkBekleyen = null;
        linkOnay.hidden = true;
        linkGonder(b.mod, b.onay, b.ust);
        return;
      }
      linkGonder("ekle", false, false);
    };
    perde.querySelector("#erp-asistan-wa-link-degistir").onclick = function () {
      if (kirli() && !(linkBekleyen && linkBekleyen.mod === "degistir")) {
        linkOnay.hidden = false;
        linkOnay.textContent = "Düzenlemeniz silinecek";
        linkBekleyen = { mod: "degistir", onay: false, ust: false, kirli: true };
        return;
      }
      var b = linkBekleyen && linkBekleyen.mod === "degistir" ? linkBekleyen : { mod: "degistir", onay: false, ust: false };
      linkBekleyen = null;
      linkOnay.hidden = true;
      linkGonder(b.mod, !!b.onay, !!b.ust);
    };
    function numaraSatir(n) {
      var durum = n && n.durum;
      var yazi = durum === "gonderildi" ? "Gönderildi"
        : (durum === "kuyrukta" || durum === "bekliyor" || durum === "gonderiliyor") ? "Kuyruğa alındı"
        : durum === "belirsiz" ? "Sonuç belirsiz, telefondan kontrol edin"
        : durum === "tekrar" ? (n.mesaj || "Bu mesaj bu numaraya az önce gönderildi.")
        : (durum === "hata" || durum === "basarisiz") ? ("Gönderilemedi" + (n.mesaj ? ": " + n.mesaj : ""))
        : (n.mesaj || "Gönderilemedi");
      var maske = n && n.maske ? " " + n.maske : "";
      return (n.sira || 1) + ". numara" + maske + ": " + yazi;
    }
    function listeOzet(liste) {
      var gitti = 0;
      var hata = 0;
      var surer = 0;
      var bel = 0;
      liste.forEach(function (n) {
        if (n.durum === "gonderildi") gitti += 1;
        else if (n.durum === "hata" || n.durum === "basarisiz") hata += 1;
        else if (n.durum === "gonderiliyor" || n.durum === "kuyrukta" || n.durum === "bekliyor") surer += 1;
        else if (n.durum === "belirsiz") bel += 1;
      });
      if (surer) return "Gönderim sürüyor";
      if (liste.length > 1 && gitti && (hata || bel)) return gitti + "/" + liste.length + " gitti";
      if (gitti === liste.length) return "Gönderildi";
      if (bel && !gitti && !hata) return "Sonuç belirsiz, telefondan kontrol edin";
      if (hata) return "Gönderilemedi";
      return "Gönderim sürüyor";
    }
    function sonucCiz(liste) {
      if (!liste || !liste.length) return;
      var bas = listeOzet(liste);
      var detay = liste.map(numaraSatir).join(" · ");
      sonuc.textContent = bas + " — " + detay;
      if (window.WaAlici) {
        var kutuR = document.getElementById("erp-asistan-wa-numaralar");
        liste.forEach(function (n) { window.WaAlici.rozet(kutuR, n.sira, n.durum); });
      }
    }
    function pollBaslat(liste) {
      waPollDur();
      var baslangic = Date.now();
      function acik(n) {
        return n && n.deneme && (n.durum === "gonderiliyor" || n.durum === "kuyrukta" || n.durum === "bekliyor");
      }
      if (!liste.some(acik)) return;
      waPoll = setInterval(function () {
        if (!document.getElementById("erp-asistan-wa-pop")) {
          waPollDur();
          return;
        }
        if (Date.now() - baslangic >= 180000) {
          waPollDur();
          liste.forEach(function (n) {
            if (acik(n)) {
              n.durum = "belirsiz";
              n.mesaj = "Sonuç belirsiz, telefondan kontrol edin";
            }
          });
          sonucCiz(liste);
          dugmeTut = true;
          waKilit = true;
          gonder.disabled = true;
          onay.hidden = true;
          if (tekrarBtn) tekrarBtn.hidden = true;
          if (belirsizBtn) belirsizBtn.hidden = false;
          return;
        }
        liste.forEach(function (n) {
          if (!acik(n)) return;
          fetch("/giris/api/sozlesme-whatsapp/durum?deneme=" + encodeURIComponent(n.deneme), { credentials: "same-origin" })
            .then(function (r) { return r.json().then(function (j) { return j || {}; }).catch(function () { return null; }); })
            .then(function (j) {
              if (!j || !j.durum || j.durum === "gonderiliyor" || j.durum === "kuyrukta" || j.durum === "bekliyor" || j.durum === "yok") return;
              n.durum = j.durum === "basarisiz" ? "hata" : j.durum;
              n.mesaj = j.mesaj || "";
              sonucCiz(liste);
              if (!liste.some(acik)) waPollDur();
            })
            .catch(function () {});
        });
      }, 1500);
    }
    function belirsizYaz() {
      waPollDur();
      dugmeTut = true;
      waKilit = true;
      gonder.disabled = true;
      onay.hidden = true;
      if (tekrarBtn) tekrarBtn.hidden = true;
      if (belirsizBtn) belirsizBtn.hidden = false;
      sonuc.textContent = "Sonuç belirsiz, Tüm mesajlar/telefondan kontrol edin";
    }
    function normTel(raw) {
      var s = String(raw || "").replace(/\D/g, "");
      if (s.indexOf("00") === 0) s = s.slice(2);
      if (s.charAt(0) === "0" && s.length === 11) s = "90" + s.slice(1);
      else if (s.length === 10 && s.charAt(0) === "5") s = "90" + s;
      return /^[1-9]\d{10,14}$/.test(s) ? s : "";
    }
    var ekSira = 3;
    function secimOku() {
      var kutuN = document.getElementById("erp-asistan-wa-numaralar");
      if (!kutuN || !kutuN.querySelector("input[type=checkbox]")) return null;
      var sec = [];
      kutuN.querySelectorAll("input:checked").forEach(function (el) {
        var n = parseInt(el.getAttribute("data-sira"), 10);
        if (n) sec.push(n);
      });
      return sec;
    }
    var sonListe = [];
    function gonderIstek(tekrarOnay, yalniz) {
      var metin = String(kutu.value || "").trim();
      if (!metin) return;
      var secim = yalniz && yalniz.length ? yalniz : secimOku();
      var ekHam = null;
      var elEk = document.getElementById("erp-asistan-wa-ek");
      if (elEk) {
        var hamEk = String(elEk.value || "").trim();
        if (hamEk) {
          if (!normTel(hamEk)) {
            uyari.hidden = false;
            uyari.textContent = "Telefon numarası geçersiz";
            gonder.disabled = false;
            return;
          }
          ekHam = hamEk;
        }
      }
      var ekGitsin = false;
      if (ekHam) {
        ekGitsin = !(yalniz && yalniz.length) || yalniz.indexOf(ekSira) >= 0;
      }
      if (secim && !secim.length && !ekGitsin) {
        uyari.hidden = false;
        uyari.textContent = "En az bir numara seçin";
        gonder.disabled = true;
        return;
      }
      waKilit = true;
      dugmeTut = true;
      gonder.disabled = true;
      onay.hidden = true;
      if (tekrarBtn) tekrarBtn.hidden = true;
      if (belirsizBtn) belirsizBtn.hidden = true;
      fetch("/erp-notlar/api/musteri/" + encodeURIComponent(mid) + "/whatsapp", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify((function () {
          var govde = {
            mesaj: metin,
            onay: true,
            deneme: deneme,
            sablon: sec.value || "ozel",
            tekrar_onay: !!tekrarOnay
          };
          if (secim) govde.secim = secim;
          else if (ekGitsin) govde.secim = [];
          if (ekGitsin) govde.ek_numara = ekHam;
          return govde;
        })())
      }).then(function (r) {
        return r.json().then(function (j) { return { j: j || {} }; }).catch(function () { return { bozuk: true }; });
      }).then(function (p) {
        if (!p || p.bozuk || !p.j) {
          belirsizYaz();
          return;
        }
        var j = p.j;
        if (j.tekrar) {
          sonuc.textContent = j.mesaj || "Aynı mesaj 10 dk içinde gönderildi, yine de gönder?";
          if (tekrarBtn) tekrarBtn.hidden = false;
          return;
        }
        var liste = (j.numaralar && j.numaralar.length) ? j.numaralar : [{
          sira: 1, durum: j.durum, mesaj: j.mesaj, deneme: deneme, ok: !!j.ok
        }];
        if (!j.ok && liste.length < 2) {
          sonuc.textContent = j.mesaj || "Gönderilemedi";
          dugmeTut = false;
          waKilit = false;
          uyar();
          return;
        }
        sonListe = liste;
        sonucCiz(liste);
        if (liste.some(function (n) { return n.durum === "tekrar"; }) && tekrarBtn) tekrarBtn.hidden = false;
        var bekleyen = liste.some(function (n) {
          return n.durum === "gonderiliyor" || n.durum === "kuyrukta" || n.durum === "bekliyor";
        });
        if (!bekleyen && !liste.some(function (n) { return n.durum === "tekrar"; })) {
          dugmeTut = false;
          waKilit = false;
          uyar();
        }
        pollBaslat(liste);
      }).catch(function () {
        belirsizYaz();
      });
    }
    function gonderOnayIste() {
      if (waKilit) return;
      var s = secili();
      if (s && s.engel) return;
      if (!String(kutu.value || "").trim()) return;
      var ad = (perde.querySelector("h3").textContent || "Müşteri");
      sonuc.textContent = ad + " müşteriye gönderilecek, onaylıyor musunuz?";
      onay.hidden = false;
    }
    gonder.onclick = function () {
      if (waKilit || gonder.disabled) return;
      gonderOnayIste();
    };
    if (yenile) yenile.onclick = function () {
      waDurumDur();
      durumCek();
    };
    if (yine) yine.onclick = function () { gonderOnayIste(); };
    onay.onclick = function () {
      if (waKilit) return;
      var s = secili();
      if (s && s.engel) return;
      if (!String(kutu.value || "").trim()) return;
      var dugme = onay;
      setTimeout(function () { dugme.disabled = true; }, 0);
      gonderIstek(false);
    };
    if (tekrarBtn) tekrarBtn.onclick = function () {
      tekrarBtn.hidden = true;
      var siralar = (sonListe || []).filter(function (n) { return n.durum === "tekrar" || n.durum === "belirsiz"; }).map(function (n) { return n.sira; });
      if (siralar.length) deneme = waDeneme();
      dugmeTut = false;
      waKilit = false;
      gonderIstek(true, siralar);
    };
    if (belirsizBtn) belirsizBtn.onclick = function () {
      if (belirsizBtn.getAttribute("data-sor") !== "1") {
        belirsizBtn.setAttribute("data-sor", "1");
        sonuc.textContent = "Önceki gönderim ulaşmış olabilir, yine de gönder?";
        return;
      }
      belirsizBtn.hidden = true;
      belirsizBtn.removeAttribute("data-sor");
      deneme = waDeneme();
      dugmeTut = false;
      waKilit = false;
      gonderIstek(false);
    };
    durumCek();
    fetch("/erp-notlar/api/musteri/" + encodeURIComponent(mid) + "/whatsapp", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.ok) {
          uyari.hidden = false;
          uyari.textContent = (j && j.mesaj) || "Açılamadı";
          gonder.disabled = true;
          return;
        }
        gridYok = !!j.grid_yok;
        toplamBorc = j.toplam_borc;
        gecikenTutar = j.geciken_tutar;
        perde.querySelector("h3").textContent = j.unvan || unvan || "Müşteri";
        perde.querySelector(".ea-wa-maske").textContent = j.maske ? ("Numara " + j.maske) : "";
        var numKutu = document.getElementById("erp-asistan-wa-numaralar");
        if (numKutu && window.WaAlici && (j.numaralar || []).some(function (n) { return n && n.gorunen; })) {
          window.WaAlici.yaz(numKutu, {
            kartlar: (j.numaralar || []).filter(function (n) { return n && n.gorunen; }).map(function (n) {
              return { ham: n.gorunen, etiket: n.etiket || "", secili: !!n.secili };
            }),
            ek: true,
            ekId: "erp-asistan-wa-ek",
            mod: "secim",
            onChange: uyar
          });
        } else if (numKutu) {
          numKutu.innerHTML = "";
          (j.numaralar || []).forEach(function (n) {
            if (!n || !n.sira || !n.maske) return;
            var lab = document.createElement("label");
            lab.className = "ea-wa-num";
            lab.style.cssText = "display:flex;flex-direction:row;align-items:center;gap:8px;margin:4px 0;";
            var cb = document.createElement("input");
            cb.type = "checkbox";
            cb.checked = !!n.secili;
            cb.setAttribute("data-sira", String(n.sira));
            cb.addEventListener("change", uyar);
            lab.appendChild(cb);
            lab.appendChild(document.createTextNode(n.sira + ". numara · " + n.maske));
            numKutu.appendChild(lab);
          });
          var ekAc = document.createElement("a");
          ekAc.href = "#";
          ekAc.id = "erp-asistan-wa-ek-ac";
          ekAc.textContent = "Başka numara ekle";
          ekAc.onclick = function (ev) {
            ev.preventDefault();
            if (document.getElementById("erp-asistan-wa-ek")) return;
            ekAc.hidden = true;
            var giris = document.createElement("input");
            giris.id = "erp-asistan-wa-ek";
            giris.setAttribute("inputmode", "tel");
            giris.placeholder = "Telefon";
            giris.addEventListener("input", uyar);
            numKutu.appendChild(giris);
            uyar();
          };
          numKutu.appendChild(ekAc);
        }
        sablonlar = (j.sablonlar || []).slice();
        sablonlar.push({ anahtar: "ozel", etiket: "Özel mesaj", metin: "", borc: false, engel: false, grup: "ozel" });
        sablonlar.forEach(function (s) {
          if (!s.grup) s.grup = "gecikme";
          if (!s.baslik) s.baslik = "Gecikme hatırlatma";
          if (s.onizleme == null) s.onizleme = s.anahtar === "link" ? "" : (s.metin || "");
        });
        var html = "";
        sablonlar.forEach(function (s) {
          html += '<option value="' + esc(s.anahtar) + '"' + (s.engel ? " disabled" : "") + ">" +
            esc(s.etiket) + (s.onerilen ? " (önerilen)" : "") + "</option>";
        });
        sec.innerHTML = html;
        if (j.onerilen) sec.value = j.onerilen;
        var ilk = secili();
        if (ilk && ilk.engel) ilk = null;
        kutu.value = ilk ? (ilk.metin || "") : "";
        temiz = kutu.value;
        aktif = sec.value;
        uyar();
      })
      .catch(function () {
        uyari.hidden = false;
        uyari.textContent = "Açılamadı";
        gonder.disabled = true;
      });
  }

  var gecmisPopEsc = null;

  function gecmisPopKapat() {
    var p = document.getElementById("erp-asistan-gecmis-pop");
    if (p) p.remove();
    if (gecmisPopEsc) {
      document.removeEventListener("keydown", gecmisPopEsc);
      gecmisPopEsc = null;
    }
  }

  function gecmisKayitHtml(n) {
    return '<article class="ea-gecmis-kayit">' +
      '<div class="ea-gecmis-ust">' +
      "<span>Görüşme: " + esc(n.created_etiket || "—") + "</span>" +
      "<span>Hatırlatma: " + esc(n.hatirlatma_etiket || "—") + "</span>" +
      "<span>Durum: " + esc(n.durum || "—") + "</span>" +
      "</div>" +
      '<div class="ea-gecmis-kisi">Görüşen: ' + esc(n.olusturan_ad || "—") +
      " · Görüşülen: " + esc(n.gorusulen_kisi || "—") + "</div>" +
      '<div class="ea-gecmis-metin">' + esc(n.not_metni || "") + "</div>" +
      "</article>";
  }

  function gecmisSayfaYukle(kart, mid, ofset) {
    var url = "/erp-notlar/api/musteri/" + encodeURIComponent(mid) +
      "/notlar?sayfa=1&limit=50&ofset=" + encodeURIComponent(ofset);
    return fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        var liste = kart.querySelector(".ea-gecmis-liste");
        var eskiBtn = kart.querySelector(".ea-gecmis-daha");
        if (eskiBtn) eskiBtn.remove();
        if (!liste) return;
        if (ofset === 0) liste.innerHTML = "";
        if (!j || !j.ok) {
          if (ofset === 0) liste.innerHTML = "<p>Notlar alınamadı.</p>";
          return;
        }
        var parca = (j.notlar || []).map(gecmisKayitHtml).join("");
        if (ofset === 0 && !parca) liste.innerHTML = "<p>Görüşme notu yok.</p>";
        else liste.insertAdjacentHTML("beforeend", parca);
        if (j.devam) {
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "ea-gecmis-daha";
          btn.textContent = "Daha eski";
          btn.onclick = function () { gecmisSayfaYukle(kart, mid, ofset + 50); };
          kart.appendChild(btn);
        }
      });
  }

  function gecmisPopAc(mid, unvan) {
    if (!mid) return;
    gecmisPopKapat();
    stilEkle();
    var perde = document.createElement("div");
    perde.id = "erp-asistan-gecmis-pop";
    perde.className = "erp-asistan-perde";
    perde.innerHTML =
      '<div class="erp-asistan-kart ea-gecmis-kart" role="dialog" aria-modal="true">' +
      '<div class="ea-gecmis-baslik"><h3>' + esc(unvan || "Müşteri") + "</h3>" +
      '<button type="button" class="ea-gecmis-x" aria-label="Kapat">×</button></div>' +
      '<div class="ea-gecmis-liste"><p>Yükleniyor…</p></div></div>';
    document.body.appendChild(perde);
    perde.querySelector(".ea-gecmis-x").onclick = function (ev) {
      ev.stopPropagation();
      gecmisPopKapat();
    };
    perde.addEventListener("click", function (ev) {
      if (ev.target === perde) gecmisPopKapat();
    });
    gecmisPopEsc = function (ev) {
      if (ev.key === "Escape") gecmisPopKapat();
    };
    document.addEventListener("keydown", gecmisPopEsc);
    gecmisSayfaYukle(perde.querySelector(".ea-gecmis-kart"), mid, 0);
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
  window.erpAsistanNotFormAc = formAc;
  window.erpAsistanSutunBirlestir = tercihBirlestir;

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
    var ozetYerel = ozetTercihOku();
    if (ozetYerel) {
      ozetTercihUygula(ozetYerel);
    } else {
      fetch("/erp-notlar/api/kolonlar?ekran=ozet", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (ozetTercihElle || ozetTercihOku()) return;
          var sira = j && j.ok && Array.isArray(j.siralama) ? j.siralama : KOLON_OZET_VARSAYILAN.slice();
          ozetTercihUygula({ sira: sira, gizli: null });
          if (document.getElementById("erp-asistan-ozet-tablo")) ozetCiz();
        })
        .catch(function () {});
    }
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
      if (suruklenenEkran === "ozet") {
        ozetSuruklendi = true;
        ozetKolonSirasi = yeni;
        suruklenenKolon = "";
        ozetTercihYaz();
        if (document.getElementById("erp-asistan-ozet-tablo")) ozetCiz();
        return;
      }
      suruklenenKolon = "";
      kolonKaydet(yeni, suruklenenEkran);
    });
    document.addEventListener("dragend", function () {
      kolonVurguTemizle();
      suruklenenKolon = "";
    });
    document.addEventListener("click", function (ev) {
      var baslik = ev.target.closest && ev.target.closest("th[data-kolon]");
      if (baslik && baslik.closest("table.ea-tablo") && baslik.closest("table").getAttribute("data-ekran") === "ozet") {
        if (ozetSuruklendi) {
          ozetSuruklendi = false;
          return;
        }
        var anahtar = baslik.getAttribute("data-kolon") || "";
        if (anahtar === "toplam_borc" || anahtar === "geciken_ay") {
          ozetSiraYon = ozetSiraKey === anahtar ? -ozetSiraYon : -1;
          ozetSiraKey = anahtar;
          ozetCiz();
        }
        return;
      }
      if (!(ev.target.closest && ev.target.closest("#erp-asistan-sutun-menu, #erp-asistan-sutunlar"))) {
        var menu = document.getElementById("erp-asistan-sutun-menu");
        if (menu) menu.hidden = true;
      }
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
      var waBtn = ev.target.closest && ev.target.closest(".ea-wa");
      if (waBtn) {
        if (waBtn.disabled) return;
        var waSatir = waBtn.closest("tr.ea-satir");
        if (waSatir && waSatir.getAttribute("data-ozet") === "1") {
          var waUnvan = waSatir.querySelector("td[data-kolon=musteri]");
          waPopAc(waSatir.getAttribute("data-musteri-id"), waUnvan ? waUnvan.textContent : "");
        }
        return;
      }
      var aciklamaHucre = ev.target.closest && ev.target.closest("td.ea-gecmis-ac");
      if (aciklamaHucre) {
        var gecmisSatir = aciklamaHucre.closest("tr.ea-satir");
        if (gecmisSatir && gecmisSatir.getAttribute("data-ozet") === "1") {
          var unvanHucre = gecmisSatir.querySelector("td[data-kolon=musteri]");
          gecmisPopAc(gecmisSatir.getAttribute("data-musteri-id"), unvanHucre ? unvanHucre.textContent : "");
          return;
        }
      }
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
