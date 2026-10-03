/* ERP Asistan — genel hatırlatma kartı. randevu-hatirlatma.js'e bağlı değildir. */
(function () {
  var POLL_MS = 30000;
  var STORAGE_KEY = "bestoffice_erp_asistan_v1";
  var acik = false;
  var sonMusteri = null;

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
      ".erp-asistan-rozet{display:inline-block;background:#8d2a2a;color:#fff;border-radius:8px;padding:1px 6px;margin-left:6px;font-size:11px;}";
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

  function kartGoster(n) {
    if (acik) return;
    stilEkle();
    acik = true;
    isaretle(n);
    sesCal();
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
      if (act === "kapat") {
        kapatKart();
        return;
      }
      if (act === "detay") {
        window.location.href = detayUrl(n);
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

  function gecmisCiz(notlar) {
    var kutu = document.getElementById("erp_asistan_gecmis");
    if (!kutu) return;
    if (!notlar || !notlar.length) {
      kutu.innerHTML = '<div class="erp-asistan-gecmis-kutu">Bu müşteri için not yok.</div>';
      return;
    }
    var html = '<div class="erp-asistan-gecmis-kutu"><strong>ERP Asistan notları</strong><ul style="margin:6px 0 0 16px;">';
    notlar.forEach(function (n) {
      var rozet = n.whatsapp_rozet === "gönderilemedi"
        ? ' <span class="erp-asistan-rozet" title="' + esc(n.whatsapp_hata) + '">gönderilemedi</span>'
        : (n.whatsapp_rozet ? " · WA " + esc(n.whatsapp_rozet) : "");
      html += "<li>" + esc(n.hatirlatma_etiket) + " · " + esc(n.kategori) + " · " +
        esc(n.durum) + " — " + esc(n.not_metni) + rozet + "</li>";
    });
    html += '</ul> <a href="/erp-notlar/" style="color:#80deea;">Tüm notlar</a></div>';
    kutu.innerHTML = html;
  }

  function gecmisYukle() {
    var kutu = document.getElementById("erp_asistan_gecmis");
    if (!kutu) return;
    var id = musteriId();
    if (!id) {
      kutu.innerHTML = '<div class="erp-asistan-gecmis-kutu">Müşteri seçilince notlar burada listelenir.</div>';
      return;
    }
    fetch("/erp-notlar/api/liste?iliski_tip=musteri&iliski_id=" + id, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (j && j.ok) gecmisCiz(j.notlar);
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
      '<label class="erp-asistan-alan">Not<textarea name="not_metni" required rows="3"></textarea></label>' +
      '<label class="erp-asistan-alan">Tarih ve saat<input type="datetime-local" name="hatirlatma_zamani" required></label>' +
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

  window.erpAsistanAc = formAc;

  function basla() {
    tara();
    setInterval(tara, POLL_MS);
    gecmisYukle();
    setInterval(function () {
      var id = musteriId();
      if (id !== sonMusteri) {
        sonMusteri = id;
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
