"""
GirisTab — 2 sütunlu layout, takvim picker, tel validasyon, python-docx sözleşme
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import shutil, os, re, calendar
from kira_senaryo import KiraSenaryoFrame
from pathlib import Path
from datetime import date, datetime

EKSIK_FG  = "#ff8a65"
TAMAM_FG  = "#69f0ae"
NORMAL_FG = "#e0f7fa"

# ─────────────────────────────────────────────────────────────────────────────
# Takvim Picker
# ─────────────────────────────────────────────────────────────────────────────
class DatePicker(ttk.Frame):
    def __init__(self, master, textvariable=None, **kw):
        super().__init__(master, **kw)
        self.var = textvariable or tk.StringVar()
        ttk.Entry(self, textvariable=self.var, width=11).pack(side=tk.LEFT)
        ttk.Button(self, text="📅", width=3, command=self._ac).pack(side=tk.LEFT, padx=(2,0))
        self._popup = None

    def get(self): return self.var.get()
    def set(self, v): self.var.set(v)

    def _ac(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy(); return
        try:
            d = datetime.strptime(self.var.get(), "%d.%m.%Y")
            self._y, self._m = d.year, d.month
        except:
            t = date.today(); self._y, self._m = t.year, t.month
        p = tk.Toplevel(self)
        p.wm_overrideredirect(True); p.attributes("-topmost", True)
        p.configure(bg="#1e2a3a"); self._popup = p
        p.geometry(f"+{self.winfo_rootx()}+{self.winfo_rooty()+self.winfo_height()+2}")
        self._draw(p); p.focus_set()

    def _draw(self, p):
        for w in p.winfo_children(): w.destroy()
        AY = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
              "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
        nav = tk.Frame(p, bg="#0f2537"); nav.pack(fill=tk.X, pady=2, padx=4)
        tk.Button(nav, text="◀", bg="#0f2537", fg="#4fc3f7", relief="flat",
                  command=lambda: self._shift(-1, p)).pack(side=tk.LEFT)
        tk.Button(nav, text="▶", bg="#0f2537", fg="#4fc3f7", relief="flat",
                  command=lambda: self._shift(1, p)).pack(side=tk.RIGHT)
        tk.Label(nav, text=f"{AY[self._m-1]} {self._y}", bg="#0f2537", fg="white",
                 font=("Segoe UI",9,"bold")).pack(side=tk.LEFT, expand=True)
        gf = tk.Frame(p, bg="#1e2a3a"); gf.pack(padx=4)
        for g in ["Pzt","Sal","Çar","Per","Cum","Cmt","Paz"]:
            tk.Label(gf, text=g, bg="#1e2a3a", fg="#90a4ae", width=3,
                     font=("Segoe UI",7)).pack(side=tk.LEFT)
        try: sg,sa,sy=[int(x) for x in self.var.get().split(".")]
        except: sg=sa=sy=-1
        today=date.today()
        tf=tk.Frame(p,bg="#1e2a3a"); tf.pack(padx=4,pady=(0,4))
        for hf in calendar.monthcalendar(self._y, self._m):
            row=tk.Frame(tf,bg="#1e2a3a"); row.pack()
            for g in hf:
                if g==0: tk.Label(row,text="  ",bg="#1e2a3a",width=3).pack(side=tk.LEFT)
                else:
                    its=(g==today.day and self._m==today.month and self._y==today.year)
                    isc=(g==sg and self._m==sa and self._y==sy)
                    bg="#00bcd4" if isc else ("#0f2537" if its else "#1e2a3a")
                    fg="white" if (isc or its) else "#e0f7fa"
                    tk.Button(row,text=str(g),width=3,bg=bg,fg=fg,relief="flat",
                              font=("Segoe UI",8),
                              command=lambda n=g:self._pick(n,p)).pack(side=tk.LEFT,pady=1)

    def _shift(self, d, p):
        self._m+=d
        if self._m>12: self._m=1; self._y+=1
        if self._m<1:  self._m=12; self._y-=1
        self._draw(p)

    def _pick(self, g, p):
        self.var.set(f"{g:02d}.{self._m:02d}.{self._y}"); p.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Telefon Entry (+90 sabit)
# ─────────────────────────────────────────────────────────────────────────────
class TelEntry(ttk.Frame):
    def __init__(self, master, textvariable=None, **kw):
        super().__init__(master, **kw)
        self.var = textvariable or tk.StringVar()
        ttk.Label(self, text="+90", foreground="#4fc3f7",
                  font=("Segoe UI",9,"bold")).pack(side=tk.LEFT)
        self._n = tk.StringVar()
        self._n.trace_add("write", self._sync)
        vc=(self.register(lambda p: bool(re.match(r"^\d{0,10}$",p))),"%P")
        ttk.Entry(self, textvariable=self._n, width=13,
                  validate="key", validatecommand=vc).pack(side=tk.LEFT, padx=(3,0))
        self.var.trace_add("write", self._dsync)

    def _sync(self,*_): self.var.set(f"+90{self._n.get()}" if self._n.get() else "")
    def _dsync(self,*_):
        v=re.sub(r"[^\d]","",self.var.get())
        if v.startswith("90"): v=v[2:]
        if v!=self._n.get(): self._n.set(v[:10])
    def get(self): return self.var.get()
    def set(self,v): self.var.set(v)


# ─────────────────────────────────────────────────────────────────────────────
# Validasyon
# ─────────────────────────────────────────────────────────────────────────────
def _vno(v): return "" if len(re.sub(r"\D","",v))==10 else "Vergi No 10 hane olmalı."
def _tcno(v): n=len(re.sub(r"\D","",v)); return "" if n in(11,12) else "Kimlik 11(TC) veya 12(Yabancı) hane olmalı."
def _tel(v): n=re.sub(r"\D","",v); n=n[2:] if n.startswith("90") else n; return "" if len(n)==10 else "Telefon 10 hane olmalı."


# ─────────────────────────────────────────────────────────────────────────────
# Scroll yardımcısı — herhangi bir frame'e ekle
# ─────────────────────────────────────────────────────────────────────────────
def _bind_scroll(widget, canvas):
    """Widget ve tüm alt widgetlarına mousewheel scroll bağla."""
    def _scroll(e):
        try: canvas.yview_scroll(int(-e.delta/120),"units")
        except: pass
    def _bind_all(w):
        w.bind("<MouseWheel>", _scroll, add="+")
        for child in w.winfo_children():
            _bind_all(child)
    _bind_all(widget)


# ─────────────────────────────────────────────────────────────────────────────
# Ana Tab
# ─────────────────────────────────────────────────────────────────────────────
class GirisTab(ttk.Frame):
    HIZMET=["Sanal Ofis","Hazır Ofis","Paylaşımlı Masa"]
    SUBE=["Merkez","Şube"]

    def __init__(self, master):
        super().__init__(master, padding=4)
        self._kyc_id=None; self._musteri_id=None
        self._belgeler=[]; self._alan_vars={}; self._alan_config={}
        self._build(); self._alan_conf_yukle()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build(self):
        self.columnconfigure(0, weight=1); self.rowconfigure(1, weight=1)
        # Üst bar
        ust=ttk.Frame(self); ust.grid(row=0,column=0,sticky="ew",pady=(0,4))
        ttk.Label(ust,text="Giriş / Müşteri Kaydı",font=("Segoe UI",12,"bold")).pack(side=tk.LEFT)
        ttk.Button(ust,text="⚙ Alan Ayarları",command=lambda:AlanAyarlariPopup(self)).pack(side=tk.RIGHT,padx=4)
        ttk.Button(ust,text="📋 Kayıtlar",command=lambda:KYCListePopup(self)).pack(side=tk.RIGHT,padx=4)
        # Notebook
        nb=ttk.Notebook(self); nb.grid(row=1,column=0,sticky="nsew")
        f=ttk.Frame(nb); nb.add(f,text="  Müşteri Ekle  ")
        self._build_ekle(f)
        # Kira Senaryo alt sekmesi
        f_kira=ttk.Frame(nb); nb.add(f_kira,text="  📈 Kira Senaryo  ")
        f_kira.columnconfigure(0,weight=1); f_kira.rowconfigure(0,weight=1)
        KiraSenaryoFrame(f_kira).grid(row=0,column=0,sticky="nsew")

    def _build_ekle(self, parent):
        parent.columnconfigure(0,weight=1); parent.rowconfigure(0,weight=1)
        # Scroll canvas
        vsb=ttk.Scrollbar(parent,orient="vertical")
        self._canvas=tk.Canvas(parent,highlightthickness=0,yscrollcommand=vsb.set)
        vsb.configure(command=self._canvas.yview)
        self._canvas.grid(row=0,column=0,sticky="nsew"); vsb.grid(row=0,column=1,sticky="ns")
        inner=ttk.Frame(self._canvas); self._inner=inner
        wid=self._canvas.create_window((0,0),window=inner,anchor="nw"); self._wid=wid
        inner.bind("<Configure>",lambda e:self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",lambda e:self._canvas.itemconfig(wid,width=e.width))
        # Global mousewheel
        self._canvas.bind("<MouseWheel>",lambda e:self._canvas.yview_scroll(int(-e.delta/120),"units"))
        self._build_inner(inner)
        # Scroll bağla (inner yüklenince)
        inner.after(300, lambda: _bind_scroll(inner, self._canvas))

    def _build_inner(self, p):
        # ── 2 Sütunlu Layout ──
        p.columnconfigure(0, weight=1); p.columnconfigure(1, weight=1)

        # Sol: Müşteri bağla + form alanları
        sol=ttk.Frame(p); sol.grid(row=0,column=0,sticky="nsew",padx=(4,2),pady=4)

        sf=ttk.LabelFrame(sol,text="Mevcut Müşteriye Bağla (opsiyonel)",padding=5)
        sf.pack(fill=tk.X,pady=(0,4))
        f0=ttk.Frame(sf); f0.pack(fill=tk.X)
        ttk.Label(f0,text="Ara:").pack(side=tk.LEFT)
        self.v_ara=tk.StringVar(); self.v_ara.trace_add("write",lambda*_:self._filtrele())
        ttk.Entry(f0,textvariable=self.v_ara,width=18).pack(side=tk.LEFT,padx=4)
        self.lbl_sec=ttk.Label(f0,text="Seçilmedi",foreground="#aaaaaa",font=("Segoe UI",8))
        self.lbl_sec.pack(side=tk.LEFT,padx=4)
        ttk.Button(f0,text="✕",width=2,command=self._temizle_sec).pack(side=tk.LEFT)
        self.m_list=tk.Listbox(sf,height=3,font=("Segoe UI",8),selectmode=tk.SINGLE)
        self.m_list.pack(fill=tk.X,pady=(3,0))
        self.m_list.bind("<<ListboxSelect>>",self._sec_m)
        self._tum_m=[]; self._fil_m=[]; self._yukle_m()

        # Progress
        pf=ttk.Frame(sol); pf.pack(fill=tk.X,pady=(0,4))
        ttk.Label(pf,text="Tamamlanma:").pack(side=tk.LEFT)
        self.prog=ttk.Progressbar(pf,length=200,mode="determinate",maximum=100)
        self.prog.pack(side=tk.LEFT,padx=4)
        self.lbl_prog=ttk.Label(pf,text="0%",font=("Segoe UI",9,"bold"))
        self.lbl_prog.pack(side=tk.LEFT)

        # Form alanları (sol sütun)
        self.form_frame=ttk.Frame(sol); self.form_frame.pack(fill=tk.BOTH,expand=True)
        self._form_olustur()

        # Sağ: Evraklar + Notlar + Butonlar
        sag=ttk.Frame(p); sag.grid(row=0,column=1,sticky="nsew",padx=(2,4),pady=4)

        # Evraklar
        ef=ttk.LabelFrame(sag,text="Evrak ve Belgeler",padding=6)
        ef.pack(fill=tk.X,pady=(0,6))
        self.evrak_vars={}
        evraklar=[("evrak_imza_sirküleri","İmza Sirküleri"),
                  ("evrak_vergi_levhasi","Vergi Levhası"),
                  ("evrak_ticaret_sicil","Ticaret Sicil"),
                  ("evrak_faaliyet_belgesi","Faaliyet Belgesi"),
                  ("evrak_kimlik_fotokopi","Kimlik Fotokopisi"),
                  ("evrak_ikametgah","İkametgah"),
                  ("evrak_kase","Kaşe Örneği")]
        for i,(kod,etiket) in enumerate(evraklar):
            var=tk.IntVar(); self.evrak_vars[kod]=var
            ttk.Checkbutton(ef,text=f"{'✅' if var.get() else '☐'} {etiket}",
                             variable=var,command=self._ilerleme).grid(
                row=i,column=0,sticky="w",padx=4,pady=1)

        # Dosya
        df=ttk.LabelFrame(sag,text="Yüklenen Dosyalar",padding=4)
        df.pack(fill=tk.X,pady=(0,6))
        self.bel_list=tk.Listbox(df,height=5,font=("Segoe UI",7))
        self.bel_list.pack(fill=tk.X,pady=(0,4))
        bf2=ttk.Frame(df); bf2.pack(fill=tk.X)
        ttk.Button(bf2,text="+ Dosya Yükle",command=self._dosya_yukle).pack(side=tk.LEFT,padx=2)
        ttk.Button(bf2,text="✕ Kaldır",command=self._dosya_kaldir).pack(side=tk.LEFT,padx=2)

        # Notlar
        nf=ttk.LabelFrame(sag,text="Notlar",padding=4)
        nf.pack(fill=tk.X,pady=(0,6))
        self.txt_not=tk.Text(nf,height=4,font=("Segoe UI",9),wrap="word")
        self.txt_not.pack(fill=tk.X)

        # Sözleşme özeti
        oz=ttk.LabelFrame(sag,text="Sözleşme Durumu",padding=4)
        oz.pack(fill=tk.X,pady=(0,6))
        self.lbl_soz=ttk.Label(oz,text="Henüz sözleşme oluşturulmadı.",
                                font=("Segoe UI",8),foreground="#aaaaaa",wraplength=280)
        self.lbl_soz.pack(anchor="w")

        # Butonlar (sağda)
        bf=ttk.Frame(sag); bf.pack(fill=tk.X,pady=4)
        ttk.Button(bf,text="💾 Kaydet",command=self._kaydet).pack(fill=tk.X,pady=2,ipady=5)
        ttk.Button(bf,text="📄 Sözleşme Oluştur",command=self._sozlesme).pack(fill=tk.X,pady=2,ipady=5)
        ttk.Button(bf,text="🖨 Yazdır",command=self._yazdir).pack(fill=tk.X,pady=2,ipady=5)
        ttk.Button(bf,text="🗑 Temizle",command=self._form_temizle).pack(fill=tk.X,pady=(8,2))

        self.lbl_durum=ttk.Label(sag,text="",font=("Segoe UI",8),
                                  foreground=TAMAM_FG,wraplength=280)
        self.lbl_durum.pack(anchor="w")

    # ── Form Oluştur (sol sütun, kompakt) ────────────────────────────────────
    def _form_olustur(self):
        for w in self.form_frame.winfo_children(): w.destroy()
        self._alan_vars={}
        from database import get_giris_alanlar
        alanlar=get_giris_alanlar()
        kat_s=["Şirket Bilgileri","Adres","Yetkili","Ortaklar","Sözleşme"]
        gruplar={}
        for a in alanlar: gruplar.setdefault(a["kategori"],[]).append(a)

        for kat in kat_s:
            if kat not in gruplar: continue
            lf=ttk.LabelFrame(self.form_frame,text=kat,padding=4)
            lf.pack(fill=tk.X,pady=3)
            for a in gruplar[kat]:
                kod=a["alan_kodu"]; self._alan_config[kod]=a
                f=ttk.Frame(lf); f.pack(fill=tk.X,pady=1)
                star=" *" if a["zorunlu"] else ""
                fg=EKSIK_FG if a["zorunlu"] else NORMAL_FG
                ttk.Label(f,text=a["alan_adi"]+star,width=20,anchor="e",
                          font=("Segoe UI",8),foreground=fg).pack(side=tk.LEFT)
                var=tk.StringVar(); self._alan_vars[kod]=var
                var.trace_add("write",lambda*_:self._ilerleme())

                if "tarih" in kod or "bitis" in kod:
                    dp=DatePicker(f,textvariable=var); dp.pack(side=tk.LEFT,padx=4)
                    var.set("" if "bitis" in kod else date.today().strftime("%d.%m.%Y"))
                elif kod in ("yetkili_tel","yetkili_tel2"):
                    TelEntry(f,textvariable=var).pack(side=tk.LEFT,padx=4)
                elif kod=="hizmet_turu":
                    cb=ttk.Combobox(f,textvariable=var,values=self.HIZMET,state="readonly",width=18)
                    cb.set("Sanal Ofis"); cb.pack(side=tk.LEFT,padx=4)
                elif kod=="sube_merkez":
                    cb=ttk.Combobox(f,textvariable=var,values=self.SUBE,state="readonly",width=10)
                    cb.set("Merkez"); cb.pack(side=tk.LEFT,padx=4)
                elif kod=="vergi_no":
                    vc=(self.register(lambda p:bool(re.match(r"^\d{0,10}$",p))),"%P")
                    ttk.Entry(f,textvariable=var,width=12,validate="key",validatecommand=vc).pack(side=tk.LEFT,padx=4)
                elif kod=="yetkili_tcno":
                    vc=(self.register(lambda p:bool(re.match(r"^\d{0,12}$",p))),"%P")
                    ttk.Entry(f,textvariable=var,width=14,validate="key",validatecommand=vc).pack(side=tk.LEFT,padx=4)
                    ttk.Label(f,text="11=TC/12=Yab",font=("Segoe UI",7),foreground="#888888").pack(side=tk.LEFT)
                elif kod in ("ortak1_pay","ortak2_pay"):
                    vc=(self.register(lambda p:bool(re.match(r"^\d{0,3}$",p))),"%P")
                    ttk.Entry(f,textvariable=var,width=5,validate="key",validatecommand=vc).pack(side=tk.LEFT,padx=4)
                    ttk.Label(f,text="%").pack(side=tk.LEFT)
                elif kod in ("aylik_kira","yillik_kira"):
                    vc=(self.register(lambda p:bool(re.match(r"^\d*\.?\d{0,2}$",p))),"%P")
                    ttk.Entry(f,textvariable=var,width=12,validate="key",validatecommand=vc).pack(side=tk.LEFT,padx=4)
                    ttk.Label(f,text="TL").pack(side=tk.LEFT)
                else:
                    ttk.Entry(f,textvariable=var,width=28).pack(side=tk.LEFT,padx=4)

        # Scroll bağla
        self._inner.after(200, lambda: _bind_scroll(self._inner, self._canvas))

    # ── Veri ─────────────────────────────────────────────────────────────────
    def _alan_conf_yukle(self):
        from database import get_giris_alanlar
        for a in get_giris_alanlar(): self._alan_config[a["alan_kodu"]]=a

    def _yukle_m(self):
        from database import fetch_all
        rows=fetch_all("SELECT id,name FROM customers ORDER BY name")
        self._tum_m=[dict(r) for r in rows]; self._filtrele()

    def _filtrele(self):
        self.m_list.delete(0,tk.END); ara=self.v_ara.get().upper().strip()
        self._fil_m=[]
        for m in self._tum_m:
            if not ara or ara in (m["name"] or "").upper():
                self.m_list.insert(tk.END,m["name"]); self._fil_m.append(m)

    def _sec_m(self,_=None):
        sel=self.m_list.curselection()
        if not sel: return
        m=self._fil_m[sel[0]]; self._musteri_id=m["id"]
        self.lbl_sec.config(text=f"✓ {m['name']}",foreground=TAMAM_FG)
        from database import kyc_getir, kyc_belgeler_getir
        kyc=kyc_getir(self._musteri_id)
        if kyc:
            self._kyc_id=kyc.get("id")
            for k,v in self._alan_vars.items(): v.set(str(kyc.get(k,"") or ""))
            for k,v in self.evrak_vars.items(): v.set(int(kyc.get(k,0) or 0))
            self.txt_not.delete("1.0",tk.END)
            if kyc.get("notlar"): self.txt_not.insert("1.0",kyc["notlar"])
            if self._kyc_id:
                self.bel_list.delete(0,tk.END); self._belgeler=[]
                for b in kyc_belgeler_getir(self._kyc_id):
                    self.bel_list.insert(tk.END,f"{b['belge_tipi']} — {b['dosya_adi']}")
                    self._belgeler.append((b["dosya_yolu"],b["belge_tipi"]))
        self._ilerleme()

    def _temizle_sec(self):
        self._musteri_id=None; self._kyc_id=None
        self.lbl_sec.config(text="Seçilmedi",foreground="#aaaaaa"); self._form_temizle()

    def _ilerleme(self,*_):
        z=[k for k,c in self._alan_config.items() if c.get("zorunlu")]
        if not z: return
        d=sum(1 for k in z if self._alan_vars.get(k) and self._alan_vars[k].get().strip())
        y=int(d/len(z)*100); self.prog["value"]=y
        r=TAMAM_FG if y==100 else (EKSIK_FG if y<50 else "#fff176")
        self.lbl_prog.config(text=f"%{y}",foreground=r)

    def _validasyon(self):
        hat=[]
        for k,fn in[("vergi_no",_vno),("yetkili_tcno",_tcno),
                    ("yetkili_tel",_tel),("yetkili_tel2",_tel)]:
            v=self._alan_vars.get(k)
            if v and v.get().strip():
                h=fn(v.get())
                if h: hat.append(h)
        return hat

    def _zorunlu(self):
        return [c["alan_adi"] for k,c in self._alan_config.items()
                if c.get("zorunlu") and not(self._alan_vars.get(k) and self._alan_vars[k].get().strip())]

    def _veri(self):
        d={k:v.get().strip() for k,v in self._alan_vars.items()}
        d.update({k:v.get() for k,v in self.evrak_vars.items()})
        d["notlar"]=self.txt_not.get("1.0",tk.END).strip()
        d["musteri_id"]=self._musteri_id; return d

    # ── Kaydet ───────────────────────────────────────────────────────────────
    def _kaydet(self):
        if e:=self._zorunlu():
            messagebox.showwarning("Eksik","Zorunlu alanlar:\n\n• "+"\n• ".join(e[:8]),parent=self); return
        if h:=self._validasyon():
            messagebox.showerror("Format","\n".join(h),parent=self); return
        from database import kyc_kaydet, kyc_belge_ekle
        try:
            kid=kyc_kaydet(self._veri()); self._kyc_id=kid
            for y,t in self._belgeler:
                if os.path.exists(y): kyc_belge_ekle(kid,y,t)
            self.lbl_durum.config(text=f"✓ Kaydedildi (KYC:{kid})",foreground=TAMAM_FG)
            messagebox.showinfo("OK","Başarıyla kaydedildi!",parent=self)
        except Exception as ex:
            import traceback; traceback.print_exc()
            messagebox.showerror("Hata",str(ex),parent=self)

    # ── Sözleşme ─────────────────────────────────────────────────────────────
    def _sozlesme(self):
        if e:=self._zorunlu():
            messagebox.showwarning("Eksik","Zorunlu alanlar:\n\n• "+"\n• ".join(e[:8]),parent=self); return
        if h:=self._validasyon():
            messagebox.showerror("Format","\n".join(h),parent=self); return
        from database import sozlesme_no_uret, kyc_kaydet, sozlesme_kaydet
        data=self._veri()
        kid=kyc_kaydet(data); self._kyc_id=kid
        no=sozlesme_no_uret(); data["sozlesme_no"]=no
        unvan=(data.get("sirket_unvani") or "Musteri").replace(" ","_")[:30]
        yol=filedialog.asksaveasfilename(
            title="Sözleşmeyi Kaydet",defaultextension=".docx",
            initialfile=f"Sozlesme_{no}_{unvan}.docx",
            filetypes=[("Word Belgesi","*.docx"),("Tüm","*.*")])
        if not yol: return
        try:
            # sozlesme_uret_py.py ile üret (ERP klasöründe olmalı)
            import sys
            erp_dir=str(Path(__file__).parent)
            if erp_dir not in sys.path: sys.path.insert(0,erp_dir)
            from sozlesme_uret_py import sozlesme_olustur
            sozlesme_olustur(data,yol)
            sozlesme_kaydet(no,kid,self._musteri_id or 0,
                            data.get("sirket_unvani",""),data.get("hizmet_turu",""),yol)
            self.lbl_durum.config(text=f"✓ Sözleşme: {no}",foreground=TAMAM_FG)
            self.lbl_soz.config(text=f"Son sözleşme: {no}\n{Path(yol).name}",foreground=TAMAM_FG)
            if messagebox.askyesno("Hazır",f"Sözleşme No: {no}\n\nŞimdi açılsın mı?",parent=self):
                os.startfile(yol) if os.name=="nt" else __import__("subprocess").Popen(["xdg-open",yol])
        except Exception as ex:
            import traceback; traceback.print_exc()
            messagebox.showerror("Hata",str(ex),parent=self)

    def _yazdir(self):
        if not self._kyc_id:
            messagebox.showwarning("Uyarı","Önce kaydedin!",parent=self); return
        from database import fetch_all
        rows=fetch_all("SELECT dosya_yolu FROM sozlesmeler WHERE kyc_id=? ORDER BY id DESC LIMIT 1",(self._kyc_id,))
        if not rows:
            messagebox.showwarning("Uyarı","Sözleşme oluşturulmamış.",parent=self); return
        yol=rows[0]["dosya_yolu"]
        if not Path(yol).exists():
            messagebox.showerror("Hata",f"Dosya yok:\n{yol}",parent=self); return
        try: os.startfile(yol,"print") if os.name=="nt" else __import__("subprocess").Popen(["lp",yol])
        except Exception as ex: messagebox.showerror("Hata",str(ex),parent=self)

    # ── Dosya Yükleme ─────────────────────────────────────────────────────────
    def _dosya_yukle(self):
        ds=filedialog.askopenfilenames(
            title="Evrak Seç",
            filetypes=[("Desteklenen","*.pdf *.jpg *.jpeg *.png *.docx"),("Tüm","*.*")])
        if not ds: return
        tip=_tip_sec(self)
        from database import DB_PATH
        kl=Path(DB_PATH).parent/"KYCBelgeler"/(str(self._musteri_id) if self._musteri_id else "yeni")
        kl.mkdir(parents=True,exist_ok=True)
        for d in ds:
            p=Path(d); h=kl/p.name; i=1
            while h.exists(): h=kl/f"{p.stem}_{i}{p.suffix}"; i+=1
            shutil.copy2(d,h); self._belgeler.append((str(h),tip))
            self.bel_list.insert(tk.END,f"{tip} — {p.name}")

    def _dosya_kaldir(self):
        sel=self.bel_list.curselection()
        if not sel: return
        i=sel[0]; self.bel_list.delete(i)
        if 0<=i<len(self._belgeler): self._belgeler.pop(i)

    def _form_temizle(self):
        self._kyc_id=None; today=date.today().strftime("%d.%m.%Y")
        for k,v in self._alan_vars.items():
            v.set("" if "bitis" in k else (today if "tarih" in k else ""))
        for v in self.evrak_vars.values(): v.set(0)
        self.txt_not.delete("1.0",tk.END); self.bel_list.delete(0,tk.END)
        self._belgeler.clear(); self.prog["value"]=0; self.lbl_prog.config(text="0%")
        self.lbl_durum.config(text=""); self.lbl_soz.config(text="Henüz sözleşme oluşturulmadı.",foreground="#aaaaaa")


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcılar
# ─────────────────────────────────────────────────────────────────────────────
def _tip_sec(parent):
    tipler=["İmza Sirküleri","Vergi Levhası","Ticaret Sicil Gazetesi",
            "Faaliyet Belgesi","Kimlik Fotokopisi","İkametgah Belgesi","Kaşe Örneği","Diğer"]
    pop=tk.Toplevel(parent); pop.title("Belge Tipi"); pop.geometry("270x250")
    pop.transient(parent); pop.grab_set()
    ttk.Label(pop,text="Belge tipini seçin:").pack(pady=8)
    var=tk.StringVar(value=tipler[0])
    for t in tipler: ttk.Radiobutton(pop,text=t,variable=var,value=t).pack(anchor="w",padx=20)
    r=[tipler[0]]
    def ok(): r[0]=var.get(); pop.destroy()
    ttk.Button(pop,text="Tamam",command=ok).pack(pady=8); pop.wait_window(); return r[0]


class AlanAyarlariPopup(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master); self.title("Alan Ayarları"); self.geometry("440,450")
        self.geometry("440x450"); self.transient(master); self.grab_set()
        from database import get_giris_alanlar; alanlar=get_giris_alanlar()
        ttk.Label(self,text="Zorunlu alanları işaretleyin:",font=("Segoe UI",10,"bold")).pack(pady=8)
        vsb=ttk.Scrollbar(self,orient="vertical")
        canvas=tk.Canvas(self,highlightthickness=0,yscrollcommand=vsb.set)
        vsb.configure(command=canvas.yview)
        canvas.pack(side=tk.LEFT,fill=tk.BOTH,expand=True); vsb.pack(side=tk.LEFT,fill=tk.Y)
        inner=ttk.Frame(canvas); canvas.create_window((0,0),window=inner,anchor="nw")
        inner.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        self._vars={}; kat=None
        for a in alanlar:
            if a["kategori"]!=kat:
                kat=a["kategori"]
                ttk.Label(inner,text=kat,font=("Segoe UI",9,"bold"),foreground="#4fc3f7").pack(anchor="w",padx=8,pady=(8,2))
            v=tk.IntVar(value=a["zorunlu"]); self._vars[a["alan_kodu"]]=v
            ttk.Checkbutton(inner,text=a["alan_adi"],variable=v).pack(anchor="w",padx=20)
        bf=tk.Frame(self); bf.pack(fill=tk.X,pady=8)
        ttk.Button(bf,text="Kaydet",command=self._kyd).pack(side=tk.LEFT,padx=8)
        ttk.Button(bf,text="Kapat",command=self.destroy).pack(side=tk.LEFT)

    def _kyd(self):
        from database import giris_alan_zorunlu_guncelle
        for k,v in self._vars.items(): giris_alan_zorunlu_guncelle(k,v.get())
        messagebox.showinfo("OK","Ayarlar güncellendi!",parent=self); self.destroy()
        if hasattr(self.master,"_form_olustur"):
            self.master._alan_conf_yukle(); self.master._form_olustur()


class KYCListePopup(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master); self.title("KYC Kayıtları"); self.geometry("860x380"); self.transient(master)
        from database import fetch_all
        rows=fetch_all("SELECT k.id,k.sirket_unvani,k.vergi_no,k.hizmet_turu,k.yetkili_adsoyad,k.sozlesme_no,k.tamamlanma_yuzdesi,k.created_at FROM musteri_kyc k ORDER BY k.created_at DESC")
        cols=("id","sirket","vergi","hizmet","yetkili","soz_no","tam","tarih")
        tree=ttk.Treeview(self,columns=cols,show="headings",height=14)
        hdrs={"id":"ID","sirket":"Şirket","vergi":"VKN","hizmet":"Hizmet","yetkili":"Yetkili","soz_no":"Sözleşme No","tam":"Tamam%","tarih":"Tarih"}
        wdts={"id":40,"sirket":200,"vergi":90,"hizmet":100,"yetkili":140,"soz_no":110,"tam":65,"tarih":90}
        for c in cols: tree.heading(c,text=hdrs[c]); tree.column(c,width=wdts[c])
        vsb=ttk.Scrollbar(self,orient="vertical",command=tree.yview); tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT,fill=tk.BOTH,expand=True); vsb.pack(side=tk.LEFT,fill=tk.Y)
        tree.tag_configure("tam",foreground="#69f0ae"); tree.tag_configure("eksik",foreground="#ff8a65"); tree.tag_configure("orta",foreground="#fff176")
        for r in rows:
            y=r["tamamlanma_yuzdesi"] or 0; tag="tam" if y==100 else("orta" if y>=50 else"eksik")
            tree.insert("",tk.END,tags=(tag,),values=(r["id"],r["sirket_unvani"] or "",r["vergi_no"] or "",r["hizmet_turu"] or "",r["yetkili_adsoyad"] or "",r["sozlesme_no"] or "",f"%{y}",(r["created_at"] or "")[:10]))
        ttk.Button(self,text="Kapat",command=self.destroy).pack(pady=6)
