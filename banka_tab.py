"""
BankaTab — Ana notebook'a eklenecek banka modülü.
ui.py'a import edilecek.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path


class BankaTab(ttk.Frame):
    """Banka Hareketleri — Ekstre yükle, eşleştir, tahsilata aktar."""

    def __init__(self, master):
        super().__init__(master, padding=6)
        self._secili_hareket_id = None
        self._build_ui()
        self._hesapları_yukle()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Üst Araç Çubuğu ──
        ust = ttk.Frame(self)
        ust.grid(row=0, column=0, sticky="ew", pady=(0,4))

        ttk.Label(ust, text="Banka Hareketleri",
                  font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)

        ttk.Label(ust, text="  Hesap:").pack(side=tk.LEFT, padx=(12,2))
        self.v_hesap = tk.StringVar()
        self.hesap_cb = ttk.Combobox(ust, textvariable=self.v_hesap,
                                      width=22, state="readonly")
        self.hesap_cb.pack(side=tk.LEFT)
        self.hesap_cb.bind("<<ComboboxSelected>>", lambda _: self._listele())

        ttk.Label(ust, text="  Durum:").pack(side=tk.LEFT, padx=(8,2))
        self.v_durum = tk.StringVar(value="Tümü")
        ttk.Combobox(ust, textvariable=self.v_durum,
                     values=["Tümü","Eşleşmedi","Eşleşti"],
                     width=10, state="readonly").pack(side=tk.LEFT)

        ttk.Button(ust, text="Listele",
                   command=self._listele).pack(side=tk.LEFT, padx=(6,2))
        ttk.Button(ust, text="Ekstre Yükle",
                   command=self._ekstre_yukle,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(ust, text="Oto Eşleştir",
                   command=self._oto_eslestir).pack(side=tk.LEFT, padx=2)

        self.lbl_ozet = ttk.Label(ust, text="", font=("Segoe UI",8),
                                   foreground="#4fc3f7")
        self.lbl_ozet.pack(side=tk.RIGHT, padx=8)

        # ── Ana İçerik: Sol liste + Sağ panel ──
        body = ttk.Frame(self)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)

        # ── Sol: Hareket Listesi ──
        sol_f = ttk.LabelFrame(body, text="Banka Hareketleri", padding=4)
        sol_f.grid(row=0, column=0, sticky="nsew", padx=(0,4))
        sol_f.rowconfigure(0, weight=1)
        sol_f.columnconfigure(0, weight=1)

        cols = ("tarih","aciklama","gonderen","tutar","tip","durum","musteri")
        self.tree = ttk.Treeview(sol_f, columns=cols,
                                  show="headings", selectmode="browse",
                                  height=22)
        hdrs = {"tarih":"Tarih","aciklama":"Açıklama","gonderen":"Gönderen",
                "tutar":"Tutar (TL)","tip":"Tip","durum":"Durum","musteri":"Müşteri"}
        wdts = {"tarih":85,"aciklama":200,"gonderen":140,"tutar":90,
                "tip":65,"durum":80,"musteri":140}
        for c in cols:
            self.tree.heading(c, text=hdrs[c])
            self.tree.column(c, width=wdts[c],
                             anchor="e" if c=="tutar" else "w")

        vsb = ttk.Scrollbar(sol_f, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(sol_f, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.tree.tag_configure("eslesti",    foreground="#69f0ae")
        self.tree.tag_configure("eslesmedi",  foreground="#fff176")
        self.tree.tag_configure("borc",       foreground="#90a4ae")
        self.tree.tag_configure("oto",        foreground="#4fc3f7")

        self.tree.bind("<<TreeviewSelect>>", self._hareket_sec)

        # ── Sağ: Eşleştirme Paneli ──
        sag_f = ttk.LabelFrame(body, text="Eşleştirme",
                                padding=8, width=280)
        sag_f.grid(row=0, column=1, sticky="nsew")
        sag_f.pack_propagate(False)

        # Seçili hareket bilgisi
        ttk.Label(sag_f, text="Seçili Hareket:",
                  font=("Segoe UI",8,"bold")).pack(anchor="w")
        self.lbl_har_tarih  = ttk.Label(sag_f, text="-", foreground="#4fc3f7")
        self.lbl_har_tarih.pack(anchor="w")
        self.lbl_har_tutar  = ttk.Label(sag_f, text="-",
                                         font=("Segoe UI",11,"bold"),
                                         foreground="#69f0ae")
        self.lbl_har_tutar.pack(anchor="w", pady=(2,0))
        self.lbl_har_aciklama = ttk.Label(sag_f, text="", wraplength=250,
                                           font=("Segoe UI",7),
                                           foreground="#b0bec5")
        self.lbl_har_aciklama.pack(anchor="w")

        ttk.Separator(sag_f, orient="h").pack(fill=tk.X, pady=8)

        # Müşteri arama
        ttk.Label(sag_f, text="Müşteri Ara:",
                  font=("Segoe UI",8,"bold")).pack(anchor="w")
        self.v_musteri_ara = tk.StringVar()
        self.v_musteri_ara.trace_add("write", lambda *_: self._musteri_filtrele())
        arama_e = ttk.Entry(sag_f, textvariable=self.v_musteri_ara, width=28)
        arama_e.pack(fill=tk.X, pady=(2,4))

        self.musteri_listbox = tk.Listbox(sag_f, height=8,
                                           font=("Segoe UI",8),
                                           selectmode=tk.SINGLE,
                                           activestyle="underline")
        self.musteri_listbox.pack(fill=tk.X)

        # Eşleştir butonu
        ttk.Separator(sag_f, orient="h").pack(fill=tk.X, pady=6)
        ttk.Button(sag_f, text="✓ Eşleştir + Tahsilatı Aktar",
                   command=self._eslestir).pack(fill=tk.X, ipady=5)
        ttk.Button(sag_f, text="Eşleştirmeyi İptal Et",
                   command=self._eslestirme_iptal).pack(fill=tk.X, pady=(3,0))

        # Mevcut eşleşme bilgisi
        self.lbl_eslestirme = ttk.Label(sag_f, text="", wraplength=250,
                                         font=("Segoe UI",8),
                                         foreground="#4fc3f7")
        self.lbl_eslestirme.pack(anchor="w", pady=(6,0))

        # Müşteri listesini doldur
        self._tum_musteriler = []
        self._musteri_listele()

    # ── Veri ───────────────────────────────────────────────────────────────

    def _hesapları_yukle(self):
        from database import get_banka_hesaplar
        self._hesaplar = get_banka_hesaplar()
        degerler = [f"{h['banka_adi']} — {h['hesap_adi']}"
                    for h in self._hesaplar]
        self.hesap_cb["values"] = degerler
        if degerler:
            self.hesap_cb.current(0)
            self._listele()

    def _secili_hesap_id(self):
        idx = self.hesap_cb.current()
        if idx < 0 or idx >= len(self._hesaplar):
            return None
        return self._hesaplar[idx]["id"]

    def _listele(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        from database import get_banka_hareketler, banka_ozet
        hesap_id = self._secili_hesap_id()
        durum_v  = self.v_durum.get()
        durum    = "" if durum_v == "Tümü" else \
                   "eslesti" if durum_v == "Eşleşti" else "eslesmedi"

        rows = get_banka_hareketler(hesap_id, limit=500, durum=durum)

        for r in rows:
            tip_str = "Alacak" if r["tip"] == "alacak" else "Borç"
            dur_str = "Eşleşti" if r["eslestirme_durumu"] == "eslesti" else "—"
            tag = ("eslesti" if r["eslestirme_durumu"] == "eslesti"
                   else "borc" if r["tip"] == "borc"
                   else "eslesmedi")
            self.tree.insert("", tk.END, iid=str(r["id"]), tags=(tag,),
                values=(
                    r.get("tarih",""),
                    (r.get("aciklama","") or "")[:60],
                    (r.get("gonderen","") or "")[:30],
                    f"{float(r.get('tutar',0)):,.2f}",
                    tip_str, dur_str,
                    (r.get("musteri_adi","") or "")[:25],
                ))

        # Özet
        ozet = banka_ozet(hesap_id) or {}
        self.lbl_ozet.config(
            text=f"Toplam: {ozet.get('toplam') or 0}  |  "
                 f"Eşleşti: {ozet.get('eslesti') or 0}  |  "
                 f"Bekleyen: {float(ozet.get('eslesmemis_tutar') or 0):,.2f} TL")

    def _musteri_listele(self):
        from database import fetch_all
        rows = fetch_all(
            "SELECT id, name, phone, tax_number FROM customers ORDER BY name"
        )
        self._tum_musteriler = [dict(r) for r in rows]
        self._musteri_filtrele()

    def _musteri_filtrele(self):
        self.musteri_listbox.delete(0, tk.END)
        ara = self.v_musteri_ara.get().upper().strip()
        self._filtreli_musteriler = []
        for m in self._tum_musteriler:
            if not ara or ara in (m["name"] or "").upper():
                self.musteri_listbox.insert(tk.END, m["name"])
                self._filtreli_musteriler.append(m)

    def _hareket_sec(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        self._secili_hareket_id = int(sel[0])
        from database import get_banka_hareketler
        # Tek kaydı bul
        rows = get_banka_hareketler(limit=5000)
        r = next((x for x in rows if x["id"] == self._secili_hareket_id), None)
        if not r:
            return
        self.lbl_har_tarih.config(text=f"{r['tarih']}  |  {r.get('tip','').title()}")
        self.lbl_har_tutar.config(text=f"{float(r.get('tutar',0)):,.2f} TL")
        self.lbl_har_aciklama.config(text=r.get("aciklama","")[:120])

        if r.get("eslestirme_durumu") == "eslesti" and r.get("musteri_adi"):
            self.lbl_eslestirme.config(
                text=f"Eşleşti: {r['musteri_adi']}\n"
                     f"Tahsilat ID: {r.get('tahsilat_id','-')}",
                foreground="#69f0ae")
        else:
            self.lbl_eslestirme.config(text="Henüz eşleştirilmedi.",
                                        foreground="#fff176")

    # ── Ekstre Yükle ───────────────────────────────────────────────────────

    def _ekstre_yukle(self):
        hesap_id = self._secili_hesap_id()
        if not hesap_id:
            messagebox.showwarning("Uyarı","Önce hesap seçin!"); return

        idx    = self.hesap_cb.current()
        banka  = self._hesaplar[idx]["banka_adi"]

        dosya = filedialog.askopenfilename(
            title=f"{banka} Ekstre Dosyası Seç",
            filetypes=[
                ("Excel/CSV","*.xlsx *.xls *.csv"),
                ("Excel","*.xlsx *.xls"),
                ("CSV","*.csv"),
                ("Tüm dosyalar","*.*"),
            ]
        )
        if not dosya:
            return

        try:
            from banka_parser import PARSERS
            parser = PARSERS.get(banka)
            if not parser:
                # Bilinmeyen banka — genel parser dene
                from banka_parser import parse_akbank
                parser = parse_akbank
                messagebox.showinfo(
                    "Bilgi",
                    f"'{banka}' için özel format tanımlanmamış.\n"
                    "Genel format deneniyor. Sütun isimleri uymazsa\n"
                    "lütfen birini paylaşın, format ekleyelim."
                )

            hareketler = parser(dosya)

            if not hareketler:
                messagebox.showwarning("Sonuç Yok",
                    "Dosyadan hiç hareket çekilemedi.\n"
                    "Dosya formatını kontrol edin.")
                return

            from database import banka_hareket_ekle_bulk
            eklenen, atlanan = banka_hareket_ekle_bulk(
                hesap_id, hareketler, Path(dosya).name
            )

            # Otomatik eşleştirmeyi çalıştır
            self._oto_eslestir_sessiz(hesap_id, hareketler)

            self._listele()
            messagebox.showinfo(
                "Yükleme Tamamlandı",
                f"Dosya: {Path(dosya).name}\n"
                f"Yeni eklenen: {eklenen} hareket\n"
                f"Zaten vardı (atlandı): {atlanan}\n\n"
                f"Otomatik eşleştirme yapıldı!"
            )
        except Exception as e:
            import traceback; traceback.print_exc()
            messagebox.showerror("Hata", str(e))

    def _oto_eslestir_sessiz(self, hesap_id: int, hareketler: list):
        """Yükleme sonrası sessiz otomatik eşleştirme."""
        from database import fetch_all, banka_eslestir, get_banka_hareketler
        from banka_parser import otomatik_eslestir

        musteriler = [dict(r) for r in fetch_all(
            "SELECT id, name, phone, tax_number FROM customers"
        )]
        rows = get_banka_hareketler(hesap_id, limit=1000, durum="eslesmedi")
        sonuclar = otomatik_eslestir(rows, musteriler)

        for s in sonuclar:
            if s["musteri"] and s["skor"] >= 75:
                h = s["hareket"]
                try:
                    banka_eslestir(h["id"], s["musteri"]["id"], True)
                except:
                    pass

    def _oto_eslestir(self):
        """Manuel tetiklenen otomatik eşleştirme."""
        hesap_id = self._secili_hesap_id()
        from database import fetch_all, banka_eslestir, get_banka_hareketler
        from banka_parser import otomatik_eslestir

        musteriler = [dict(r) for r in fetch_all(
            "SELECT id, name, phone, tax_number FROM customers"
        )]
        rows = get_banka_hareketler(hesap_id, limit=1000, durum="eslesmedi")
        sonuclar = otomatik_eslestir(rows, musteriler)

        eslesti = 0
        for s in sonuclar:
            if s["musteri"] and s["skor"] >= 75:
                h = s["hareket"]
                try:
                    banka_eslestir(h["id"], s["musteri"]["id"], True)
                    eslesti += 1
                except:
                    pass

        self._listele()
        messagebox.showinfo(
            "Otomatik Eşleştirme",
            f"{eslesti} hareket otomatik eşleştirildi!\n\n"
            f"Kalanlar için sağ panelden manuel eşleştirme yapın."
        )

    # ── Manuel Eşleştirme ──────────────────────────────────────────────────

    def _eslestir(self):
        if not self._secili_hareket_id:
            messagebox.showwarning("Uyarı","Önce bir hareket seçin!"); return
        sel = self.musteri_listbox.curselection()
        if not sel:
            messagebox.showwarning("Uyarı","Müşteri listesinden seçim yapın!"); return

        m = self._filtreli_musteriler[sel[0]]

        if not messagebox.askyesno(
            "Eşleştir",
            f"Bu hareketi şu müşteriyle eşleştirip\n"
            f"tahsilatı otomatik oluşturulsun mu?\n\n"
            f"Müşteri: {m['name']}"
        ): return

        from database import banka_eslestir
        try:
            t_id = banka_eslestir(self._secili_hareket_id, m["id"], True)
            self._listele()
            self._secili_hareket_id = None
            self.lbl_eslestirme.config(
                text=f"Eşleştirildi: {m['name']}\nTahsilat ID: {t_id}",
                foreground="#69f0ae")
            messagebox.showinfo(
                "Eşleştirildi",
                f"Hareket '{m['name']}' ile eşleştirildi.\n"
                f"Tahsilat carisine eklendi!"
            )
        except Exception as e:
            messagebox.showerror("Hata", str(e))

    def _eslestirme_iptal(self):
        if not self._secili_hareket_id:
            messagebox.showwarning("Uyarı","Önce bir hareket seçin!"); return
        if not messagebox.askyesno(
            "İptal", "Eşleştirme ve oluşturulan tahsilat silinsin mi?"
        ): return
        from database import banka_eslestirme_iptal
        banka_eslestirme_iptal(self._secili_hareket_id)
        self._listele()
        self.lbl_eslestirme.config(text="Eşleştirme iptal edildi.",
                                    foreground="#fff176")
