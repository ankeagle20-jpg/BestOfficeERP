/* Plan değiştir. Kapı kapalıyken seçenek ve bölüm oluşturulmaz. Durum kaydı yapmaz. */
function planDurumKarari(secilen, onceki, kapiAcik) {
    var geri = (onceki === "pasif") ? "pasif" : "aktif";
    if (!kapiAcik) {
        return {
            goster: false,
            durum: (secilen === "pasif") ? "pasif" : "aktif",
            modal: false,
            kaydetDurum: secilen === "aktif" || secilen === "pasif"
        };
    }
    if (secilen === "plan" || secilen === "dondur") {
        return { goster: true, durum: geri, modal: true, kaydetDurum: false, dondur: secilen === "dondur" };
    }
    return {
        goster: true,
        durum: (secilen === "pasif") ? "pasif" : "aktif",
        modal: false,
        kaydetDurum: true
    };
}

function planIptalEdilebilir(ayIso, bugunIso) {
    var ay = String(ayIso || "").slice(0, 7);
    /* Aktif (iptal edilmemiş) her plan, geçmiş dahil iptal edilebilir. bugunIso geriye uyumluluk için kalır. */
    return ay.length >= 7;
}

function planSecenekEklensin(kapiAcik) {
    return !!kapiAcik;
}

function planDurumOdak() {
    var sel = document.getElementById("musteri_durum");
    if (!sel) return;
    if (sel.value === "aktif" || sel.value === "pasif") sel.setAttribute("data-onceki", sel.value);
}

function musteriDurumSecildi(secilen) {
    var sel = document.getElementById("musteri_durum");
    var onceki = (sel && sel.getAttribute("data-onceki")) || "aktif";
    var kapi = !!window.__planMid;
    var karar = planDurumKarari(secilen, onceki, kapi);
    if (karar.modal) {
        if (sel) sel.value = karar.durum;
        if (typeof syncDurumUI === "function") syncDurumUI(karar.durum, (karar.durum === "pasif" && onceki !== "pasif") ? { yeniPasif: true } : {});
        planDegistirModalAc(karar.dondur ? "dondur" : "plan");
        return;
    }
    if (sel && (karar.durum === "aktif" || karar.durum === "pasif")) sel.setAttribute("data-onceki", karar.durum);
    if (typeof syncDurumUI === "function") syncDurumUI(karar.durum, (karar.durum === "pasif" && onceki !== "pasif") ? { yeniPasif: true } : {});
    dondurmaPasifUygula();
}

function planArayuzKaldir() {
    var op = document.querySelector("#musteri_durum option[value='plan']");
    if (op) op.remove();
    var opDon = document.querySelector("#musteri_durum option[value='dondur']");
    if (opDon) opDon.remove();
    var btn = document.getElementById("plan_degistir_btn");
    if (btn) btn.remove();
    var btnDon = document.getElementById("plan_dondur_btn");
    if (btnDon) btnDon.remove();
    window.__dondurmaYenileSira = (window.__dondurmaYenileSira || 0) + 1;
    /* Popup (geçmiş + form) tek elemandır; kalkınca içindeki her şey birlikte gider. */
    var modal = document.getElementById("plan_degistir_modal");
    if (modal) modal.remove();
    window.__planMid = null;
    window.__planBilgi = null;
    window.__planSonGovde = null;
    window.__planHataIpucu = "";
    window.__planTaban = "";
    window.__planOnizlemeHazir = false;
    window.__planKilit = [];
    window.__planOdemeli = [];
    window.__planSozBas = "";
}

/* Sayfada `let selectedId` tanımlı; top-level let window'a eklenmez, bu yüzden window.selectedId her zaman undefined.
   Önce global (lexical) değişkene, yoksa window'a bakılır. */
function planMusteriId() {
    try {
        if (typeof selectedId !== "undefined" && selectedId != null && selectedId !== "") return selectedId;
    } catch (e) { /* TDZ */ }
    var w = window.selectedId;
    return (w != null && w !== "") ? w : null;
}

/* Kart yükleme yolları (ilk yükleme, kayıt sonrası tazeleme, plan kaydı/iptali) bunu üst üste çağırır.
   İdempotent: son çağrı kazanır (eskimiş yanıt çizilmez), aynı müşteride arayüz yerinde kalır ve yalnız içerik yenilenir,
   başka müşteride eski müşterinin popup'ı/butonu/geçmişi hemen kalkar. */
function planKapisiniYenile(mid) {
    var sira = (window.__planYenileSira = (window.__planYenileSira || 0) + 1);
    if (mid == null || mid === "") {
        planArayuzKaldir();
        return;
    }
    var istenen = String(mid);
    if (window.__planMid !== istenen) planArayuzKaldir();
    fetch("/giris/api/musteri/" + encodeURIComponent(istenen) + "/plan", { credentials: "same-origin" })
        .then(function (r) {
            if (!r.ok) return { kod: r.status, j: null };
            return r.json().then(function (j) { return { kod: r.status, j: j }; });
        })
        .then(function (pack) {
            if (sira !== window.__planYenileSira) return;
            var simdi = String(planMusteriId() || "");
            if (simdi !== "" && simdi !== istenen) return;
            if (pack.kod === 401 || pack.kod === 403 || pack.kod === 404) {
                planArayuzKaldir();
                return;
            }
            if (!pack.j || !pack.j.ok) return;
            planArayuzKur(istenen, pack.j);
        })
        .catch(function () {});
}

function planArayuzKur(mid, veri) {
    if (!planSecenekEklensin(true)) return;
    window.__planMid = String(mid);
    window.__planSozBas = (veri && veri.sozlesme_baslangic) ? String(veri.sozlesme_baslangic).slice(0, 7) : "";
    var sel = document.getElementById("musteri_durum");
    if (sel && !sel.querySelector("option[value='plan']")) {
        var op = document.createElement("option");
        op.value = "plan";
        op.textContent = "Plan değiştir";
        sel.appendChild(op);
    }
    if (sel && !sel.querySelector("option[value='dondur']")) {
        var opDon = document.createElement("option");
        opDon.value = "dondur";
        opDon.textContent = "Plan dondur";
        sel.appendChild(opDon);
    }
    planDugmesiHazirla();
    planModalHazirla();
    planGecmisCiz(mid, veri || {});
    dondurmaPasifUygula();
    dondurmaGecmisYukle(mid);
}

/* Durum seçicinin sağındaki küçük buton. Kapı açıkken hep görünür; aynı eleman ikinci kez oluşturulmaz. */
function planDugmesiHazirla() {
    var sel = document.getElementById("musteri_durum");
    if (!sel || !sel.parentNode) return;
    var stil = "flex:0 0 auto;padding:5px 10px;font-size:12px;border-radius:4px;border:1px solid #3d5070;"
        + "background:#1b3a57;color:#e0f7fa;cursor:pointer;white-space:nowrap;font-family:inherit;";
    if (!document.getElementById("plan_degistir_btn")) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.id = "plan_degistir_btn";
        btn.textContent = "Plan değiştir";
        btn.title = "Plan geçmişi ve yeni plan";
        btn.style.cssText = stil;
        btn.onclick = function () { planDegistirModalAc("plan"); };
        sel.parentNode.insertBefore(btn, sel.nextSibling);
    }
    if (!document.getElementById("plan_dondur_btn")) {
        var btnDon = document.createElement("button");
        btnDon.type = "button";
        btnDon.id = "plan_dondur_btn";
        btnDon.textContent = "Plan dondur";
        btnDon.title = "Plan dondurma";
        btnDon.style.cssText = stil;
        btnDon.onclick = function () { planDegistirModalAc("dondur"); };
        var planBtn = document.getElementById("plan_degistir_btn");
        if (planBtn && planBtn.parentNode) planBtn.parentNode.insertBefore(btnDon, planBtn.nextSibling);
    }
    dondurmaPasifUygula();
}

