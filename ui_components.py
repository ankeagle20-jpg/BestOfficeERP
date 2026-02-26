import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import date
from typing import Optional, Dict, List

from database import (
    get_all_customers_with_rent_progression,
    get_all_products,
    get_all_invoices,
    get_tufe_for_year,
    save_tufe_for_year,
    import_tufe_from_excel,
    import_customers_from_excel,
    insert_customer,
    update_customer,
    delete_customer,
    insert_product,
    update_product,
    delete_product,
    insert_invoice,
    delete_invoice,
    calculate_rent_progression,
    get_months_list,
    initialize_database,
    get_connection,
    MONTHS_TR,
    save_rent_payment,
    get_rent_payments_for_customer,
    get_rent_payments_for_year,
    get_yearly_totals_for_customer,
)


# ============================================================================
# ANA PENCERE
# ============================================================================

class BaseWindow(tk.Tk):
    """ERP ana penceresi."""

    def __init__(self) -> None:
        super().__init__()
        self.title("BestOffice ERP - Kira Takip Sistemi")
        self.geometry("1600x860")
        self.minsize(1200, 700)

        self.style = ttk.Style(self)
        self._setup_theme()
        self._create_menu()

        # Üst başlık
        header = ttk.Frame(self, padding=(12, 8))
        header.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(
            header,
            text="BestOffice ERP — Kira Takip Sistemi",
            font=("Segoe UI", 16, "bold"),
        ).pack(side=tk.LEFT)

        # Sekmeler
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.customer_tab = CustomerTab(self.notebook)
        self.product_tab  = ProductTab(self.notebook)
        self.invoice_tab  = InvoiceTab(self.notebook)
        self.tufe_tab     = TufeTab(self.notebook)

        self.notebook.add(self.customer_tab, text="  Müşteriler  ")
        self.notebook.add(self.product_tab,  text="  Ürünler  ")
        self.notebook.add(self.invoice_tab,  text="  Faturalar  ")
        self.notebook.add(self.tufe_tab,     text="  TÜFE  ")

    def _setup_theme(self) -> None:
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")

        bg = "#f0f2f5"
        self.configure(bg=bg)
        self.style.configure("TFrame",        background=bg)
        self.style.configure("TLabelframe",   background=bg)
        self.style.configure("TLabelframe.Label", background=bg, font=("Segoe UI", 9, "bold"))
        self.style.configure("TLabel",        background=bg, font=("Segoe UI", 9))
        self.style.configure("TButton",       padding=(8, 4), font=("Segoe UI", 9))
        self.style.configure("TEntry",        padding=3)
        self.style.configure("TNotebook",     background=bg)
        self.style.configure("TNotebook.Tab", font=("Segoe UI", 9), padding=(6, 4))
        self.style.configure("Treeview",      rowheight=26, font=("Segoe UI", 9))
        self.style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _create_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Tüm Verileri Yenile", command=self._reload_data)
        file_menu.add_separator()
        file_menu.add_command(label="Çıkış", command=self.destroy)
        menubar.add_cascade(label="Dosya", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(
            label="Hakkında",
            command=lambda: messagebox.showinfo(
                "Hakkında", "BestOffice ERP - Kira Takip Sistemi\nv1.1"
            ),
        )
        menubar.add_cascade(label="Yardım", menu=help_menu)
        self.config(menu=menubar)

    def _reload_data(self) -> None:
        self.customer_tab.refresh()
        self.product_tab.refresh()
        self.invoice_tab.refresh()
        self.tufe_tab.refresh()          # FIX: tufe_tab da yenileniyordu eksikti
        messagebox.showinfo("Bilgi", "Veriler yenilendi.")


# ============================================================================
# YARDIMCI BILEŞEN: FormField
# ============================================================================

class FormField(ttk.Frame):
    """Etiket + Giriş alanı bileşeni."""

    def __init__(
        self,
        master: tk.Widget,
        label: str,
        width: int = 28,
        field_type: str = "entry",
        options: List[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)

        ttk.Label(self, text=label, width=20, anchor="w").pack(side=tk.LEFT, padx=(0, 6))
        self.var = tk.StringVar()

        if field_type == "combobox" and options:
            self.entry = ttk.Combobox(
                self, textvariable=self.var, values=options, width=width, state="readonly"
            )
        else:
            self.entry = ttk.Entry(self, textvariable=self.var, width=width)

        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, value) -> None:
        self.var.set(str(value) if value is not None else "")

    def clear(self) -> None:
        self.var.set("")


# ============================================================================
# MÜŞTERİ SEKMESİ
# ============================================================================

class CustomerTab(ttk.Frame):
    """Müşteri ve Kira Takip Sekmesi — Dinamik Yıl Sütunları."""

    def __init__(self, master: tk.Widget) -> None:
        super().__init__(master, padding=10)
        self.selected_id: Optional[int] = None
        self._sort_col: Optional[str] = None
        self._sort_reverse: bool = False

        # Grid layout: row 0 = sabit üst bar, row 1 = scroll'lu içerik
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0)

        # Scroll canvas (row 1)
        self._vsb = ttk.Scrollbar(self, orient="vertical")
        self._canvas = tk.Canvas(self, highlightthickness=0,
                                  yscrollcommand=self._vsb.set)
        self._vsb.configure(command=self._canvas.yview)
        self._canvas.grid(row=1, column=0, sticky="nsew")
        self._vsb.grid(row=1, column=1, sticky="ns")

        self._inner = ttk.Frame(self._canvas)
        self._inner_id = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")

        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._inner.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<MouseWheel>", self._on_mousewheel)

        self._build_ui_top()
        self._build_ui_body()
        self._init_panels()
        self.refresh()

    def _on_inner_configure(self, e=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, e=None):
        self._canvas.itemconfig(self._inner_id, width=e.width)

    def _on_mousewheel(self, e):
        self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _build_ui_top(self) -> None:
        # ── Üst buton çubuğu — row 0, sabit ──
        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        ttk.Label(top, text="Müşteriler", font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="Excel'den İçeri Aktar", command=self._on_import_excel).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(top, text="↺ Yenile", command=self.refresh).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(top, text="  Ay:").pack(side=tk.LEFT, padx=(16, 2))
        self.filter_ay_var = tk.StringVar(value="Tümü")
        self.filter_ay = ttk.Combobox(top, textvariable=self.filter_ay_var,
                                       values=["Tümü"] + MONTHS_TR, width=10, state="readonly")
        self.filter_ay.pack(side=tk.LEFT)
        self.filter_ay.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())

        ttk.Label(top, text="  Yıl:").pack(side=tk.LEFT, padx=(8, 2))
        self.filter_yil_var = tk.StringVar(value="Tümü")
        self.filter_yil = ttk.Combobox(top, textvariable=self.filter_yil_var,
                                        values=["Tümü"], width=8, state="readonly")
        self.filter_yil.pack(side=tk.LEFT)
        self.filter_yil.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())

        ttk.Button(top, text="✖ Filtre Temizle", command=self._clear_filter).pack(side=tk.LEFT, padx=(6, 0))

        self._show_form = tk.BooleanVar(value=True)
        self._show_rent = tk.BooleanVar(value=True)

        ttk.Checkbutton(top, text="👤 Müşteri Detayları",
                        variable=self._show_form,
                        command=self._toggle_panels).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Checkbutton(top, text="💰 Aylık Kira Girişi",
                        variable=self._show_rent,
                        command=self._toggle_panels).pack(side=tk.RIGHT, padx=(4, 0))

        ttk.Label(top, text="Müşteriler", font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="Excel'den İçeri Aktar", command=self._on_import_excel).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(top, text="↺ Yenile", command=self.refresh).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(top, text="  Ay:").pack(side=tk.LEFT, padx=(16, 2))
        self.filter_ay_var = tk.StringVar(value="Tümü")
        self.filter_ay = ttk.Combobox(top, textvariable=self.filter_ay_var,
                                       values=["Tümü"] + MONTHS_TR, width=10, state="readonly")
        self.filter_ay.pack(side=tk.LEFT)
        self.filter_ay.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())

        ttk.Label(top, text="  Yıl:").pack(side=tk.LEFT, padx=(8, 2))
        self.filter_yil_var = tk.StringVar(value="Tümü")
        self.filter_yil = ttk.Combobox(top, textvariable=self.filter_yil_var,
                                        values=["Tümü"], width=8, state="readonly")
        self.filter_yil.pack(side=tk.LEFT)
        self.filter_yil.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())

        ttk.Button(top, text="✖ Filtre Temizle", command=self._clear_filter).pack(side=tk.LEFT, padx=(6, 0))

        # ── Toggle butonları (sağ taraf) ──
        self._show_form = tk.BooleanVar(value=True)
        self._show_rent = tk.BooleanVar(value=True)

        ttk.Checkbutton(top, text="👤 Müşteri Detayları",
                        variable=self._show_form,
                        command=self._toggle_panels).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Checkbutton(top, text="💰 Aylık Kira Girişi",
                        variable=self._show_rent,
                        command=self._toggle_panels).pack(side=tk.RIGHT, padx=(4, 0))

    def _build_ui_body(self) -> None:
        # ── Ana alan ──
        self.main_frame = ttk.Frame(self._inner)
        self.main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # ── Treeview ──
        tree_frame = ttk.Frame(self.main_frame)
        tree_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 0))

        self.tree = ttk.Treeview(tree_frame, show="headings", selectmode="browse", height=15)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)

        # Toplam satırı
        self.totals_frame = ttk.Frame(self.main_frame)
        self.totals_frame.pack(side=tk.TOP, fill=tk.X, pady=(2, 0))
        self.totals_canvas = tk.Canvas(self.totals_frame, height=26, bg="#dde3ed", highlightthickness=0)
        self.totals_canvas.pack(fill=tk.X)

        self.tree.bind("<Configure>",       lambda e: self.after(50, self._update_totals))
        self.tree.bind("<ButtonRelease-1>", lambda e: self.after(50, self._update_totals))
        orig_hsb_set = hsb.set
        def hsb_set_and_update(*args):
            orig_hsb_set(*args)
            self.after(10, self._update_totals)
        self.tree.configure(xscrollcommand=hsb_set_and_update)

        # ── Müşteri Detayları Formu (başta gizli) ──
        self.form_frame = ttk.LabelFrame(self.main_frame, text="👤 Müşteri Detayları", padding=8)
        # pack edilmeyecek — toggle ile açılacak

        form_inner = ttk.Frame(self.form_frame)
        form_inner.pack(fill=tk.X)

        left_f  = ttk.Frame(form_inner)
        right_f = ttk.Frame(form_inner)
        left_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        right_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.form: Dict[str, FormField] = {}

        left_fields = [
            ("name",            "Ad / Ünvan:",      "entry"),
            ("email",           "E-posta:",          "entry"),
            ("phone",           "Telefon:",          "entry"),
            ("address",         "Adres:",            "entry"),
            ("tax_number",      "Vergi No:",         "entry"),
        ]
        right_fields = [
            ("rent_start_year",  "Başlangıç Tarihi:", "entry"),
            ("rent_start_month", "Başlangıç Ayı:",    "combobox"),
            ("ilk_kira_bedeli",  "İlk Kira (₺):",    "entry"),
            ("current_rent",     "Gerçek Kira (₺):", "entry"),
        ]
        for key, label, ftype in left_fields:
            f = FormField(left_f, label=label, field_type=ftype,
                          options=get_months_list() if ftype == "combobox" else None)
            f.pack(fill=tk.X, pady=2)
            self.form[key] = f
        for key, label, ftype in right_fields:
            f = FormField(right_f, label=label, field_type=ftype,
                          options=get_months_list() if ftype == "combobox" else None)
            f.pack(fill=tk.X, pady=2)
            self.form[key] = f

        btn_f = ttk.Frame(self.form_frame)
        btn_f.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn_f, text="💾 Ekle/Güncelle", command=self._on_save).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_f, text="🗑 Sil",           command=self._on_delete).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_f, text="✖ Temizle",        command=self._clear_form).pack(side=tk.LEFT)

        # ── Aylık Kira Girişi (başta gizli) ──
        self.rent_frame = ttk.LabelFrame(self.main_frame, text="💰 Aylık Kira Girişi", padding=8)
        # pack edilmeyecek — toggle ile açılacak

        rent_top = ttk.Frame(self.rent_frame)
        rent_top.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(rent_top, text="Yıl:").pack(side=tk.LEFT)
        self.rent_year_var = tk.StringVar(value=str(date.today().year))
        self.rent_year_spin = tk.Spinbox(
            rent_top, from_=2000, to=2100, width=6,
            textvariable=self.rent_year_var, font=("Segoe UI", 9),
            command=self._on_rent_year_change
        )
        self.rent_year_spin.pack(side=tk.LEFT, padx=(4, 12))
        self.rent_year_spin.bind("<Return>", lambda e: self._on_rent_year_change())

        ttk.Label(rent_top, text="TÜFE Kira:").pack(side=tk.LEFT)
        self.tufe_rent_label = ttk.Label(rent_top, text="—", foreground="#1a3a6b",
                                          font=("Segoe UI", 9, "bold"))
        self.tufe_rent_label.pack(side=tk.LEFT, padx=(4, 20))
        ttk.Label(rent_top, text="Ödenen Toplam:").pack(side=tk.LEFT)
        self.paid_total_label = ttk.Label(rent_top, text="—", foreground="#1a6b2a",
                                           font=("Segoe UI", 9, "bold"))
        self.paid_total_label.pack(side=tk.LEFT, padx=(4, 0))

        # Yıllık toplam giriş
        ttk.Label(rent_top, text="   Yıllık Toplam:").pack(side=tk.LEFT, padx=(16, 2))
        self.yearly_total_var = tk.StringVar()
        yearly_entry = ttk.Entry(rent_top, textvariable=self.yearly_total_var, width=12, justify="right")
        yearly_entry.pack(side=tk.LEFT)
        ttk.Button(rent_top, text="Dağıt", command=self._distribute_yearly_total).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Button(rent_top, text="💾 Kaydet", command=self._on_save_rent_payments).pack(side=tk.RIGHT)

        months_grid = ttk.Frame(self.rent_frame)
        months_grid.pack(fill=tk.X)
        self.rent_month_vars: Dict[str, tk.StringVar] = {}

        for i, month_name in enumerate(MONTHS_TR):
            row_idx = i % 6
            col_idx = (i // 6) * 2
            ttk.Label(months_grid, text=month_name, width=9, anchor="e").grid(
                row=row_idx, column=col_idx, padx=(4, 2), pady=2, sticky="e")
            var = tk.StringVar()
            self.rent_month_vars[month_name] = var
            entry = ttk.Entry(months_grid, textvariable=var, width=10, justify="right")
            entry.grid(row=row_idx, column=col_idx + 1, padx=(0, 8), pady=2, sticky="ew")
            months_grid.columnconfigure(col_idx + 1, weight=1)
            var.trace_add("write", lambda *args: self._update_paid_total())

    def _toggle_panels(self) -> None:
        """Panelleri aç/kapat — sırası her zaman: form → rent."""
        self.form_frame.pack_forget()
        self.rent_frame.pack_forget()

        if self._show_form.get():
            self.form_frame.pack(side=tk.TOP, fill=tk.X, pady=(4, 0))
        if self._show_rent.get():
            self.rent_frame.pack(side=tk.TOP, fill=tk.X, pady=(4, 0))

    def _init_panels(self) -> None:
        """Başlangıçta panelleri göster."""
        self._toggle_panels()

    # ── Sütunlar ──

    def _get_year_range(self, customers) -> List[int]:
        years = set()
        for c in customers:
            years.update(c.get("rent_years_dict", {}).keys())
        return sorted(years)

    def _sort_by_col(self, col) -> None:
        """Sütun başlığına tıklanınca sırala."""
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False

        items = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]

        # Sayısal mı metin mi?
        def parse_val(v):
            try:
                # Para formatı: '1,234.56' → float
                return float(v.replace(",", "").replace(".", "").replace(" ", "") or "0")
            except:
                # Tarih: 'GG.AA.YYYY' → sıralama için YYYY.AA.GG
                parts = v.split(".")
                if len(parts) == 3 and all(p.isdigit() for p in parts):
                    return f"{parts[2]}.{parts[1]}.{parts[0]}"
                return v.lower()

        items.sort(key=lambda x: parse_val(x[0]), reverse=self._sort_reverse)

        for index, (_, k) in enumerate(items):
            self.tree.move(k, "", index)

        # Başlık okunu güncelle
        cols = self.tree["columns"]
        headers = {
            "name": "Ad / Ünvan", "tax_number": "Vergi No",
            "baslangic_tarihi": "Başlangıç Tarihi",
            "ilk_kira_bedeli": "İlk Kira (₺)", "current_rent": "Gerçek Kira (₺)",
        }
        for c in cols:
            label = headers.get(c, c)
            arrow = (" ▲" if not self._sort_reverse else " ▼") if c == col else ""
            self.tree.heading(c, text=label + arrow)

    def _refresh_columns(self, year_range: List[int]) -> None:
        base   = ["name", "tax_number", "baslangic_tarihi", "baslangic_ayi", "baslangic_yili", "ilk_kira_bedeli"]
        y_cols = [str(y) for y in year_range]
        cols   = base + y_cols + ["current_rent"]

        self.tree["columns"] = cols
        self.tree.column("#0", width=0, stretch=tk.NO)

        headers = {
            "name":              "Ad / Ünvan",
            "tax_number":        "Vergi No",
            "baslangic_tarihi":  "Başlangıç Tarihi",
            "baslangic_ayi":     "Ay",
            "baslangic_yili":    "Yıl",
            "ilk_kira_bedeli":   "İlk Kira (₺)",
            "current_rent":      "Gerçek Kira (₺)",
        }

        for col in cols:
            label = headers.get(col, col)
            self.tree.heading(col, text=label,
                              command=lambda c=col: self._sort_by_col(c))
            if col == "name":
                w = 160
            elif col == "baslangic_tarihi":
                w = 120
            elif col in ("baslangic_ayi",):
                w = 80
            elif col == "baslangic_yili":
                w = 60
            else:
                w = 100
            self.tree.column(col, width=w, anchor="w" if col == "name" else "e", minwidth=60)

    # ── Yenile ──

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        self._all_customers = get_all_customers_with_rent_progression()
        year_range = self._get_year_range(self._all_customers)
        self._refresh_columns(year_range)

        # Yıl filtresini güncelle
        yil_values = ["Tümü"] + [str(y) for y in sorted(
            set(c.get("rent_start_date", "").split(".")[-1]
                for c in self._all_customers
                if c.get("rent_start_date", ""))
        )]
        self.filter_yil["values"] = yil_values

        self._apply_filter()

    def _apply_filter(self) -> None:
        """Ay ve yıl filtresini uygula."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        filtre_ay  = self.filter_ay_var.get()
        filtre_yil = self.filter_yil_var.get()

        year_range = self._get_year_range(self._all_customers)

        for c in self._all_customers:
            rent_date = c.get("rent_start_date", "") or ""
            baslangic_ayi  = ""
            baslangic_yili = ""

            if rent_date:
                try:
                    parts = rent_date.split(".")
                    baslangic_ayi  = MONTHS_TR[int(parts[1]) - 1]
                    baslangic_yili = parts[2]
                except (IndexError, ValueError):
                    pass

            # Filtre kontrolü
            if filtre_ay  != "Tümü" and baslangic_ayi  != filtre_ay:
                continue
            if filtre_yil != "Tümü" and baslangic_yili != filtre_yil:
                continue

            years_dict = c.get("rent_years_dict", {})
            values = [
                c["name"],
                c.get("tax_number", ""),
                rent_date,
                baslangic_ayi,
                baslangic_yili,
                f"{float(c.get('ilk_kira_bedeli', 0)):,.2f}",
            ]

            for year in year_range:
                rent = years_dict.get(year, years_dict.get(str(year), 0))
                values.append(f"{float(rent):,.2f}")

            current = c.get("current_rent")
            if current:
                values.append(f"{float(current):,.2f}")
            elif year_range:
                last = years_dict.get(year_range[-1], years_dict.get(str(year_range[-1]), 0))
                values.append(f"{float(last):,.2f}")
            else:
                values.append("0,00")

            self.tree.insert("", tk.END, iid=str(c["id"]), values=values)

        print(f"[UI] Filtre: ay={filtre_ay} yıl={filtre_yil}")
        self._update_totals()

    def _update_totals(self) -> None:
        """Görünen satırların toplamlarını sütun altına hizalı göster."""
        self.totals_canvas.delete("all")
        self.totals_canvas.config(bg="#dde3ed")

        items = self.tree.get_children()
        if not items:
            self.totals_canvas.create_text(6, 14, text="📊 0 müşteri", anchor="w",
                                           font=("Segoe UI", 9, "bold"), fill="#1a3a6b")
            return

        cols = self.tree["columns"]
        totals = {}
        count = len(items)

        for iid in items:
            vals = self.tree.item(iid, "values")
            for i, col in enumerate(cols):
                if col in ("name", "tax_number", "baslangic_tarihi", "baslangic_ayi", "baslangic_yili"):
                    continue
                try:
                    v = float(str(vals[i]).replace(",", ""))
                    totals[col] = totals.get(col, 0.0) + v
                except (ValueError, IndexError):
                    pass

        # Müşteri sayısı sol başa
        self.totals_canvas.create_text(6, 14, text=f"📊 {count} müşteri",
                                       anchor="w", font=("Segoe UI", 9, "bold"), fill="#1a3a6b")

        # Treeview toplam genişliği
        total_width = sum(self.tree.column(c, "width") for c in cols)
        # Scroll oranı (0.0 - 1.0)
        try:
            scroll_left = self.tree.xview()[0]
        except Exception:
            scroll_left = 0.0
        scroll_offset = scroll_left * total_width

        # Canvas genişliği
        canvas_width = self.totals_canvas.winfo_width()

        x_offset = 0
        for col in cols:
            col_width = self.tree.column(col, "width")
            if col in totals:
                # Ekranda görünen x pozisyonu
                screen_x = x_offset - scroll_offset + col_width - 4
                if 0 < screen_x < canvas_width:
                    self.totals_canvas.create_text(
                        screen_x, 14,
                        text=f"{totals[col]:,.2f}",
                        anchor="e",
                        font=("Segoe UI", 9, "bold"),
                        fill="#1a3a6b"
                    )
            x_offset += col_width

    def _clear_filter(self) -> None:
        """Filtreleri temizle."""
        self.filter_ay_var.set("Tümü")
        self.filter_yil_var.set("Tümü")
        self._apply_filter()

    # ── Satır Seçimi ──

    def _on_row_select(self, event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return

        self.selected_id = int(sel[0])

        from database import fetch_one
        row = fetch_one(
            """SELECT id, name, email, phone, address, tax_number,
                      rent_start_date, rent_start_year, rent_start_month,
                      ilk_kira_bedeli, current_rent
               FROM customers WHERE id = ?""",
            (self.selected_id,)
        )
        if not row:
            return

        self.form["name"].set(row["name"])
        self.form["email"].set(row["email"] or "")
        self.form["phone"].set(row["phone"] or "")
        self.form["address"].set(row["address"] or "")
        self.form["tax_number"].set(row["tax_number"] or "")
        self.form["rent_start_year"].set(row["rent_start_date"] or "")
        self.form["rent_start_month"].set(row["rent_start_month"] or "Ocak")
        self.form["ilk_kira_bedeli"].set(row["ilk_kira_bedeli"] or "")
        self.form["current_rent"].set(row["current_rent"] or "")

        # Aylık kira panelini yükle
        self._load_rent_panel()

    def _load_rent_panel(self) -> None:
        """Seçili müşteri ve yıl için aylık kira verilerini panele yükle."""
        if not self.selected_id:
            return
        year = int(self.rent_year_var.get())
        payments = get_rent_payments_for_year(self.selected_id, year)

        for month_name in MONTHS_TR:
            val = payments.get(month_name, "")
            self.rent_month_vars[month_name].set(f"{val:.2f}" if val else "")

        self._update_tufe_label(year)
        self._update_paid_total()

    def _on_rent_year_change(self) -> None:
        self._load_rent_panel()

    def _update_tufe_label(self, year: int) -> None:
        if not self.selected_id:
            self.tufe_rent_label.config(text="—")
            return
        from database import fetch_one
        row = fetch_one(
            "SELECT rent_start_year, rent_start_month, ilk_kira_bedeli FROM customers WHERE id=?",
            (self.selected_id,)
        )
        if not row or not row["ilk_kira_bedeli"]:
            self.tufe_rent_label.config(text="—")
            return
        prog = calculate_rent_progression(
            start_year=row["rent_start_year"],
            start_month=row["rent_start_month"] or "Ocak",
            initial_rent=float(row["ilk_kira_bedeli"])
        )
        tufe_val = prog.get("years", {}).get(year, 0)
        self.tufe_rent_label.config(text=f"{tufe_val:,.2f} ₺")

    def _update_paid_total(self) -> None:
        total = 0.0
        for var in self.rent_month_vars.values():
            raw = var.get().replace(",", ".").replace(" ", "")
            try:
                total += float(raw) if raw else 0.0
            except ValueError:
                pass
        self.paid_total_label.config(text=f"{total:,.2f} ₺")

    def _distribute_yearly_total(self) -> None:
        """Yıllık toplam tutarı aylara dağıt."""
        raw = self.yearly_total_var.get().replace(",", ".").replace(" ", "")
        try:
            yearly = float(raw) if raw else 0.0
        except ValueError:
            messagebox.showerror("Hata", "Geçerli bir tutar girin!")
            return

        if yearly <= 0:
            return

        # Müşterinin aylık kira tutarını bul
        if self.selected_id:
            from database import fetch_one
            row = fetch_one("SELECT ilk_kira_bedeli FROM customers WHERE id=?", (self.selected_id,))
            aylik = float(row["ilk_kira_bedeli"]) if row else 0.0
        else:
            aylik = 0.0

        # Aylık tutar bilinemiyorsa eşit böl
        if aylik <= 0:
            aylik = yearly / 12

        kalan = yearly
        for i, month_name in enumerate(MONTHS_TR):
            if kalan <= 0:
                self.rent_month_vars[month_name].set("")
            elif kalan >= aylik:
                self.rent_month_vars[month_name].set(f"{aylik:.2f}")
                kalan -= aylik
            else:
                # Kalan yarım ay
                self.rent_month_vars[month_name].set(f"{kalan:.2f}")
                kalan = 0

        self._update_paid_total()

    def _on_save_rent_payments(self) -> None:
        if not self.selected_id:
            messagebox.showwarning("Uyarı", "Önce bir müşteri seçin!")
            return
        try:
            year = int(self.rent_year_var.get())
        except ValueError:
            messagebox.showerror("Hata", "Geçerli bir yıl girin!")
            return

        for month_name in MONTHS_TR:
            raw = self.rent_month_vars[month_name].get().replace(",", ".").replace(" ", "")
            try:
                amount = float(raw) if raw else 0.0
                save_rent_payment(self.selected_id, year, month_name, amount)
            except ValueError:
                pass

        messagebox.showinfo("Başarılı", f"{year} yılı aylık kira kaydedildi.")
        self.refresh()

    def _clear_form(self) -> None:
        for f in self.form.values():
            f.clear()
        for var in self.rent_month_vars.values():
            var.set("")
        self.tufe_rent_label.config(text="—")
        self.paid_total_label.config(text="—")
        if self.tree.selection():
            self.tree.selection_remove(self.tree.selection())
        self.selected_id = None

    # ── Kaydet ──

    def _on_save(self) -> None:
        try:
            name = self.form["name"].get()
            if not name:
                messagebox.showwarning("Uyarı", "Ad / Ünvan alanı zorunludur!")
                return

            email      = self.form["email"].get()
            phone      = self.form["phone"].get()
            address    = self.form["address"].get()
            tax_number = self.form["tax_number"].get()
            month      = self.form["rent_start_month"].get() or "Ocak"

            year_str = self.form["rent_start_year"].get()  # artık tarih alanı
            year: Optional[int] = None
            month: str = "Ocak"
            rent_date: str = ""

            if year_str:
                # GG.AA.YYYY formatını parse et
                parts = year_str.strip().split('.')
                if len(parts) == 3:
                    try:
                        gun = int(parts[0])
                        ay  = int(parts[1])
                        yil = int(parts[2])
                        year = yil
                        month = get_months_list()[ay - 1]
                        rent_date = f"{gun:02d}.{ay:02d}.{yil}"
                    except (ValueError, IndexError):
                        messagebox.showerror("Hata", "Tarih formatı GG.AA.YYYY olmalıdır! Örn: 01.01.2024")
                        return
                else:
                    # Sadece yıl girilmişse
                    try:
                        year = int(year_str)
                        rent_date = f"01.01.{year}"
                    except ValueError:
                        messagebox.showerror("Hata", "Tarih formatı GG.AA.YYYY olmalıdır! Örn: 01.01.2024")
                        return

            def to_float(field_key: str) -> float:
                raw = self.form[field_key].get().replace(".", "").replace(",", ".")
                return float(raw) if raw else 0.0

            ilk_kira = to_float("ilk_kira_bedeli")
            current  = to_float("current_rent")

            if self.selected_id is None:
                insert_customer(
                    name=name, email=email, phone=phone, address=address,
                    tax_number=tax_number, rent_start_date=rent_date,
                    rent_start_year=year, rent_start_month=month,
                    ilk_kira_bedeli=ilk_kira, current_rent=current,
                )
                messagebox.showinfo("Başarılı", "Müşteri eklendi.")
            else:
                update_customer(
                    customer_id=self.selected_id,
                    name=name, email=email, phone=phone, address=address,
                    tax_number=tax_number, rent_start_date=rent_date,
                    rent_start_year=year, rent_start_month=month,
                    ilk_kira_bedeli=ilk_kira, current_rent=current,
                )
                messagebox.showinfo("Başarılı", "Müşteri güncellendi.")

            self.refresh()
            self._clear_form()

        except ValueError as e:
            messagebox.showerror("Hata", f"Geçersiz veri: {e}")
        except Exception as e:
            messagebox.showerror("Hata", f"Kaydetme hatası: {e}")

    # ── Sil ──

    def _on_delete(self) -> None:
        if not self.selected_id:
            messagebox.showwarning("Uyarı", "Silmek için bir müşteri seçin!")
            return
        if not messagebox.askyesno("Onay", "Seçili müşteri silinecek. Emin misiniz?"):
            return
        try:
            delete_customer(self.selected_id)
            messagebox.showinfo("Başarılı", "Müşteri silindi.")
            self.refresh()
            self._clear_form()
        except Exception as e:
            messagebox.showerror("Hata", f"Silme hatası: {e}")

    # ── Excel ──

    def _on_import_excel(self) -> None:
        path = filedialog.askopenfilename(
            title="Müşteri Excel Dosyası Seç",
            filetypes=[("Excel Dosyaları", "*.xlsx *.xls"), ("Tüm Dosyalar", "*.*")],
        )
        if not path:
            return
        try:
            count = import_customers_from_excel(path)
            messagebox.showinfo("Başarılı", f"{count} müşteri içe aktarıldı.")
            self.refresh()
        except Exception as e:
            messagebox.showerror("Hata", f"Excel aktarma hatası: {e}")


# ============================================================================
# ÜRÜN SEKMESİ
# ============================================================================

class ProductTab(ttk.Frame):
    """Ürün Yönetimi Sekmesi — CRUD dahil."""

    def __init__(self, master: tk.Widget) -> None:
        super().__init__(master, padding=10)
        self.selected_id: Optional[int] = None
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        ttk.Label(top, text="Ürünler", font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="↺ Yenile", command=self.refresh).pack(side=tk.LEFT, padx=(10, 0))

        # Treeview
        tree_frame = ttk.Frame(self)
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        cols = ("name", "sku", "unit_price", "stock_quantity")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        headers = {"name": "Ürün Adı", "sku": "Stok Kodu", "unit_price": "Birim Fiyat (₺)", "stock_quantity": "Stok"}
        for col in cols:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=160, anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)

        # Form
        form_frame = ttk.LabelFrame(self, text="Ürün Detayları", padding=10)
        form_frame.pack(side=tk.TOP, fill=tk.X, pady=(8, 0))

        self.form: Dict[str, FormField] = {}
        for key, label in [("name", "Ürün Adı:"), ("sku", "Stok Kodu:"),
                            ("unit_price", "Birim Fiyat (₺):"), ("stock_quantity", "Stok Miktarı:")]:
            f = FormField(form_frame, label=label)
            f.pack(fill=tk.X, pady=2)
            self.form[key] = f

        btn = ttk.Frame(form_frame)
        btn.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn, text="💾 Ekle / Güncelle", command=self._on_save).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn, text="🗑 Sil",             command=self._on_delete).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn, text="✖ Temizle",          command=self._clear_form).pack(side=tk.LEFT)

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for p in get_all_products():
            self.tree.insert("", tk.END, iid=str(p["id"]), values=(
                p["name"], p["sku"] or "",
                f"{p['unit_price']:,.2f}", f"{p['stock_quantity']:,.2f}",
            ))

    def _on_row_select(self, event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        self.selected_id = int(sel[0])
        vals = self.tree.item(sel[0], "values")
        keys = ["name", "sku", "unit_price", "stock_quantity"]
        for k, v in zip(keys, vals):
            self.form[k].set(v)

    def _clear_form(self) -> None:
        for f in self.form.values():
            f.clear()
        if self.tree.selection():
            self.tree.selection_remove(self.tree.selection())
        self.selected_id = None

    def _on_save(self) -> None:
        try:
            name = self.form["name"].get()
            if not name:
                messagebox.showwarning("Uyarı", "Ürün adı zorunludur!")
                return
            sku   = self.form["sku"].get()
            price = float(self.form["unit_price"].get().replace(",", ".") or "0")
            stock = float(self.form["stock_quantity"].get().replace(",", ".") or "0")

            if self.selected_id is None:
                insert_product(name=name, sku=sku, unit_price=price, stock_quantity=stock)
                messagebox.showinfo("Başarılı", "Ürün eklendi.")
            else:
                update_product(self.selected_id, name=name, sku=sku, unit_price=price, stock_quantity=stock)
                messagebox.showinfo("Başarılı", "Ürün güncellendi.")

            self.refresh()
            self._clear_form()
        except ValueError as e:
            messagebox.showerror("Hata", f"Geçersiz değer: {e}")

    def _on_delete(self) -> None:
        if not self.selected_id:
            messagebox.showwarning("Uyarı", "Silmek için bir ürün seçin!")
            return
        if not messagebox.askyesno("Onay", "Ürün silinecek. Emin misiniz?"):
            return
        delete_product(self.selected_id)
        messagebox.showinfo("Başarılı", "Ürün silindi.")
        self.refresh()
        self._clear_form()


# ============================================================================
# FATURA SEKMESİ
# ============================================================================

class InvoiceTab(ttk.Frame):
    """Fatura Yönetimi Sekmesi — CRUD dahil."""

    def __init__(self, master: tk.Widget) -> None:
        super().__init__(master, padding=10)
        self.selected_id: Optional[int] = None
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        ttk.Label(top, text="Faturalar", font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="↺ Yenile", command=self.refresh).pack(side=tk.LEFT, padx=(10, 0))

        # Treeview
        tree_frame = ttk.Frame(self)
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        cols = ("invoice_number", "customer_name", "issue_date", "total_amount")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        headers = {
            "invoice_number": "Fatura No",
            "customer_name":  "Müşteri",
            "issue_date":     "Tarih",
            "total_amount":   "Tutar (₺)",
        }
        for col in cols:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=180, anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)

        # Form
        form_frame = ttk.LabelFrame(self, text="Fatura Detayları", padding=10)
        form_frame.pack(side=tk.TOP, fill=tk.X, pady=(8, 0))

        self.form: Dict[str, FormField] = {}

        # Müşteri combobox
        cust_frame = ttk.Frame(form_frame)
        cust_frame.pack(fill=tk.X, pady=2)
        ttk.Label(cust_frame, text="Müşteri:", width=20, anchor="w").pack(side=tk.LEFT, padx=(0, 6))
        self.customer_var = tk.StringVar()
        self.customer_combo = ttk.Combobox(cust_frame, textvariable=self.customer_var, width=40, state="readonly")
        self.customer_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

        for key, label in [("invoice_number", "Fatura No:"),
                            ("issue_date",     "Tarih (YYYY-AA-GG):"),
                            ("total_amount",   "Tutar (₺):")]:
            f = FormField(form_frame, label=label)
            f.pack(fill=tk.X, pady=2)
            self.form[key] = f

        # Varsayılan tarih bugün
        self.form["issue_date"].set(date.today().isoformat())

        btn = ttk.Frame(form_frame)
        btn.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn, text="💾 Fatura Ekle", command=self._on_save).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn, text="🗑 Sil",         command=self._on_delete).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn, text="✖ Temizle",      command=self._clear_form).pack(side=tk.LEFT)

        self._load_customer_list()

    def _load_customer_list(self) -> None:
        """Müşteri listesini combobox'a yükle."""
        customers = get_all_customers_with_rent_progression()
        self._customer_map: Dict[str, int] = {}
        names = []
        for c in customers:
            label = f"{c['name']} ({c.get('tax_number', '')})"
            self._customer_map[label] = c["id"]
            names.append(label)
        self.customer_combo["values"] = names

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for inv in get_all_invoices():
            self.tree.insert("", tk.END, iid=str(inv["id"]), values=(
                inv["invoice_number"],
                inv["customer_name"],
                inv["issue_date"],
                f"{inv['total_amount']:,.2f}",
            ))
        self._load_customer_list()

    def _on_row_select(self, event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        self.selected_id = int(sel[0])
        vals = self.tree.item(sel[0], "values")
        self.form["invoice_number"].set(vals[0])
        self.customer_var.set(vals[1])
        self.form["issue_date"].set(vals[2])
        self.form["total_amount"].set(vals[3])

    def _clear_form(self) -> None:
        for f in self.form.values():
            f.clear()
        self.customer_var.set("")
        self.form["issue_date"].set(date.today().isoformat())
        if self.tree.selection():
            self.tree.selection_remove(self.tree.selection())
        self.selected_id = None

    def _on_save(self) -> None:
        try:
            inv_no = self.form["invoice_number"].get()
            if not inv_no:
                messagebox.showwarning("Uyarı", "Fatura no zorunludur!")
                return

            cust_label = self.customer_var.get()
            if not cust_label or cust_label not in self._customer_map:
                messagebox.showwarning("Uyarı", "Geçerli bir müşteri seçin!")
                return

            customer_id = self._customer_map[cust_label]
            issue_date  = self.form["issue_date"].get() or date.today().isoformat()
            amount      = float(self.form["total_amount"].get().replace(",", ".") or "0")

            insert_invoice(
                invoice_number=inv_no,
                customer_id=customer_id,
                issue_date=issue_date,
                total_amount=amount,
            )
            messagebox.showinfo("Başarılı", "Fatura eklendi.")
            self.refresh()
            self._clear_form()
        except Exception as e:
            messagebox.showerror("Hata", f"Fatura ekleme hatası: {e}")

    def _on_delete(self) -> None:
        if not self.selected_id:
            messagebox.showwarning("Uyarı", "Silmek için bir fatura seçin!")
            return
        if not messagebox.askyesno("Onay", "Fatura silinecek. Emin misiniz?"):
            return
        delete_invoice(self.selected_id)
        messagebox.showinfo("Başarılı", "Fatura silindi.")
        self.refresh()
        self._clear_form()


# ============================================================================
# TÜFE SEKMESİ
# ============================================================================

class TufeTab(ttk.Frame):
    """TÜFE Yönetimi Sekmesi."""

    MONTHS = [
        "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
        "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
    ]

    def __init__(self, master: tk.Widget) -> None:
        super().__init__(master, padding=10)
        self._build_ui()

    def _build_ui(self) -> None:
        # Üst kontrol çubuğu
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, pady=(0, 12))

        ttk.Label(top, text="TÜFE Oranları", font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)

        ttk.Label(top, text="Yıl:").pack(side=tk.LEFT, padx=(16, 4))
        self.year_var = tk.IntVar(value=date.today().year)
        tk.Spinbox(top, from_=2000, to=2100, width=7, textvariable=self.year_var,
                   font=("Segoe UI", 9)).pack(side=tk.LEFT)

        ttk.Button(top, text="📂 Yükle",         command=self._on_load).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(top, text="💾 Kaydet",         command=self._on_save).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top, text="📊 Excel'den Aktar", command=self._on_import_excel).pack(side=tk.LEFT, padx=(4, 0))

        # Ay grid (3 sütun × 4 satır)
        self.month_vars: Dict[int, tk.StringVar] = {}
        grid = ttk.Frame(self)
        grid.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))

        for i, month_name in enumerate(self.MONTHS):
            row, col = divmod(i, 3)
            frame = ttk.LabelFrame(grid, text=month_name + " (%)", padding=8)
            frame.grid(row=row, column=col, sticky="ew", padx=6, pady=4)
            grid.columnconfigure(col, weight=1)

            var = tk.StringVar()
            self.month_vars[i + 1] = var
            ttk.Entry(frame, textvariable=var, width=14, justify="right").pack()

        # Alt bilgi
        self.info_label = ttk.Label(self, text="", foreground="#555")
        self.info_label.pack(side=tk.BOTTOM, anchor="w")

    def refresh(self) -> None:
        """Mevcut yılı yeniden yükle (dış çağrı için)."""
        self._on_load()

    def _get_year(self) -> Optional[int]:
        try:
            return int(self.year_var.get())
        except Exception:
            return None

    def _on_load(self) -> None:
        year = self._get_year()
        if not year:
            messagebox.showwarning("Uyarı", "Geçerli bir yıl girin!")
            return

        data = get_tufe_for_year(year)

        for i, month_name in enumerate(self.MONTHS):
            val = data.get(month_name)
            self.month_vars[i + 1].set(str(val) if val is not None else "")

        if data:
            self.info_label.config(text=f"{year} yılı verileri yüklendi — {len(data)//2} ay kayıtlı.")
        else:
            self.info_label.config(text=f"{year} yılına ait kayıt bulunamadı.")

    def _on_save(self) -> None:
        year = self._get_year()
        if not year:
            messagebox.showwarning("Uyarı", "Geçerli bir yıl girin!")
            return

        # FIX: Kaydet öncesi yılı yeniden okumak yerine mevcut spinbox değerini kullan
        rates: Dict[str, float] = {}
        for i, month_name in enumerate(self.MONTHS):
            raw = self.month_vars[i + 1].get().strip().replace(",", ".")
            if not raw:
                continue
            try:
                rates[month_name] = float(raw)
            except ValueError:
                messagebox.showwarning("Uyarı", f"{month_name} için geçersiz oran!")
                return

        if not rates:
            messagebox.showwarning("Uyarı", "En az bir oran girin.")
            return

        save_tufe_for_year(year, rates)
        self.info_label.config(text=f"{year} yılı kaydedildi — {len(rates)} ay.")
        messagebox.showinfo("Başarılı", f"{year} yılı TÜFE oranları kaydedildi.")

    def _on_import_excel(self) -> None:
        path = filedialog.askopenfilename(
            title="TÜFE Excel Dosyası Seç",
            filetypes=[("Excel Dosyaları", "*.xlsx *.xls"), ("Tüm Dosyalar", "*.*")],
        )
        if not path:
            return
        try:
            count = import_tufe_from_excel(path)
            messagebox.showinfo("Başarılı", f"{count} TÜFE kaydı içe aktarıldı.")
            self._on_load()
        except Exception as e:
            messagebox.showerror("Hata", f"Excel aktarma hatası: {e}")


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    # DB sadece ilk çalıştırmada init edilmeli — mevcut veriler korunur
    # initialize_database()  ← Bunu sadece ilk kurulumda çağır, burada kapalı!
    app = BaseWindow()
    app.mainloop()
