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
    if (secilen === "plan") {
        return { goster: true, durum: geri, modal: true, kaydetDurum: false };
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
    var bugun = String(bugunIso || "").slice(0, 7);
    if (ay.length < 7 || bugun.length < 7) return false;
    return ay > bugun;
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
    var kapi = !!document.getElementById("plan_gecmis_bolum");
    var karar = planDurumKarari(secilen, onceki, kapi);
    if (karar.modal) {
        if (sel) sel.value = karar.durum;
        if (typeof syncDurumUI === "function") syncDurumUI(karar.durum, {});
        planDegistirModalAc();
        return;
    }
    if (typeof syncDurumUI === "function") syncDurumUI(karar.durum, {});
}

function planArayuzKaldir() {
    var op = document.querySelector("#musteri_durum option[value='plan']");
    if (op) op.remove();
    var gec = document.getElementById("plan_gecmis_bolum");
    if (gec) gec.remove();
    var modal = document.getElementById("plan_degistir_modal");
    if (modal) modal.remove();
    window.__planOnizlemeHazir = false;
    window.__planKilit = [];
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

function planKapisiniYenile(mid) {
    planArayuzKaldir();
    if (mid == null || mid === "") return;
    var istenen = String(mid);
    fetch("/giris/api/musteri/" + encodeURIComponent(istenen) + "/plan", { credentials: "same-origin" })
        .then(function (r) {
            if (!r.ok) return null;
            return r.json();
        })
        .then(function (j) {
            if (!j || !j.ok) return;
            var simdi = String(planMusteriId() || "");
            if (simdi !== "" && simdi !== istenen) return;
            planArayuzKur(istenen, j);
        })
        .catch(function () {});
}

function planArayuzKur(mid, veri) {
    if (!planSecenekEklensin(true)) return;
    var sel = document.getElementById("musteri_durum");
    if (sel && !sel.querySelector("option[value='plan']")) {
        var op = document.createElement("option");
        op.value = "plan";
        op.textContent = "Plan değiştir";
        sel.appendChild(op);
    }
    planGecmisCiz(mid, veri || {});
    planModalHazirla();
}

function planGecmisCiz(mid, veri) {
    var sel = document.getElementById("musteri_durum");
    var kutu = document.createElement("div");
    kutu.id = "plan_gecmis_bolum";
    /* .form-grid etiket|alan iki sütunludur. Kutu tek hücre kaplarsa sonraki etiketler alan sütununa kayar.
       Tam genişlikli ayrı satır: sütun sayısı fark etmez, diğer satırların yapısı değişmez. */
    kutu.style.gridColumn = "1 / -1";
    kutu.style.minWidth = "0";
    kutu.style.boxSizing = "border-box";
    kutu.style.margin = "8px 0 12px";
    kutu.style.padding = "8px";
    kutu.style.border = "1px solid #2d4060";
    kutu.style.borderRadius = "6px";
    var baslik = document.createElement("div");
    baslik.textContent = "Plan geçmişi";
    baslik.style.fontWeight = "600";
    baslik.style.marginBottom = "6px";
    kutu.appendChild(baslik);
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
            btn.onclick = function () { planIptalGonder(mid, p.id); };
            satir.appendChild(btn);
        } else if (!p.iptal_at) {
            var not = document.createElement("span");
            not.textContent = "Geçmiş plan iptal edilemez, yeni plan değişikliği girin";
            not.style.color = "#90a4ae";
            satir.appendChild(not);
        }
        kutu.appendChild(satir);
    });
    if (sel && sel.parentNode) sel.parentNode.insertBefore(kutu, sel.nextSibling);
}