/* Geçmiş listesi popup içindeki sabit kapta çizilir; her çizimde kap boşaltılır (ikinci kopya oluşamaz). */
function planGecmisCiz(mid, veri) {
    var kutu = document.getElementById("plan_gecmis_bolum");
    if (!kutu) return;
    kutu.textContent = "";
    var baslik = document.createElement("div");
    baslik.textContent = "Plan geçmişi";
    baslik.style.fontWeight = "600";
    baslik.style.marginBottom = "6px";
    kutu.appendChild(baslik);
    /* "Açık plan yok." (404) sonrası liste sunucudan yenilenir; bilgi yalnız aynı müşterinin listesinde bir kez gösterilir. */
    var bilgiPaket = window.__planBilgi;
    if (bilgiPaket && bilgiPaket.mesaj && String(bilgiPaket.mid) === String(mid)) {
        var bilgi = document.createElement("div");
        bilgi.id = "plan_gecmis_bilgi";
        bilgi.textContent = bilgiPaket.mesaj;
        bilgi.style.color = "#90caf9";
        bilgi.style.fontSize = "12px";
        bilgi.style.marginBottom = "6px";
        kutu.appendChild(bilgi);
        window.__planBilgi = null;
    }
    var bugun = new Date();
    var bugunIso = bugun.getFullYear() + "-" + String(bugun.getMonth() + 1).padStart(2, "0") + "-01";
    var satirlar = (veri.gecmis && veri.gecmis.length) ? veri.gecmis : (veri.aktif || []);
    if (!satirlar.length) {
        var bos = document.createElement("div");
        bos.textContent = "Açık plan yok.";
        bos.style.color = "#90a4ae";
        bos.style.fontSize = "12px";
        kutu.appendChild(bos);
    }
    satirlar.forEach(function (p) {
        var satir = document.createElement("div");
        satir.style.display = "flex";
        satir.style.gap = "8px";
        satir.style.alignItems = "center";
        satir.style.flexWrap = "wrap";
        satir.style.fontSize = "12px";
        satir.style.marginBottom = "4px";
        var metin = document.createElement("span");
        var ay = String(p.gecerlilik_ay || "").slice(0, 10);
        var kim = p.iptal_at ? (p.iptal_eden || "") : (p.olusturan || "");
        var zaman = p.iptal_at || p.created_at || "";
        metin.textContent = ay + "  net " + (p.yeni_net != null ? p.yeni_net : "") + "  brüt " + (p.yeni_brut != null ? p.yeni_brut : "") + (p.iptal_at ? "  iptal" : "  aktif") + (kim ? "  " + kim : "") + (zaman ? "  " + String(zaman).slice(0, 19) : "");
        satir.appendChild(metin);
        if (!p.iptal_at && planIptalEdilebilir(ay, bugunIso)) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.textContent = "İptal";
            btn.onclick = function () { planIptalGonder(mid, p.id, btn); };
            satir.appendChild(btn);
        }
        kutu.appendChild(satir);
    });
}

