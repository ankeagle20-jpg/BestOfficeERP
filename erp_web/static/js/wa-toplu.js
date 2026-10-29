(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined") return;
  api.bagla();
})(typeof window !== "undefined" ? window : this, function () {
  var SABLON = {
    bayram: "Merhaba {yetkili}, bayramınız kutlu olsun.",
    tebrik: "Merhaba {yetkili}, tebrik ederiz.",
    duyuru: "Merhaba {yetkili}, bir duyurumuz var."
  };
  var SEBEP = { pasif: "pasif", haric: "hariç", numarasiz: "numarasız", uygunsuz: "uygunsuz" };

  function wtNorm(raw) {
    var s = String(raw || "").replace(/\D/g, "");
    if (s.indexOf("00") === 0) s = s.slice(2);
    if (s.charAt(0) === "0" && s.length === 11) s = "90" + s.slice(1);
    else if (s.length === 10 && s.charAt(0) === "5") s = "90" + s;
    return /^[1-9]\d{10,14}$/.test(s) ? s : "";
  }

  function wtSecimUygula(satirlar, tumunuAcik) {
    return (satirlar || []).map(function (s) {
      if (s && s.gorunur && s.secilebilir) return !!tumunuAcik;
      return !!(s && s.secili);
    });
  }

  function wtSureMetin(o) {
    var sure = Number((o && o.sure_dk) || 0);
    var gun = Number((o && o.gun) || 0);
    var gunGoster = !!(o && o.gun_goster);
    if (!(sure > 0)) return "";
    var metin = sure < 60
      ? ("yaklaşık " + sure + " dakika")
      : ("yaklaşık " + Math.ceil(sure / 60) + " saat");
    if (gunGoster && gun > 1) metin += " / " + gun + " günde";
    return metin;
  }

  function wtOzet(numaralar, tavan) {
    var gorulen = {};
    var n = 0;
    (numaralar || []).forEach(function (ham) {
      var k = wtNorm(ham);
      if (!k || gorulen[k]) return;
      gorulen[k] = 1;
      n += 1;
    });
    var cap = parseInt(tavan, 10);
    if (!(cap >= 50 && cap <= 250)) cap = 150;
    var sure = n <= 0 ? 0 : Math.ceil(n / 0.75);
    var gun = n <= 0 ? 0 : Math.ceil(n / cap);
    var gunGoster = gun > 1;
    var o = {
      secili: (numaralar || []).length,
      alici: n,
      sure_dk: sure,
      saat: sure <= 0 ? 0 : Math.ceil(sure / 60),
      gun: gun,
      gun_goster: gunGoster
    };
    o.sure_metin = wtSureMetin(o);
    return o;
  }

  function wtTaslakNedenMetin(neden) {
    if (neden === "gecersiz") {
      return "Mesaj boş veya 1000 karakteri aşıyor / tavan 50–250 dışında";
    }
    if (neden === "durum") {
      return "Açık kampanya var (çalışıyor/durduruldu). Önce Bitir'e bas, sonra yeni şablonu kaydet";
    }
    if (neden === "yok") return "Kampanya bulunamadı";
    return "Şablon kaydedilemedi";
  }

  function bagla() {
    haricBagla();
    var panel = document.getElementById("wtPanel");
    if (!panel) return;
    if (panel.getAttribute("data-wt-bagli") === "1") return;
    panel.setAttribute("data-wt-bagli", "1");

    var panelGecikme = document.getElementById("wtPanelGecikme");
    var btnGecikme = document.getElementById("wtAltGecikme");
    var btnToplu = document.getElementById("wtAltToplu");
    var sablon = document.getElementById("wtSablon");
    var sayac = document.getElementById("wtSayac");
    var tavan = document.getElementById("wtTavan");
    var ara = document.getElementById("wtAra");
    var durum = document.getElementById("wtDurum");
    var tumunu = document.getElementById("wtTumunu");
    var govde = document.getElementById("wtGovde");
    var ozet = document.getElementById("wtOzet");
    var testNo = document.getElementById("wtTestNo");
    var testSonuc = document.getElementById("wtTestSonuc");
    var baslat = document.getElementById("wtBaslat");
    var satirlar = [];
    var secili = {};
    var kampanyaId = null;
    var kampanyaDurum = "";
    var bekleyenSayi = 0;
    var testOk = false;
    var listeYuklendi = false;
    var listeYukleniyor = false;

    function kac() {
      if (sayac && sablon) sayac.textContent = String((sablon.value || "").length) + "/1000";
    }

    function post(url, govdePost) {
      return fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(govdePost || {})
      }).then(function (r) {
        return r.json().then(function (j) { return { kod: r.status, govde: j || {} }; });
      });
    }

    function baslatDurumu() {
      var o = hesap();
      if (baslat) baslat.disabled = !testOk || o.alici < 1;
      var bitirBtn = document.getElementById("wtBitir");
      if (bitirBtn) bitirBtn.disabled = !kampanyaId || kampanyaDurum === "calisiyor";
    }

    function gorunurMu(s) {
      var q = (ara && ara.value || "").trim().toLowerCase();
      var d = durum ? durum.value : "hepsi";
      if (d && d !== "hepsi" && s.durum !== d) return false;
      if (q && String(s.ad || "").toLowerCase().indexOf(q) < 0) return false;
      return true;
    }

    function hesap() {
      var nolar = [];
      satirlar.forEach(function (s) {
        if (!secili[s.mid] || s.sebep) return;
        var bir = (s.adaylar && s.adaylar[0] && s.adaylar[0].telefon) || "";
        nolar.push(bir);
      });
      return wtOzet(nolar, tavan ? tavan.value : 150);
    }

    function kampanyaDurumYaz() {
      var el = document.getElementById("wtKampanyaDurum");
      if (!el) return;
      if (kampanyaDurum === "calisiyor" || kampanyaDurum === "durduruldu") {
        var etiket = kampanyaDurum === "calisiyor" ? "çalışıyor" : "durduruldu";
        // durumYukle yalnız bekleyen verir; X/Y yok — yeni uç yok
        el.textContent = "Açık kampanya: " + etiket + ", bekleyen " + Number(bekleyenSayi || 0);
        el.hidden = false;
      } else {
        el.textContent = "";
        el.hidden = true;
      }
    }

    function ozetYaz() {
      var o = hesap();
      if (ozet) {
        var sure = o.sure_metin || wtSureMetin(o);
        ozet.textContent = "Seçili: " + o.secili + " · Alıcı: " + o.alici +
          (sure ? (" · " + sure) : "");
      }
      if (tumunu) {
        var gor = satirlar.filter(function (s) { return gorunurMu(s) && !s.sebep; });
        tumunu.checked = gor.length > 0 && gor.every(function (s) { return !!secili[s.mid]; });
      }
      baslatDurumu();
    }

    function hucre(metin) {
      var td = document.createElement("td");
      td.textContent = metin || "";
      return td;
    }

    function ciz() {
      if (!govde) return;
      govde.innerHTML = "";
      satirlar.forEach(function (s) {
        if (!gorunurMu(s)) return;
        var tr = document.createElement("tr");
        var tdSec = document.createElement("td");
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.disabled = !!s.sebep;
        cb.checked = !s.sebep && !!secili[s.mid];
        cb.addEventListener("change", function () {
          if (s.sebep) return;
          secili[s.mid] = cb.checked;
          ozetYaz();
        });
        tdSec.appendChild(cb);
        if (s.sebep) tdSec.appendChild(document.createTextNode(" " + (SEBEP[s.sebep] || s.sebep)));
        if (s.mukerrer) {
          var rozet = document.createElement("span");
          rozet.textContent = " aynı numara";
          tdSec.appendChild(rozet);
        }
        tr.appendChild(tdSec);
        tr.appendChild(hucre(s.ad || ""));
        tr.appendChild(hucre(s.durum === "aktif" ? "aktif" : (s.durum === "pasif" ? "pasif" : s.durum || "")));
        var aday = s.adaylar || [];
        var i;
        for (i = 0; i < 4; i++) {
          var a = aday[i];
          tr.appendChild(hucre(a ? ((a.etiket ? a.etiket + " " : "") + (a.telefon || "")) : ""));
        }
        govde.appendChild(tr);
      });
      ozetYaz();
    }

    function listeYukle(zorla) {
      if (listeYukleniyor) return;
      if (listeYuklendi && !zorla) return;
      listeYukleniyor = true;
      fetch("/whatsapp/api/toplu-liste", { cache: "no-store" }).then(function (r) {
        return r.json();
      }).then(function (j) {
        satirlar = (j && j.satirlar) || [];
        listeYuklendi = true;
        ciz();
      }).catch(function () {
        if (testSonuc) testSonuc.textContent = "Liste alınamadı";
      }).then(function () {
        listeYukleniyor = false;
      });
    }

    function durumYukle() {
      return fetch("/whatsapp/api/toplu-durum").then(function (r) { return r.json(); }).then(function (j) {
        var k = j && j.kampanya;
        if (!k) {
          kampanyaId = null;
          kampanyaDurum = "";
          bekleyenSayi = 0;
          testOk = false;
          kampanyaDurumYaz();
          baslatDurumu();
          return;
        }
        kampanyaId = k.id;
        kampanyaDurum = k.durum || "";
        bekleyenSayi = Number(k.bekleyen || 0);
        testOk = !!k.test_ok;
        if (k.tavan && tavan) tavan.value = String(k.tavan);
        kampanyaDurumYaz();
        baslatDurumu();
      }).catch(function () {
        if (testSonuc) testSonuc.textContent = "İstek başarısız";
      });
    }

    function altSekmeAktif(ad) {
      var toplu = ad === "toplu";
      if (btnGecikme) {
        btnGecikme.classList.toggle("active", !toplu);
        btnGecikme.setAttribute("aria-selected", toplu ? "false" : "true");
      }
      if (btnToplu) {
        btnToplu.classList.toggle("active", toplu);
        btnToplu.setAttribute("aria-selected", toplu ? "true" : "false");
      }
      if (panelGecikme) panelGecikme.hidden = !!toplu;
      panel.hidden = !toplu;
      if (toplu) {
        kac();
        durumYukle();
        listeYukle(false);
      }
    }

    function testiDusur() {
      testOk = false;
      baslatDurumu();
    }

    function taslak() {
      return post("/whatsapp/api/toplu-taslak", {
        id: kampanyaId,
        sablon: sablon ? sablon.value : "",
        tavan: tavan ? tavan.value : 150
      }).then(function (r) {
        if (!r.govde.ok) {
          testOk = false;
          baslatDurumu();
          return { ok: false, neden: (r.govde && r.govde.neden) || "kayit" };
        }
        kampanyaId = r.govde.id;
        testOk = false;
        baslatDurumu();
        return { ok: true, id: kampanyaId, govde: r.govde };
      });
    }

    function taslakHataYaz(neden) {
      if (testSonuc) testSonuc.textContent = wtTaslakNedenMetin(neden);
    }

    function sablonKaydet() {
      if (testSonuc) testSonuc.textContent = "";
      return taslak().then(function (r) {
        if (!r || !r.ok) {
          taslakHataYaz(r && r.neden);
          return r;
        }
        if (testSonuc) testSonuc.textContent = "Şablon kaydedildi";
        return r;
      }).catch(function () {
        if (testSonuc) testSonuc.textContent = "İstek başarısız";
      });
    }

    function seciliMidler() {
      return satirlar.filter(function (s) { return secili[s.mid] && !s.sebep; }).map(function (s) { return s.mid; });
    }

    function testGonder() {
      var no = testNo ? testNo.value : "";
      if (testNo) testNo.value = "";
      if (testSonuc) testSonuc.textContent = "";
      taslak().then(function (r) {
        if (!r || !r.ok) {
          taslakHataYaz(r && r.neden);
          return;
        }
        if (!kampanyaId) {
          if (testSonuc) testSonuc.textContent = "Önce şablonu kaydedin";
          return;
        }
        return post("/whatsapp/api/toplu-test", { id: kampanyaId, telefon: no });
      }).then(function (r) {
        if (!r) return;
        var d = (r.govde && r.govde.durum) || "";
        if (r.govde && r.govde.test_ok) {
          testOk = true;
          baslatDurumu();
          if (testSonuc) testSonuc.textContent = "Test sonucu: gonderildi";
          return;
        }
        if (r.govde && r.govde.anahtar && d === "kuyrukta") return yokla(r.govde.anahtar, 0);
        testOk = false;
        baslatDurumu();
        if (testSonuc) testSonuc.textContent = "Test sonucu: " + (d || (r.govde && r.govde.neden) || "hata");
      }).catch(function () {
        if (testSonuc) testSonuc.textContent = "İstek başarısız";
      });
    }

    function yokla(anahtar, deneme) {
      if (deneme > 8) {
        if (testSonuc) testSonuc.textContent = "Test sonucu: bekleniyor";
        return;
      }
      var url = "/whatsapp/api/toplu-test-sonuc?id=" + encodeURIComponent(kampanyaId) +
        "&anahtar=" + encodeURIComponent(anahtar);
      fetch(url).then(function (r) { return r.json(); }).then(function (j) {
        if (j && j.test_ok) {
          testOk = true;
          baslatDurumu();
          if (testSonuc) testSonuc.textContent = "Test sonucu: gonderildi";
          return;
        }
        if (j && j.durum && j.durum !== "kuyrukta") {
          testOk = false;
          baslatDurumu();
          if (testSonuc) testSonuc.textContent = "Test sonucu: " + j.durum;
          return;
        }
        setTimeout(function () { yokla(anahtar, deneme + 1); }, 2000);
      }).catch(function () {
        if (testSonuc) testSonuc.textContent = "İstek başarısız";
      });
    }

    function baslatKomut() {
      var o = hesap();
      if (!testOk || o.alici < 1) return;
      var sure = o.sure_metin || wtSureMetin(o) || "süre hesaplanamadı";
      var metin = o.alici + " kişiye, " + sure + " gönderilecek";
      if (!window.confirm(metin)) return;
      var midler = seciliMidler();
      post("/whatsapp/api/toplu-baslat", { id: kampanyaId, midler: midler }).then(function (r) {
        if (!r) return;
        if (testSonuc) {
          testSonuc.textContent = r.govde && r.govde.ok
            ? ("Alıcı: " + Number(r.govde.alici || 0) + " · Atılan: " + Number(r.govde.atilan || 0))
            : "İşlem olmadı";
        }
        durumYukle();
      }).catch(function () {
        if (testSonuc) testSonuc.textContent = "İstek başarısız";
      });
    }

    function bitir() {
      if (!kampanyaId || kampanyaDurum === "calisiyor") return;
      var n = Number(bekleyenSayi || 0);
      if (!window.confirm("Bekleyen " + n + " alıcı iptal edilecek")) return;
      komut("/whatsapp/api/toplu-bitir");
    }

    function komut(url) {
      if (!kampanyaId) return;
      post(url, { id: kampanyaId }).then(function (r) {
        if (testSonuc) testSonuc.textContent = r.govde && r.govde.ok ? (r.govde.durum || "tamam") : "İşlem olmadı";
        durumYukle();
      }).catch(function () {
        if (testSonuc) testSonuc.textContent = "İstek başarısız";
      });
    }

    if (sablon) sablon.addEventListener("input", function () { kac(); testiDusur(); });
    if (tavan) tavan.addEventListener("input", function () { testiDusur(); ozetYaz(); });
    if (ara) ara.addEventListener("input", ciz);
    if (durum) durum.addEventListener("change", ciz);
    if (tumunu) tumunu.addEventListener("change", function () {
      var bayrak = satirlar.map(function (s) {
        return { gorunur: gorunurMu(s), secilebilir: !s.sebep, secili: !!secili[s.mid] };
      });
      var sonuc = wtSecimUygula(bayrak, tumunu.checked);
      satirlar.forEach(function (s, i) {
        if (!s.sebep) secili[s.mid] = !!sonuc[i];
      });
      ciz();
    });
    document.querySelectorAll("[data-wt-sablon]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var anahtar = btn.getAttribute("data-wt-sablon");
        if (sablon && SABLON[anahtar]) {
          sablon.value = SABLON[anahtar];
          kac();
          testiDusur();
        }
      });
    });
    if (btnGecikme) btnGecikme.addEventListener("click", function () { altSekmeAktif("gecikme"); });
    if (btnToplu) btnToplu.addEventListener("click", function () { altSekmeAktif("toplu"); });
    var kaydetBtn = document.getElementById("wtKaydet");
    if (kaydetBtn) kaydetBtn.addEventListener("click", function () { sablonKaydet(); });
    var yenileBtn = document.getElementById("wtYenile");
    if (yenileBtn) yenileBtn.addEventListener("click", function () {
      durumYukle();
      listeYukle(true);
    });
    var testBtn = document.getElementById("wtTest");
    if (testBtn) testBtn.addEventListener("click", testGonder);
    if (baslat) baslat.addEventListener("click", baslatKomut);
    var durdur = document.getElementById("wtDurdur");
    if (durdur) durdur.addEventListener("click", function () { komut("/whatsapp/api/toplu-durdur"); });
    var devam = document.getElementById("wtDevam");
    if (devam) devam.addEventListener("click", function () { komut("/whatsapp/api/toplu-devam"); });
    var bitirBtn = document.getElementById("wtBitir");
    if (bitirBtn) bitirBtn.addEventListener("click", bitir);
    kac();
    baslatDurumu();
  }

  function haricBagla() {
    if (typeof document === "undefined") return;
    document.querySelectorAll(".wt-haric").forEach(function (btn) {
      if (btn.getAttribute("data-wt-bagli") === "1") return;
      btn.setAttribute("data-wt-bagli", "1");
      btn.addEventListener("click", function () {
        var kap = btn.getAttribute("data-kapali") !== "1";
        fetch("/whatsapp/api/toplu-haric", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ musteri_id: Number(btn.getAttribute("data-mid")), kapali: kap })
        }).then(function (r) { return r.json(); }).then(function (j) {
          if (!j || !j.ok) return;
          btn.setAttribute("data-kapali", j.kapali ? "1" : "0");
          btn.textContent = j.kapali ? "kapalı" : "açık";
        }).catch(function () {});
      });
    });
    if (!document.querySelector(".wt-haric")) return;
    fetch("/whatsapp/api/toplu-haric").then(function (r) { return r.json(); }).then(function (j) {
      var kapali = {};
      ((j && j.kapali) || []).forEach(function (id) { kapali[String(id)] = true; });
      document.querySelectorAll(".wt-haric").forEach(function (btn) {
        var kap = !!kapali[btn.getAttribute("data-mid")];
        btn.textContent = kap ? "kapalı" : "açık";
        btn.setAttribute("data-kapali", kap ? "1" : "0");
      });
    }).catch(function () {});
  }

  return {
    wtNorm: wtNorm,
    wtSecimUygula: wtSecimUygula,
    wtOzet: wtOzet,
    wtSureMetin: wtSureMetin,
    wtTaslakNedenMetin: wtTaslakNedenMetin,
    bagla: bagla
  };
});
