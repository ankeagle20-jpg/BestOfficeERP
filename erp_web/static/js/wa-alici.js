(function () {
  function norm(raw) {
    var s = String(raw || "").replace(/\D/g, "");
    if (s.indexOf("00") === 0) s = s.slice(2);
    if (s.charAt(0) === "0" && s.length === 11) s = "90" + s.slice(1);
    else if (s.length === 10 && s.charAt(0) === "5") s = "90" + s;
    return /^[1-9]\d{10,14}$/.test(s) ? s : "";
  }

  function bicim(raw) {
    var n = norm(raw);
    if (!n) return "";
    if (n.indexOf("90") === 0 && n.length === 12) {
      var d = "0" + n.slice(2);
      return d.slice(0, 4) + " " + d.slice(4, 7) + " " + d.slice(7, 9) + " " + d.slice(9, 11);
    }
    return "+" + n;
  }

  function stil() {
    if (document.getElementById("wa-alici-stil")) return;
    var st = document.createElement("style");
    st.id = "wa-alici-stil";
    st.textContent =
      ".wa-alici-satir{display:flex;align-items:center;flex-wrap:wrap;gap:10px;border:1px solid color-mix(in srgb, currentColor 32%, transparent);border-radius:8px;padding:8px 10px;margin:6px 0;background:transparent;color:inherit;box-sizing:border-box;max-width:100%;}" +
      ".wa-alici-satir:has(input:checked){background:color-mix(in srgb, currentColor 14%, transparent);border-color:color-mix(in srgb, currentColor 55%, transparent);}" +
      "#sozlesme-wa-kart .wa-alici-satir input,#erp-asistan-form .wa-alici-satir input,.wa-alici-kart .wa-alici-satir input,.wa-alici-satir input{flex:0 0 auto;width:auto;}" +
      ".wa-alici-metin{flex:1 1 auto;min-width:0;}" +
      ".wa-alici-etiket{font-size:12px;color:color-mix(in srgb, currentColor 70%, transparent);}" +
      ".wa-alici-no{font-size:15px;letter-spacing:.02em;word-break:break-word;color:inherit;}" +
      ".wa-alici-rozet{flex:0 0 auto;max-width:100%;font-size:12px;border-radius:999px;padding:2px 8px;background:#eceff1;color:#455a64;white-space:nowrap;}" +
      ".wa-alici-rozet[data-durum=gonderildi]{background:#e8f5e9;color:#2e7d32;}" +
      ".wa-alici-rozet[data-durum=kuyrukta]{background:#e3f2fd;color:#1565c0;}" +
      ".wa-alici-rozet[data-durum=belirsiz]{background:#fff8e1;color:#ef6c00;}" +
      ".wa-alici-rozet[data-durum=hata]{background:#ffebee;color:#c62828;}" +
      ".wa-alici-ek{display:inline-block;margin:4px 0 8px;font-size:13px;color:#1565c0;}" +
      ".wa-alici-perde{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:80;display:flex;align-items:center;justify-content:center;padding:12px;}" +
      ".wa-alici-kart{background:#fff;color:#263238;border-radius:10px;padding:14px;width:min(420px,100%);box-sizing:border-box;font:14px/1.4 inherit;}";
    document.head.appendChild(st);
  }

  function rozetYazi(durum) {
    if (durum === "gonderildi") return "Gönderildi ✓";
    if (durum === "kuyrukta" || durum === "gonderiliyor" || durum === "bekliyor") return "Kuyruğa alındı";
    if (durum === "belirsiz") return "Sonuç belirsiz";
    if (durum === "hata" || durum === "basarisiz") return "Gönderilemedi";
    return "";
  }

  function rozetKod(durum) {
    if (durum === "gonderildi") return "gonderildi";
    if (durum === "kuyrukta" || durum === "gonderiliyor" || durum === "bekliyor") return "kuyrukta";
    if (durum === "belirsiz") return "belirsiz";
    if (durum === "hata" || durum === "basarisiz") return "hata";
    return "";
  }

  function yaz(kutu, opts) {
    if (!kutu) return;
    stil();
    opts = opts || {};
    var kaynak = opts.kartlar || [];
    var gorulen = {};
    var liste = [];
    kaynak.forEach(function (k) {
      var ham = k && (k.ham || k.gorunen || k.telefon || "");
      var n = norm(ham);
      if (!n || gorulen[n] || liste.length >= 2) return;
      gorulen[n] = 1;
      liste.push({
        n: n,
        ad: String((k && k.ad) || "").trim(),
        etiket: String((k && k.etiket) || ""),
        secili: k && typeof k.secili === "boolean" ? k.secili : liste.length === 0
      });
    });
    if (opts.mod === "radyo") {
      liste.forEach(function (k, i) { k.secili = i === 0; });
    }
    kutu.innerHTML = "";
    kutu.hidden = false;
    liste.forEach(function (k, i) {
      var satir = document.createElement("div");
      satir.className = "wa-alici-satir";
      var sira = k.sira || (i + 1);
      satir.setAttribute("data-sira", String(sira));
      satir.setAttribute("data-tel", k.n);
      var girdi = document.createElement("input");
      girdi.type = opts.mod === "radyo" ? "radio" : "checkbox";
      if (opts.mod === "radyo") girdi.name = "wa-alici-radyo";
      girdi.checked = !!k.secili;
      girdi.setAttribute("data-sira", String(sira));
      girdi.setAttribute("data-tel", k.n);
      girdi.addEventListener("change", function () { if (opts.onChange) opts.onChange(); });
      var metin = document.createElement("div");
      metin.className = "wa-alici-metin";
      var etiket = document.createElement("div");
      etiket.className = "wa-alici-etiket";
      var et = k.etiket || ((i === 0 ? "1. Yetkili" : "2. Yetkili") + (k.ad ? " · " + k.ad : ""));
      etiket.textContent = et;
      var no = document.createElement("div");
      no.className = "wa-alici-no";
      no.textContent = bicim(k.n);
      metin.appendChild(etiket);
      metin.appendChild(no);
      var rozet = document.createElement("span");
      rozet.className = "wa-alici-rozet";
      rozet.setAttribute("data-durum", "");
      satir.appendChild(girdi);
      satir.appendChild(metin);
      satir.appendChild(rozet);
      kutu.appendChild(satir);
    });
    if (opts.ek && opts.mod !== "radyo") {
      var a = document.createElement("a");
      a.href = "#";
      a.className = "wa-alici-ek";
      a.textContent = "Başka numara ekle";
      a.onclick = function (ev) {
        ev.preventDefault();
        var kim = opts.ekId || "wa-alici-ek";
        if (document.getElementById(kim)) return;
        a.hidden = true;
        var giris = document.createElement("input");
        giris.id = kim;
        giris.className = "wa-alici-ek-input";
        giris.setAttribute("inputmode", "tel");
        giris.placeholder = "Telefon";
        giris.style.cssText = "width:100%;box-sizing:border-box;margin:4px 0 8px;";
        giris.addEventListener("input", function () { if (opts.onChange) opts.onChange(); });
        kutu.appendChild(giris);
      };
      kutu.appendChild(a);
    }
    if (opts.onChange) opts.onChange();
  }

  function secilen(kutu) {
    var out = [];
    if (!kutu) return out;
    kutu.querySelectorAll(".wa-alici-satir input:checked").forEach(function (el) {
      out.push({
        sira: parseInt(el.getAttribute("data-sira"), 10) || out.length + 1,
        tel: el.getAttribute("data-tel") || "",
        serbest: false
      });
    });
    return out;
  }

  function rozet(kutu, sira, durum) {
    if (!kutu) return;
    var satir = kutu.querySelector('.wa-alici-satir[data-sira="' + String(sira) + '"]');
    if (!satir) return;
    var el = satir.querySelector(".wa-alici-rozet");
    if (!el) return;
    el.setAttribute("data-durum", rozetKod(durum));
    el.textContent = rozetYazi(durum);
  }

  function radyo(hamlar, cb) {
    stil();
    var gorulen = {};
    var liste = [];
    (hamlar || []).forEach(function (h) {
      var n = norm(h);
      if (!n || gorulen[n]) return;
      gorulen[n] = 1;
      liste.push(n);
    });
    if (liste.length <= 1) {
      cb(liste[0] || "");
      return;
    }
    var perde = document.createElement("div");
    perde.className = "wa-alici-perde";
    var kart = document.createElement("div");
    kart.className = "wa-alici-kart";
    var bas = document.createElement("h3");
    bas.textContent = "Hangi numara";
    bas.style.margin = "0 0 8px";
    kart.appendChild(bas);
    var kutu = document.createElement("div");
    kart.appendChild(kutu);
    var ac = document.createElement("button");
    ac.type = "button";
    ac.textContent = "WhatsApp Web'de aç";
    ac.style.marginRight = "8px";
    var kapat = document.createElement("button");
    kapat.type = "button";
    kapat.textContent = "Vazgeç";
    kart.appendChild(ac);
    kart.appendChild(kapat);
    perde.appendChild(kart);
    document.body.appendChild(perde);
    yaz(kutu, {
      mod: "radyo",
      kartlar: liste.map(function (n, i) {
        return { ham: n, secili: i === 0, etiket: i === 0 ? "1. Yetkili" : "2. Yetkili" };
      })
    });
    kapat.onclick = function () { perde.remove(); };
    ac.onclick = function () {
      var sec = secilen(kutu);
      perde.remove();
      cb(sec[0] ? sec[0].tel : liste[0]);
    };
  }

  window.WaAlici = { norm: norm, bicim: bicim, yaz: yaz, secilen: secilen, rozet: rozet, radyo: radyo };
})();