function planModalHazirla() {
    if (document.getElementById("plan_degistir_modal")) return;
    var kok = document.createElement("div");
    kok.id = "plan_degistir_modal";
    kok.style.display = "none";
    kok.style.position = "fixed";
    kok.style.inset = "0";
    kok.style.background = "rgba(0,0,0,.45)";
    kok.style.zIndex = "12000";
    var kutu = document.createElement("div");
    kutu.style.background = "#0f2537";
    kutu.style.color = "#e0f7fa";
    kutu.style.maxWidth = "720px";
    kutu.style.boxSizing = "border-box";
    kutu.style.width = "calc(100% - 16px)";
    kutu.style.margin = "4vh auto";
    kutu.style.padding = "16px";
    kutu.style.borderRadius = "8px";
    kutu.style.maxHeight = "92vh";
    kutu.style.overflowY = "auto";
    kutu.style.overflowX = "hidden";
    kutu.style.overscrollBehavior = "contain";
    /* Etiket / alan / düğme stilleri satır içi: sayfa CSS'inden bağımsız, etiketler alanın üstünde ayrı satırda. */
    var E = "display:block;margin:8px 0 2px;font-size:12px;color:#b0bec5;";
    var G = "display:block;box-sizing:border-box;width:100%;max-width:280px;margin:0 0 4px;padding:6px 10px;"
        + "background:#2d4060;border:1px solid #3d5070;border-radius:3px;color:#e0f7fa;font-size:13px;font-family:inherit;";
    var D = "padding:7px 16px;border-radius:4px;border:none;cursor:pointer;font-size:13px;font-family:inherit;";
    var K = "margin:8px 0;padding:10px;border:1px solid #e53935;border-radius:4px;background:rgba(229,57,53,.08);color:#ff8a80;";
    var C = "display:flex;align-items:center;gap:8px;margin-top:8px;font-size:14px;font-weight:600;color:#ffcdd2;cursor:pointer;";
    kutu.style.fontFamily = "inherit";
    kutu.style.fontSize = "13px";
    kutu.innerHTML = ""
        + "<div id=\"plan_baslik\" style=\"position:sticky;top:-16px;z-index:2;display:flex;align-items:center;justify-content:space-between;gap:8px;margin:-16px -16px 10px;padding:10px 16px;background:#0f2537;border-bottom:1px solid #2d4060;\">"
        + "<div id=\"plan_baslik_metin\" style=\"font-weight:600;font-size:15px;\">Plan değiştir</div>"
        + "<button type=\"button\" id=\"plan_x\" aria-label=\"Kapat\" title=\"Kapat\" style=\"background:none;border:none;color:#b0bec5;font-size:24px;line-height:1;cursor:pointer;padding:0 4px;font-family:inherit;\">&times;</button>"
        + "</div>"
        + "<div id=\"plan_form_bolum\">"
        + "<div id=\"plan_gecmis_bolum\" style=\"margin:0 0 10px;padding:8px;border:1px solid #2d4060;border-radius:6px;\"></div>"
        + "<div id=\"plan_pasif_not\" role=\"status\" style=\"display:none;margin:0 0 10px;padding:8px;border:1px solid #ff9800;border-radius:4px;color:#ffcc80;\">Bu müşteri pasif: yeni plan kaydedilemez, yalnız plan geçmişi görüntülenir.</div>"
        + "<div id=\"plan_kapsam_not\" style=\"margin:0 0 10px;padding:8px;border:1px solid #546e7a;border-radius:4px;color:#b0bec5;font-size:12px;line-height:1.4;\">Plan; grid, ekstre, ödeme linki ve resmi fatura tutarını günceller. Cari kart özeti ve fatura satırları (borç kayıtları) eski tutarda kalır.</div>"
        + "<div style=\"font-weight:600;margin:6px 0 2px;\">Yeni plan</div>"
        + "<label for=\"plan_gecerlilik\" style=\"" + E + "\">Geçerlilik ayı</label><input id=\"plan_gecerlilik\" type=\"month\" style=\"" + G + "\">"
        + "<label for=\"plan_yeni_net\" style=\"" + E + "\">Yeni aylık net</label><input id=\"plan_yeni_net\" type=\"number\" step=\"0.01\" style=\"" + G + "\">"
        + "<label for=\"plan_kdv\" style=\"" + E + "\">KDV oranı</label><input id=\"plan_kdv\" type=\"number\" step=\"0.01\" style=\"" + G + "\">"
        + "<label for=\"plan_odeme\" style=\"" + E + "\">Ödeme tipi</label><select id=\"plan_odeme\" style=\"" + G + "\"><option value=\"banka\">Banka</option><option value=\"nakit\">Nakit</option><option value=\"karma\">Karma</option></select>"
        + "<div id=\"plan_paylar\" style=\"display:none;margin:8px 0;\"><label for=\"plan_nakit\" style=\"" + E + "\">Nakit</label><input id=\"plan_nakit\" type=\"number\" step=\"0.01\" style=\"" + G + "\"><label for=\"plan_banka\" style=\"" + E + "\">Banka net</label><input id=\"plan_banka\" type=\"number\" step=\"0.01\" style=\"" + G + "\"></div>"
        + "<div style=\"margin-top:10px;\">Hesaplanan brüt: <span id=\"plan_brut\">—</span></div>"
        + "<div id=\"plan_hata\" role=\"alert\" style=\"display:none;margin:8px 0;padding:8px;border:1px solid #e53935;border-radius:4px;color:#ff8a80;\"></div>"
        + "<div id=\"plan_uyarilar\" style=\"margin:8px 0;color:#ffcc80;\"></div>"
        + "<div id=\"plan_kilit_kutu\" style=\"" + K + "display:none;\">"
        + "<div id=\"plan_kilit_metin\"></div>"
        + "<label id=\"plan_kilit_etiket\" style=\"" + C + "\"><input id=\"plan_kilit_onay\" type=\"checkbox\" style=\"width:18px;height:18px;margin:0;\"> Bu aylar değişmeyecek, anladım</label>"
        + "</div>"
        + "<div id=\"plan_k3_kutu\" style=\"" + K + "display:none;border-color:#2d6a8f;\">"
        + "<div id=\"plan_k3_metin\"></div>"
        + "<div id=\"plan_k3_tablo\" style=\"overflow:auto;max-height:160px;margin-top:6px;\"></div>"
        + "</div>"
        + "<div id=\"plan_odemeli_kutu\" style=\"" + K + "display:none;\">"
        + "<div id=\"plan_odemeli_metin\"></div>"
        + "<div id=\"plan_odemeli_tablo\" style=\"overflow:auto;max-height:180px;margin-top:6px;\"></div>"
        + "<label id=\"plan_odemeli_etiket\" style=\"" + C + "\"><input id=\"plan_odemeli_onay\" type=\"checkbox\" style=\"width:18px;height:18px;margin:0;\"> Ödemesi olan geçmiş aylar etkilenecek, anladım</label>"
        + "</div>"
        + "<div id=\"plan_onizleme\" style=\"margin-top:8px;overflow:auto;\"></div>"
        + "</div>"
        + "<div id=\"dondurma_bolum\" style=\"display:none;\">"
        + "<div style=\"font-weight:600;margin-bottom:6px;\">Plan dondur</div>"
        + "<div id=\"dondurma_not\" style=\"margin:0 0 10px;padding:8px;border:1px solid #546e7a;border-radius:4px;color:#b0bec5;font-size:12px;line-height:1.4;\">Dondurma; grid, borçlandırma/tahsilat panelleri, ekstre ve ödeme tutarlarını 0 yapar. Cari kart özeti ve fatura satırları (borç kayıtları) eski tutarda kalır.</div>"
        + "<div id=\"dondurma_pasif_not\" role=\"status\" style=\"display:none;margin:0 0 10px;padding:8px;border:1px solid #ff9800;border-radius:4px;color:#ffcc80;\">Bu müşteri pasif: yeni dondurma kaydedilemez, geçmiş görüntülenir.</div>"
        + "<div id=\"dondurma_gecmis_bolum\" style=\"margin:0 0 10px;padding:8px;border:1px solid #2d4060;border-radius:6px;\"></div>"
        + "<label for=\"dondurma_bas\" style=\"" + E + "\">Başlangıç ayı</label><input id=\"dondurma_bas\" type=\"month\" style=\"" + G + "\">"
        + "<label for=\"dondurma_sure\" style=\"" + E + "\">Süre (ay)</label><input id=\"dondurma_sure\" type=\"number\" min=\"1\" max=\"24\" step=\"1\" value=\"1\" style=\"" + G + "\">"
        + "<div id=\"dondurma_hata\" role=\"alert\" style=\"display:none;margin:8px 0;padding:8px;border:1px solid #e53935;border-radius:4px;color:#ff8a80;\"></div>"
        + "<div id=\"dondurma_uyari\" role=\"status\" style=\"display:none;margin:8px 0;padding:8px;border:1px solid #ff9800;border-radius:4px;color:#ffcc80;\"></div>"
        + "<div id=\"dondurma_onizleme\" style=\"margin-top:8px;overflow:auto;\"></div>"
        + "<div style=\"display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;\">"
        + "<button type=\"button\" id=\"dondurma_onizle_btn\" style=\"" + D + "background:#00838f;color:#fff;\">Önizle</button>"
        + "<button type=\"button\" id=\"dondurma_kaydet\" style=\"" + D + "background:#2e7d32;color:#fff;\" disabled>Kaydet</button>"
        + "</div></div>"
        /* Düğme satırı modal kaydırılsa da altta sabit kalır; Kaydet her zaman görünür, pasifse nedeni yanında yazar. */
        + "<div id=\"plan_alt\" style=\"position:sticky;bottom:-16px;margin:12px -16px -16px;padding:10px 16px;background:#0f2537;border-top:1px solid #2d4060;display:flex;gap:8px;align-items:center;flex-wrap:wrap;\">"
        + "<button type=\"button\" id=\"plan_onizle_btn\" style=\"" + D + "background:#00838f;color:#fff;\">Önizle</button>"
        + "<button type=\"button\" id=\"plan_kaydet\" style=\"" + D + "background:#2e7d32;color:#fff;\" disabled>Kaydet</button>"
        + "<button type=\"button\" id=\"plan_kapat\" style=\"" + D + "background:#455a64;color:#fff;\">Kapat</button>"
        + "<span id=\"plan_kaydet_ipucu\" style=\"font-size:12px;color:#ffcc80;\"></span>"
        + "</div>";
    kok.appendChild(kutu);
    document.body.appendChild(kok);
    document.getElementById("plan_kapat").onclick = planModalKapat;
    document.getElementById("plan_x").onclick = planModalKapat;
    document.getElementById("dondurma_onizle_btn").onclick = dondurmaOnizlemeIste;
    document.getElementById("dondurma_kaydet").onclick = dondurmaKaydet;
    ["dondurma_bas", "dondurma_sure"].forEach(function (id) {
        var el = document.getElementById(id);
        if (!el) return;
        el.addEventListener("input", function () {
            window.__dondurmaOnizlemeHazir = false;
            window.__dondurmaSonAnahtar = "";
        });
    });
    /* Dışına tıklama: basış ve bırakış ikisi de arka plandaysa (kutu içinde başlayan metin seçimi kapatmaz). */
    kok.addEventListener("mousedown", function (e) { kok.__basisDisarida = (e.target === kok); });
    kok.addEventListener("click", function (e) {
        if (e.target === kok && kok.__basisDisarida) planKapatIstek();
    });
    if (!window.__planEscKurulu) {
        window.__planEscKurulu = true;
        document.addEventListener("keydown", function (e) {
            if (e.key !== "Escape" && e.keyCode !== 27) return;
            var m = document.getElementById("plan_degistir_modal");
            if (!m || m.style.display === "none") return;
            e.preventDefault();
            e.stopPropagation();
            planKapatIstek();
        }, true);
    }
    document.getElementById("plan_onizle_btn").onclick = planOnizlemeIste;
    document.getElementById("plan_kaydet").onclick = planKaydet;
    document.getElementById("plan_kilit_onay").onchange = planKaydetDurumu;
    document.getElementById("plan_odemeli_onay").onchange = planKaydetDurumu;
    document.getElementById("plan_odeme").onchange = function () {
        document.getElementById("plan_paylar").style.display = this.value === "karma" ? "" : "none";
    };
    /* Form alanı değişince eski önizleme geçersiz: Kaydet kapanır, yeniden Önizle gerekir. */
    ["plan_gecerlilik", "plan_yeni_net", "plan_kdv", "plan_odeme", "plan_nakit", "plan_banka"].forEach(function (id) {
        var el = document.getElementById(id);
        if (!el) return;
        el.addEventListener("input", planOnizlemeSifirla);
        el.addEventListener("change", planOnizlemeSifirla);
    });
}

