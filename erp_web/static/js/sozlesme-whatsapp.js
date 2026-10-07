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
      "#sozlesme-wa-indir a{color:#80deea;}" +
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
  var pollTimer = null;
  var POLL_MS = 1500;
  var POLL_SON = 30000;

  function denemeUret() {
    if (window.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function pollDur() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  function dugmeAc() {
    gonderiyor = false;
    var btn = document.getElementById("sozlesme-wa-gonder");
    var yine = document.getElementById("sozlesme-wa-yine");
    if (btn) btn.disabled = false;
    if (yine) yine.disabled = false;
  }

  function yineGoster(goster) {
    var yine = document.getElementById("sozlesme-wa-yine");
    if (yine) yine.style.display = goster ? "inline-block" : "none";
  }

  function belirsizGoster() {
    var uyari = document.getElementById("sozlesme-wa-uyari");
    var sonuc = document.getElementById("sozlesme-wa-sonuc");
    if (uyari) uyari.textContent = "Sonuç belirsiz, telefondan kontrol edin";
    if (sonuc) sonuc.textContent = "";
    yineGoster(true);
    dugmeAc();
  }

  function sonucYaz(j) {
    var uyari = document.getElementById("sozlesme-wa-uyari");
    var sonuc = document.getElementById("sozlesme-wa-sonuc");
    var durum = j && j.durum;
    if (durum === "kuyrukta") {
      if (uyari) uyari.textContent = j.cakisma ? (j.mesaj || "") : "";
      if (sonuc) sonuc.textContent = "Kuyruğa alındı ✓ (birkaç saniyede iletilir)";
      yineGoster(false);
      return;
    }
    if (durum === "gonderildi") {
      if (uyari) uyari.textContent = j.cakisma ? (j.mesaj || "") : "";
      if (sonuc) sonuc.textContent = "Gönderildi ✓";
      yineGoster(false);
      return;
    }
    if (durum === "basarisiz") {
      if (uyari) uyari.textContent = (j && j.mesaj) || "Gönderilemedi: kuyruk kabul etmedi";
      if (sonuc) sonuc.textContent = "";
      yineGoster(!(j && j.yine === false));
      return;
    }
    if (durum === "belirsiz") {
      belirsizGoster();
      return;
    }
    if (sonuc) sonuc.textContent = (j && j.mesaj) || "Gönderim sürüyor";
  }

  function pollBaslat(deneme) {
    pollDur();
    var bas = Date.now();
    pollTimer = setInterval(function () {
      if (!acik || acik.deneme !== deneme) {
        pollDur();
        return;
      }
      if (Date.now() - bas >= POLL_SON) {
        pollDur();
        belirsizGoster();
        return;
      }
      fetch("/giris/api/sozlesme-whatsapp/durum?deneme=" + encodeURIComponent(deneme), { credentials: "same-origin" })
        .then(function (r) {
          return r.json().then(function (j) { return j || {}; }).catch(function () { return {}; });
        })
        .then(function (j) {
          if (!acik || acik.deneme !== deneme) return;
          if (!j.durum || j.durum === "gonderiliyor" || j.durum === "yok") return;
          pollDur();
          dugmeAc();
          sonucYaz(j);
        })
        .catch(function () {});
    }, POLL_MS);
  }

  function pdfIndirGoster(j) {
    var uyari = document.getElementById("sozlesme-wa-uyari");
    var sonuc = document.getElementById("sozlesme-wa-sonuc");
    var mesaj = document.getElementById("sozlesme-wa-mesaj");
    var kutu = document.getElementById("sozlesme-wa-indir");
    var a = document.getElementById("sozlesme-wa-indir-a");
    if (mesaj && j && j.mesaj) mesaj.value = j.mesaj;
    var uy = (j && j.uyari) || "PDF'yi indirip WhatsApp'a ekleyin";
    if (j && j.qr) uy = (j.mesaj_qr || j.neden_yazi || "WhatsApp oturumu yenilenmeli (QR)") + " " + uy;
    if (uyari) uyari.textContent = uy;
    if (sonuc) sonuc.textContent = "";
    var yol = j && j.indir ? String(j.indir) : "";
    if (a && yol.indexOf("/faturalar/tahsilat-pdf/") === 0) {
      a.href = yol;
      a.textContent = "Makbuzu indir";
      if (kutu) kutu.hidden = false;
    }
    yineGoster(false);
  }

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
    var deneme = acik.deneme;
    gonderiyor = true;
    if (btn) btn.disabled = true;
    if (yine) yine.disabled = true;
    var suruyor = false;
    var adres = "/giris/api/sozlesme-whatsapp/gonder";
    var govde = {
      musteri_id: acik.musteriId,
      telefon: ham,
      mesaj: metin,
      buton: acik.buton,
      onay: !!onay,
      deneme: deneme
    };
    if (acik.tahsilatId) {
      adres = "/giris/api/tahsilat-whatsapp/gonder";
      govde = {
        tahsilat_id: acik.tahsilatId,
        musteri_id: acik.musteriId,
        telefon: ham,
        mesaj: metin,
        onay: !!onay,
        deneme: deneme
      };
    }
    fetch(adres, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(govde)
    }).then(function (r) {
      return r.json().then(function (j) { return { kod: r.status, j: j || {} }; }).catch(function () { return { kod: r.status, j: {} }; });
    }).then(function (paket) {
      if (!acik || acik.deneme !== deneme) return;
      var j = paket.j || {};
      if (acik.tahsilatId && (j.pdf_indir || j.geri_dus || j.qr)) {
        if (j.qr) j.neden_yazi = j.mesaj && j.pdf_indir ? "WhatsApp oturumu yenilenmeli (QR)" : (j.mesaj || "WhatsApp oturumu yenilenmeli (QR)");
        pdfIndirGoster(j);
      } else if (j.qr) {
        if (uyari) uyari.textContent = j.mesaj || "WhatsApp oturumu yenilenmeli (QR)";
        if (sonuc) sonuc.textContent = "";
        yineGoster(false);
      } else if (j.geri_dus) {
        webAc(ham, metin);
        if (uyari) uyari.textContent = j.mesaj || "WhatsApp servisi bağlı değil, WhatsApp Web sayfası açılıyor";
        if (sonuc) sonuc.textContent = "";
        yineGoster(false);
      } else if (j.tekrar) {
        if (uyari) uyari.textContent = j.mesaj || "Bu mesaj bu numaraya az önce gönderildi.";
        yineGoster(true);
        if (sonuc) sonuc.textContent = "";
      } else if (j.ok && j.durum === "gonderiliyor") {
        suruyor = true;
        sonucYaz(j);
        pollBaslat(deneme);
      } else if (j.durum === "kuyrukta" || j.durum === "gonderildi" || j.durum === "belirsiz" || j.durum === "basarisiz") {
        sonucYaz(j);
      } else if (j.ok) {
        if (uyari) uyari.textContent = "";
        if (sonuc) sonuc.textContent = j.mesaj || "Gönderiliyor";
        yineGoster(false);
      } else {
        if (uyari) uyari.textContent = j.mesaj || "Gönderilemedi";
        if (sonuc) sonuc.textContent = "";
      }
    }).catch(function () {
      if (!acik || acik.deneme !== deneme) return;
      if (uyari) uyari.textContent = "Gönderilemedi";
    }).then(function () {
      if (suruyor) return;
      dugmeAc();
    });
  }

  function sozlesmeWhatsAppAc(opts) {
    opts = opts || {};
    stil();
    pollDur();
    gonderiyor = false;
    var eski = document.getElementById("sozlesme-wa-perde");
    if (eski) eski.remove();
    acik = {
      buton: opts.buton === "makbuz" ? "makbuz" : "ust",
      musteriId: parseInt(opts.musteriId, 10) || 0,
      tahsilatId: parseInt(opts.tahsilatId, 10) || 0,
      deneme: denemeUret()
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
      '<p id="sozlesme-wa-indir" hidden><a id="sozlesme-wa-indir-a" href="#"></a></p>' +
      '<p id="sozlesme-wa-sonuc"></p>' +
      '<div><button type="button" id="sozlesme-wa-gonder">Gönder</button>' +
      '<button type="button" id="sozlesme-wa-yine">Yine de gönder</button>' +
      '<button type="button" id="sozlesme-wa-kapat">Kapat</button></div></form>';
    document.body.appendChild(perde);
    document.getElementById("sozlesme-wa-kapat").onclick = function () {
      pollDur();
      gonderiyor = false;
      perde.remove();
      acik = null;
    };
    document.getElementById("sozlesme-wa-gonder").onclick = function () { gonder(false); };
    document.getElementById("sozlesme-wa-yine").onclick = function () {
      pollDur();
      if (acik) acik.deneme = denemeUret();
      gonder(true);
    };
    var mesaj = document.getElementById("sozlesme-wa-mesaj");
    if (mesaj) {
      mesaj.value = opts.mesaj || "";
      mesaj.addEventListener("input", function () { if (acik) acik.mesajDokunuldu = true; });
    }
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
    if (acik.tahsilatId) {
      var tid = acik.tahsilatId;
      fetch("/giris/api/tahsilat-whatsapp/hazir?tahsilat_id=" + encodeURIComponent(tid), { credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (j) { return j || {}; }).catch(function () { return {}; }); })
        .then(function (j) {
          if (!acik || acik.tahsilatId !== tid) return;
          var kutu = document.getElementById("sozlesme-wa-mesaj");
          if (kutu && !acik.mesajDokunuldu && j.mesaj) kutu.value = j.mesaj;
          if (j.pdf_indir || j.qr) pdfIndirGoster(j);
        })
        .catch(function () {});
    }
    perde.addEventListener("submit", function (ev) { ev.preventDefault(); });
  }

  function onEkId(onEk) {
    return onEk === "sozlesme" ? "sozlesme_tahsilat" : "giris_tahsilat";
  }

  var formlar = { giris: null, sozlesme: null };

  function durumAl(onEk) {
    var api = window.tahsilatWaDurum;
    if (!api) return null;
    if (!formlar[onEk]) formlar[onEk] = api.bos();
    return formlar[onEk];
  }

  function dugme(onEk) {
    return document.getElementById(onEk + "-tahsilat-wa");
  }

  function ciz(onEk) {
    var b = dugme(onEk);
    var s = formlar[onEk];
    if (!b) return;
    var acikMi = !!(s && s.aktif && s.tahsilatId);
    b.disabled = !acikMi;
    b.title = acikMi ? "" : "Önce makbuzu kaydedin";
  }

  function alanOku(onEk, ad) {
    var el = document.getElementById(onEkId(onEk) + "_" + ad);
    return el ? String(el.value || "") : "";
  }

  function tahsilatWaKayit(onEk, data, musteriId) {
    var api = window.tahsilatWaDurum;
    if (!api) return;
    var id = parseInt(data && data.tahsilat_id, 10) || 0;
    if (!id) {
      formlar[onEk] = api.pasif(durumAl(onEk));
      ciz(onEk);
      return;
    }
    formlar[onEk] = api.kayit(durumAl(onEk), {
      tahsilat_id: id,
      makbuz_no: data.makbuz_no,
      musteri_id: musteriId,
      tutar: alanOku(onEk, "tutar"),
      tarih: alanOku(onEk, "tarih"),
      aciklama: alanOku(onEk, "aciklama")
    });
    ciz(onEk);
  }

  function tahsilatWaPasif(onEk) {
    var api = window.tahsilatWaDurum;
    if (!api) return;
    formlar[onEk] = api.pasif(durumAl(onEk));
    ciz(onEk);
  }

  function tahsilatWaMusteri(onEk, id) {
    var api = window.tahsilatWaDurum;
    if (!api) return;
    formlar[onEk] = api.musteri(durumAl(onEk), id);
    ciz(onEk);
  }

  function tahsilatWaAc(onEk) {
    var s = formlar[onEk];
    if (!s || !s.aktif || !(parseInt(s.tahsilatId, 10) > 0)) return;
    var ph = document.getElementById(onEkId(onEk) + "_musteri_phone");
    sozlesmeWhatsAppAc({
      buton: "makbuz",
      musteriId: s.musteriId,
      tahsilatId: s.tahsilatId,
      telefon: ph ? ph.value || "" : "",
      mesaj: ""
    });
  }

  function bagla() {
    if (typeof document === "undefined") return;
    if (!document.getElementById("tahsilat-wa-stil")) {
      var st = document.createElement("style");
      st.id = "tahsilat-wa-stil";
      st.textContent = ".tahsilat-btn-whatsapp:disabled{opacity:.45;cursor:not-allowed;}";
      if (document.head) document.head.appendChild(st);
    }
    ["giris", "sozlesme"].forEach(function (onEk) {
      ciz(onEk);
      ["tutar", "tarih", "aciklama"].forEach(function (ad) {
        var el = document.getElementById(onEkId(onEk) + "_" + ad);
        if (!el || el.getAttribute("data-wa-bag")) return;
        el.setAttribute("data-wa-bag", "1");
        var degisti = function () {
          var api = window.tahsilatWaDurum;
          if (!api) return;
          formlar[onEk] = api.alan(durumAl(onEk), ad, el.value);
          ciz(onEk);
        };
        el.addEventListener("input", degisti);
        el.addEventListener("change", degisti);
      });
    });
  }

  window.sozlesmeWhatsAppAc = sozlesmeWhatsAppAc;
  window.tahsilatWaKayit = tahsilatWaKayit;
  window.tahsilatWaPasif = tahsilatWaPasif;
  window.tahsilatWaMusteri = tahsilatWaMusteri;
  window.tahsilatWaAc = tahsilatWaAc;
  bagla();
})();
