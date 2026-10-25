(function () {
  var modal = document.getElementById("wtModal");
  var acBtn = document.getElementById("btn-toplu-mesaj");
  if (!modal || !acBtn) return;

  var sablon = document.getElementById("wtSablon");
  var tavan = document.getElementById("wtTavan");
  var onizleme = document.getElementById("wtOnizleme");
  var testNo = document.getElementById("wtTestNo");
  var testSonuc = document.getElementById("wtTestSonuc");
  var baslat = document.getElementById("wtBaslat");
  var kampanyaId = null;
  var testOk = false;
  var ALAN = [
    ["alici", "Alıcı"],
    ["yedek", "Yedek hitap"],
    ["haric", "Hariç"],
    ["pasif", "Pasif"],
    ["numarasiz", "Numarasız"],
    ["sure_dk", "Tahmini dakika"],
    ["gun", "Gün"]
  ];

  function ac() {
    modal.hidden = false;
    modal.style.display = "flex";
    durumYukle();
    haricYukle();
  }

  function kapat() {
    modal.hidden = true;
    modal.style.display = "none";
  }

  function post(url, govde) {
    return fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(govde || {})
    }).then(function (r) {
      return r.json().then(function (j) { return {kod: r.status, govde: j || {}}; });
    });
  }

  function baslatDurumu() {
    baslat.disabled = !testOk;
  }

  function durumYukle() {
    fetch("/whatsapp/api/toplu-durum").then(function (r) { return r.json(); }).then(function (j) {
      var k = j && j.kampanya;
      if (!k) {
        kampanyaId = null;
        testOk = false;
        baslatDurumu();
        return;
      }
      kampanyaId = k.id;
      testOk = !!k.test_ok;
      if (k.tavan) tavan.value = String(k.tavan);
      baslatDurumu();
    }).catch(function () {
      testSonuc.textContent = "İstek başarısız";
    });
  }

  function onizle() {
    onizleme.textContent = "";
    post("/whatsapp/api/toplu-taslak", {
      id: kampanyaId,
      sablon: sablon.value,
      tavan: tavan.value
    }).then(function (r) {
      if (!r.govde.ok) {
        onizleme.textContent = "Kayıt alınamadı";
        testOk = false;
        baslatDurumu();
        return;
      }
      kampanyaId = r.govde.id;
      testOk = false;
      baslatDurumu();
      return post("/whatsapp/api/toplu-onizleme", {id: kampanyaId});
    }).then(function (r) {
      if (!r || !r.govde) return;
      if (!r.govde.ok) {
        onizleme.textContent = "Önizleme alınamadı";
        return;
      }
      onizleme.textContent = ALAN.map(function (cift) {
        return cift[1] + ": " + Number(r.govde[cift[0]] || 0);
      }).join(" · ");
    }).catch(function () {
      onizleme.textContent = "İstek başarısız";
    });
  }

  function testGonder() {
    var no = testNo.value;
    testNo.value = "";
    testSonuc.textContent = "";
    if (!kampanyaId) {
      testSonuc.textContent = "Önce önizleme alın";
      return;
    }
    post("/whatsapp/api/toplu-test", {id: kampanyaId, telefon: no}).then(function (r) {
      var d = (r.govde && r.govde.durum) || "";
      if (r.govde && r.govde.test_ok) {
        testOk = true;
        baslatDurumu();
        testSonuc.textContent = "Test sonucu: gonderildi";
        return;
      }
      if (r.govde && r.govde.anahtar && d === "kuyrukta") {
        return yokla(r.govde.anahtar, 0);
      }
      testOk = false;
      baslatDurumu();
      testSonuc.textContent = "Test sonucu: " + (d || (r.govde && r.govde.neden) || "hata");
    }).catch(function () {
      testSonuc.textContent = "İstek başarısız";
    });
  }

  function yokla(anahtar, deneme) {
    if (deneme > 8) {
      testSonuc.textContent = "Test sonucu: bekleniyor";
      return;
    }
    var url = "/whatsapp/api/toplu-test-sonuc?id=" + encodeURIComponent(kampanyaId) +
      "&anahtar=" + encodeURIComponent(anahtar);
    fetch(url).then(function (r) { return r.json(); }).then(function (j) {
      if (j && j.test_ok) {
        testOk = true;
        baslatDurumu();
        testSonuc.textContent = "Test sonucu: gonderildi";
        return;
      }
      if (j && j.durum && j.durum !== "kuyrukta") {
        testOk = false;
        baslatDurumu();
        testSonuc.textContent = "Test sonucu: " + j.durum;
        return;
      }
      setTimeout(function () { yokla(anahtar, deneme + 1); }, 2000);
    }).catch(function () {
      testSonuc.textContent = "İstek başarısız";
    });
  }

  function komut(url) {
    if (!kampanyaId) return;
    post(url, {id: kampanyaId}).then(function (r) {
      testSonuc.textContent = r.govde && r.govde.ok ? (r.govde.durum || "tamam") : "İşlem olmadı";
      if (url.indexOf("baslat") >= 0 && r.govde && r.govde.ok) testOk = true;
      durumYukle();
    }).catch(function () {
      testSonuc.textContent = "İstek başarısız";
    });
  }

  function haricYukle() {
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

  document.querySelectorAll(".wt-haric").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var kap = btn.getAttribute("data-kapali") !== "1";
      post("/whatsapp/api/toplu-haric", {
        musteri_id: Number(btn.getAttribute("data-mid")),
        kapali: kap
      }).then(function (r) {
        if (!r.govde || !r.govde.ok) return;
        btn.setAttribute("data-kapali", r.govde.kapali ? "1" : "0");
        btn.textContent = r.govde.kapali ? "kapalı" : "açık";
      }).catch(function () {});
    });
  });

  function testiDusur() {
    testOk = false;
    baslatDurumu();
  }
  sablon.addEventListener("input", testiDusur);
  tavan.addEventListener("input", testiDusur);
  acBtn.addEventListener("click", ac);
  document.getElementById("wtKapat").addEventListener("click", kapat);
  document.getElementById("wtOnizle").addEventListener("click", onizle);
  document.getElementById("wtTest").addEventListener("click", testGonder);
  baslat.addEventListener("click", function () { komut("/whatsapp/api/toplu-baslat"); });
  document.getElementById("wtDurdur").addEventListener("click", function () { komut("/whatsapp/api/toplu-durdur"); });
  document.getElementById("wtDevam").addEventListener("click", function () { komut("/whatsapp/api/toplu-devam"); });
  baslatDurumu();
})();