/* Önizleme sonucunu ve onay kutularını sıfırlar. Hata kutusuna dokunmaz. */
function planOnizlemeSifirla() {
    window.__planOnizlemeHazir = false;
    window.__planKilit = [];
    window.__planOdemeli = [];
    ["plan_kilit_kutu", "plan_odemeli_kutu", "plan_k3_kutu"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.style.display = "none";
    });
    ["plan_kilit_onay", "plan_odemeli_onay"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.checked = false;
    });
    planKaydetDurumu();
}

/* Plan ve dondurma ayni popup, ayri icerik. Gecmis kaplari tek kalir. */
function planModuUygula(bolum) {
    var dondur = bolum === "dondur";
    window.__planBolum = dondur ? "dondur" : "plan";
    var baslik = document.getElementById("plan_baslik_metin");
    if (baslik) baslik.textContent = dondur ? "Plan dondur" : "Plan değiştir";
    var form = document.getElementById("plan_form_bolum");
    if (form) form.style.display = dondur ? "none" : "";
    var don = document.getElementById("dondurma_bolum");
    if (don) don.style.display = dondur ? "" : "none";
    ["plan_onizle_btn", "plan_kaydet", "plan_kaydet_ipucu"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.style.display = dondur ? "none" : "";
    });
    if (!dondur) planKaydetDurumu();
}

function planDegistirModalAc(bolum) {
    /* Başka müşteriye geçilmişse eski müşterinin arayüzü kalkar, popup açılmaz. */
    var simdiMid = planMusteriId();
    if (window.__planMid && simdiMid != null && String(simdiMid) !== window.__planMid) {
        planArayuzKaldir();
        return;
    }
    if (!window.__planMid) return;
    planModalHazirla();
    var modal = document.getElementById("plan_degistir_modal");
    if (!modal) return;
    var ay = new Date();
    var ayIso = ay.getFullYear() + "-" + String(ay.getMonth() + 1).padStart(2, "0");
    var gec = document.getElementById("plan_gecerlilik");
    if (gec) {
        /* Geçmiş dahil serbest; alt sınır sözleşme başlangıç ayı. Varsayılan bulunulan ay. */
        var sozBas = window.__planSozBas || "";
        gec.min = sozBas;
        if (!gec.value) gec.value = ayIso;
        if (sozBas && gec.value < sozBas) gec.value = sozBas;
    }
    var kdvKart = document.getElementById("kdv_oran");
    var kdv = document.getElementById("plan_kdv");
    if (kdv && kdvKart && kdvKart.value !== "") kdv.value = kdvKart.value;
    else if (kdv && !kdv.value) kdv.value = "20";
    var odeme = document.getElementById("plan_odeme");
    var nakit = document.getElementById("kira_nakit");
    var banka = document.getElementById("kira_banka");
    if (odeme) {
        if (nakit && nakit.checked && banka && banka.checked) odeme.value = "karma";
        else if (nakit && nakit.checked) odeme.value = "nakit";
        else odeme.value = "banka";
        document.getElementById("plan_paylar").style.display = odeme.value === "karma" ? "" : "none";
    }
    var nakitT = document.getElementById("kira_nakit_tutar");
    var bankaT = document.getElementById("kira_banka_tutar");
    if (document.getElementById("plan_nakit") && nakitT) document.getElementById("plan_nakit").value = nakitT.value || "";
    if (document.getElementById("plan_banka") && bankaT) document.getElementById("plan_banka").value = bankaT.value || "";
    planHataGoster("");
    planOnizlemeSifirla();
    var durSel = document.getElementById("musteri_durum");
    var pasifNot = document.getElementById("plan_pasif_not");
    if (pasifNot) pasifNot.style.display = (durSel && durSel.value === "pasif") ? "block" : "none";
    dondurmaPasifUygula();
    var dBas = document.getElementById("dondurma_bas");
    if (dBas && !dBas.value) dBas.value = ayIso;
    planModuUygula(bolum === "dondur" ? "dondur" : "plan");
    modal.style.display = "block";
    var kutuEl = modal.firstChild;
    if (kutuEl) kutuEl.scrollTop = 0;
    window.__planTaban = planFormOzeti();
}

/* Formdaki kullanıcı girdileri; açılıştaki hâlinden farklıysa form "kirli". */
function planFormOzeti() {
    return ["plan_gecerlilik", "plan_yeni_net", "plan_kdv", "plan_odeme", "plan_nakit", "plan_banka"].map(function (id) {
        var el = document.getElementById(id);
        return el ? String(el.value) : "";
    }).join("|");
}

function planFormKirliMi() {
    return planFormOzeti() !== (window.__planTaban || "");
}

/* Form girdilerini ve önizlemeyi temizler; sonraki açılışta varsayılanlar yeniden doldurulur. */
function planFormSifirla() {
    ["plan_gecerlilik", "plan_yeni_net", "plan_nakit", "plan_banka"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.value = "";
    });
    var ob = document.getElementById("plan_brut");
    if (ob) ob.textContent = "—";
    ["plan_uyarilar", "plan_onizleme"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.textContent = "";
    });
    window.__planSonGovde = null;
    planHataGoster("");
    planOnizlemeSifirla();
    window.__planTaban = "";
}

/* Kapat düğmesi ve X: doğrudan kapatır. */
function planModalKapat() {
    var modal = document.getElementById("plan_degistir_modal");
    if (modal) modal.style.display = "none";
    planFormSifirla();
}

/* ESC ve dışına tıklama: form değiştirilmişse kapatmadan önce basit onay. */
function planKapatIstek() {
    if (planFormKirliMi() && !window.confirm("Kaydedilmemiş plan bilgileri var. Kapatılsın mı?")) return;
    planModalKapat();
}

