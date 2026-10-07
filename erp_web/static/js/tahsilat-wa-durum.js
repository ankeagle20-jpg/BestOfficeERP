(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.tahsilatWaDurum = api;
})(typeof window !== "undefined" ? window : this, function () {
  function bos() {
    return {
      aktif: false,
      tahsilatId: 0,
      makbuzNo: "",
      musteriId: 0,
      tutar: "",
      tarih: "",
      aciklama: ""
    };
  }

  function pasif(s) {
    var once = s || bos();
    return {
      aktif: false,
      tahsilatId: 0,
      makbuzNo: "",
      musteriId: once.musteriId || 0,
      tutar: once.tutar || "",
      tarih: once.tarih || "",
      aciklama: once.aciklama || ""
    };
  }

  function kayit(onceki, data) {
    var id = parseInt(data && data.tahsilat_id, 10) || 0;
    if (!id) return pasif(onceki);
    return {
      aktif: true,
      tahsilatId: id,
      makbuzNo: String((data && data.makbuz_no) || ""),
      musteriId: parseInt(data && data.musteri_id, 10) || 0,
      tutar: String(data && data.tutar != null ? data.tutar : ""),
      tarih: String(data && data.tarih != null ? data.tarih : ""),
      aciklama: String(data && data.aciklama != null ? data.aciklama : "")
    };
  }

  function musteri(s, yeniId) {
    if (!s || !s.aktif) return pasif(s);
    var id = parseInt(yeniId, 10) || 0;
    if (id !== (parseInt(s.musteriId, 10) || 0)) return pasif(s);
    return s;
  }

  function alan(s, ad, deger) {
    if (!s || !s.aktif) return pasif(s);
    var beklenen = String(s[ad] == null ? "" : s[ad]);
    if (String(deger == null ? "" : deger) !== beklenen) return pasif(s);
    return s;
  }

  return { bos: bos, kayit: kayit, pasif: pasif, musteri: musteri, alan: alan };
});
