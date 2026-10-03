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
      ".ea-kolon-tasi{border:0;background:transparent;color:#90a4ae;cursor:pointer;padding:0 2px;font-size:12px;}" +
      ".ea-tablo th:first-child [data-kolon-tasi='-1'],.ea-tablo th:last-child [data-kolon-tasi='1']{visibility:hidden;}" +
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

  function kolonNormalize(raw) {
    if (!Array.isArray(raw) || raw.length !== KOLON_VARSAYILAN.length) return KOLON_VARSAYILAN.slice();
    var kopya = raw.map(function (x) { return String(x); });
    var a = kopya.slice().sort().join("|");
    var b = KOLON_VARSAYILAN.slice().sort().join("|");
    return a === b ? kopya : KOLON_VARSAYILAN.slice();
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
    if (key === "durum") return esc(n.durum);
    return "";
  }

  function satirHtml(n) {
    var soluk = n.durum === "tamamlandi" ? " ea-not-soluk" : "";
    var html = '<tr class="ea-satir' + soluk + '" data-not-id="' + esc(n.id) + '">';
    kolonSirasi.forEach(function (key) { html += "<td>" + hucreHtml(n, key) + "</td>"; });
    return html + "</tr>";
  }

  function tabloHtml(notlar) {
    var html = '<div class="ea-tablo-kaydir"><table class="ea-tablo"><thead><tr>';
    kolonSirasi.forEach(function (key) {
      html += '<th data-kolon="' + esc(key) + '">' +
        '<button type="button" class="ea-kolon-tasi" data-kolon-tasi="-1" title="Sola taşı">‹</button>' +
        esc(KOLON_ETIKET[key] || key) +
        '<button type="button" class="ea-kolon-tasi" data-kolon-tasi="1" title="Sağa taşı">›</button></th>';
    });
    html += "</tr></thead><tbody>";
    (notlar || []).forEach(function (n) { html += satirHtml(n); });
    return html + "</tbody></table></div>";
  }

  function kolonHucreDegistir(table, i, j) {
    if (i === j) return;
    var lo = Math.min(i, j);
    var hi = Math.max(i, j);
    for (var r = 0; r < table.rows.length; r++) {
      var cells = table.rows[r].cells;
      var left = cells[lo];
      var right = cells[hi];
      var marker = right.nextSibling;
      table.rows[r].insertBefore(right, left);
      table.rows[r].insertBefore(left, marker);
    }
  }

  function kolonTasi(key, yon) {
    var i = kolonSirasi.indexOf(key);
    var j = i + yon;
    if (i < 0 || j < 0 || j >= kolonSirasi.length) return;
    var yeni = kolonSirasi.slice();
    var tmp = yeni[i];
    yeni[i] = yeni[j];
    yeni[j] = tmp;
    postJson("/erp-notlar/api/kolonlar", { siralama: yeni }).then(function (res) {
      if (!res || !res.ok || !res.siralama) {
        alert((res && res.mesaj) || "Sıra kaydedilemedi");
        return;
      }
      kolonSirasi = kolonNormalize(res.siralama);
      if (panelAcikMi()) {
        gecmisCiz(panelNotlar);
        return;
      }
      var tablolar = document.querySelectorAll("table.ea-tablo");
      for (var t = 0; t < tablolar.length; t++) kolonHucreDegistir(tablolar[t], i, j);
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

  window.erpAsistanAc = panelToggle;
  window.erpAsistanTabloHtml = tabloHtml;

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
          kolonSirasi = kolonNormalize(j.siralama);
          if (panelAcikMi()) gecmisCiz(panelNotlar);
        })
        .catch(function () {});
    }
    document.addEventListener("click", function (ev) {
      var ok = ev.target.closest && ev.target.closest("[data-kolon-tasi]");
      if (ok) {
        var th = ok.closest("th");
        var key = th && th.getAttribute("data-kolon");
        if (key) kolonTasi(key, Number(ok.getAttribute("data-kolon-tasi")));
        return;
      }
      var kart = ev.target.closest && ev.target.closest("tr.ea-satir");
      if (!kart || ev.target.closest("a,button,input,textarea,select,label")) return;
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