function planGovde() {
    var odeme = (document.getElementById("plan_odeme") || {}).value || "banka";
    var govde = {
        gecerlilik_ay: (document.getElementById("plan_gecerlilik") || {}).value || "",
        yeni_net: parseFloat((document.getElementById("plan_yeni_net") || {}).value),
        kdv_oran: parseFloat((document.getElementById("plan_kdv") || {}).value),
        odeme: odeme
    };
    if (odeme === "karma") {
        govde.nakit_tutar = parseFloat((document.getElementById("plan_nakit") || {}).value);
        govde.banka_tutar = parseFloat((document.getElementById("plan_banka") || {}).value);
    }
    return govde;
}

function planKaydetDurumu() {
    var btn = document.getElementById("plan_kaydet");
    if (!btn) return;
    var kilit = window.__planKilit || [];
    var odemeli = window.__planOdemeli || [];
    var onay = document.getElementById("plan_kilit_onay");
    var oOnay = document.getElementById("plan_odemeli_onay");
    var ipucu = "";
    if (!window.__planOnizlemeHazir) ipucu = "Önce Önizle'ye basın";
    else if (kilit.length > 0 && !(onay && onay.checked)) ipucu = "Kilitli ay onayını işaretleyin";
    else if (odemeli.length > 0 && !(oOnay && oOnay.checked)) ipucu = "Ödemeli ay onayını işaretleyin";
    var engel = ipucu !== "";
    var kayit = !!window.__planKaydediliyor;
    var hataIpucu = "";
    if (!engel) {
        /* Engel yoksa kısa durum: istek sürüyor / son hata / aynı aydaki aktif planın iptal uyarısı. */
        var son = window.__planSonGovde;
        if (kayit) ipucu = "Kaydediliyor…";
        else if (window.__planHataIpucu) { ipucu = window.__planHataIpucu; hataIpucu = ipucu; }
        else if (son && son.iptal_uyari) ipucu = son.iptal_uyari;
    }
    var kilitli = engel || kayit;
    btn.disabled = kilitli;
    btn.style.opacity = kilitli ? "0.5" : "1";
    btn.style.cursor = kilitli ? "not-allowed" : "pointer";
    var ip = document.getElementById("plan_kaydet_ipucu");
    if (ip) {
        ip.textContent = ipucu;
        ip.style.color = hataIpucu ? "#ff8a80" : "#ffcc80";
    }
}

function planAyAraligiMetni(liste) {
    if (!liste.length) return "";
    var ilk = liste[0].ay, son = liste[liste.length - 1].ay;
    return liste.length === 1 ? ilk : (ilk + " … " + son);
}

function planKilitCiz(kilit) {
    var kutu = document.getElementById("plan_kilit_kutu");
    var metin = document.getElementById("plan_kilit_metin");
    if (!kutu || !metin) return;
    metin.textContent = "";
    if (!kilit.length) {
        kutu.style.display = "none";
        return;
    }
    kutu.style.display = "";
    var SINIF_AD = { K1: "GİB'e gönderilmiş", K2: "ödemeli, kilitli", K0: "belirsiz, kilitli" };
    var sayac = { K1: 0, K2: 0, K0: 0 };
    var satirlar = kilit.map(function (x) {
        var s = SINIF_AD[x.sinif] ? x.sinif : "K1";
        sayac[s] += 1;
        return x.ay + " (" + SINIF_AD[s] + (x.neden ? ": " + x.neden : "") + ", kayıt " + x.fatura_tutari + ")";
    });
    var dagilim = ["K1", "K2", "K0"].filter(function (k) { return sayac[k] > 0; }).map(function (k) {
        return SINIF_AD[k] + " " + sayac[k];
    }).join(", ");
    if (kilit.length <= 6) {
        metin.textContent = "Kilitli aylar değişmez: " + satirlar.join("; ");
        return;
    }
    /* Çok sayıda kilitli ay: tek satır özet, ayrıntı katlanır; onay kutusu hep görünür kalır. */
    var ozet = document.createElement("div");
    ozet.style.fontWeight = "600";
    ozet.textContent = "Kilitli " + kilit.length + " ay değişmez (" + dagilim + "): " + planAyAraligiMetni(kilit);
    metin.appendChild(ozet);
    var det = document.createElement("details");
    var sum = document.createElement("summary");
    sum.textContent = "Ayları göster";
    sum.style.cursor = "pointer";
    sum.style.fontSize = "12px";
    det.appendChild(sum);
    var ic = document.createElement("div");
    ic.style.maxHeight = "120px";
    ic.style.overflow = "auto";
    ic.style.fontSize = "12px";
    ic.textContent = satirlar.join("; ");
    det.appendChild(ic);
    metin.appendChild(det);
}

/* K3 (serbest) aylar: yalnız bilgi, onay gerektirmez. Fatura kayıt tutarları güncellenmez; plan zincire uygulanır. */
function planK3Ciz(liste, bilgi) {
    var kutu = document.getElementById("plan_k3_kutu");
    var metin = document.getElementById("plan_k3_metin");
    var tablo = document.getElementById("plan_k3_tablo");
    if (!kutu || !metin || !tablo) return;
    metin.textContent = "";
    tablo.textContent = "";
    if (!liste || !liste.length) {
        kutu.style.display = "none";
        return;
    }
    kutu.style.display = "";
    var baslik = document.createElement("div");
    baslik.style.fontWeight = "600";
    baslik.textContent = "Plana göre yeni tutara geçecek " + liste.length + " ay (GİB'siz, tahsilatsız): " + planAyAraligiMetni(liste);
    metin.appendChild(baslik);
    if (bilgi) {
        var b = document.createElement("div");
        b.style.marginTop = "4px";
        b.style.color = "#90caf9";
        b.textContent = bilgi;
        metin.appendChild(b);
    }
    var t = document.createElement("table");
    t.style.fontSize = "12px";
    var h = document.createElement("tr");
    ["Ay", "Mevcut fatura kaydı", "Önceki zincir brütü", "Plan sonrası zincir brütü"].forEach(function (s) {
        var th = document.createElement("th");
        th.textContent = s;
        th.style.padding = "2px 8px";
        th.style.textAlign = "left";
        h.appendChild(th);
    });
    t.appendChild(h);
    liste.forEach(function (o) {
        var tr = document.createElement("tr");
        [o.ay, o.fatura_tutari, o.eski_zincir_brut, o.zincir_brut].forEach(function (v) {
            var td = document.createElement("td");
            td.style.padding = "2px 8px";
            td.textContent = v == null ? "" : String(v);
            tr.appendChild(td);
        });
        t.appendChild(tr);
    });
    tablo.appendChild(t);
}