function planModalHazirla() {
    if (document.getElementById("plan_degistir_modal")) return;
    var kok = document.createElement("div");
    kok.id = "plan_degistir_modal";
    kok.style.display = "none";
    kok.style.position = "fixed";
    kok.style.inset = "0";
    kok.style.background = "rgba(0,0,0,.45)";
    kok.style.zIndex = "80";
    var kutu = document.createElement("div");
    kutu.style.background = "#0f2537";
    kutu.style.color = "#e0f7fa";
    kutu.style.maxWidth = "720px";
    kutu.style.margin = "40px auto";
    kutu.style.padding = "16px";
    kutu.style.borderRadius = "8px";
    kutu.style.maxHeight = "86vh";
    kutu.style.overflow = "auto";
    /* Etiket / alan / düğme stilleri satır içi: sayfa CSS'inden bağımsız, etiketler alanın üstünde ayrı satırda. */
    var E = "display:block;margin:8px 0 2px;font-size:12px;color:#b0bec5;";
    var G = "display:block;box-sizing:border-box;width:100%;max-width:280px;margin:0 0 4px;padding:6px 10px;"
        + "background:#2d4060;border:1px solid #3d5070;border-radius:3px;color:#e0f7fa;font-size:13px;font-family:inherit;";
    var D = "padding:7px 16px;border-radius:4px;border:none;cursor:pointer;font-size:13px;font-family:inherit;";
    kutu.style.fontFamily = "inherit";
    kutu.style.fontSize = "13px";
    kutu.innerHTML = ""
        + "<div style=\"font-weight:600;font-size:15px;margin-bottom:6px;\">Plan değiştir</div>"
        + "<label for=\"plan_gecerlilik\" style=\"" + E + "\">Geçerlilik ayı</label><input id=\"plan_gecerlilik\" type=\"month\" style=\"" + G + "\">"
        + "<label for=\"plan_yeni_net\" style=\"" + E + "\">Yeni aylık net</label><input id=\"plan_yeni_net\" type=\"number\" step=\"0.01\" style=\"" + G + "\">"
        + "<label for=\"plan_kdv\" style=\"" + E + "\">KDV oranı</label><input id=\"plan_kdv\" type=\"number\" step=\"0.01\" style=\"" + G + "\">"
        + "<label for=\"plan_odeme\" style=\"" + E + "\">Ödeme tipi</label><select id=\"plan_odeme\" style=\"" + G + "\"><option value=\"banka\">Banka</option><option value=\"nakit\">Nakit</option><option value=\"karma\">Karma</option></select>"
        + "<div id=\"plan_paylar\" style=\"display:none;margin:8px 0;\"><label for=\"plan_nakit\" style=\"" + E + "\">Nakit</label><input id=\"plan_nakit\" type=\"number\" step=\"0.01\" style=\"" + G + "\"><label for=\"plan_banka\" style=\"" + E + "\">Banka net</label><input id=\"plan_banka\" type=\"number\" step=\"0.01\" style=\"" + G + "\"></div>"
        + "<div style=\"margin-top:10px;\">Hesaplanan brüt: <span id=\"plan_brut\">—</span></div>"
        + "<div id=\"plan_hata\" role=\"alert\" style=\"display:none;margin:8px 0;padding:8px;border:1px solid #e53935;border-radius:4px;color:#ff8a80;\"></div>"
        + "<div id=\"plan_uyarilar\" style=\"margin:8px 0;color:#ffcc80;\"></div>"
        + "<div id=\"plan_kilit_kutu\" style=\"display:none;margin:8px 0;padding:8px;border:1px solid #e53935;color:#ff8a80;\"></div>"
        + "<label id=\"plan_kilit_etiket\" style=\"display:none;\"><input id=\"plan_kilit_onay\" type=\"checkbox\"> Bu aylar değişmeyecek, anladım</label>"
        + "<div id=\"plan_onizleme\" style=\"margin-top:8px;overflow:auto;\"></div>"
        + "<div style=\"margin-top:12px;display:flex;gap:8px;\">"
        + "<button type=\"button\" id=\"plan_onizle_btn\" style=\"" + D + "background:#00838f;color:#fff;\">Önizle</button>"
        + "<button type=\"button\" id=\"plan_kaydet\" style=\"" + D + "background:#2e7d32;color:#fff;\" disabled>Kaydet</button>"
        + "<button type=\"button\" id=\"plan_kapat\" style=\"" + D + "background:#455a64;color:#fff;\">Kapat</button>"
        + "</div>";
    kok.appendChild(kutu);
    document.body.appendChild(kok);
    document.getElementById("plan_kapat").onclick = planModalKapat;
    document.getElementById("plan_onizle_btn").onclick = planOnizlemeIste;
    document.getElementById("plan_kaydet").onclick = planKaydet;
    document.getElementById("plan_kilit_onay").onchange = planKaydetDurumu;
    document.getElementById("plan_odeme").onchange = function () {
        document.getElementById("plan_paylar").style.display = this.value === "karma" ? "" : "none";
    };
}

