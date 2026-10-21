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
      "#sozlesme-wa-indir{font-size:12px;margin:2px 0;}" +
      "#sozlesme-wa-indir a{color:#80deea;}" +
      "#sozlesme-wa-web{font-size:12px;margin:2px 0;}" +
      "#sozlesme-wa-web a{color:#80deea;}" +
      "#sozlesme-wa-web-ipucu{display:block;color:#b0bec5;margin-top:4px;}" +
      "#sozlesme-wa-qr-kutu{margin:8px 0;}" +
      "#sozlesme-wa-qr-yedek{margin-left:6px;color:#80deea;}" +
      "#sozlesme-wa-qr-not{margin-left:6px;color:#ffcc80;}" +
      "#sozlesme-wa-yine{display:none;background:#6a4a12;}" +
      "#sozlesme-wa-numaralar label{flex-direction:row;align-items:center;gap:8px;}";
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

  function maske4(raw) {
    var s = String(raw || "").replace(/\D/g, "");
    if (s.length < 4) return "****";
    return "****" + s.slice(-4);
  }

  function normTel(raw) {
    var s = String(raw || "").replace(/\D/g, "");
    if (s.indexOf("00") === 0) s = s.slice(2);
    if (s.charAt(0) === "0" && s.length === 11) s = "90" + s.slice(1);
    else if (s.length === 10 && s.charAt(0) === "5") s = "90" + s;
    return /^[1-9]\d{10,14}$/.test(s) ? s : "";
  }

  function seciliNumaralar() {
    var kutu = document.getElementById("sozlesme-wa-numaralar");
    var out = [];
    if (!kutu) return out;
    kutu.querySelectorAll("input[type=checkbox]:checked").forEach(function (el) {
      out.push({
        sira: parseInt(el.getAttribute("data-sira"), 10) || out.length + 1,
        tel: el.getAttribute("data-tel") || ""
      });
    });
    return out;
  }

  function secimDugme() {
    if (!acik || acik.tahsilatId || gonderiyor) return;
    var n = seciliNumaralar().length;
    var btn = document.getElementById("sozlesme-wa-gonder");
    var uyari = document.getElementById("sozlesme-wa-uyari");
    var ek = document.getElementById("sozlesme-wa-ek");
    var ekVar = ek && String(ek.value || "").trim();
    if (btn) btn.disabled = n < 1 && !ekVar;
    if (uyari && n < 1 && !ekVar) uyari.textContent = "En az bir numara seçin";
    else if (uyari && uyari.textContent === "En az bir numara seçin") uyari.textContent = "";
  }

  function ekBirlestir(liste) {
    var ek = document.getElementById("sozlesme-wa-ek");
    if (!ek) return "";
    var ham = String(ek.value || "").trim();
    if (!ham) return "";
    var n = normTel(ham);
    if (!n) return "Telefon numarası geçersiz";
    var ayni = liste.some(function (o) { return normTel(o.tel) === n; });
    if (ayni) return "";
    if (liste.length >= 3) return "En fazla 3 alıcı";
    var sira = 3;
    liste.forEach(function (o) { if (o.sira >= sira) sira = o.sira + 1; });
    liste.push({ sira: sira, tel: n, serbest: true });
    return "";
  }

  function kutulariYaz(kisiler, ilkTel) {
    var kutu = document.getElementById("sozlesme-wa-numaralar");
    if (!kutu) return;
    var kaynak = (kisiler && kisiler.length) ? kisiler.map(function (k) { return k && k.telefon; }) : [ilkTel];
    var gorulen = {};
    var liste = [];
    kaynak.forEach(function (ham) {
      var n = normTel(ham);
      if (!n || gorulen[n] || liste.length >= 2) return;
      gorulen[n] = 1;
      liste.push(String(ham || ""));
    });
    kutu.innerHTML = "";
    kutu.hidden = false;
    liste.forEach(function (ham, i) {
      var lab = document.createElement("label");
      lab.className = "sozlesme-wa-kutu";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = i === 0;
      cb.setAttribute("data-sira", String(i + 1));
      cb.setAttribute("data-tel", ham);
      cb.addEventListener("change", secimDugme);
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(" " + (i + 1) + ". numara · " + maske4(ham)));
      kutu.appendChild(lab);
    });
    var ekAc = document.createElement("a");
    ekAc.href = "#";
    ekAc.id = "sozlesme-wa-ek-ac";
    ekAc.textContent = "Başka numara ekle";
    ekAc.onclick = function (ev) {
      ev.preventDefault();
      if (document.getElementById("sozlesme-wa-ek")) return;
      ekAc.hidden = true;
      var giris = document.createElement("input");
      giris.id = "sozlesme-wa-ek";
      giris.setAttribute("inputmode", "tel");
      giris.placeholder = "Telefon";
      giris.addEventListener("input", secimDugme);
      kutu.appendChild(giris);
    };
    kutu.appendChild(ekAc);
    var satir = document.getElementById("sozlesme-wa-satir");
    var sec = document.getElementById("sozlesme-wa-sec");
    if (satir) satir.hidden = true;
    if (sec) sec.hidden = true;
    secimDugme();
  }

  function kisileriYaz(kisiler, ilkTel, ilkAd) {
    if (acik && !acik.tahsilatId) {
      kutulariYaz(kisiler, ilkTel);
      return;
    }
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
  var POLL_SON = 180000;

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
    if (yine) yine.disabled = false;
    if (acik && !acik.tahsilatId) {
      secimDugme();
      return;
    }
    if (btn) btn.disabled = false;
  }

  function yineGoster(goster) {
    var yine = document.getElementById("sozlesme-wa-yine");
    if (yine) yine.style.display = goster ? "inline-block" : "none";
  }

  var CIFT_UYARI = "Mesaj gitmiş olabilir, çift gönderilebilir. Telefondan kontrol ettiniz mi?";

  function belirsizGoster() {
    var uyari = document.getElementById("sozlesme-wa-uyari");
    var sonuc = document.getElementById("sozlesme-wa-sonuc");
    if (uyari) uyari.textContent = "Sonuç belirsiz, telefondan kontrol edin";
    if (sonuc) sonuc.textContent = "";
    if (acik) acik.ciftSor = true;
    yineGoster(true);
    dugmeAc();
  }

  function sonucYaz(j) {
    var uyari = document.getElementById("sozlesme-wa-uyari");
    var sonuc = document.getElementById("sozlesme-wa-sonuc");
    var durum = j && j.durum;
    if (acik && durum !== "belirsiz") acik.ciftSor = false;
    if (durum === "kuyrukta" || durum === "gonderiliyor") {
      if (uyari) uyari.textContent = j.cakisma ? (j.mesaj || "") : "";
      if (sonuc) sonuc.textContent = "Kuyruğa alındı…";
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
        var uyari = document.getElementById("sozlesme-wa-uyari");
        var sonuc = document.getElementById("sozlesme-wa-sonuc");
        if (uyari) uyari.textContent = "Sonuç henüz yok; birkaç dakika sonra durum yenilenir";
        if (sonuc) sonuc.textContent = "";
        yineGoster(true);
        dugmeAc();
        return;
      }
      fetch("/giris/api/sozlesme-whatsapp/durum?deneme=" + encodeURIComponent(deneme), { credentials: "same-origin" })
        .then(function (r) {
          return r.json().then(function (j) { return j || {}; }).catch(function () { return {}; });
        })
        .then(function (j) {
          if (!acik || acik.deneme !== deneme) return;
          if (!j.durum || j.durum === "gonderiliyor" || j.durum === "kuyrukta" || j.durum === "yok") {
            if (j.durum === "kuyrukta" || j.durum === "gonderiliyor") sonucYaz(j);
            return;
          }
          pollDur();
          dugmeAc();
          sonucYaz(j);
        })
        .catch(function () {});
    }, POLL_MS);
  }

  function yedekLink(j) {
    var kutu = document.getElementById("sozlesme-wa-indir");
    var a = document.getElementById("sozlesme-wa-indir-a");
    var yol = j && j.indir ? String(j.indir) : "";
    if (!(a && kutu)) return;
    if (yol.indexOf("/faturalar/tahsilat-pdf/") === 0) {
      a.href = yol;
      a.textContent = "Makbuzu indir";
      kutu.hidden = false;
    }
  }

  function webAc(tel, mesaj) {
    var num = tel;
    if (typeof girisTelefonuWhatsAppRakam === "function") num = girisTelefonuWhatsAppRakam(tel) || tel;
    if (typeof bestOfficeWhatsAppWebAc === "function") bestOfficeWhatsAppWebAc(num, mesaj);
  }

  function webElleGoster(tel, mesaj) {
    var kutu = document.getElementById("sozlesme-wa-web");
    var a = document.getElementById("sozlesme-wa-web-a");
    if (!(kutu && a)) return;
    a.href = "#";
    a.textContent = "Kendi WhatsApp Web'imde aç (ofis hattı olmayabilir)";
    a.onclick = function (ev) {
      ev.preventDefault();
      webAc(tel, mesaj);
    };
    kutu.hidden = false;
  }

  function durumSatir(s) {
    var yazi = s.durum === "gonderildi" ? "Gönderildi"
      : (s.durum === "kuyrukta" || s.durum === "gonderiliyor") ? "Kuyruğa alındı"
      : s.durum === "belirsiz" ? "Sonuç belirsiz, telefondan kontrol edin"
      : s.durum === "tekrar" ? (s.mesaj || "Bu mesaj bu numaraya az önce gönderildi.")
      : ("Gönderilemedi" + (s.mesaj ? ": " + s.mesaj : ""));
    return s.sira + ". numara " + maske4(s.tel) + ": " + yazi;
  }

  function durumCiz(satirlar) {
    var sonuc = document.getElementById("sozlesme-wa-sonuc");
    if (sonuc) sonuc.textContent = (satirlar || []).map(durumSatir).join(" · ");
  }

  function gonderSecili(onay) {
    if (gonderiyor || !acik) return;
    var uyari = document.getElementById("sozlesme-wa-uyari");
    var mesaj = document.getElementById("sozlesme-wa-mesaj");
    var btn = document.getElementById("sozlesme-wa-gonder");
    var yine = document.getElementById("sozlesme-wa-yine");
    var metin = mesaj ? mesaj.value : "";
    var liste = seciliNumaralar();
    var yeniden = false;
    if (onay && acik.satirlar) {
      var tekrar = acik.satirlar.filter(function (s) {
        return s.durum === "tekrar" || s.durum === "belirsiz";
      });
      if (tekrar.length) {
        liste = tekrar.map(function (s) { return { sira: s.sira, tel: s.tel, serbest: !!s.serbest }; });
        yeniden = true;
      }
    }
    if (!yeniden) {
      var ekHata = ekBirlestir(liste);
      if (ekHata) {
        if (uyari) uyari.textContent = ekHata;
        return;
      }
    }
    if (!liste.length) {
      if (uyari) uyari.textContent = "En az bir numara seçin";
      if (btn) btn.disabled = true;
      return;
    }
    if (!String(metin || "").trim()) {
      if (uyari) uyari.textContent = "Mesaj boş";
      return;
    }
    if (uyari && !onay) uyari.textContent = "";
    gonderiyor = true;
    if (btn) btn.disabled = true;
    if (yine) yine.disabled = true;
    var satirlar = [];
    var i = 0;
    function bitir() {
      acik.satirlar = satirlar;
      durumCiz(satirlar);
      var bekler = satirlar.some(function (s) { return s.durum === "kuyrukta" || s.durum === "gonderiliyor"; });
      var sor = satirlar.some(function (s) { return s.durum === "tekrar" || s.durum === "belirsiz"; });
      if (sor) yineGoster(true);
      if (bekler) pollCoklu();
      else dugmeAc();
    }
    function adim() {
      if (!acik) return;
      if (i >= liste.length) { bitir(); return; }
      var oge = liste[i];
      i += 1;
      var deneme = denemeUret();
      var govde = {
        musteri_id: acik.musteriId,
        telefon: oge.tel,
        mesaj: metin,
        buton: acik.buton,
        onay: !!onay,
        deneme: deneme
      };
      if (oge.serbest) govde.serbest = true;
      fetch("/giris/api/sozlesme-whatsapp/gonder", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(govde)
      }).then(function (r) {
        return r.json().then(function (j) { return { kod: r.status, j: j || {} }; }).catch(function () { return { kod: r.status, j: {} }; });
      }).then(function (paket) {
        var j = paket.j || {};
        var durum = "hata";
        var yazi = j.mesaj || "Gönderilemedi";
        if (paket.kod === 409 && j.tekrar) {
          durum = "tekrar";
          yazi = j.mesaj || "Bu mesaj bu numaraya az önce gönderildi.";
        } else if (paket.kod === 429) {
          durum = "hata";
          yazi = j.mesaj || "Gönderim sınırı doldu. Bu numara gönderilmedi.";
        } else if (j.durum === "gonderildi") {
          durum = "gonderildi";
          yazi = "Gönderildi";
        } else if (j.durum === "belirsiz") {
          durum = "belirsiz";
          yazi = "Sonuç belirsiz, telefondan kontrol edin";
        } else if (j.ok && (j.durum === "gonderiliyor" || j.durum === "kuyrukta")) {
          durum = "kuyrukta";
          yazi = "Kuyruğa alındı";
        } else if (j.qr || j.geri_dus) {
          if (uyari) uyari.textContent = j.mesaj || yazi;
          if (j.qr) qrDugmeGoster(true);
        }
        satirlar.push({ sira: oge.sira, tel: oge.tel, durum: durum, mesaj: yazi, deneme: deneme, serbest: !!oge.serbest });
        durumCiz(satirlar);
        if (paket.kod === 429 || j.qr || j.geri_dus) {
          while (i < liste.length) {
            var kalan = liste[i];
            i += 1;
            satirlar.push({
              sira: kalan.sira,
              tel: kalan.tel,
              durum: "hata",
              mesaj: j.qr || j.geri_dus ? "Gönderilmedi" : yazi,
              deneme: "",
              serbest: !!kalan.serbest
            });
          }
        }
        adim();
      }).catch(function () {
        satirlar.push({ sira: oge.sira, tel: oge.tel, durum: "hata", mesaj: "Gönderilemedi", deneme: deneme });
        adim();
      });
    }
    adim();
  }

  function pollCoklu() {
    pollDur();
    var bas = Date.now();
    pollTimer = setInterval(function () {
      if (!acik || !acik.satirlar) { pollDur(); return; }
      var aciklar = acik.satirlar.filter(function (s) {
        return s.deneme && (s.durum === "kuyrukta" || s.durum === "gonderiliyor");
      });
      if (!aciklar.length) { pollDur(); dugmeAc(); return; }
      if (Date.now() - bas >= POLL_SON) {
        pollDur();
        aciklar.forEach(function (s) { s.durum = "belirsiz"; s.mesaj = "Sonuç belirsiz, telefondan kontrol edin"; });
        durumCiz(acik.satirlar);
        yineGoster(true);
        dugmeAc();
        return;
      }
      aciklar.forEach(function (s) {
        fetch("/giris/api/sozlesme-whatsapp/durum?deneme=" + encodeURIComponent(s.deneme), { credentials: "same-origin" })
          .then(function (r) { return r.json().then(function (j) { return j || {}; }).catch(function () { return {}; }); })
          .then(function (j) {
            if (!j.durum || j.durum === "gonderiliyor" || j.durum === "kuyrukta" || j.durum === "yok") {
              if (j.durum === "kuyrukta" || j.durum === "gonderiliyor") s.durum = "kuyrukta";
              durumCiz(acik.satirlar);
              return;
            }
            s.durum = j.durum;
            s.mesaj = j.mesaj || "";
            durumCiz(acik.satirlar);
            var kaldi = acik.satirlar.some(function (x) {
              return x.deneme && (x.durum === "kuyrukta" || x.durum === "gonderiliyor");
            });
            if (!kaldi) { pollDur(); dugmeAc(); }
          })
          .catch(function () {});
      });
    }, POLL_MS);
  }

  function gonder(onay) {
    if (gonderiyor || !acik) return;
    if (!acik.tahsilatId) {
      gonderSecili(onay);
      return;
    }
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
      yedekLink(j);
      if (j.qr) {
        if (uyari) uyari.textContent = j.mesaj || "WhatsApp oturumu yenilenmeli (QR)";
        if (sonuc) sonuc.textContent = "";
        qrDugmeGoster(true);
        yineGoster(false);
      } else if (j.geri_dus && j.neden === "oturum") {
        webElleGoster(ham, metin);
        if (uyari) uyari.textContent = j.mesaj || "Oturum yenileniyor, 1 dk sonra tekrar deneyin.";
        if (sonuc) sonuc.textContent = "";
        yineGoster(false);
      } else if (j.geri_dus) {
        webAc(ham, metin);
        if (uyari) uyari.textContent = j.mesaj || "WhatsApp servisi bağlı değil, WhatsApp Web sayfası açılıyor";
        if (sonuc) sonuc.textContent = "";
        yineGoster(false);
      } else if (j.tekrar) {
        if (uyari) uyari.textContent = j.mesaj || "Bu mesaj bu numaraya az önce gönderildi.";
        if (acik) acik.ciftSor = j.durum === "belirsiz";
        yineGoster(true);
        if (sonuc) sonuc.textContent = "";
      } else if (j.ok && (j.durum === "gonderiliyor" || j.durum === "kuyrukta")) {
        suruyor = true;
        sonucYaz(j);
        pollBaslat(deneme);
      } else if (j.durum === "gonderildi" || j.durum === "belirsiz" || j.durum === "basarisiz") {
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
      '<div id="sozlesme-wa-numaralar"></div>' +
      '<select id="sozlesme-wa-sec" hidden></select>' +
      '<div id="sozlesme-wa-satir"><span id="sozlesme-wa-ad"></span>' +
      '<input id="sozlesme-wa-tel" inputmode="tel" placeholder="Telefon"></div>' +
      '<label>Mesaj<textarea id="sozlesme-wa-mesaj" rows="6"></textarea></label>' +
      '<p id="sozlesme-wa-uyari"></p>' +
      '<p id="sozlesme-wa-qr-kutu" hidden><button type="button" id="sozlesme-wa-qr-okut">QR Okut</button>' +
      '<button type="button" id="sozlesme-wa-qr-kapat">QR Kapat</button>' +
      '<a id="sozlesme-wa-qr-yedek" href="/whatsapp/qr-ac" hidden>Açılamadı. QR sayfasını bu sekmede aç</a>' +
      '<span id="sozlesme-wa-qr-not" hidden>bu sekmeyi kapatabilirsiniz</span></p>' +
      '<p id="sozlesme-wa-indir" hidden><a id="sozlesme-wa-indir-a" href="#"></a></p>' +
      '<p id="sozlesme-wa-web" hidden><a id="sozlesme-wa-web-a" href="#">Kendi WhatsApp Web\'imde aç (ofis hattı olmayabilir)</a>' +
      '<span id="sozlesme-wa-web-ipucu">ERP üzerinden ofis hattıyla göndermek için Giriş → WhatsApp ekranından QR\'ı ofis telefonuyla okutun.</span></p>' +
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
    document.getElementById("sozlesme-wa-qr-okut").onclick = function () { qrSayfaAc(); };
    document.getElementById("sozlesme-wa-qr-kapat").onclick = function () { qrSayfaKapat(); };
    document.getElementById("sozlesme-wa-gonder").onclick = function () { gonder(false); };
    document.getElementById("sozlesme-wa-yine").onclick = function () {
      pollDur();
      if (acik && acik.ciftSor) {
        var uyari = document.getElementById("sozlesme-wa-uyari");
        if (uyari) uyari.textContent = CIFT_UYARI;
        acik.ciftSor = false;
        return;
      }
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
      var numKutu = document.getElementById("sozlesme-wa-numaralar");
      if (numKutu) numKutu.hidden = true;
      var tid = acik.tahsilatId;
      fetch("/giris/api/tahsilat-whatsapp/hazir?tahsilat_id=" + encodeURIComponent(tid), { credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (j) { return j || {}; }).catch(function () { return {}; }); })
        .then(function (j) {
          if (!acik || acik.tahsilatId !== tid) return;
          var kutu = document.getElementById("sozlesme-wa-mesaj");
          if (kutu && !acik.mesajDokunuldu && j.mesaj) kutu.value = j.mesaj;
          yedekLink(j);
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

  var qrPencere = null;
  function qrSayfaAc() {
    var w = null;
    try { w = window.open("/whatsapp/qr-ac", "BestOfficeERP_QR"); } catch (e) { w = null; }
    var yedek = document.getElementById("sozlesme-wa-qr-yedek");
    if (!w) {
      qrPencere = null;
      window.__erpQrPencere = null;
      if (yedek) yedek.hidden = false;
      return false;
    }
    qrPencere = w;
    window.__erpQrPencere = w;
    if (yedek) yedek.hidden = true;
    return true;
  }
  function qrSayfaKapat() {
    var w = window.__erpQrPencere || qrPencere;
    var kapandi = false;
    try {
      if (w && !w.closed) {
        w.close();
        kapandi = !!w.closed;
      }
    } catch (e) { kapandi = false; }
    var not = document.getElementById("sozlesme-wa-qr-not");
    if (not) not.hidden = kapandi;
    return kapandi;
  }
  function qrDugmeGoster(goster) {
    var kutu = document.getElementById("sozlesme-wa-qr-kutu");
    if (kutu) kutu.hidden = !goster;
  }
  function qrYedekGoster() {
    qrDugmeGoster(true);
    var yedek = document.getElementById("sozlesme-wa-qr-yedek");
    if (yedek) yedek.hidden = false;
  }
  window.erpQrSayfaAc = qrSayfaAc;
  window.erpQrSayfaKapat = qrSayfaKapat;
  window.erpQrDugmeGoster = qrDugmeGoster;
  window.erpQrYedekGoster = qrYedekGoster;
  window.sozlesmeWhatsAppAc = sozlesmeWhatsAppAc;
  window.tahsilatWaKayit = tahsilatWaKayit;
  window.tahsilatWaPasif = tahsilatWaPasif;
  window.tahsilatWaMusteri = tahsilatWaMusteri;
  window.tahsilatWaAc = tahsilatWaAc;
  bagla();
})();