function planOdemeliCiz(odemeli, fazla) {
    var kutu = document.getElementById("plan_odemeli_kutu");
    var metin = document.getElementById("plan_odemeli_metin");
    var tablo = document.getElementById("plan_odemeli_tablo");
    if (!kutu || !metin || !tablo) return;
    metin.textContent = "";
    tablo.textContent = "";
    if (!odemeli.length) {
        kutu.style.display = "none";
        return;
    }
    kutu.style.display = "";
    metin.textContent = "Geçmiş aya plan: ödemesi olan " + odemeli.length + " ayın tutarı değişecek.";
    if (fazla) {
        var uyar = document.createElement("div");
        uyar.style.fontWeight = "700";
        uyar.style.color = "#ff5252";
        uyar.style.marginTop = "4px";
        uyar.textContent = "DİKKAT: Bazı aylarda ödenen tutar yeni brütten fazla (fazla ödeme oluşacak).";
        metin.appendChild(uyar);
    }
    var t = document.createElement("table");
    t.style.fontSize = "12px";
    var h = document.createElement("tr");
    ["Ay", "Eski brüt", "Yeni brüt", "Ödenen", "Yeni kalan / Fazla ödeme"].forEach(function (s) {
        var th = document.createElement("th");
        th.textContent = s;
        th.style.padding = "2px 8px";
        th.style.textAlign = "left";
        h.appendChild(th);
    });
    t.appendChild(h);
    odemeli.forEach(function (o) {
        var tr = document.createElement("tr");
        var fo = Number(o.fazla_odeme || 0) > 0.004;
        var son = fo ? ("Fazla ödeme " + o.fazla_odeme) : ("Kalan " + o.yeni_kalan);
        [o.ay, o.eski_brut, o.yeni_brut, o.odenen, son].forEach(function (v, i) {
            var td = document.createElement("td");
            td.style.padding = "2px 8px";
            td.textContent = v == null ? "" : String(v);
            if (fo) {
                td.style.color = "#ff5252";
                if (i === 4) td.style.fontWeight = "700";
            }
            tr.appendChild(td);
        });
        t.appendChild(tr);
    });
    tablo.appendChild(t);
}

function planHataGoster(mesaj) {
    var kutu = document.getElementById("plan_hata");
    if (!kutu) return;
    kutu.textContent = mesaj || "";
    kutu.style.display = mesaj ? "" : "none";
    /* Alt çubukta kısa ipucu: hata kutusu kaydırılmış modalın üstünde kalabilir. */
    var kisa = String(mesaj || "");
    if (kisa.length > 80) kisa = kisa.slice(0, 77) + "…";
    window.__planHataIpucu = mesaj ? ("Hata: " + kisa) : "";
    if (mesaj && typeof kutu.scrollIntoView === "function") {
        try { kutu.scrollIntoView({ block: "nearest" }); } catch (e) { /* eski tarayıcı */ }
    }
    planKaydetDurumu();
}

/* Sunucu cevabından kullanıcı mesajı: JSON mesaj varsa o; JSON değilse (HTML 500, oturum yönlendirmesi) durum koduyla. */
function planHataMetni(pack, onek) {
    if (pack && pack.j && pack.j.mesaj) return String(pack.j.mesaj);
    var kod = pack ? pack.kod : "";
    if (pack && !pack.j) {
        return Number(kod) >= 500 ? (onek + ": sunucu hatası (" + kod + ")") : (onek + ": sunucudan geçersiz yanıt (" + kod + ")");
    }
    return onek + ": " + kod;
}

/* Yanıt JSON değilse de (oturum yönlendirmesi, 500 HTML sayfası) durum koduyla döner; sessiz kalmaz. */
function planIstekGonder(url, govde) {
    return fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(govde)
    }).then(function (r) {
        return r.text().then(function (t) {
            var j = null;
            try { j = JSON.parse(t); } catch (e) { j = null; }
            return { ok: r.ok, kod: r.status, j: j };
        });
    });
}

function planOnizlemeIste() {
    var mid = planMusteriId();
    planHataGoster("");
    if (!mid) {
        planHataGoster("Önizleme alınamadı: müşteri seçili değil");
        return;
    }
    var dugme = document.getElementById("plan_onizle_btn");
    if (dugme) dugme.disabled = true;
    window.__planOnizlemeHazir = false;
    planKaydetDurumu();
    planIstekGonder("/giris/api/musteri/" + encodeURIComponent(mid) + "/plan/onizleme", planGovde())
        .then(function (pack) {
            var j = pack.j || {};
            if (!pack.ok || !j.ok) {
                planOnizlemeSifirla();
                planHataGoster(j.mesaj || ("Önizleme alınamadı: " + pack.kod));
                return;
            }
            var brut = document.getElementById("plan_brut");
            if (brut) brut.textContent = String(j.yeni_brut);
            window.__planKilit = j.kilitlenen_aylar || [];
            window.__planOdemeli = j.odemeli_aylar || [];
            ["plan_kilit_onay", "plan_odemeli_onay"].forEach(function (id) {
                var el = document.getElementById(id);
                if (el) el.checked = false;
            });
            planKilitCiz(window.__planKilit);
            planK3Ciz((j.kilit_listeleri || {}).K3_guncellenecek_aylar || [], j.k3_bilgi || "");
            planOdemeliCiz(window.__planOdemeli, !!j.fazla_odeme);
            var uy = document.getElementById("plan_uyarilar");
            if (uy) {
                uy.textContent = "";
                (j.uyarilar || []).forEach(function (u) {
                    var d = document.createElement("div");
                    /* "Görünür değişiklik yok" yalnız bilgidir; Kaydet'i engellemez. */
                    d.textContent = (u.kod === "gorunur_degisiklik_yok" ? "Bilgi: " : "") + (u.mesaj || u.ay || "");
                    if (u.kod === "gorunur_degisiklik_yok") d.style.color = "#90caf9";
                    uy.appendChild(d);
                });
            }
            var tablo = document.getElementById("plan_onizleme");
            if (tablo) {
                tablo.textContent = "";
                var table = document.createElement("table");
                var head = document.createElement("tr");
                ["Ay", "Eski brüt", "Yeni brüt"].forEach(function (h) {
                    var th = document.createElement("th");
                    th.textContent = h;
                    head.appendChild(th);
                });
                table.appendChild(head);
                (j.aylar || []).forEach(function (a) {
                    var tr = document.createElement("tr");
                    [a.ay, a.eski_brut, a.yeni_brut].forEach(function (v) {
                        var td = document.createElement("td");
                        td.textContent = v == null ? "" : String(v);
                        tr.appendChild(td);
                    });
                    table.appendChild(tr);
                });
                tablo.appendChild(table);
            }
            window.__planOnizlemeHazir = true;
            window.__planSonGovde = j;
            planKaydetDurumu();
        })
        .catch(function () {
            window.__planOnizlemeHazir = false;
            planHataGoster("Önizleme alınamadı: bağlantı hatası");
            planKaydetDurumu();
        })
        .then(function () {
            if (dugme) dugme.disabled = false;
        });
}

function planKaydet() {
    var mid = planMusteriId();
    planHataGoster("");
    if (!mid) {
        planHataGoster("Kaydedilemedi: müşteri seçili değil");
        return;
    }
    var govde = planGovde();
    if (window.__planSonGovde && window.__planSonGovde.yeni_brut != null) govde.yeni_brut = window.__planSonGovde.yeni_brut;
    var onay = document.getElementById("plan_kilit_onay");
    if (onay && onay.checked) govde.onay_kilitli_aylar = true;
    var oOnay = document.getElementById("plan_odemeli_onay");
    if (oOnay && oOnay.checked) govde.onay_odemeli_aylar = true;
    var btn = document.getElementById("plan_kaydet");
    if (btn && btn.disabled) return;
    if (window.__planKaydediliyor) return;
    /* İstek boyunca Kaydet (ve Önizle) kilitli: çift tıklama ikinci istek göndermez. */
    window.__planKaydediliyor = true;
    var onizleBtn = document.getElementById("plan_onizle_btn");
    if (onizleBtn) onizleBtn.disabled = true;
    planKaydetDurumu();
    planIstekGonder("/giris/api/musteri/" + encodeURIComponent(mid) + "/plan", govde)
        .then(function (pack) {
            if (!pack.ok || !pack.j || !pack.j.ok) {
                planHataGoster(planHataMetni(pack, "Kaydedilemedi"));
                return;
            }
            planKayitSonrasi(mid);
        })
        .catch(function () { planHataGoster("Kaydedilemedi: bağlantı hatası"); })
        .then(function () {
            window.__planKaydediliyor = false;
            if (onizleBtn) onizleBtn.disabled = false;
            planKaydetDurumu();
        });
}

