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
      "#odeme-linki-wa-kutu{margin-top:8px;}" +
      "#odeme-linki-kart label{display:flex;flex-direction:column;gap:4px;margin:0 0 8px;font-size:13px;}" +
      "#odeme-linki-kart input,#odeme-linki-kart textarea{background:#0a1929;color:#e0f7fa;border:1px solid #1e3a50;border-radius:6px;padding:6px;}" +
      "#odeme-linki-kart button{cursor:pointer;border-radius:6px;border:1px solid #1e3a50;background:#1565c0;color:#fff;padding:6px 10px;margin-right:6px;}" +
      "#odeme-linki-liste{font-size:13px;margin-top:12px;}" +
      "#odeme-linki-liste table{width:100%;border-collapse:collapse;}" +
      "#odeme-linki-liste td{border-bottom:1px solid #1e3a50;padding:4px;}";
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
      var iptal = n.durum === "bekliyor"
        ? ' <button type="button" data-iptal="' + esc(n.id) + '">İptal et</button>'
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

  function waBagla(mid, linkId, url) {
    var acBtn = document.getElementById("odeme-linki-wa-ac");
    var gonder = document.getElementById("odeme-linki-wa-gonder");
    if (!acBtn || !gonder) return;
    var token = tokenFromUrl(url);
    function waPost(govde) {
      return fetch("/giris/api/odeme-linki/" + encodeURIComponent(linkId) + "/whatsapp", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(govde)
      }).then(function (r) {
        return r.json().then(function (j) { return j || {}; }).catch(function () { return {}; });
      });
    }
    acBtn.onclick = function () {
      var kutu = document.getElementById("odeme-linki-wa-kutu");
      if (kutu) kutu.hidden = false;
      waPost({ onizle: true, token: token }).then(function (j) {
        var tel = document.getElementById("odeme-linki-wa-tel");
        var mesaj = document.getElementById("odeme-linki-wa-mesaj");
        var uyari = document.getElementById("odeme-linki-wa-uyari");
        if (tel && !tel.value) tel.value = j.telefon || "";
        if (mesaj && !mesaj.value) mesaj.value = j.mesaj || "";
        if (uyari) uyari.textContent = j.uyari || (j.gonderilebilir ? "" : "Bu link gönderilemez");
      }).catch(function () {});
    };
    gonder.onclick = function () {
      if (gonder.disabled) return;
      gonder.disabled = true;
      var tel = document.getElementById("odeme-linki-wa-tel");
      var mesaj = document.getElementById("odeme-linki-wa-mesaj");
      var sonuc = document.getElementById("odeme-linki-wa-sonuc");
      var uyari = document.getElementById("odeme-linki-wa-uyari");
      function bitir(j) {
        if (uyari && j && j.kayitli === false) uyari.textContent = j.mesaj || "";
        if (sonuc) sonuc.textContent = (j && (j.whatsapp_etiket || j.mesaj)) || "";
        if (j && j.ok) listeGetir(mid);
        else gonder.disabled = false;
      }
      waPost({
        token: token,
        telefon: tel ? tel.value : "",
        mesaj: mesaj ? mesaj.value : "",
        onay: false
      }).then(function (j) {
        if (j && j.tekrar_onay) {
          var evet = window.confirm(j.mesaj || "Bu linke daha önce gönderildi, tekrar göndermek istiyor musun?");
          if (!evet) {
            gonder.disabled = false;
            return null;
          }
          return waPost({
            token: token,
            telefon: tel ? tel.value : "",
            mesaj: mesaj ? mesaj.value : "",
            onay: true
          });
        }
        return j;
      }).then(function (j) {
        if (!j) return;
        bitir(j);
      }).catch(function () {
        gonder.disabled = false;
      });
    };
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
      '<div><button type="submit">Link oluştur</button>' +
      '<button type="button" id="odeme-linki-kapat">Kapat</button></div>' +
      '<p id="odeme-linki-url"></p>' +
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
          kutu.innerHTML = esc(j.url) + ' <button type="button" id="odeme-linki-kopyala">Kopyala</button>' +
            ' <button type="button" id="odeme-linki-wa-ac">WhatsApp ile gönder</button>' +
            '<div id="odeme-linki-wa-kutu" hidden>' +
            '<label>Telefon<input id="odeme-linki-wa-tel"></label>' +
            '<p id="odeme-linki-wa-uyari"></p>' +
            '<label>Mesaj<textarea id="odeme-linki-wa-mesaj" rows="6"></textarea></label>' +
            '<button type="button" id="odeme-linki-wa-gonder">Onayla ve gönder</button>' +
            '<p id="odeme-linki-wa-sonuc"></p></div>';
          document.getElementById("odeme-linki-kopyala").onclick = function () { kopyala(j.url); };
          waBagla(mid, j.id, j.url);
          listeGetir(mid);
        })
        .catch(function () {});
    };
    perde.addEventListener("click", function (ev) {
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