function planDegistirModalAc() {
    planModalHazirla();
    var modal = document.getElementById("plan_degistir_modal");
    if (!modal) return;
    var ay = new Date();
    var ayIso = ay.getFullYear() + "-" + String(ay.getMonth() + 1).padStart(2, "0");
    var gec = document.getElementById("plan_gecerlilik");
    if (gec && !gec.value) gec.value = ayIso;
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
    window.__planOnizlemeHazir = false;
    planHataGoster("");
    planKaydetDurumu();
    modal.style.display = "block";
}

function planModalKapat() {
    var modal = document.getElementById("plan_degistir_modal");
    if (modal) modal.style.display = "none";
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
    var onay = document.getElementById("plan_kilit_onay");
    var engel = !window.__planOnizlemeHazir || (kilit.length > 0 && !(onay && onay.checked));
    btn.disabled = !!engel;
    btn.style.opacity = engel ? "0.5" : "1";
    btn.style.cursor = engel ? "not-allowed" : "pointer";
}

function planHataGoster(mesaj) {
    var kutu = document.getElementById("plan_hata");
    if (!kutu) return;
    kutu.textContent = mesaj || "";
    kutu.style.display = mesaj ? "" : "none";
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
                window.__planOnizlemeHazir = false;
                planHataGoster(j.mesaj || ("Önizleme alınamadı: " + pack.kod));
                planKaydetDurumu();
                return;
            }
            var brut = document.getElementById("plan_brut");
            if (brut) brut.textContent = String(j.yeni_brut);
            window.__planKilit = j.kilitlenen_aylar || [];
            var kutu = document.getElementById("plan_kilit_kutu");
            var etiket = document.getElementById("plan_kilit_etiket");
            if (kutu) {
                kutu.textContent = "";
                if (window.__planKilit.length) {
                    kutu.style.display = "";
                    kutu.textContent = "Kilitli aylar değişmez: " + window.__planKilit.map(function (x) {
                        return x.ay + " (" + x.kaynak + " " + x.fatura_tutari + ")";
                    }).join(", ");
                } else kutu.style.display = "none";
            }
            if (etiket) etiket.style.display = window.__planKilit.length ? "" : "none";
            var uy = document.getElementById("plan_uyarilar");
            if (uy) {
                uy.textContent = (j.uyarilar || []).map(function (u) { return u.mesaj || u.ay || ""; }).join(" ");
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
    var btn = document.getElementById("plan_kaydet");
    if (btn && btn.disabled) return;
    planIstekGonder("/giris/api/musteri/" + encodeURIComponent(mid) + "/plan", govde)
        .then(function (pack) {
            if (!pack.ok || !pack.j || !pack.j.ok) {
                planHataGoster((pack.j && pack.j.mesaj) || ("Kaydedilemedi: " + pack.kod));
                return;
            }
            planKayitSonrasi(mid);
        })
        .catch(function () { planHataGoster("Kaydedilemedi: bağlantı hatası"); });
}

function planIptalGonder(mid, planId) {
    fetch("/giris/api/musteri/" + encodeURIComponent(mid) + "/plan/" + encodeURIComponent(planId) + "/iptal", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: "{}"
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (pack) {
            if (!pack.ok || !pack.j || !pack.j.ok) {
                alert((pack.j && pack.j.mesaj) || "İptal edilemedi");
                return;
            }
            planKayitSonrasi(mid);
        })
        .catch(function () { alert("İptal edilemedi"); });
}

function planKayitSonrasi(mid) {
    planModalKapat();
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
        planSecenekEklensin: planSecenekEklensin
    };
}
