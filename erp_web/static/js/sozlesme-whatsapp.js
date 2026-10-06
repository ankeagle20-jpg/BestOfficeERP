(function () {
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function telGecerli(raw) {
    var s = String(raw || "").replace(/\D/g, "");
    if (s.indexOf("00") === 0) s = s.slice(2);
    if (s.charAt(0) === "0" && s.length === 11) s = "90" + s.slice(1);
    else if (s.length === 10 && s.charAt(0) === "5") s = "90" + s;
    return /^[1-9]\d{10,14}$/.test(s);
  }

  function stil() {
    if (document.getElementById("sozlesme-wa-stil")) return;
    var st = document.createElement("style");
    st.id = "sozlesme-wa-stil";
    st.textContent =
      "#sozlesme-wa-perde{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:14100;display:flex;align-items:center;justify-content:center;padding:16px;}" +
      "#sozlesme-wa-kart{background:#0f2537;color:#e0f7fa;border:1px solid #1e3a50;border-radius:10px;max-width:520px;width:100%;padding:16px;max-height:86vh;overflow:auto;}" +
      "#sozlesme-wa-kart label{display:flex;flex-direction:column;gap:4px;margin:0 0 8px;font-size:13px;}" +
      "#sozlesme-wa-kart input,#sozlesme-wa-kart textarea,#sozlesme-wa-kart select{background:#0a1929;color:#e0f7fa;border:1px solid #1e3a50;border-radius:6px;padding:6px;width:100%;box-sizing:border-box;}" +
      "#sozlesme-wa-kart button{cursor:pointer;border-radius:6px;border:1px solid #1e3a50;background:#1565c0;color:#fff;padding:6px 10px;margin-right:6px;}" +
      "#sozlesme-wa-satir{display:flex;align-items:center;gap:8px;margin:8px 0;flex-wrap:wrap;}" +
      "#sozlesme-wa-ad{font-size:13px;}" +
      "#sozlesme-wa-tel{flex:1;min-width:140px;}" +
      "#sozlesme-wa-uyari{color:#ffcc80;margin:4px 0;}" +
      "#sozlesme-wa-yine{display:none;background:#6a4a12;}";
    document.head.appendChild(st);
  }

  function etiketCiz() {
    var adEl = document.getElementById("sozlesme-wa-ad");
    var tel = document.getElementById("sozlesme-wa-tel");
    var sec = document.getElementById("sozlesme-wa-sec");
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
    if (!ad && tel && tel.getAttribute("data-ilk") === num) ad = tel.getAttribute("data-ad") || "";
    if (adEl) adEl.textContent = ad ? ad + " ·" : "";
  }

  function kisileriYaz(kisiler, ilkTel, ilkAd) {
    var sec = document.getElementById("sozlesme-wa-sec");
    var tel = document.getElementById("sozlesme-wa-tel");
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
    tel.value = ilkTel || (liste[0] ? String(liste[0].telefon || "") : "");
    tel.setAttribute("data-ilk", tel.value);
    tel.setAttribute("data-ad", ilkAd || (liste[0] ? String(liste[0].ad || "") : ""));
    if (liste.length && ilkTel) {
      var j;
      for (j = 0; j < sec.options.length; j++) {
        if (sec.options[j].getAttribute("data-tel") === ilkTel) {
          sec.selectedIndex = j;
          break;
        }
      }
    }
    etiketCiz();
  }

  var gonderiyor = false;
  var acik = null;

  function webAc(tel, mesaj) {
    var num = tel;
    if (typeof girisTelefonuWhatsAppRakam === "function") num = girisTelefonuWhatsAppRakam(tel) || tel;
    if (typeof bestOfficeWhatsAppWebAc === "function") bestOfficeWhatsAppWebAc(num, mesaj);
  }

  function gonder(onay) {
    if (gonderiyor || !acik) return;
    var uyari = document.getElementById("sozlesme-wa-uyari");
    var sonuc = document.getElementById("sozlesme-wa-sonuc");
    var tel = document.getElementById("sozlesme-wa-tel");
    var mesaj = document.getElementById("sozlesme-wa-mesaj");
    var btn = document.getElementById("sozlesme-wa-gonder");
    var yine = document.getElementById("sozlesme-wa-yine");
    var ham = tel ? tel.value : "";
    var metin = mesaj ? mesaj.value : "";
    if (!telGecerli(ham)) {
      if (uyari) uyari.textContent = "Telefon numarası geçersiz";
      if (sonuc) sonuc.textContent = "";
      return;
    }
    if (!String(metin || "").trim()) {
      if (uyari) uyari.textContent = "Mesaj boş";
      return;
    }
    if (uyari && !onay) uyari.textContent = "";
    if (sonuc) sonuc.textContent = "WhatsApp bağlanıyor…";
    gonderiyor = true;
    if (btn) btn.disabled = true;
    if (yine) yine.disabled = true;
    fetch("/giris/api/sozlesme-whatsapp/gonder", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        musteri_id: acik.musteriId,
        telefon: ham,
        mesaj: metin,
        buton: acik.buton,
        onay: !!onay
      })
    }).then(function (r) {
      return r.json().then(function (j) { return { kod: r.status, j: j || {} }; }).catch(function () { return { kod: r.status, j: {} }; });
    }).then(function (paket) {
      var j = paket.j || {};
      if (j.qr) {
        if (uyari) uyari.textContent = j.mesaj || "WhatsApp oturumu yenilenmeli (QR)";
        if (sonuc) sonuc.textContent = "";
        if (yine) yine.style.display = "none";
      } else if (j.geri_dus) {
        webAc(ham, metin);
        if (uyari) uyari.textContent = j.mesaj || "WhatsApp servisi bağlı değil, WhatsApp Web sayfası açılıyor";
        if (sonuc) sonuc.textContent = "";
        if (yine) yine.style.display = "none";
      } else if (j.tekrar) {
        if (uyari) uyari.textContent = j.mesaj || "Bu mesaj bu numaraya az önce gönderildi.";
        if (yine) yine.style.display = "inline-block";
        if (sonuc) sonuc.textContent = "";
      } else if (j.ok) {
        if (uyari) uyari.textContent = "";
        if (sonuc) sonuc.textContent = j.mesaj || "Gönderiliyor";
        if (yine) yine.style.display = "none";
      } else {
        if (uyari) uyari.textContent = j.mesaj || "Gönderilemedi";
        if (sonuc) sonuc.textContent = "";
      }
    }).catch(function () {
      if (uyari) uyari.textContent = "Gönderilemedi";
    }).then(function () {
      gonderiyor = false;
      if (btn) btn.disabled = false;
      if (yine) yine.disabled = false;
    });
  }

  function sozlesmeWhatsAppAc(opts) {
    opts = opts || {};
    stil();
    var eski = document.getElementById("sozlesme-wa-perde");
    if (eski) eski.remove();
    acik = {
      buton: opts.buton === "makbuz" ? "makbuz" : "ust",
      musteriId: parseInt(opts.musteriId, 10) || 0
    };
    var perde = document.createElement("div");
    perde.id = "sozlesme-wa-perde";
    perde.innerHTML =
      '<form id="sozlesme-wa-kart">' +
      "<h3>WhatsApp</h3>" +
      '<select id="sozlesme-wa-sec" hidden></select>' +
      '<div id="sozlesme-wa-satir"><span id="sozlesme-wa-ad"></span>' +
      '<input id="sozlesme-wa-tel" inputmode="tel" placeholder="Telefon"></div>' +
      '<label>Mesaj<textarea id="sozlesme-wa-mesaj" rows="6"></textarea></label>' +
      '<p id="sozlesme-wa-uyari"></p>' +
      '<p id="sozlesme-wa-sonuc"></p>' +
      '<div><button type="button" id="sozlesme-wa-gonder">Gönder</button>' +
      '<button type="button" id="sozlesme-wa-yine">Yine de gönder</button>' +
      '<button type="button" id="sozlesme-wa-kapat">Kapat</button></div></form>';
    document.body.appendChild(perde);
    document.getElementById("sozlesme-wa-kapat").onclick = function () { perde.remove(); acik = null; };
    document.getElementById("sozlesme-wa-gonder").onclick = function () { gonder(false); };
    document.getElementById("sozlesme-wa-yine").onclick = function () { gonder(true); };
    var mesaj = document.getElementById("sozlesme-wa-mesaj");
    if (mesaj) mesaj.value = opts.mesaj || "";
    var sec = document.getElementById("sozlesme-wa-sec");
    var telInp = document.getElementById("sozlesme-wa-tel");
    if (sec) sec.onchange = function () {
      var opt = sec.options[sec.selectedIndex];
      if (telInp && opt) {
        telInp.value = opt.getAttribute("data-tel") || "";
        telInp.setAttribute("data-ad", opt.getAttribute("data-ad") || "");
        telInp.setAttribute("data-ilk", telInp.value);
      }
      etiketCiz();
    };
    if (telInp) telInp.oninput = etiketCiz;
    kisileriYaz([], opts.telefon || "", opts.ad || "");
    if (acik.musteriId > 0) {
      fetch("/giris/api/sozlesme-whatsapp/kisiler?musteri_id=" + encodeURIComponent(acik.musteriId), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!j || !j.ok) return;
          kisileriYaz(j.kisiler || [], opts.telefon || "", opts.ad || "");
        })
        .catch(function () {});
    }
    perde.addEventListener("submit", function (ev) { ev.preventDefault(); });
  }

  window.sozlesmeWhatsAppAc = sozlesmeWhatsAppAc;
})();