/* 404 "Açık plan yok.": plan zaten kapanmış (başka sekme, aynı aya yeni plan ile otomatik iptal); ekrandaki liste eski. */
function planAcikPlanYok(pack) {
    return !!(pack && pack.kod === 404 && pack.j && String(pack.j.mesaj || "").toLowerCase().indexOf("açık plan yok") >= 0);
}

function planIptalGonder(mid, planId, btn) {
    /* İstek boyunca İptal kilitli: çift tıklama tek istek gönderir. */
    if (window.__planIptalDevam) return;
    window.__planIptalDevam = true;
    var kutuDugmeleri = document.querySelectorAll("#plan_gecmis_bolum button");
    Array.prototype.forEach.call(kutuDugmeleri, function (b) { b.disabled = true; });
    if (btn) btn.disabled = true;
    function kilitAc() {
        window.__planIptalDevam = false;
        Array.prototype.forEach.call(kutuDugmeleri, function (b) { b.disabled = false; });
        if (btn) btn.disabled = false;
    }
    planIstekGonder("/giris/api/musteri/" + encodeURIComponent(mid) + "/plan/" + encodeURIComponent(planId) + "/iptal", {})
        .then(function (pack) {
            if (pack.ok && pack.j && pack.j.ok) {
                planSayfaYenile(mid);
                return;
            }
            if (planAcikPlanYok(pack)) {
                /* alert yok: liste sunucudan yenilenir, bilgi listenin içinde görünür. */
                window.__planBilgi = { mid: String(mid), mesaj: "Plan zaten iptal edilmiş, liste güncellendi." };
                planKapisiniYenile(mid);
                return;
            }
            alert((pack.j && pack.j.mesaj) || "İptal edilemedi");
        })
        .catch(function () { alert("İptal edilemedi"); })
        .then(kilitAc);
}

/* Plan kaydı sonrası popup kapanır ve sayfa yenilenir. */
function planKayitSonrasi(mid) {
    planModalKapat();
    planSayfaYenile(mid);
}

/* Geçmiş listesi sunucu cevabından yenilenir; grid/borç/ekstre güncellenir. Popup'a dokunmaz (İptal popup içinde kalır). */
function planPasifMi() {
    var sel = document.getElementById("musteri_durum");
    return !!(sel && sel.value === "pasif");
}

function dondurmaPasifUygula() {
    var pasif = planPasifMi();
    var btn = document.getElementById("plan_dondur_btn");
    if (btn) btn.disabled = pasif;
    ["dondurma_bas", "dondurma_sure", "dondurma_onizle_btn"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.disabled = pasif;
    });
    var kaydet = document.getElementById("dondurma_kaydet");
    if (kaydet) kaydet.disabled = !!(pasif || window.__dondurmaIstek);
    var not = document.getElementById("dondurma_pasif_not");
    if (not) not.style.display = pasif ? "block" : "none";
}

function dondurmaHataGoster(mesaj) {
    var kutu = document.getElementById("dondurma_hata");
    if (!kutu) return;
    kutu.textContent = mesaj || "";
    kutu.style.display = mesaj ? "block" : "none";
    if (mesaj && typeof kutu.scrollIntoView === "function") {
        try { kutu.scrollIntoView({ block: "nearest" }); } catch (_eDonHata) {}
    }
}

function dondurmaGovdeAnahtar(govde) {
    return String((govde && govde.baslangic_ay) || "") + "|" + String(govde && govde.ay_sayisi);
}

function dondurmaNedenYaz(sinif, neden) {
    if (sinif === "K1") return "K1 GİB";
    if (sinif === "K2") return "K2 tahsilat";
    if (sinif === "K0") return "K0 belirsiz";
    return neden || sinif || "";
}

function dondurmaGovde() {
    var bas = (document.getElementById("dondurma_bas") || {}).value || "";
    var n = parseInt((document.getElementById("dondurma_sure") || {}).value, 10);
    return { baslangic_ay: bas, ay_sayisi: n, musteri_id: planMusteriId() };
}

function dondurmaGecmisCiz(mid, veri) {
    var kutu = document.getElementById("dondurma_gecmis_bolum");
    if (!kutu) return;
    kutu.textContent = "";
    var baslik = document.createElement("div");
    baslik.textContent = "Dondurma geçmişi";
    baslik.style.fontWeight = "600";
    baslik.style.marginBottom = "6px";
    kutu.appendChild(baslik);
    var satirlar = (veri && veri.gecmis && veri.gecmis.length) ? veri.gecmis : ((veri && veri.aktif) || []);
    if (!satirlar.length) {
        var bos = document.createElement("div");
        bos.textContent = "Açık dondurma yok.";
        bos.style.color = "#90a4ae";
        bos.style.fontSize = "12px";
        kutu.appendChild(bos);
        return;
    }
    satirlar.forEach(function (p) {
        var satir = document.createElement("div");
        satir.style.display = "flex";
        satir.style.gap = "8px";
        satir.style.alignItems = "center";
        satir.style.flexWrap = "wrap";
        satir.style.fontSize = "12px";
        satir.style.marginBottom = "4px";
        if (p.iptal_at) satir.style.opacity = "0.45";
        var metin = document.createElement("span");
        var ay = String(p.baslangic_ay || "").slice(0, 7);
        var kim = p.iptal_at ? (p.iptal_eden || "") : (p.olusturan || "");
        var zaman = p.iptal_at || p.created_at || "";
        metin.textContent = ay + "  " + (p.ay_sayisi != null ? p.ay_sayisi : "") + " ay" + (p.iptal_at ? "  iptal" : "  açık") + (kim ? "  " + kim : "") + (zaman ? "  " + String(zaman).slice(0, 19) : "");
        satir.appendChild(metin);
        if (!p.iptal_at && p.id != null) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.textContent = "Dondurmayı kaldır";
            btn.onclick = function () { dondurmaKaldir(mid, p.id, btn); };
            satir.appendChild(btn);
        }
        kutu.appendChild(satir);
    });
}

function dondurmaGecmisYukle(mid) {
    var sira = (window.__dondurmaYenileSira = (window.__dondurmaYenileSira || 0) + 1);
    var istenen = String(mid || "");
    if (!istenen) return;
    fetch("/giris/api/musteri/" + encodeURIComponent(istenen) + "/plan/dondurma", { credentials: "same-origin" })
        .then(function (r) {
            return r.json().then(function (j) { return { ok: r.ok, j: j }; }).catch(function () { return { ok: false, j: null }; });
        })
        .then(function (pack) {
            if (sira !== window.__dondurmaYenileSira) return;
            var simdi = String(planMusteriId() || "");
            if (simdi !== "" && simdi !== istenen) return;
            if (!pack.ok || !pack.j || !pack.j.ok) return;
            dondurmaGecmisCiz(istenen, pack.j);
        })
        .catch(function () {});
}

