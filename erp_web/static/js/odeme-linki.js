(function () {
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function musteriId() {
    try {
      if (typeof cariEkstreMusteriIdSec === "function") {
        var id = parseInt(cariEkstreMusteriIdSec(), 10);
        if (!isNaN(id) && id > 0) return id;
      }
    } catch (e) {}
    return 0;
  }

  function stil() {
    if (document.getElementById("odeme-linki-stil")) return;
    var st = document.createElement("style");
    st.id = "odeme-linki-stil";
    st.textContent =
      "#odeme-linki-ac{margin-left:8px;cursor:pointer;border:1px solid #1e3a50;background:#1565c0;color:#fff;border-radius:6px;padding:4px 10px;}" +
      "#odeme-linki-perde{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:14000;display:flex;align-items:center;justify-content:center;padding:16px;}" +
      "#odeme-linki-kart{background:#0f2537;color:#e0f7fa;border:1px solid #1e3a50;border-radius:10px;max-width:560px;width:100%;padding:16px;max-height:86vh;overflow:auto;}" +
      "#odeme-linki-wa-uyari{color:#ffcc80;margin:4px 0;}" +
      "#odeme-linki-kart label{display:flex;flex-direction:column;gap:4px;margin:0 0 8px;font-size:13px;}" +
      "#odeme-linki-kart input,#odeme-linki-kart textarea{background:#0a1929;color:#e0f7fa;border:1px solid #1e3a50;border-radius:6px;padding:6px;}" +
      "#odeme-linki-kart button{cursor:pointer;border-radius:6px;border:1px solid #1e3a50;background:#1565c0;color:#fff;padding:6px 10px;margin-right:6px;}" +
      "#odeme-linki-liste{font-size:13px;margin-top:12px;}" +
      "#odeme-linki-liste table{width:100%;border-collapse:collapse;}" +
      "#odeme-linki-liste td{border-bottom:1px solid #1e3a50;padding:4px;}" +
      "#odeme-linki-url{word-break:break-all;margin:8px 0;}" +
      ".odeme-linki-sonuc-btn{margin:8px 0;}" +
      "#odeme-linki-wa-sec{width:100%;margin:8px 0;background:#0a1929;color:#e0f7fa;border:1px solid #1e3a50;border-radius:6px;padding:6px;}" +
      "#odeme-linki-wa-satir{display:flex;align-items:center;gap:8px;margin:8px 0;flex-wrap:wrap;}" +
      "#odeme-linki-wa-ad{font-size:13px;}" +
      "#odeme-linki-wa-tel{flex:1;min-width:140px;}";
    document.head.appendChild(st);
  }

  function kopyala(url) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).catch(function () {});
      return;
    }
    var inp = document.createElement("input");
    inp.value = url;
    document.body.appendChild(inp);
    inp.select();
    try { document.execCommand("copy"); } catch (e) {}
    inp.remove();
  }

  function listeCiz(linkler) {
    var kutu = document.getElementById("odeme-linki-liste");
    if (!kutu) return;
    if (!linkler || !linkler.length) {
      kutu.innerHTML = "<p>Önceki link yok.</p>";
      return;
    }
    var html = "<table><tbody>";
    linkler.forEach(function (n) {
      var tarih = String(n.created_at || "").replace("T", " ").slice(0, 16);
      var etiket = String(n.whatsapp_etiket || "");
      var once = etiket === "Gönderildi" || etiket.indexOf("doğrulanamadı") >= 0 ? "1" : "0";
      var iptal = n.durum === "bekliyor"
        ? ' <button type="button" data-wa="' + esc(n.id) + '" data-once="' + once + '">WhatsApp ile gönder</button>' +
          ' <button type="button" data-iptal="' + esc(n.id) + '">İptal et</button>'
        : "";
      var wa = n.whatsapp_etiket ? " · " + esc(n.whatsapp_etiket) : "";
      html += "<tr><td>" + esc(tarih) + "</td><td>" + esc(n.tutar) + " TL</td><td>" +
        esc(n.durum) + wa + iptal + "</td></tr>";
    });
    html += "</tbody></table>";
    kutu.innerHTML = html;
  }

  function listeGetir(mid) {
    fetch("/giris/api/odeme-linki?musteri_id=" + encodeURIComponent(mid), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j && j.ok) listeCiz(j.linkler || []); })
      .catch(function () {});
  }

  function tokenFromUrl(url) {
    var s = String(url || "");
    var i = s.indexOf("/odeme/");
    if (i < 0) return "";
    return s.slice(i + 7).split(/[?#]/)[0];
  }

  var adresler = {};
  try {
    adresler = JSON.parse(sessionStorage.getItem("olnkWaAdres") || "{}") || {};
  } catch (e) {
    adresler = {};
  }

  function adresKaydet(id, url) {
    adresler[String(id)] = url;
    try { sessionStorage.setItem("olnkWaAdres", JSON.stringify(adresler)); } catch (e) {}
  }

  function waPost(linkId, govde) {
    return fetch("/giris/api/odeme-linki/" + encodeURIComponent(linkId) + "/whatsapp", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(govde)
    }).then(function (r) {
      return r.json().then(function (j) { return j || {}; }).catch(function () { return {}; });
    });
  }

  function telGecerli(raw) {
    var s = String(raw || "").replace(/\D/g, "");
    if (s.indexOf("00") === 0) s = s.slice(2);
    if (s.charAt(0) === "0" && s.length === 11) s = "90" + s.slice(1);
    else if (s.length === 10 && s.charAt(0) === "5") s = "90" + s;
    return /^[1-9]\d{10,14}$/.test(s);
  }

  function etiketCiz() {
    var adEl = document.getElementById("odeme-linki-wa-ad");
    var tel = document.getElementById("odeme-linki-wa-tel");
    var sec = document.getElementById("odeme-linki-wa-sec");
    var ad = "";
    var num = tel ? tel.value : "";
    if (sec) {
      var i;
      for (i = 0; i < sec.options.length; i++) {
        if (sec.options[i].getAttribute("data-tel") === num) {
          ad = sec.options[i].getAttribute("data-ad") || "";
          break;
        }
      }
    }
    if (adEl) adEl.textContent = ad ? ad + " ·" : "";
  }

  function kisileriYaz(kisiler) {
    var sec = document.getElementById("odeme-linki-wa-sec");
    var tel = document.getElementById("odeme-linki-wa-tel");
    var liste = kisiler || [];
    if (!sec || !tel) return;
    sec.innerHTML = "";
    liste.forEach(function (k, i) {
      var o = document.createElement("option");
      var ad = String((k && k.ad) || "").trim();
      var num = String((k && k.telefon) || "");
      o.value = String(i);
      o.setAttribute("data-ad", ad);
      o.setAttribute("data-tel", num);
      o.textContent = ad ? ad + " · " + num : num;
      sec.appendChild(o);
    });
    sec.hidden = liste.length < 2;
    if (liste.length) tel.value = String(liste[0].telefon || "");
    etiketCiz();
  }

  var gonderiyor = false;

  function waButonlar(kapali) {
    var perde = document.getElementById("odeme-linki-perde");
    if (!perde) return;
    perde.querySelectorAll("[data-wa], #odeme-linki-wa-ac").forEach(function (b) {
      b.disabled = kapali;
    });
  }

  function waGonder(mid, linkId, btn) {
    if (gonderiyor) return;
    var uyari = document.getElementById("odeme-linki-wa-uyari");
    var sonuc = document.getElementById("odeme-linki-wa-sonuc");
    var tel = document.getElementById("odeme-linki-wa-tel");
    var url = adresler[String(linkId)] || "";
    if (!url) {
      if (uyari) uyari.textContent = "Bu linkin adresi bu oturumda yok. Az önce oluşturduğunuz linkten gönderebilirsiniz.";
      if (sonuc) sonuc.textContent = "";
      return;
    }
    var ham = tel ? tel.value : "";
    if (!telGecerli(ham)) {
      if (uyari) uyari.textContent = "Telefon numarası geçersiz";
      if (sonuc) sonuc.textContent = "";
      return;
    }
    if (uyari) uyari.textContent = "";
    var onayli = btn && btn.getAttribute("data-once") === "1";
    if (onayli && !window.confirm("Bu link daha önce gönderildi, tekrar gönderilsin mi?")) return;
    gonderiyor = true;
    waButonlar(true);
    function birak() {
      gonderiyor = false;
      waButonlar(false);
    }
    function govde(onay) {
      return { token: tokenFromUrl(url), telefon: ham, onay: !!onay };
    }
    waPost(linkId, govde(onayli)).then(function (j) {
      if (j && j.tekrar_onay) {
        var evet = window.confirm(j.mesaj || "Bu link daha önce gönderildi, tekrar gönderilsin mi?");
        if (!evet) {
          birak();
          return null;
        }
        return waPost(linkId, govde(true));
      }
      return j;
    }).then(function (j) {
      if (!j) return;
      if (uyari && j.kayitli === false) uyari.textContent = j.mesaj || "";
      if (sonuc) sonuc.textContent = j.whatsapp_etiket || j.mesaj || "";
      if (j.ok) listeGetir(mid);
      birak();
    }).catch(function () {
      if (sonuc) sonuc.textContent = "Gönderilemedi";
      birak();
    });
  }

  function ac() {
    var mid = musteriId();
    if (!mid) {
      alert("Önce müşteri seçin.");
      return;
    }
    stil();
    var eski = document.getElementById("odeme-linki-perde");
    if (eski) eski.remove();
    var perde = document.createElement("div");
    perde.id = "odeme-linki-perde";
    perde.innerHTML =
      '<form id="odeme-linki-kart">' +
      "<h3>Ödeme Linki</h3>" +
      '<p id="odeme-linki-ad">Yükleniyor…</p>' +
      '<label>Tutar (TL)<input name="tutar" inputmode="decimal" required></label>' +
      '<label>Açıklama<textarea name="aciklama" rows="2"></textarea></label>' +
      '<label>Geçerlilik (gün)<input name="gun" type="number" min="1" max="30" value="7" required></label>' +
      '<select id="odeme-linki-wa-sec" hidden></select>' +
      '<div id="odeme-linki-wa-satir"><span id="odeme-linki-wa-ad"></span>' +
      '<input id="odeme-linki-wa-tel" inputmode="tel" placeholder="Telefon"></div>' +
      '<p id="odeme-linki-wa-uyari"></p>' +
      '<p id="odeme-linki-wa-sonuc"></p>' +
      '<div><button type="submit">Link oluştur</button>' +
      '<button type="button" id="odeme-linki-kapat">Kapat</button></div>' +
      '<div id="odeme-linki-url"></div>' +
      '<div id="odeme-linki-liste"></div></form>';
    document.body.appendChild(perde);
    var form = document.getElementById("odeme-linki-kart");
    document.getElementById("odeme-linki-kapat").onclick = function () { perde.remove(); };
    fetch("/giris/api/odeme-linki/kalan?musteri_id=" + encodeURIComponent(mid), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        var ad = document.getElementById("odeme-linki-ad");
        if (!ad) return;
        if (!j || !j.ok) {
          ad.textContent = "Müşteri okunamadı.";
          return;
        }
        ad.textContent = j.ad || "Müşteri";
        var inp = perde.querySelector('input[name="tutar"]');
        if (inp && j.tutar != null) inp.value = String(j.tutar);
        kisileriYaz(j.kisiler || []);
      })
      .catch(function () {});
    listeGetir(mid);
    form.onsubmit = function (ev) {
      ev.preventDefault();
      var kart = form;
      fetch("/giris/api/odeme-linki", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          musteri_id: mid,
          tutar: kart.tutar.value,
          aciklama: kart.aciklama.value,
          gun: kart.gun.value
        })
      }).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          var kutu = document.getElementById("odeme-linki-url");
          if (!kutu) return;
          if (!j || !j.ok || !j.url) {
            kutu.textContent = "Link oluşturulamadı.";
            return;
          }
          adresKaydet(j.id, j.url);
          kutu.innerHTML =
            '<div class="odeme-linki-sonuc-url">' + esc(j.url) + "</div>" +
            '<div class="odeme-linki-sonuc-btn">' +
            '<button type="button" id="odeme-linki-kopyala">Kopyala</button>' +
            '<button type="button" id="odeme-linki-wa-ac">WhatsApp ile gönder</button></div>';
          document.getElementById("odeme-linki-kopyala").onclick = function () { kopyala(j.url); };
          document.getElementById("odeme-linki-wa-ac").onclick = function () { waGonder(mid, j.id, this); };
          listeGetir(mid);
        })
        .catch(function () {});
    };
    var sec = document.getElementById("odeme-linki-wa-sec");
    var telInp = document.getElementById("odeme-linki-wa-tel");
    if (sec) sec.onchange = function () {
      var opt = sec.options[sec.selectedIndex];
      if (telInp && opt) telInp.value = opt.getAttribute("data-tel") || "";
      etiketCiz();
    };
    if (telInp) telInp.oninput = etiketCiz;
    perde.addEventListener("click", function (ev) {
      var wa = ev.target.closest && ev.target.closest("[data-wa]");
      if (wa) {
        waGonder(mid, wa.getAttribute("data-wa"), wa);
        return;
      }
      var btn = ev.target.closest && ev.target.closest("[data-iptal]");
      if (!btn) return;
      fetch("/giris/api/odeme-linki/" + encodeURIComponent(btn.getAttribute("data-iptal")) + "/iptal", {
        method: "POST",
        credentials: "same-origin"
      }).then(function () { listeGetir(mid); }).catch(function () {});
    });
  }

  function bagla() {
    var btn = document.getElementById("odeme-linki-ac");
    if (!btn || btn.getAttribute("data-bagli") === "1") return;
    btn.setAttribute("data-bagli", "1");
    btn.onclick = ac;
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bagla);
  else bagla();
})();
