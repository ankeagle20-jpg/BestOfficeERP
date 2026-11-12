/**
 * Ortak GİB oturum çıkışı — Finans ve Yeni Fatura ekranları.
 * Uç: POST /faturalar/api/gib-cikis. Onay penceresi yok (Finans ile aynı).
 */
(function (global) {
  "use strict";

  function _busy(btn, on, busyText) {
    if (!btn) return;
    if (on) {
      if (btn.dataset.gibCikisBusy === "1") return;
      btn.dataset.gibCikisBusy = "1";
      btn.dataset.gibCikisPrevHtml = btn.innerHTML;
      btn.disabled = true;
      if (busyText) btn.textContent = busyText;
    } else {
      btn.disabled = false;
      btn.dataset.gibCikisBusy = "0";
      if (btn.dataset.gibCikisPrevHtml) {
        btn.innerHTML = btn.dataset.gibCikisPrevHtml;
        delete btn.dataset.gibCikisPrevHtml;
      }
    }
  }

  /**
   * @param {object|Event|undefined} optsOrEv
   *   opts.btnId (varsayılan: gib_cikis_btn)
   *   opts.statusId (varsayılan: gib_manual_status, yoksa yeni-gib-status)
   */
  function gibCikisYap(optsOrEv) {
    var opts = {};
    if (optsOrEv && typeof optsOrEv === "object" && !optsOrEv.target) {
      opts = optsOrEv;
    }
    var btnId = opts.btnId || "gib_cikis_btn";
    var btn = document.getElementById(btnId);
    if (optsOrEv && optsOrEv.currentTarget && optsOrEv.currentTarget.tagName === "BUTTON") {
      btn = optsOrEv.currentTarget;
    }
    if (btn && btn.dataset.gibCikisBusy === "1") return;

    var st =
      document.getElementById(opts.statusId || "gib_manual_status") ||
      document.getElementById("yeni-gib-status") ||
      document.getElementById("gib_cikis_status");

    if (typeof global.autoSetBusy === "function" && btn) {
      global.autoSetBusy(btn, true, "Çıkılıyor...");
    } else {
      _busy(btn, true, "Çıkılıyor...");
    }
    if (st) st.textContent = "GİB oturumu kapatılıyor...";

    fetch("/faturalar/api/gib-cikis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (j) {
        if (st) st.textContent = (j && j.mesaj) ? j.mesaj : "GİB çıkış isteği bitti.";
      })
      .catch(function (err) {
        if (st) {
          st.textContent =
            "GİB çıkış isteği başarısız: " +
            (err && err.message ? err.message : err);
        }
      })
      .finally(function () {
        if (typeof global.autoSetBusy === "function" && btn) {
          global.autoSetBusy(btn, false);
        } else {
          _busy(btn, false);
        }
      });
  }

  global.gibCikisYap = gibCikisYap;
})(typeof window !== "undefined" ? window : this);