function dondurmaOnizlemeCiz(j) {
    var kutu = document.getElementById("dondurma_onizleme");
    var uyari = document.getElementById("dondurma_uyari");
    if (kutu) kutu.textContent = "";
    var atlanan = (j && j.atlanan_aylar) || [];
    if (uyari) {
        if (atlanan.length) {
            uyari.style.display = "block";
            uyari.textContent = "Atlanan aylar dondurulmaz: " + atlanan.map(function (a) {
                return a.ay + " (" + dondurmaNedenYaz(a.sinif, a.neden) + ")";
            }).join(", ");
        } else {
            uyari.style.display = "none";
            uyari.textContent = "";
        }
    }
    if (!kutu) return;
    var tablo = document.createElement("table");
    tablo.style.width = "100%";
    tablo.style.borderCollapse = "collapse";
    tablo.style.fontSize = "12px";
    var bas = document.createElement("tr");
    ["Ay", "Durum", "Neden"].forEach(function (h) {
        var th = document.createElement("th");
        th.textContent = h;
        th.style.textAlign = "left";
        th.style.padding = "4px";
        bas.appendChild(th);
    });
    tablo.appendChild(bas);
    (j.aylar || []).forEach(function (a) {
        var tr = document.createElement("tr");
        var durum = a.durum === "atlanacak" ? "atlanacak" : "dondurulacak";
        var neden = durum === "atlanacak" ? dondurmaNedenYaz(a.sinif, a.neden) : "";
        [a.ay, durum, neden].forEach(function (v) {
            var td = document.createElement("td");
            td.textContent = v == null ? "" : String(v);
            td.style.padding = "4px";
            td.style.borderTop = "1px solid #2d4060";
            tr.appendChild(td);
        });
        tablo.appendChild(tr);
    });
    kutu.appendChild(tablo);
}

function dondurmaOnizlemeIste() {
    if (window.__dondurmaIstek) return;
    var mid = planMusteriId();
    dondurmaHataGoster("");
    if (planPasifMi()) {
        dondurmaHataGoster("Bu müşteri pasif: yeni dondurma kaydedilemez.");
        return;
    }
    if (!mid) {
        dondurmaHataGoster("Önizleme alınamadı: müşteri seçili değil");
        return;
    }
    var govde = dondurmaGovde();
    if (!govde.baslangic_ay || !(govde.ay_sayisi >= 1 && govde.ay_sayisi <= 24)) {
        dondurmaHataGoster("Başlangıç ayı ve süre (1-24) gerekli.");
        return;
    }
    window.__dondurmaOnizlemeHazir = false;
    window.__dondurmaSonAnahtar = "";
    window.__dondurmaIstek = true;
    dondurmaPasifUygula();
    planIstekGonder("/giris/api/musteri/" + encodeURIComponent(mid) + "/plan/dondurma/onizleme", govde)
        .then(function (pack) {
            if (!pack.ok || !pack.j || !pack.j.ok) {
                dondurmaHataGoster(planHataMetni(pack, "Önizleme alınamadı"));
                return;
            }
            dondurmaOnizlemeCiz(pack.j);
            window.__dondurmaOnizlemeHazir = !!(pack.j.dondurulan_aylar && pack.j.dondurulan_aylar.length);
            window.__dondurmaSonGovde = govde;
            if (window.__dondurmaOnizlemeHazir) window.__dondurmaSonAnahtar = dondurmaGovdeAnahtar(govde);
            if (!window.__dondurmaOnizlemeHazir) dondurmaHataGoster("Dondurulacak ay yok.");
        })
        .catch(function () { dondurmaHataGoster("Önizleme alınamadı: bağlantı hatası"); })
        .then(function () {
            window.__dondurmaIstek = false;
            dondurmaPasifUygula();
        });
}

function dondurmaSayfaYenile(mid) {
    try { window.__sozlesmelerAylikSonCacheObj = null; } catch (_e) {}
    planSayfaYenile(mid);
    dondurmaGecmisYukle(mid);
}

function dondurmaKaydet() {
    if (window.__dondurmaIstek) return;
    var mid = planMusteriId();
    dondurmaHataGoster("");
    if (planPasifMi()) {
        dondurmaHataGoster("Bu müşteri pasif: yeni dondurma kaydedilemez.");
        return;
    }
    if (!mid) {
        dondurmaHataGoster("Kaydedilemedi: müşteri seçili değil");
        return;
    }
    var govde = dondurmaGovde();
    if (!govde.baslangic_ay || !(govde.ay_sayisi >= 1 && govde.ay_sayisi <= 24)) {
        dondurmaHataGoster("Başlangıç ayı ve süre (1-24) gerekli.");
        return;
    }
    /* Ilk tik onizlemeyi gosterir; kayit ikinci tikta. */
    if (!window.__dondurmaOnizlemeHazir || dondurmaGovdeAnahtar(govde) !== window.__dondurmaSonAnahtar) {
        dondurmaOnizlemeIste();
        return;
    }
    window.__dondurmaIstek = true;
    dondurmaPasifUygula();
    planIstekGonder("/giris/api/musteri/" + encodeURIComponent(mid) + "/plan/dondurma", govde)
        .then(function (pack) {
            if (!pack.ok || !pack.j || !pack.j.ok) {
                dondurmaHataGoster(planHataMetni(pack, "Kaydedilemedi"));
                return;
            }
            window.__dondurmaOnizlemeHazir = false;
            window.__dondurmaSonAnahtar = "";
            var oniz = document.getElementById("dondurma_onizleme");
            if (oniz) oniz.textContent = "";
            dondurmaSayfaYenile(mid);
        })
        .catch(function () {
            dondurmaHataGoster("Kaydedilemedi: bağlantı hatası");
        })
        .then(function () {
            window.__dondurmaIstek = false;
            dondurmaPasifUygula();
        });
}

function dondurmaKaldir(mid, dondurmaId, btn) {
    if (btn) btn.disabled = true;
    planIstekGonder("/giris/api/musteri/" + encodeURIComponent(mid) + "/plan/dondurma/" + encodeURIComponent(dondurmaId) + "/iptal", { musteri_id: mid })
        .then(function (pack) {
            if (!pack.ok || !pack.j || !pack.j.ok) {
                dondurmaHataGoster(planHataMetni(pack, "Kaldırılamadı"));
                if (btn) btn.disabled = false;
                return;
            }
            dondurmaSayfaYenile(mid);
        })
        .catch(function () {
            dondurmaHataGoster("Kaldırılamadı: bağlantı hatası");
            if (btn) btn.disabled = false;
        });
}

function planSayfaYenile(mid) {
    planKapisiniYenile(mid);
    if (typeof sozlesmelerAylikHizliYukle === "function") {
        try { sozlesmelerAylikHizliYukle(); } catch (_e) {}
    }
    if (typeof sozlesmelerAylikGuncelle === "function") {
        try { sozlesmelerAylikGuncelle(); } catch (_e2) {}
    }
    if (typeof girisTahsilatOzetGuncelle === "function") {
        try { girisTahsilatOzetGuncelle(); } catch (_e3) {}
    }
    if (typeof cariEkstreYukle === "function") {
        try { cariEkstreYukle(); } catch (_e4) {}
    }
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        planDurumKarari: planDurumKarari,
        planIptalEdilebilir: planIptalEdilebilir,
        planSecenekEklensin: planSecenekEklensin,
        planHataMetni: planHataMetni,
        planAcikPlanYok: planAcikPlanYok
    };
}
