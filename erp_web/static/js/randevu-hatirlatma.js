/**
 * Randevu başlangıcından ~5 dk önce (yaklaşık pencere + periyodik kontrol) tek seferlik modal uyarı.
 * Oturum boyunca sessionStorage ile aynı randevu için tekrar göstermez.
 */
(function () {
  var STORAGE_KEY = "bestoffice_randevu_hatirlatilan_v1";
  var POLL_MS = 30000;
  /** Kalan süre (başlangıca kadar): bu saniye aralığındayken uyar (5 dk ± tolerans, kaçırılmaması için geniş). */
  var REMIND_SEC_MIN = 210;
  var REMIND_SEC_MAX = 330;

  function ymd(d) {
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return y + "-" + m + "-" + day;
  }

  function addDays(d, n) {
    var x = new Date(d.getTime());
    x.setDate(x.getDate() + n);
    return x;
  }

  function loadShown() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      var o = JSON.parse(raw);
      return o && typeof o === "object" ? o : {};
    } catch (e) {
      return {};
    }
  }

  function saveShown(map) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(map));
    } catch (e) {}
  }

  function reminderKey(r) {
    return String(r.id) + "|" + (r.baslangic_zamani || "");
  }

  function skipDurum(d) {
    var d0 = (d || "").trim();
    return d0 === "İptal" || d0 === "Tamamlandı";
  }

  function formatClock(iso) {
    if (!iso) return "—";
    var t = new Date(iso);
    if (isNaN(t.getTime())) return iso;
    return t.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });
  }

  function ensureModal() {
    var id = "randevu-hatirlatma-modal";
    var el = document.getElementById(id);
    if (el) return el;
    el = document.createElement("div");
    el.id = id;
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.style.cssText =
      "display:none;position:fixed;inset:0;z-index:10050;background:rgba(0,0,0,.55);align-items:center;justify-content:center;padding:16px;";
    el.innerHTML =
      '<div class="rh-card" style="max-width:420px;width:100%;background:#0f2537;border:1px solid #1e3a50;border-radius:10px;box-shadow:0 12px 40px rgba(0,0,0,.45);overflow:hidden;">' +
      '<div style="padding:14px 16px;border-bottom:1px solid #1e3a50;display:flex;align-items:center;gap:10px;">' +
      '<span style="font-size:22px;color:#4fc3f7;"><i class="fa fa-bell"></i></span>' +
      '<div style="flex:1;">' +
      '<div style="font-weight:700;color:#4fc3f7;font-size:15px;">Randevu hatırlatma</div>' +
      '<div style="font-size:12px;color:#90a4ae;margin-top:2px;">Yaklaşık 5 dakika sonra başlayacak</div>' +
      "</div></div>" +
      '<div id="rh-body" style="padding:16px;color:#e0f7fa;font-size:14px;line-height:1.5;"></div>' +
      '<div style="padding:12px 16px 16px;text-align:right;">' +
      '<button type="button" id="rh-ok" style="background:#1565c0;color:#fff;border:none;padding:8px 18px;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600;">Tamam</button>' +
      "</div></div>";
    document.body.appendChild(el);
    el.addEventListener("click", function (e) {
      if (e.target === el) hideModal();
    });
    document.getElementById("rh-ok").addEventListener("click", hideModal);
    return el;
  }

  function hideModal() {
    var el = document.getElementById("randevu-hatirlatma-modal");
    if (el) el.style.display = "none";
  }

  function showModal(r) {
    var root = ensureModal();
    var body = document.getElementById("rh-body");
    var lines = [];
    if (r.musteri_adi) lines.push("<strong>Müşteri:</strong> " + escapeHtml(r.musteri_adi));
    if (r.oda_adi) lines.push("<strong>Oda:</strong> " + escapeHtml(r.oda_adi));
    var saat =
      formatClock(r.baslangic_zamani) + " – " + formatClock(r.bitis_zamani);
    lines.push("<strong>Saat:</strong> " + escapeHtml(saat));
    if (r.notlar) lines.push("<strong>Not:</strong> " + escapeHtml(String(r.notlar).slice(0, 280)));
    body.innerHTML = lines.join("<br>");
    root.style.display = "flex";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fetchList() {
    var today = new Date();
    var bas = ymd(today);
    var bitis = ymd(addDays(today, 1));
    var url = "/randevu/api/list?bas=" + encodeURIComponent(bas) + "&bitis=" + encodeURIComponent(bitis);
    return fetch(url, { credentials: "same-origin" }).then(function (res) {
      if (!res.ok) return [];
      return res.json();
    });
  }

  function tick() {
    fetchList()
      .then(function (rows) {
        if (!Array.isArray(rows)) return;
        var now = Date.now();
        var shown = loadShown();
        for (var i = 0; i < rows.length; i++) {
          var r = rows[i];
          if (!r || !r.baslangic_zamani || skipDurum(r.durum)) continue;
          var start = new Date(r.baslangic_zamani).getTime();
          if (isNaN(start)) continue;
          var remainSec = (start - now) / 1000;
          if (remainSec < REMIND_SEC_MIN || remainSec > REMIND_SEC_MAX) continue;
          var k = reminderKey(r);
          if (shown[k]) continue;
          shown[k] = 1;
          saveShown(shown);
          showModal(r);
          break;
        }
      })
      .catch(function () {});
  }

  function start() {
    tick();
    setInterval(tick, POLL_MS);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) tick();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
