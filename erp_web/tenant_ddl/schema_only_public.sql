--
-- PostgreSQL database dump
--

\restrict iYToLm6nKFhGrZhtmmtwTatUPR2IwYRRRMr6dovQVxKZadvgagJzZDkB0e1LoGV

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.10

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: fn_group_financial_aggregate(integer[], text[], boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_group_financial_aggregate(p_group_ids integer[], p_parent_uuids text[], p_include_passive boolean DEFAULT false) RETURNS TABLE(gid integer, child_count integer, mids integer[], borc_total numeric, alacak_total numeric, net_balance numeric, sozlesme_gun integer)
    LANGUAGE sql
    AS $_$
            WITH parents AS (
                SELECT t.gid, t.puid
                FROM unnest(p_group_ids, p_parent_uuids) AS t(gid, puid)
            ),
            children AS (
                SELECT p.gid, c.id AS mid
                FROM parents p
                JOIN customers c
                  ON c.parent_id IS NOT NULL
                 AND c.parent_id::text = p.puid
                WHERE p_include_passive = TRUE
                   OR (
                        COALESCE(c.is_active, TRUE) = TRUE
                        AND (
                            c.durum IS NULL
                            OR TRIM(COALESCE(c.durum, '')) = ''
                            OR LOWER(TRIM(c.durum)) NOT IN (
                                'pasif', 'terk', 'kapandi', 'kapandı', 'kapalı', 'kapali', 'kapanmış', 'kapanmis'
                            )
                        )
                   )
            ),
            f_by_mid AS (
                SELECT f.musteri_id AS mid,
                       COALESCE(SUM(COALESCE(f.toplam, f.tutar, 0)), 0) AS borc
                FROM faturalar f
                JOIN children c ON c.mid = f.musteri_id
                WHERE (
                    f.notlar IS NULL OR NOT (
                        regexp_replace(COALESCE(f.notlar, ''), '[İIıi]', 'I', 'g')
                        ~* 'GIB[[:space:]]+DURUM[[:space:]]*:[[:space:]]+TASLAK'
                    )
                )
                GROUP BY f.musteri_id
            ),
            t_by_mid AS (
                SELECT t.musteri_id AS mid,
                       COALESCE(SUM(t.tutar), 0) AS alacak
                FROM tahsilatlar t
                JOIN children c ON c.mid = t.musteri_id
                GROUP BY t.musteri_id
            ),
            kyc_last AS (
                SELECT DISTINCT ON (mk.musteri_id)
                       mk.musteri_id,
                       CASE
                           WHEN mk.sozlesme_tarihi IS NULL THEN NULL
                           WHEN BTRIM(mk.sozlesme_tarihi::text) = '' THEN NULL
                           WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                               THEN (SUBSTRING(BTRIM(mk.sozlesme_tarihi::text) FROM 1 FOR 10))::date
                           WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}'
                               THEN TO_DATE(REGEXP_REPLACE(BTRIM(mk.sozlesme_tarihi::text), ' .*$', ''), 'DD.MM.YYYY')
                           WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{1,2}-[0-9]{1,2}-[0-9]{4}'
                               THEN TO_DATE(REGEXP_REPLACE(BTRIM(mk.sozlesme_tarihi::text), ' .*$', ''), 'DD-MM-YYYY')
                           ELSE NULL
                       END AS soz_bas
                FROM musteri_kyc mk
                JOIN children c ON c.mid = mk.musteri_id
                ORDER BY mk.musteri_id, mk.id DESC
            )
            SELECT c.gid,
                   COUNT(*)::int AS child_count,
                   ARRAY_AGG(c.mid)::int[] AS mids,
                   COALESCE(SUM(COALESCE(fm.borc, 0)), 0) AS borc_total,
                   COALESCE(SUM(COALESCE(tm.alacak, 0)), 0) AS alacak_total,
                   COALESCE(SUM(COALESCE(fm.borc, 0)), 0) - COALESCE(SUM(COALESCE(tm.alacak, 0)), 0) AS net_balance,
                   COALESCE(MAX(EXTRACT(DAY FROM kl.soz_bas)::int), 0) AS sozlesme_gun
            FROM children c
            LEFT JOIN f_by_mid fm ON fm.mid = c.mid
            LEFT JOIN t_by_mid tm ON tm.mid = c.mid
            LEFT JOIN kyc_last kl ON kl.musteri_id = c.mid
            GROUP BY c.gid
            $_$;


--
-- Name: fn_group_financial_aggregate(integer[], text[], boolean, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_group_financial_aggregate(p_group_ids integer[], p_parent_uuids text[], p_include_passive boolean DEFAULT false, p_include_sozlesme_gun boolean DEFAULT true) RETURNS TABLE(gid integer, child_count integer, mids integer[], borc_total numeric, alacak_total numeric, net_balance numeric, sozlesme_gun integer)
    LANGUAGE sql
    AS $_$
            WITH parents AS (
                SELECT t.gid, t.puid::uuid AS puid
                FROM unnest(p_group_ids, p_parent_uuids) AS t(gid, puid)
            ),
            children AS (
                SELECT p.gid, c.id AS mid
                FROM parents p
                JOIN customers c
                  ON c.parent_id IS NOT NULL
                 AND c.parent_id = p.puid
                WHERE p_include_passive = TRUE
                   OR (
                        COALESCE(c.is_active, TRUE) = TRUE
                        AND (
                            c.durum IS NULL
                            OR TRIM(COALESCE(c.durum, '')) = ''
                            OR LOWER(TRIM(c.durum)) NOT IN (
                                'pasif', 'terk', 'kapandi', 'kapandı', 'kapalı', 'kapali', 'kapanmış', 'kapanmis'
                            )
                        )
                   )
            ),
            f_by_mid AS (
                SELECT f.musteri_id AS mid,
                       COALESCE(SUM(COALESCE(f.toplam, f.tutar, 0)), 0) AS borc
                FROM faturalar f
                JOIN children c ON c.mid = f.musteri_id
                WHERE (
                    f.notlar IS NULL OR NOT (
                        regexp_replace(COALESCE(f.notlar, ''), '[İIıi]', 'I', 'g')
                        ~* 'GIB[[:space:]]+DURUM[[:space:]]*:[[:space:]]+TASLAK'
                    )
                )
                GROUP BY f.musteri_id
            ),
            t_by_mid AS (
                SELECT t.musteri_id AS mid,
                       COALESCE(SUM(t.tutar), 0) AS alacak
                FROM tahsilatlar t
                JOIN children c ON c.mid = t.musteri_id
                GROUP BY t.musteri_id
            ),
            kyc_last AS (
                SELECT DISTINCT ON (mk.musteri_id)
                       mk.musteri_id,
                       CASE
                           WHEN mk.sozlesme_tarihi IS NULL THEN NULL
                           WHEN BTRIM(mk.sozlesme_tarihi::text) = '' THEN NULL
                           WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                               THEN (SUBSTRING(BTRIM(mk.sozlesme_tarihi::text) FROM 1 FOR 10))::date
                           WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}'
                               THEN TO_DATE(REGEXP_REPLACE(BTRIM(mk.sozlesme_tarihi::text), ' .*$', ''), 'DD.MM.YYYY')
                           WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{1,2}-[0-9]{1,2}-[0-9]{4}'
                               THEN TO_DATE(REGEXP_REPLACE(BTRIM(mk.sozlesme_tarihi::text), ' .*$', ''), 'DD-MM-YYYY')
                           ELSE NULL
                       END AS soz_bas
                FROM musteri_kyc mk
                JOIN children c ON c.mid = mk.musteri_id
                WHERE p_include_sozlesme_gun = TRUE
                ORDER BY mk.musteri_id, mk.id DESC
            )
            SELECT c.gid,
                   COUNT(*)::int AS child_count,
                   ARRAY_AGG(c.mid)::int[] AS mids,
                   COALESCE(SUM(COALESCE(fm.borc, 0)), 0) AS borc_total,
                   COALESCE(SUM(COALESCE(tm.alacak, 0)), 0) AS alacak_total,
                   COALESCE(SUM(COALESCE(fm.borc, 0)), 0) - COALESCE(SUM(COALESCE(tm.alacak, 0)), 0) AS net_balance,
                   CASE
                       WHEN p_include_sozlesme_gun THEN COALESCE(MAX(EXTRACT(DAY FROM kl.soz_bas)::int), 0)
                       ELSE 0
                   END AS sozlesme_gun
            FROM children c
            LEFT JOIN f_by_mid fm ON fm.mid = c.mid
            LEFT JOIN t_by_mid tm ON tm.mid = c.mid
            LEFT JOIN kyc_last kl ON kl.musteri_id = c.mid
            GROUP BY c.gid
            $_$;


--
-- Name: fn_update_customer_balance(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_update_customer_balance() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                v_borc NUMERIC(14,2);
                v_alacak NUMERIC(14,2);
                v_mid INTEGER;
            BEGIN
                v_mid := COALESCE(NEW.musteri_id, OLD.musteri_id);
                IF v_mid IS NULL THEN
                    RETURN NULL;
                END IF;
                SELECT COALESCE(SUM(COALESCE(toplam, tutar, 0)), 0)
                  INTO v_borc
                  FROM faturalar
                 WHERE musteri_id = v_mid;
                SELECT COALESCE(SUM(tutar), 0)
                  INTO v_alacak
                  FROM tahsilatlar
                 WHERE musteri_id = v_mid;
                UPDATE customers
                   SET current_balance = COALESCE(v_borc,0) - COALESCE(v_alacak,0)
                 WHERE id = v_mid;
                RETURN NULL;
            END;
            $$;


--
-- Name: fn_update_customer_balance(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_update_customer_balance(p_musteri_id integer) RETURNS void
    LANGUAGE plpgsql
    AS $$
            DECLARE
                v_borc NUMERIC(14,2);
                v_alacak NUMERIC(14,2);
            BEGIN
                IF p_musteri_id IS NULL THEN
                    RETURN;
                END IF;
                SELECT COALESCE(SUM(COALESCE(toplam, tutar, 0)), 0)
                  INTO v_borc
                  FROM faturalar
                 WHERE musteri_id = p_musteri_id;
                SELECT COALESCE(SUM(tutar), 0)
                  INTO v_alacak
                  FROM tahsilatlar
                 WHERE musteri_id = p_musteri_id;
                UPDATE customers
                   SET current_balance = COALESCE(v_borc,0) - COALESCE(v_alacak,0)
                 WHERE id = p_musteri_id;
            END;
            $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: akbank_dekont_musteri_map; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.akbank_dekont_musteri_map (
    sender_key text NOT NULL,
    musteri_id integer NOT NULL,
    ornek_aciklama text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: akbank_import_dosyalar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.akbank_import_dosyalar (
    id integer NOT NULL,
    ad_gosterim text NOT NULL,
    orijinal_filename text,
    yuklenme_tarihi timestamp with time zone DEFAULT now() NOT NULL,
    excel_binary bytea NOT NULL
);


--
-- Name: akbank_import_dosyalar_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.akbank_import_dosyalar_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: akbank_import_dosyalar_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.akbank_import_dosyalar_id_seq OWNED BY public.akbank_import_dosyalar.id;


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id integer NOT NULL,
    tablo_adi text,
    kayit_id integer,
    islem text,
    eski_deger text,
    yeni_deger text,
    user_id integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;


--
-- Name: auto_invoice_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auto_invoice_items (
    id integer NOT NULL,
    run_id integer,
    musteri_id integer,
    fatura_id integer,
    period_key text,
    status text DEFAULT 'created'::text,
    gib_uuid text,
    error_message text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: auto_invoice_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.auto_invoice_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: auto_invoice_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.auto_invoice_items_id_seq OWNED BY public.auto_invoice_items.id;


--
-- Name: auto_invoice_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auto_invoice_runs (
    id integer NOT NULL,
    period_key text NOT NULL,
    run_date date NOT NULL,
    status text DEFAULT 'running'::text,
    started_at timestamp with time zone DEFAULT now(),
    finished_at timestamp with time zone,
    success_count integer DEFAULT 0,
    fail_count integer DEFAULT 0,
    message text
);


--
-- Name: auto_invoice_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.auto_invoice_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: auto_invoice_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.auto_invoice_runs_id_seq OWNED BY public.auto_invoice_runs.id;


--
-- Name: auto_invoice_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auto_invoice_settings (
    id integer NOT NULL,
    enabled boolean DEFAULT false,
    run_day integer DEFAULT 1,
    run_hour integer DEFAULT 9,
    send_gib boolean DEFAULT false,
    auto_sms_code text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: auto_invoice_settings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.auto_invoice_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: auto_invoice_settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.auto_invoice_settings_id_seq OWNED BY public.auto_invoice_settings.id;


--
-- Name: banka_hareketler; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.banka_hareketler (
    id integer NOT NULL,
    hesap_id integer NOT NULL,
    tarih text NOT NULL,
    aciklama text,
    tutar real NOT NULL,
    bakiye real,
    tip text DEFAULT 'alacak'::text,
    referans text,
    gonderen text,
    eslestirme_durumu text DEFAULT 'eslesmedi'::text,
    musteri_id integer,
    tahsilat_id integer,
    kaynak_dosya text,
    created_at text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: banka_hareketler_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.banka_hareketler_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: banka_hareketler_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.banka_hareketler_id_seq OWNED BY public.banka_hareketler.id;


--
-- Name: banka_hareketleri; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.banka_hareketleri (
    id integer NOT NULL,
    banka_hesap_id integer NOT NULL,
    hareket_tarihi date NOT NULL,
    aciklama text,
    gonderici text,
    tutar numeric(14,2) NOT NULL,
    tip text DEFAULT 'gelen'::text,
    durum text DEFAULT 'bekleyen'::text,
    musteri_id integer,
    tahsilat_id integer,
    created_at timestamp with time zone DEFAULT now(),
    referans_no text,
    bakiye_ekstre numeric(14,2),
    kaynak_banka_adi text
);


--
-- Name: banka_hareketleri_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.banka_hareketleri_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: banka_hareketleri_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.banka_hareketleri_id_seq OWNED BY public.banka_hareketleri.id;


--
-- Name: banka_hesaplar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.banka_hesaplar (
    id integer NOT NULL,
    banka_adi text NOT NULL,
    hesap_no text,
    iban text,
    para_birimi text DEFAULT 'TRY'::text,
    bakiye numeric(14,2) DEFAULT 0,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    hesap_adi text,
    sube text
);


--
-- Name: banka_hesaplar_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.banka_hesaplar_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: banka_hesaplar_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.banka_hesaplar_id_seq OWNED BY public.banka_hesaplar.id;


--
-- Name: cari_belgeler; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cari_belgeler (
    id integer NOT NULL,
    musteri_id integer NOT NULL,
    belge_turu text NOT NULL,
    dosya_adi text,
    dosya_yolu text,
    versiyon integer DEFAULT 1,
    yukleyen_id integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: cari_belgeler_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cari_belgeler_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cari_belgeler_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cari_belgeler_id_seq OWNED BY public.cari_belgeler.id;


--
-- Name: contract_installments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contract_installments (
    id integer NOT NULL,
    contract_id integer NOT NULL,
    musteri_id integer NOT NULL,
    taksit_no integer NOT NULL,
    vade_tarihi date NOT NULL,
    tutar numeric(12,2) NOT NULL,
    odeme_durumu text DEFAULT 'planlandi'::text,
    odenen_tutar numeric(12,2) DEFAULT 0,
    kalan_tutar numeric(12,2) DEFAULT 0,
    tahakkuk_tarihi date,
    odeme_tarihi date,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: contract_installments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contract_installments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contract_installments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contract_installments_id_seq OWNED BY public.contract_installments.id;


--
-- Name: contracts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contracts (
    id integer NOT NULL,
    musteri_id integer NOT NULL,
    cari_kodu text,
    sozlesme_no text,
    baslangic_tarihi date NOT NULL,
    bitis_tarihi date,
    sure_ay integer,
    aylik_kira numeric(12,2) NOT NULL,
    toplam_tutar numeric(14,2),
    para_birimi text DEFAULT 'TRY'::text,
    odeme_gunu integer,
    depozito numeric(12,2),
    gecikme_faizi_orani numeric(6,2),
    yillik_artis_orani numeric(6,2),
    muacceliyet_var boolean DEFAULT false,
    durum text DEFAULT 'aktif'::text,
    aciklama text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: contracts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contracts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contracts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contracts_id_seq OWNED BY public.contracts.id;


--
-- Name: crm_leads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.crm_leads (
    id integer NOT NULL,
    ad_soyad text NOT NULL,
    firma_adi text,
    telefon text,
    email text,
    sektor text,
    hizmet_turu text,
    lead_durumu text,
    lead_skoru integer DEFAULT 0,
    ilk_gorusme date,
    son_gorusme date,
    takip_tarihi date,
    sorumlu_satis text,
    notlar text
);


--
-- Name: crm_leads_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.crm_leads_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: crm_leads_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.crm_leads_id_seq OWNED BY public.crm_leads.id;


--
-- Name: customer_financial_profile; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_financial_profile (
    id integer NOT NULL,
    musteri_id integer NOT NULL,
    risk_limit numeric(14,2),
    vade_gunu integer DEFAULT 5,
    odeme_tercihi text,
    gecikme_faiz_orani numeric(6,2),
    stopaj_durumu text,
    tahmini_odeme_gunu integer,
    yillik_karlilik_endeksi numeric(12,2),
    hukuki_esk_puan integer DEFAULT 0,
    mutabakat_tarihi date,
    ic_not text,
    hukuki_surec text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: customer_financial_profile_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_financial_profile_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_financial_profile_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customer_financial_profile_id_seq OWNED BY public.customer_financial_profile.id;


--
-- Name: customers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customers (
    id integer NOT NULL,
    name text NOT NULL,
    email text,
    phone text,
    address text,
    tax_number text,
    rent_start_date text,
    rent_start_year integer,
    rent_start_month text DEFAULT 'Ocak'::text,
    ilk_kira_bedeli real DEFAULT 0 NOT NULL,
    current_rent real DEFAULT 0 NOT NULL,
    office_code text,
    created_at text DEFAULT CURRENT_TIMESTAMP,
    updated_at text DEFAULT CURRENT_TIMESTAMP,
    notes text,
    ev_adres text,
    durum text,
    guncel_kira_bedeli numeric(12,2) DEFAULT 0,
    yetkili_kisi text,
    hizmet_turu text,
    manuel_borc numeric(12,2),
    son_odeme_tarihi date,
    vergi_dairesi text,
    mersis_no text,
    nace_kodu text,
    ofis_tipi text,
    tebligat_adresi text,
    current_balance numeric(14,2) DEFAULT 0,
    musteri_adi text,
    kapanis_tarihi date,
    phone2 text,
    yetkili_tcno text,
    reel_kira_bedeli numeric(12,2) DEFAULT 0,
    is_active boolean DEFAULT true,
    musteri_no integer,
    hazir_ofis_oda_no integer,
    parent_id uuid,
    is_group boolean DEFAULT false,
    bizim_hesap boolean DEFAULT false NOT NULL,
    grup2_secimleri text[] DEFAULT ARRAY[]::text[] NOT NULL,
    kapanis_sonrasi_borc_ay smallint,
    calisma_sekli text DEFAULT 'sirali'::text,
    arsivli boolean DEFAULT false NOT NULL,
    arsiv_nedeni text,
    arsiv_at timestamp with time zone,
    arsiv_kanonik_id integer
);


--
-- Name: customers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customers_id_seq OWNED BY public.customers.id;


--
-- Name: customers_musteri_no_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customers_musteri_no_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dashboard_kisayollar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dashboard_kisayollar (
    slot_key text NOT NULL,
    label text NOT NULL,
    url text,
    icon text NOT NULL,
    sira integer DEFAULT 0 NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: dashboard_kisayollar_user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dashboard_kisayollar_user (
    user_id integer NOT NULL,
    slot_key text NOT NULL,
    label text,
    url text,
    icon text,
    sira integer,
    gorunur_mu boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: devam; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.devam (
    id integer NOT NULL,
    personel_id integer,
    tarih text,
    giris_saati text,
    cikis_saati text,
    gec_kaldi integer DEFAULT 0,
    gec_dakika integer DEFAULT 0,
    created_at text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: devam_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.devam_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: devam_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.devam_id_seq OWNED BY public.devam.id;


--
-- Name: devam_kayitlari; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.devam_kayitlari (
    id integer NOT NULL,
    personel_id integer NOT NULL,
    ad_soyad character varying(100),
    tarih date DEFAULT CURRENT_DATE NOT NULL,
    giris_saati time without time zone,
    cikis_saati time without time zone,
    durum character varying(20) DEFAULT 'giris'::character varying,
    gec_dakika integer DEFAULT 0,
    kaynak character varying(20) DEFAULT 'qr'::character varying,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: devam_kayitlari_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.devam_kayitlari_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: devam_kayitlari_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.devam_kayitlari_id_seq OWNED BY public.devam_kayitlari.id;


--
-- Name: duzenli_fatura_secenekleri; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.duzenli_fatura_secenekleri (
    id integer NOT NULL,
    kod text NOT NULL,
    etiket text NOT NULL,
    sira integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: duzenli_fatura_secenekleri_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.duzenli_fatura_secenekleri_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: duzenli_fatura_secenekleri_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.duzenli_fatura_secenekleri_id_seq OWNED BY public.duzenli_fatura_secenekleri.id;


--
-- Name: fatura_kalemleri; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fatura_kalemleri (
    id integer NOT NULL,
    fatura_id integer NOT NULL,
    aciklama text NOT NULL,
    miktar real DEFAULT 1,
    birim text DEFAULT 'Adet'::text,
    birim_fiyat real DEFAULT 0,
    iskonto_oran real DEFAULT 0,
    kdv_oran real DEFAULT 20,
    matrah real DEFAULT 0,
    kdv_tutar real DEFAULT 0,
    toplam real DEFAULT 0
);


--
-- Name: fatura_kalemleri_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fatura_kalemleri_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fatura_kalemleri_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fatura_kalemleri_id_seq OWNED BY public.fatura_kalemleri.id;


--
-- Name: fatura_tahsilat; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fatura_tahsilat (
    id integer NOT NULL,
    fatura_id integer NOT NULL,
    tarih text NOT NULL,
    tutar real NOT NULL,
    odeme_sekli text DEFAULT 'Banka'::text,
    aciklama text
);


--
-- Name: fatura_tahsilat_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fatura_tahsilat_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fatura_tahsilat_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fatura_tahsilat_id_seq OWNED BY public.fatura_tahsilat.id;


--
-- Name: faturalandirilacak_hizmetler; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.faturalandirilacak_hizmetler (
    id integer NOT NULL,
    kaynak text DEFAULT 'randevu'::text NOT NULL,
    kaynak_id integer NOT NULL,
    musteri_id integer,
    aciklama text,
    tutar numeric(12,2) DEFAULT 0,
    islendi boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: faturalandirilacak_hizmetler_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.faturalandirilacak_hizmetler_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: faturalandirilacak_hizmetler_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.faturalandirilacak_hizmetler_id_seq OWNED BY public.faturalandirilacak_hizmetler.id;


--
-- Name: faturalar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.faturalar (
    id integer NOT NULL,
    fatura_no text NOT NULL,
    musteri_id integer,
    musteri_adi text NOT NULL,
    musteri_vkn text,
    musteri_adres text,
    fatura_tarihi text NOT NULL,
    vade_tarihi text,
    fatura_turu text DEFAULT 'SATIŞ'::text,
    durum text DEFAULT 'taslak'::text,
    toplam_matrah real DEFAULT 0,
    toplam_kdv real DEFAULT 0,
    toplam_iskonto real DEFAULT 0,
    genel_toplam real DEFAULT 0,
    not_aciklama text,
    pdf_yolu text,
    created_at text DEFAULT CURRENT_TIMESTAMP,
    tutar numeric(12,2) DEFAULT 0,
    toplam numeric(12,2) DEFAULT 0,
    kdv_tutar numeric(12,2) DEFAULT 0,
    notlar text,
    satirlar_json text,
    sevk_adresi text,
    ettn text,
    yon text DEFAULT 'giden'::text
);


--
-- Name: faturalar_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.faturalar_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: faturalar_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.faturalar_id_seq OWNED BY public.faturalar.id;


--
-- Name: firma_ayar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.firma_ayar (
    id integer DEFAULT 1 NOT NULL,
    firma_adi text,
    firma_vkn text,
    firma_adres text,
    firma_tel text,
    firma_vergi_dairesi text,
    fatura_seri text DEFAULT 'EA'::text,
    baslangic_no integer DEFAULT 1
);


--
-- Name: gib_portal_liste_satir; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gib_portal_liste_satir (
    satir_anahtar text NOT NULL,
    ettn text,
    fatura_no text,
    fatura_tarihi date,
    musteri_adi text,
    tutar double precision,
    onay_durumu text,
    gib_durum text,
    satir_json text NOT NULL,
    guncelleme_ts timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: gib_portal_sync_aralik; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gib_portal_sync_aralik (
    bas_date date NOT NULL,
    bit_date date NOT NULL,
    son_gib_cekim timestamp with time zone DEFAULT now() NOT NULL,
    satir_sayisi integer DEFAULT 0 NOT NULL
);


--
-- Name: giris_alanlar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.giris_alanlar (
    id integer NOT NULL,
    alan_kodu text NOT NULL,
    alan_adi text NOT NULL,
    kategori text NOT NULL,
    zorunlu integer DEFAULT 1,
    aktif integer DEFAULT 1,
    sira integer DEFAULT 0
);


--
-- Name: giris_alanlar_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.giris_alanlar_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: giris_alanlar_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.giris_alanlar_id_seq OWNED BY public.giris_alanlar.id;


--
-- Name: grup2_etiketleri; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.grup2_etiketleri (
    id integer NOT NULL,
    slug text NOT NULL,
    etiket text NOT NULL,
    sira integer DEFAULT 0 NOT NULL,
    aktif boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: grup2_etiketleri_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.grup2_etiketleri_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: grup2_etiketleri_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.grup2_etiketleri_id_seq OWNED BY public.grup2_etiketleri.id;


--
-- Name: hizmet_turleri; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hizmet_turleri (
    id integer NOT NULL,
    ad text NOT NULL,
    sira integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: hizmet_turleri_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hizmet_turleri_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hizmet_turleri_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hizmet_turleri_id_seq OWNED BY public.hizmet_turleri.id;


--
-- Name: iletisim_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.iletisim_log (
    id integer NOT NULL,
    musteri_id integer NOT NULL,
    kanal text NOT NULL,
    konu text,
    icerik text,
    personel_id integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: iletisim_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.iletisim_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: iletisim_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.iletisim_log_id_seq OWNED BY public.iletisim_log.id;


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoices (
    id integer NOT NULL,
    invoice_number text NOT NULL,
    customer_id integer,
    issue_date text DEFAULT CURRENT_TIMESTAMP,
    total_amount real DEFAULT 0 NOT NULL,
    created_at text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: invoices_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.invoices_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: invoices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.invoices_id_seq OWNED BY public.invoices.id;


--
-- Name: kargo_resimler; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kargo_resimler (
    id integer NOT NULL,
    kargo_id integer NOT NULL,
    dosya_yolu text NOT NULL,
    dosya_adi text,
    created_at text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: kargo_resimler_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kargo_resimler_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kargo_resimler_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kargo_resimler_id_seq OWNED BY public.kargo_resimler.id;


--
-- Name: kargolar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kargolar (
    id integer NOT NULL,
    musteri_id integer NOT NULL,
    tarih text NOT NULL,
    teslim_alan text,
    kargo_firmasi text,
    takip_no text,
    notlar text,
    whatsapp_gonderildi integer DEFAULT 0,
    odeme_tutari real DEFAULT 0,
    odeme_durumu text DEFAULT 'odenmedi'::text,
    fatura_id integer,
    created_at text DEFAULT CURRENT_TIMESTAMP,
    durum text DEFAULT 'beklemede'::text
);


--
-- Name: kargolar_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kargolar_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kargolar_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kargolar_id_seq OWNED BY public.kargolar.id;


--
-- Name: kyc_belgeler; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kyc_belgeler (
    id integer NOT NULL,
    kyc_id integer NOT NULL,
    belge_tipi text,
    dosya_yolu text NOT NULL,
    dosya_adi text,
    yuklenme_tarihi text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: kyc_belgeler_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kyc_belgeler_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kyc_belgeler_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kyc_belgeler_id_seq OWNED BY public.kyc_belgeler.id;


--
-- Name: legal_cases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.legal_cases (
    id integer NOT NULL,
    musteri_id integer NOT NULL,
    contract_id integer,
    durum text,
    aciklama text,
    toplam_borc numeric(14,2),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: legal_cases_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.legal_cases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: legal_cases_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.legal_cases_id_seq OWNED BY public.legal_cases.id;


--
-- Name: ledger_parties; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ledger_parties (
    id bigint NOT NULL,
    name text NOT NULL,
    type text DEFAULT 'person'::text NOT NULL,
    phone text,
    email text,
    country text,
    notes text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ledger_parties_name_chk CHECK ((length(TRIM(BOTH FROM name)) > 0)),
    CONSTRAINT ledger_parties_type_chk CHECK ((type = ANY (ARRAY['person'::text, 'company'::text])))
);


--
-- Name: ledger_parties_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ledger_parties_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ledger_parties_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ledger_parties_id_seq OWNED BY public.ledger_parties.id;


--
-- Name: ledger_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ledger_transactions (
    id bigint NOT NULL,
    party_id bigint NOT NULL,
    direction text NOT NULL,
    amount numeric(18,2) NOT NULL,
    currency text DEFAULT 'TRY'::text NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    note text,
    created_by integer,
    is_void boolean DEFAULT false NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ledger_transactions_amount_chk CHECK ((amount > (0)::numeric)),
    CONSTRAINT ledger_transactions_currency_chk CHECK ((currency ~ '^[A-Z]{3}$'::text)),
    CONSTRAINT ledger_transactions_direction_chk CHECK ((direction = ANY (ARRAY['give'::text, 'receive'::text])))
);


--
-- Name: ledger_transactions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ledger_transactions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ledger_transactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ledger_transactions_id_seq OWNED BY public.ledger_transactions.id;


--
-- Name: ledger_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ledger_groups (
    id bigint NOT NULL,
    name text NOT NULL,
    notes text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ledger_groups_name_chk CHECK ((length(TRIM(BOTH FROM name)) > 0))
);


--
-- Name: ledger_groups_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ledger_groups_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ledger_groups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ledger_groups_id_seq OWNED BY public.ledger_groups.id;


--
-- Name: ledger_group_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ledger_group_members (
    id bigint NOT NULL,
    group_id bigint NOT NULL,
    party_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ledger_group_members_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ledger_group_members_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ledger_group_members_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ledger_group_members_id_seq OWNED BY public.ledger_group_members.id;


--
-- Name: ledger_reminders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ledger_reminders (
    id bigint NOT NULL,
    party_id bigint NOT NULL,
    due_at timestamp with time zone NOT NULL,
    note text,
    status text DEFAULT 'pending'::text NOT NULL,
    channel text DEFAULT 'in_app'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ledger_reminders_channel_chk CHECK ((channel = ANY (ARRAY['email'::text, 'in_app'::text]))),
    CONSTRAINT ledger_reminders_status_chk CHECK ((status = ANY (ARRAY['pending'::text, 'sent'::text, 'dismissed'::text])))
);


--
-- Name: ledger_reminders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ledger_reminders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ledger_reminders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ledger_reminders_id_seq OWNED BY public.ledger_reminders.id;


--
-- Name: ledger_transaction_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ledger_transaction_attachments (
    id bigint NOT NULL,
    transaction_id bigint NOT NULL,
    object_key text NOT NULL,
    content_type text NOT NULL,
    byte_size integer NOT NULL,
    original_filename text,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    CONSTRAINT ledger_tx_attach_byte_size_chk CHECK (((byte_size > 0) AND (byte_size <= 5242880))),
    CONSTRAINT ledger_tx_attach_content_type_chk CHECK ((content_type = ANY (ARRAY['image/jpeg'::text, 'image/png'::text, 'image/webp'::text]))),
    CONSTRAINT ledger_tx_attach_object_key_chk CHECK (((length(TRIM(BOTH FROM object_key)) > 0) AND (length(object_key) <= 1024) AND (POSITION(('..'::text) IN object_key) = 0))),
    CONSTRAINT ledger_tx_attach_original_filename_chk CHECK (((original_filename IS NULL) OR ((length(TRIM(BOTH FROM original_filename)) > 0) AND (length(original_filename) <= 255))))
);


--
-- Name: ledger_transaction_attachments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ledger_transaction_attachments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ledger_transaction_attachments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ledger_transaction_attachments_id_seq OWNED BY public.ledger_transaction_attachments.id;


--
-- Name: ledger_registered_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ledger_registered_assets (
    id bigint NOT NULL,
    code text NOT NULL,
    label text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ledger_registered_assets_code_chk CHECK ((code ~ '^[A-Z]{3}$'::text)),
    CONSTRAINT ledger_registered_assets_code_uq UNIQUE (code),
    CONSTRAINT ledger_registered_assets_label_chk CHECK (((label IS NULL) OR ((length(TRIM(BOTH FROM label)) > 0) AND (length(label) <= 64))))
);


--
-- Name: ledger_registered_assets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ledger_registered_assets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ledger_registered_assets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ledger_registered_assets_id_seq OWNED BY public.ledger_registered_assets.id;


--
-- Name: masraflar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.masraflar (
    id integer NOT NULL,
    magaza_adi text,
    fis_no text,
    tarih date,
    toplam_tutar numeric(14,2),
    kdv_orani numeric(6,2),
    kdv_tutari numeric(14,2),
    urunler_json text,
    kategori text,
    fis_gorsel_path text,
    durum text DEFAULT 'onay_bekliyor'::text NOT NULL,
    ai_ham_yanit text,
    olusturan_kullanici_id integer,
    olusturan_kullanici text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone,
    fis_gorsel_hash text
);


--
-- Name: masraflar_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.masraflar_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: masraflar_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.masraflar_id_seq OWNED BY public.masraflar.id;


--
-- Name: mukerrer_arsiv_batch; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mukerrer_arsiv_batch (
    id integer NOT NULL,
    user_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    group_key text NOT NULL,
    tier text,
    kanonik_id integer NOT NULL,
    archived_ids integer[] NOT NULL,
    payload_json jsonb,
    undone_at timestamp with time zone,
    undone_by integer
);


--
-- Name: mukerrer_arsiv_batch_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mukerrer_arsiv_batch_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mukerrer_arsiv_batch_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mukerrer_arsiv_batch_id_seq OWNED BY public.mukerrer_arsiv_batch.id;


--
-- Name: musteri_aylik_grid_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.musteri_aylik_grid_cache (
    musteri_id integer NOT NULL,
    payload text NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: musteri_kyc; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.musteri_kyc (
    id integer NOT NULL,
    musteri_id integer,
    sirket_unvani text,
    vergi_no text,
    vergi_dairesi text,
    mersis_no text,
    ticaret_sicil_no text,
    kurulus_tarihi text,
    faaliyet_konusu text,
    nace_kodu text,
    eski_adres text,
    yeni_adres text,
    sube_merkez text DEFAULT 'Merkez'::text,
    yetkili_adsoyad text,
    yetkili_tcno text,
    yetkili_dogum text,
    yetkili_ikametgah text,
    yetkili_tel text,
    yetkili_tel2 text,
    yetkili_email text,
    ortak1_adsoyad text,
    ortak1_pay text,
    ortak2_adsoyad text,
    ortak2_pay text,
    ortak3_adsoyad text,
    ortak3_pay text,
    yabanci_adsoyad text,
    yabanci_uyruk text,
    yabanci_pasaport text,
    hizmet_turu text DEFAULT 'Sanal Ofis'::text,
    ofis_kodu text,
    aylik_kira real DEFAULT 0,
    yillik_kira real DEFAULT 0,
    sozlesme_no text,
    sozlesme_tarihi text,
    sozlesme_bitis text,
    evrak_imza_sirkuleri integer DEFAULT 0,
    evrak_vergi_levhasi integer DEFAULT 0,
    evrak_ticaret_sicil integer DEFAULT 0,
    evrak_faaliyet_belgesi integer DEFAULT 0,
    evrak_kimlik_fotokopi integer DEFAULT 0,
    evrak_ikametgah integer DEFAULT 0,
    evrak_kase integer DEFAULT 0,
    notlar text,
    tamamlanma_yuzdesi integer DEFAULT 0,
    created_at text DEFAULT CURRENT_TIMESTAMP,
    updated_at text DEFAULT CURRENT_TIMESTAMP,
    unvan text,
    email text,
    musteri_adi text,
    kira_artis_tarihi date,
    kira_suresi_ay integer,
    kira_nakit boolean DEFAULT false,
    duzenli_fatura text,
    yetkili_tel_aciklama text,
    yetkili_tel2_aciklama text,
    kdv_oran numeric(8,2) DEFAULT 20,
    hazir_ofis_oda_no integer,
    kira_banka boolean DEFAULT false,
    kira_nakit_tutar numeric(14,2),
    kira_banka_tutar numeric(14,2),
    odeme_duzeni text,
    odeme_duzeni_manuel text,
    uyruk text DEFAULT 'TC'::text
);


--
-- Name: musteri_kyc_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.musteri_kyc_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: musteri_kyc_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.musteri_kyc_id_seq OWNED BY public.musteri_kyc.id;


--
-- Name: musteri_reel_donem_tutar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.musteri_reel_donem_tutar (
    musteri_id integer NOT NULL,
    donem_yil integer NOT NULL,
    tutar_kdv_dahil numeric(14,2) NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    giris_tip text,
    giris_tutar numeric(14,2),
    hibrit_toplam numeric(14,2),
    hibrit_net numeric(14,2),
    hibrit_banka numeric(14,2)
);


--
-- Name: musteri_tahsilat_panel_detay; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.musteri_tahsilat_panel_detay (
    musteri_id integer NOT NULL,
    by_iso text DEFAULT '{}'::text NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: musteri_tahsilat_panel_detay_backup_20260617; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.musteri_tahsilat_panel_detay_backup_20260617 (
    musteri_id integer,
    by_iso text,
    updated_at timestamp without time zone
);


--
-- Name: office_rentals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.office_rentals (
    id integer NOT NULL,
    ofis_turu text NOT NULL,
    baslik text,
    il text,
    ilce text,
    adres text,
    aylik_fiyat numeric(12,2) DEFAULT 0,
    para_birimi text DEFAULT 'TRY'::text,
    yasal_adres boolean DEFAULT false,
    sekreterya_karsilama boolean DEFAULT false,
    posta_takibi boolean DEFAULT false,
    toplanti_odasi boolean DEFAULT false,
    aciklama text,
    aciklama_ai text,
    eids_yetki_no text,
    status text DEFAULT 'taslak'::text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    sinirsiz_cay_kahve boolean DEFAULT false,
    fiber_internet boolean DEFAULT false,
    numara_0850_tahsisi boolean DEFAULT false,
    anlik_bildirim_sistemi boolean DEFAULT false,
    misafir_agirlama boolean DEFAULT false,
    mutfak_erisimi boolean DEFAULT false,
    temizlik_hizmeti boolean DEFAULT false
);


--
-- Name: office_rentals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.office_rentals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: office_rentals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.office_rentals_id_seq OWNED BY public.office_rentals.id;


--
-- Name: offices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.offices (
    id integer NOT NULL,
    code text NOT NULL,
    type text NOT NULL,
    unit_no text,
    monthly_price real DEFAULT 0,
    status text DEFAULT 'bos'::text,
    is_active integer DEFAULT 1,
    customer_id integer,
    notes text,
    created_at text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: offices_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.offices_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: offices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.offices_id_seq OWNED BY public.offices.id;


--
-- Name: personel; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personel (
    id integer NOT NULL,
    ad_soyad text NOT NULL,
    pozisyon text,
    telefon text,
    email text,
    giris_tarihi date,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    mesai_baslangic text DEFAULT '09:00'::text,
    mac_adres text,
    notlar text,
    mesai_bitis text
);


--
-- Name: personel_bilgi; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personel_bilgi (
    personel_id integer NOT NULL,
    ise_baslama_tarihi text,
    yillik_izin_hakki integer DEFAULT 14,
    manuel_izin_gun integer DEFAULT 0,
    unvan text,
    departman text,
    tc_no text,
    dogum_tarihi date
);


--
-- Name: personel_hareketleri; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personel_hareketleri (
    id integer NOT NULL,
    personel_id integer NOT NULL,
    tarih date NOT NULL,
    saat time without time zone NOT NULL,
    tip character varying(20) NOT NULL,
    kaynak character varying(20) DEFAULT 'qr'::character varying,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: personel_hareketleri_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.personel_hareketleri_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: personel_hareketleri_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.personel_hareketleri_id_seq OWNED BY public.personel_hareketleri.id;


--
-- Name: personel_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.personel_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: personel_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.personel_id_seq OWNED BY public.personel.id;


--
-- Name: personel_izin; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personel_izin (
    id integer NOT NULL,
    personel_id integer NOT NULL,
    izin_turu text NOT NULL,
    baslangic_tarihi text NOT NULL,
    bitis_tarihi text NOT NULL,
    gun_sayisi real DEFAULT 1 NOT NULL,
    yari_gun integer DEFAULT 0,
    aciklama text,
    onay_durumu text DEFAULT 'bekliyor'::text,
    created_at text DEFAULT CURRENT_TIMESTAMP,
    saat_sayisi numeric(4,1) DEFAULT 0
);


--
-- Name: personel_izin_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.personel_izin_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: personel_izin_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.personel_izin_id_seq OWNED BY public.personel_izin.id;


--
-- Name: personel_ozluk; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personel_ozluk (
    personel_id integer NOT NULL,
    tc_kimlik text,
    dogum_tarihi date,
    dogum_yeri text,
    medeni_durum text,
    esi_calisiyor text,
    cocuk_sayisi integer,
    cinsiyet text,
    kan_grubu text,
    ikametgah text,
    cep_telefon text,
    mac_adres text,
    email text,
    acil_kisi text,
    ise_giris_tarihi date,
    departman text,
    unvan text,
    gorev_tanimi text,
    calisma_sekli text,
    ucret_bilgisi text,
    iban text,
    yemek_yol_yardim text,
    ogrenim_durumu text,
    mezun_okul_bolum text,
    yabanci_dil text,
    adli_sicil text,
    saglik_raporu text,
    ikametgah_belgesi text,
    diploma text,
    nufus_kayit text,
    askerlik_durum text,
    notlar text,
    updated_at timestamp with time zone DEFAULT now(),
    izin_hakedis_gun integer,
    izin_hakedis_saat integer,
    izin_kalan_gun integer,
    izin_kalan_saat integer
);


--
-- Name: personel_yetki; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personel_yetki (
    id integer NOT NULL,
    personel_id integer NOT NULL,
    modul text NOT NULL,
    yetki text DEFAULT 'goruntuleme'::text
);


--
-- Name: personel_yetki_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.personel_yetki_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: personel_yetki_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.personel_yetki_id_seq OWNED BY public.personel_yetki.id;


--
-- Name: personeller; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personeller (
    id integer NOT NULL,
    ad_soyad text NOT NULL,
    pozisyon text,
    telefon text,
    email text,
    aktif integer DEFAULT 1,
    notlar text DEFAULT ''::text,
    created_at text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: personeller_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.personeller_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: personeller_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.personeller_id_seq OWNED BY public.personeller.id;


--
-- Name: potansiyel_musteriler; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.potansiyel_musteriler (
    id integer NOT NULL,
    ad text NOT NULL,
    telefon text,
    paket text,
    gorusme_notu text,
    hatirlatma_tarihi date,
    durum text DEFAULT 'düşünüyor'::text,
    kaynak text,
    converted_customer_id integer,
    last_reminder_sent_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: potansiyel_musteriler_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.potansiyel_musteriler_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: potansiyel_musteriler_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.potansiyel_musteriler_id_seq OWNED BY public.potansiyel_musteriler.id;


--
-- Name: products; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.products (
    id integer NOT NULL,
    name text NOT NULL,
    sku text,
    unit_price real DEFAULT 0 NOT NULL,
    stock_quantity real DEFAULT 0 NOT NULL,
    created_at text DEFAULT CURRENT_TIMESTAMP,
    updated_at text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: products_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.products_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: products_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.products_id_seq OWNED BY public.products.id;


--
-- Name: randevular; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.randevular (
    id integer NOT NULL,
    musteri_id integer NOT NULL,
    randevu_tarihi date NOT NULL,
    saat time without time zone,
    oda text,
    sure_dakika integer,
    ucret numeric(12,2) DEFAULT 0,
    faturalandi boolean DEFAULT false,
    personel_id integer,
    notlar text,
    created_at timestamp with time zone DEFAULT now(),
    baslangic_zamani timestamp with time zone,
    bitis_zamani timestamp with time zone,
    toplam_ucret numeric(12,2) DEFAULT 0,
    pakete_dahil_mi boolean DEFAULT false,
    durum text DEFAULT 'Beklemede'::text,
    oda_adi text,
    randevu_tipi text DEFAULT 'randevu'::text,
    recurrence_rule text,
    recurrence_end_date date,
    parent_id integer,
    reminder_sent boolean DEFAULT false
);


--
-- Name: randevular_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.randevular_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: randevular_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.randevular_id_seq OWNED BY public.randevular.id;


--
-- Name: rent_payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rent_payments (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    year integer NOT NULL,
    month text NOT NULL,
    amount real DEFAULT 0 NOT NULL
);


--
-- Name: rent_payments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rent_payments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rent_payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rent_payments_id_seq OWNED BY public.rent_payments.id;


--
-- Name: sozlesmeler; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sozlesmeler (
    id integer NOT NULL,
    sozlesme_no text NOT NULL,
    kyc_id integer,
    musteri_id integer,
    musteri_adi text,
    hizmet_turu text,
    dosya_yolu text,
    olusturma_tarihi text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: sozlesmeler_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sozlesmeler_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sozlesmeler_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sozlesmeler_id_seq OWNED BY public.sozlesmeler.id;


--
-- Name: tahsilatlar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tahsilatlar (
    id integer NOT NULL,
    customer_id integer NOT NULL,
    tutar real DEFAULT 0 NOT NULL,
    odeme_turu text DEFAULT 'N'::text NOT NULL,
    tahsilat_tarihi text NOT NULL,
    aciklama text,
    created_at text DEFAULT now(),
    musteri_id integer,
    fatura_id integer,
    makbuz_no text,
    cek_detay text,
    havale_banka text,
    banka_referans_no text,
    tahsil_eden text,
    islem_grubu_id text,
    kaynak text
);


--
-- Name: tahsilatlar_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tahsilatlar_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tahsilatlar_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tahsilatlar_id_seq OWNED BY public.tahsilatlar.id;


--
-- Name: tediyeler; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tediyeler (
    id integer NOT NULL,
    musteri_id integer NOT NULL,
    tutar numeric(12,2) NOT NULL,
    odeme_turu text DEFAULT 'nakit'::text NOT NULL,
    tediye_tarihi date DEFAULT CURRENT_DATE NOT NULL,
    aciklama text,
    makbuz_no text,
    cek_detay text,
    havale_banka text,
    tediye_yapan text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tediyeler_tutar_check CHECK ((tutar > (0)::numeric))
);


--
-- Name: tediyeler_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tediyeler_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tediyeler_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tediyeler_id_seq OWNED BY public.tediyeler.id;


--
-- Name: toplanti_odasi_fiyat; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.toplanti_odasi_fiyat (
    oda_adi text NOT NULL,
    saatlik_ucret numeric(12,2) DEFAULT 0 NOT NULL,
    aciklama text
);


--
-- Name: tufe_verileri; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tufe_verileri (
    year integer NOT NULL,
    month text NOT NULL,
    oran real NOT NULL,
    created_at text DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: urunler; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.urunler (
    id integer NOT NULL,
    urun_adi text NOT NULL,
    stok_kodu text NOT NULL,
    birim_fiyat numeric(12,2) DEFAULT 0,
    stok_miktari numeric(14,2) DEFAULT 0,
    birim text DEFAULT 'adet'::text,
    aciklama text,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    kdv_orani integer DEFAULT 20
);


--
-- Name: urunler_id_seq1; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.urunler_id_seq1
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: urunler_id_seq1; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.urunler_id_seq1 OWNED BY public.urunler.id;


--
-- Name: user_ui_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_ui_preferences (
    user_id integer NOT NULL,
    pref_key text NOT NULL,
    pref_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username text NOT NULL,
    password_hash text NOT NULL,
    full_name text,
    role text DEFAULT 'personel'::text NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    last_login timestamp with time zone,
    aktif boolean DEFAULT true,
    son_giris timestamp with time zone,
    security_stamp text NOT NULL,
    email_verified_at timestamp with time zone
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: password_reset_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_reset_tokens (
    id bigint NOT NULL,
    user_id integer NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    request_ip text
);


--
-- Name: password_reset_tokens_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.password_reset_tokens_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: password_reset_tokens_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.password_reset_tokens_id_seq OWNED BY public.password_reset_tokens.id;


--
-- Name: email_verification_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_verification_tokens (
    id bigint NOT NULL,
    user_id integer NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: email_verification_tokens_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.email_verification_tokens_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: email_verification_tokens_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.email_verification_tokens_id_seq OWNED BY public.email_verification_tokens.id;


--
-- Name: web_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.web_users (
    id integer NOT NULL,
    username text NOT NULL,
    email text,
    password_hash text NOT NULL,
    rol text DEFAULT 'goruntuleme'::text NOT NULL,
    aktif boolean DEFAULT true,
    olusturma timestamp with time zone DEFAULT now(),
    son_giris timestamp with time zone
);


--
-- Name: web_users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.web_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: web_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.web_users_id_seq OWNED BY public.web_users.id;


--
-- Name: whatsapp_geciken_haric; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whatsapp_geciken_haric (
    musteri_id integer NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: akbank_import_dosyalar id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.akbank_import_dosyalar ALTER COLUMN id SET DEFAULT nextval('public.akbank_import_dosyalar_id_seq'::regclass);


--
-- Name: audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);


--
-- Name: auto_invoice_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_invoice_items ALTER COLUMN id SET DEFAULT nextval('public.auto_invoice_items_id_seq'::regclass);


--
-- Name: auto_invoice_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_invoice_runs ALTER COLUMN id SET DEFAULT nextval('public.auto_invoice_runs_id_seq'::regclass);


--
-- Name: auto_invoice_settings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_invoice_settings ALTER COLUMN id SET DEFAULT nextval('public.auto_invoice_settings_id_seq'::regclass);


--
-- Name: banka_hareketler id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banka_hareketler ALTER COLUMN id SET DEFAULT nextval('public.banka_hareketler_id_seq'::regclass);


--
-- Name: banka_hareketleri id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banka_hareketleri ALTER COLUMN id SET DEFAULT nextval('public.banka_hareketleri_id_seq'::regclass);


--
-- Name: banka_hesaplar id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banka_hesaplar ALTER COLUMN id SET DEFAULT nextval('public.banka_hesaplar_id_seq'::regclass);


--
-- Name: cari_belgeler id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cari_belgeler ALTER COLUMN id SET DEFAULT nextval('public.cari_belgeler_id_seq'::regclass);


--
-- Name: contract_installments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_installments ALTER COLUMN id SET DEFAULT nextval('public.contract_installments_id_seq'::regclass);


--
-- Name: contracts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts ALTER COLUMN id SET DEFAULT nextval('public.contracts_id_seq'::regclass);


--
-- Name: crm_leads id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crm_leads ALTER COLUMN id SET DEFAULT nextval('public.crm_leads_id_seq'::regclass);


--
-- Name: customer_financial_profile id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_financial_profile ALTER COLUMN id SET DEFAULT nextval('public.customer_financial_profile_id_seq'::regclass);


--
-- Name: customers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers ALTER COLUMN id SET DEFAULT nextval('public.customers_id_seq'::regclass);


--
-- Name: devam id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devam ALTER COLUMN id SET DEFAULT nextval('public.devam_id_seq'::regclass);


--
-- Name: devam_kayitlari id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devam_kayitlari ALTER COLUMN id SET DEFAULT nextval('public.devam_kayitlari_id_seq'::regclass);


--
-- Name: duzenli_fatura_secenekleri id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.duzenli_fatura_secenekleri ALTER COLUMN id SET DEFAULT nextval('public.duzenli_fatura_secenekleri_id_seq'::regclass);


--
-- Name: fatura_kalemleri id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fatura_kalemleri ALTER COLUMN id SET DEFAULT nextval('public.fatura_kalemleri_id_seq'::regclass);


--
-- Name: fatura_tahsilat id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fatura_tahsilat ALTER COLUMN id SET DEFAULT nextval('public.fatura_tahsilat_id_seq'::regclass);


--
-- Name: faturalandirilacak_hizmetler id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faturalandirilacak_hizmetler ALTER COLUMN id SET DEFAULT nextval('public.faturalandirilacak_hizmetler_id_seq'::regclass);


--
-- Name: faturalar id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faturalar ALTER COLUMN id SET DEFAULT nextval('public.faturalar_id_seq'::regclass);


--
-- Name: giris_alanlar id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.giris_alanlar ALTER COLUMN id SET DEFAULT nextval('public.giris_alanlar_id_seq'::regclass);


--
-- Name: grup2_etiketleri id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.grup2_etiketleri ALTER COLUMN id SET DEFAULT nextval('public.grup2_etiketleri_id_seq'::regclass);


--
-- Name: hizmet_turleri id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hizmet_turleri ALTER COLUMN id SET DEFAULT nextval('public.hizmet_turleri_id_seq'::regclass);


--
-- Name: iletisim_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.iletisim_log ALTER COLUMN id SET DEFAULT nextval('public.iletisim_log_id_seq'::regclass);


--
-- Name: invoices id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices ALTER COLUMN id SET DEFAULT nextval('public.invoices_id_seq'::regclass);


--
-- Name: kargo_resimler id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kargo_resimler ALTER COLUMN id SET DEFAULT nextval('public.kargo_resimler_id_seq'::regclass);


--
-- Name: kargolar id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kargolar ALTER COLUMN id SET DEFAULT nextval('public.kargolar_id_seq'::regclass);


--
-- Name: kyc_belgeler id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kyc_belgeler ALTER COLUMN id SET DEFAULT nextval('public.kyc_belgeler_id_seq'::regclass);


--
-- Name: legal_cases id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_cases ALTER COLUMN id SET DEFAULT nextval('public.legal_cases_id_seq'::regclass);


--
-- Name: ledger_parties id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_parties ALTER COLUMN id SET DEFAULT nextval('public.ledger_parties_id_seq'::regclass);


--
-- Name: ledger_transactions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_transactions ALTER COLUMN id SET DEFAULT nextval('public.ledger_transactions_id_seq'::regclass);


--
-- Name: ledger_groups id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_groups ALTER COLUMN id SET DEFAULT nextval('public.ledger_groups_id_seq'::regclass);


--
-- Name: ledger_group_members id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_group_members ALTER COLUMN id SET DEFAULT nextval('public.ledger_group_members_id_seq'::regclass);


--
-- Name: ledger_reminders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_reminders ALTER COLUMN id SET DEFAULT nextval('public.ledger_reminders_id_seq'::regclass);


--
-- Name: ledger_transaction_attachments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_transaction_attachments ALTER COLUMN id SET DEFAULT nextval('public.ledger_transaction_attachments_id_seq'::regclass);


--
-- Name: ledger_registered_assets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_registered_assets ALTER COLUMN id SET DEFAULT nextval('public.ledger_registered_assets_id_seq'::regclass);


--
-- Name: masraflar id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masraflar ALTER COLUMN id SET DEFAULT nextval('public.masraflar_id_seq'::regclass);


--
-- Name: mukerrer_arsiv_batch id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mukerrer_arsiv_batch ALTER COLUMN id SET DEFAULT nextval('public.mukerrer_arsiv_batch_id_seq'::regclass);


--
-- Name: musteri_kyc id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.musteri_kyc ALTER COLUMN id SET DEFAULT nextval('public.musteri_kyc_id_seq'::regclass);


--
-- Name: office_rentals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_rentals ALTER COLUMN id SET DEFAULT nextval('public.office_rentals_id_seq'::regclass);


--
-- Name: offices id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offices ALTER COLUMN id SET DEFAULT nextval('public.offices_id_seq'::regclass);


--
-- Name: personel id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel ALTER COLUMN id SET DEFAULT nextval('public.personel_id_seq'::regclass);


--
-- Name: personel_hareketleri id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_hareketleri ALTER COLUMN id SET DEFAULT nextval('public.personel_hareketleri_id_seq'::regclass);


--
-- Name: personel_izin id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_izin ALTER COLUMN id SET DEFAULT nextval('public.personel_izin_id_seq'::regclass);


--
-- Name: personel_yetki id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_yetki ALTER COLUMN id SET DEFAULT nextval('public.personel_yetki_id_seq'::regclass);


--
-- Name: personeller id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personeller ALTER COLUMN id SET DEFAULT nextval('public.personeller_id_seq'::regclass);


--
-- Name: potansiyel_musteriler id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.potansiyel_musteriler ALTER COLUMN id SET DEFAULT nextval('public.potansiyel_musteriler_id_seq'::regclass);


--
-- Name: products id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products ALTER COLUMN id SET DEFAULT nextval('public.products_id_seq'::regclass);


--
-- Name: randevular id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.randevular ALTER COLUMN id SET DEFAULT nextval('public.randevular_id_seq'::regclass);


--
-- Name: rent_payments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rent_payments ALTER COLUMN id SET DEFAULT nextval('public.rent_payments_id_seq'::regclass);


--
-- Name: sozlesmeler id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sozlesmeler ALTER COLUMN id SET DEFAULT nextval('public.sozlesmeler_id_seq'::regclass);


--
-- Name: tahsilatlar id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tahsilatlar ALTER COLUMN id SET DEFAULT nextval('public.tahsilatlar_id_seq'::regclass);


--
-- Name: tediyeler id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tediyeler ALTER COLUMN id SET DEFAULT nextval('public.tediyeler_id_seq'::regclass);


--
-- Name: urunler id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.urunler ALTER COLUMN id SET DEFAULT nextval('public.urunler_id_seq1'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: password_reset_tokens id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens ALTER COLUMN id SET DEFAULT nextval('public.password_reset_tokens_id_seq'::regclass);


--
-- Name: email_verification_tokens id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verification_tokens ALTER COLUMN id SET DEFAULT nextval('public.email_verification_tokens_id_seq'::regclass);


--
-- Name: web_users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.web_users ALTER COLUMN id SET DEFAULT nextval('public.web_users_id_seq'::regclass);


--
-- Name: akbank_dekont_musteri_map akbank_dekont_musteri_map_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.akbank_dekont_musteri_map
    ADD CONSTRAINT akbank_dekont_musteri_map_pkey PRIMARY KEY (sender_key);


--
-- Name: akbank_import_dosyalar akbank_import_dosyalar_ad_gosterim_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.akbank_import_dosyalar
    ADD CONSTRAINT akbank_import_dosyalar_ad_gosterim_key UNIQUE (ad_gosterim);


--
-- Name: akbank_import_dosyalar akbank_import_dosyalar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.akbank_import_dosyalar
    ADD CONSTRAINT akbank_import_dosyalar_pkey PRIMARY KEY (id);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: auto_invoice_items auto_invoice_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_invoice_items
    ADD CONSTRAINT auto_invoice_items_pkey PRIMARY KEY (id);


--
-- Name: auto_invoice_runs auto_invoice_runs_period_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_invoice_runs
    ADD CONSTRAINT auto_invoice_runs_period_key_key UNIQUE (period_key);


--
-- Name: auto_invoice_runs auto_invoice_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_invoice_runs
    ADD CONSTRAINT auto_invoice_runs_pkey PRIMARY KEY (id);


--
-- Name: auto_invoice_settings auto_invoice_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_invoice_settings
    ADD CONSTRAINT auto_invoice_settings_pkey PRIMARY KEY (id);


--
-- Name: banka_hareketler banka_hareketler_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banka_hareketler
    ADD CONSTRAINT banka_hareketler_pkey PRIMARY KEY (id);


--
-- Name: banka_hareketleri banka_hareketleri_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banka_hareketleri
    ADD CONSTRAINT banka_hareketleri_pkey PRIMARY KEY (id);


--
-- Name: banka_hesaplar banka_hesaplar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banka_hesaplar
    ADD CONSTRAINT banka_hesaplar_pkey PRIMARY KEY (id);


--
-- Name: cari_belgeler cari_belgeler_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cari_belgeler
    ADD CONSTRAINT cari_belgeler_pkey PRIMARY KEY (id);


--
-- Name: contract_installments contract_installments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_installments
    ADD CONSTRAINT contract_installments_pkey PRIMARY KEY (id);


--
-- Name: contracts contracts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_pkey PRIMARY KEY (id);


--
-- Name: crm_leads crm_leads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crm_leads
    ADD CONSTRAINT crm_leads_pkey PRIMARY KEY (id);


--
-- Name: customer_financial_profile customer_financial_profile_musteri_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_financial_profile
    ADD CONSTRAINT customer_financial_profile_musteri_id_key UNIQUE (musteri_id);


--
-- Name: customer_financial_profile customer_financial_profile_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_financial_profile
    ADD CONSTRAINT customer_financial_profile_pkey PRIMARY KEY (id);


--
-- Name: customers customers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_pkey PRIMARY KEY (id);


--
-- Name: dashboard_kisayollar dashboard_kisayollar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dashboard_kisayollar
    ADD CONSTRAINT dashboard_kisayollar_pkey PRIMARY KEY (slot_key);


--
-- Name: dashboard_kisayollar_user dashboard_kisayollar_user_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dashboard_kisayollar_user
    ADD CONSTRAINT dashboard_kisayollar_user_pkey PRIMARY KEY (user_id, slot_key);


--
-- Name: devam_kayitlari devam_kayitlari_personel_id_tarih_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devam_kayitlari
    ADD CONSTRAINT devam_kayitlari_personel_id_tarih_key UNIQUE (personel_id, tarih);


--
-- Name: devam_kayitlari devam_kayitlari_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devam_kayitlari
    ADD CONSTRAINT devam_kayitlari_pkey PRIMARY KEY (id);


--
-- Name: devam devam_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devam
    ADD CONSTRAINT devam_pkey PRIMARY KEY (id);


--
-- Name: duzenli_fatura_secenekleri duzenli_fatura_secenekleri_kod_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.duzenli_fatura_secenekleri
    ADD CONSTRAINT duzenli_fatura_secenekleri_kod_key UNIQUE (kod);


--
-- Name: duzenli_fatura_secenekleri duzenli_fatura_secenekleri_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.duzenli_fatura_secenekleri
    ADD CONSTRAINT duzenli_fatura_secenekleri_pkey PRIMARY KEY (id);


--
-- Name: fatura_kalemleri fatura_kalemleri_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fatura_kalemleri
    ADD CONSTRAINT fatura_kalemleri_pkey PRIMARY KEY (id);


--
-- Name: fatura_tahsilat fatura_tahsilat_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fatura_tahsilat
    ADD CONSTRAINT fatura_tahsilat_pkey PRIMARY KEY (id);


--
-- Name: faturalandirilacak_hizmetler faturalandirilacak_hizmetler_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faturalandirilacak_hizmetler
    ADD CONSTRAINT faturalandirilacak_hizmetler_pkey PRIMARY KEY (id);


--
-- Name: faturalar faturalar_fatura_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faturalar
    ADD CONSTRAINT faturalar_fatura_no_key UNIQUE (fatura_no);


--
-- Name: faturalar faturalar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faturalar
    ADD CONSTRAINT faturalar_pkey PRIMARY KEY (id);


--
-- Name: firma_ayar firma_ayar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.firma_ayar
    ADD CONSTRAINT firma_ayar_pkey PRIMARY KEY (id);


--
-- Name: gib_portal_liste_satir gib_portal_liste_satir_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gib_portal_liste_satir
    ADD CONSTRAINT gib_portal_liste_satir_pkey PRIMARY KEY (satir_anahtar);


--
-- Name: gib_portal_sync_aralik gib_portal_sync_aralik_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gib_portal_sync_aralik
    ADD CONSTRAINT gib_portal_sync_aralik_pkey PRIMARY KEY (bas_date, bit_date);


--
-- Name: giris_alanlar giris_alanlar_alan_kodu_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.giris_alanlar
    ADD CONSTRAINT giris_alanlar_alan_kodu_key UNIQUE (alan_kodu);


--
-- Name: giris_alanlar giris_alanlar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.giris_alanlar
    ADD CONSTRAINT giris_alanlar_pkey PRIMARY KEY (id);


--
-- Name: grup2_etiketleri grup2_etiketleri_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.grup2_etiketleri
    ADD CONSTRAINT grup2_etiketleri_pkey PRIMARY KEY (id);


--
-- Name: grup2_etiketleri grup2_etiketleri_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.grup2_etiketleri
    ADD CONSTRAINT grup2_etiketleri_slug_key UNIQUE (slug);


--
-- Name: hizmet_turleri hizmet_turleri_ad_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hizmet_turleri
    ADD CONSTRAINT hizmet_turleri_ad_key UNIQUE (ad);


--
-- Name: hizmet_turleri hizmet_turleri_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hizmet_turleri
    ADD CONSTRAINT hizmet_turleri_pkey PRIMARY KEY (id);


--
-- Name: iletisim_log iletisim_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.iletisim_log
    ADD CONSTRAINT iletisim_log_pkey PRIMARY KEY (id);


--
-- Name: invoices invoices_invoice_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_invoice_number_key UNIQUE (invoice_number);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (id);


--
-- Name: kargo_resimler kargo_resimler_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kargo_resimler
    ADD CONSTRAINT kargo_resimler_pkey PRIMARY KEY (id);


--
-- Name: kargolar kargolar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kargolar
    ADD CONSTRAINT kargolar_pkey PRIMARY KEY (id);


--
-- Name: kyc_belgeler kyc_belgeler_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kyc_belgeler
    ADD CONSTRAINT kyc_belgeler_pkey PRIMARY KEY (id);


--
-- Name: legal_cases legal_cases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_cases
    ADD CONSTRAINT legal_cases_pkey PRIMARY KEY (id);


--
-- Name: ledger_parties ledger_parties_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_parties
    ADD CONSTRAINT ledger_parties_pkey PRIMARY KEY (id);


--
-- Name: ledger_transactions ledger_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_transactions
    ADD CONSTRAINT ledger_transactions_pkey PRIMARY KEY (id);


--
-- Name: ledger_groups ledger_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_groups
    ADD CONSTRAINT ledger_groups_pkey PRIMARY KEY (id);


--
-- Name: ledger_group_members ledger_group_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_group_members
    ADD CONSTRAINT ledger_group_members_pkey PRIMARY KEY (id);


--
-- Name: ledger_group_members ledger_group_members_group_party_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_group_members
    ADD CONSTRAINT ledger_group_members_group_party_uq UNIQUE (group_id, party_id);


--
-- Name: ledger_reminders ledger_reminders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_reminders
    ADD CONSTRAINT ledger_reminders_pkey PRIMARY KEY (id);


--
-- Name: ledger_transaction_attachments ledger_transaction_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_transaction_attachments
    ADD CONSTRAINT ledger_transaction_attachments_pkey PRIMARY KEY (id);


--
-- Name: ledger_registered_assets ledger_registered_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_registered_assets
    ADD CONSTRAINT ledger_registered_assets_pkey PRIMARY KEY (id);


--
-- Name: masraflar masraflar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masraflar
    ADD CONSTRAINT masraflar_pkey PRIMARY KEY (id);


--
-- Name: mukerrer_arsiv_batch mukerrer_arsiv_batch_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mukerrer_arsiv_batch
    ADD CONSTRAINT mukerrer_arsiv_batch_pkey PRIMARY KEY (id);


--
-- Name: musteri_aylik_grid_cache musteri_aylik_grid_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.musteri_aylik_grid_cache
    ADD CONSTRAINT musteri_aylik_grid_cache_pkey PRIMARY KEY (musteri_id);


--
-- Name: musteri_kyc musteri_kyc_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.musteri_kyc
    ADD CONSTRAINT musteri_kyc_pkey PRIMARY KEY (id);


--
-- Name: musteri_reel_donem_tutar musteri_reel_donem_tutar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.musteri_reel_donem_tutar
    ADD CONSTRAINT musteri_reel_donem_tutar_pkey PRIMARY KEY (musteri_id, donem_yil);


--
-- Name: musteri_tahsilat_panel_detay musteri_tahsilat_panel_detay_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.musteri_tahsilat_panel_detay
    ADD CONSTRAINT musteri_tahsilat_panel_detay_pkey PRIMARY KEY (musteri_id);


--
-- Name: office_rentals office_rentals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.office_rentals
    ADD CONSTRAINT office_rentals_pkey PRIMARY KEY (id);


--
-- Name: offices offices_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offices
    ADD CONSTRAINT offices_code_key UNIQUE (code);


--
-- Name: offices offices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offices
    ADD CONSTRAINT offices_pkey PRIMARY KEY (id);


--
-- Name: personel_bilgi personel_bilgi_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_bilgi
    ADD CONSTRAINT personel_bilgi_pkey PRIMARY KEY (personel_id);


--
-- Name: personel_hareketleri personel_hareketleri_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_hareketleri
    ADD CONSTRAINT personel_hareketleri_pkey PRIMARY KEY (id);


--
-- Name: personel_izin personel_izin_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_izin
    ADD CONSTRAINT personel_izin_pkey PRIMARY KEY (id);


--
-- Name: personel_ozluk personel_ozluk_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_ozluk
    ADD CONSTRAINT personel_ozluk_pkey PRIMARY KEY (personel_id);


--
-- Name: personel personel_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel
    ADD CONSTRAINT personel_pkey PRIMARY KEY (id);


--
-- Name: personel_yetki personel_yetki_personel_id_modul_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_yetki
    ADD CONSTRAINT personel_yetki_personel_id_modul_key UNIQUE (personel_id, modul);


--
-- Name: personel_yetki personel_yetki_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_yetki
    ADD CONSTRAINT personel_yetki_pkey PRIMARY KEY (id);


--
-- Name: personeller personeller_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personeller
    ADD CONSTRAINT personeller_pkey PRIMARY KEY (id);


--
-- Name: potansiyel_musteriler potansiyel_musteriler_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.potansiyel_musteriler
    ADD CONSTRAINT potansiyel_musteriler_pkey PRIMARY KEY (id);


--
-- Name: products products_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (id);


--
-- Name: products products_sku_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_sku_key UNIQUE (sku);


--
-- Name: randevular randevular_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.randevular
    ADD CONSTRAINT randevular_pkey PRIMARY KEY (id);


--
-- Name: rent_payments rent_payments_customer_id_year_month_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rent_payments
    ADD CONSTRAINT rent_payments_customer_id_year_month_key UNIQUE (customer_id, year, month);


--
-- Name: rent_payments rent_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rent_payments
    ADD CONSTRAINT rent_payments_pkey PRIMARY KEY (id);


--
-- Name: sozlesmeler sozlesmeler_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sozlesmeler
    ADD CONSTRAINT sozlesmeler_pkey PRIMARY KEY (id);


--
-- Name: sozlesmeler sozlesmeler_sozlesme_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sozlesmeler
    ADD CONSTRAINT sozlesmeler_sozlesme_no_key UNIQUE (sozlesme_no);


--
-- Name: tahsilatlar tahsilatlar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tahsilatlar
    ADD CONSTRAINT tahsilatlar_pkey PRIMARY KEY (id);


--
-- Name: tediyeler tediyeler_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tediyeler
    ADD CONSTRAINT tediyeler_pkey PRIMARY KEY (id);


--
-- Name: toplanti_odasi_fiyat toplanti_odasi_fiyat_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.toplanti_odasi_fiyat
    ADD CONSTRAINT toplanti_odasi_fiyat_pkey PRIMARY KEY (oda_adi);


--
-- Name: tufe_verileri tufe_verileri_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tufe_verileri
    ADD CONSTRAINT tufe_verileri_pkey PRIMARY KEY (year, month);


--
-- Name: urunler urunler_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.urunler
    ADD CONSTRAINT urunler_pkey PRIMARY KEY (id);


--
-- Name: urunler urunler_stok_kodu_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.urunler
    ADD CONSTRAINT urunler_stok_kodu_key UNIQUE (stok_kodu);


--
-- Name: user_ui_preferences user_ui_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_ui_preferences
    ADD CONSTRAINT user_ui_preferences_pkey PRIMARY KEY (user_id, pref_key);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: password_reset_tokens password_reset_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_pkey PRIMARY KEY (id);


--
-- Name: password_reset_tokens password_reset_tokens_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_token_hash_key UNIQUE (token_hash);


--
-- Name: email_verification_tokens email_verification_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verification_tokens
    ADD CONSTRAINT email_verification_tokens_pkey PRIMARY KEY (id);


--
-- Name: email_verification_tokens email_verification_tokens_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verification_tokens
    ADD CONSTRAINT email_verification_tokens_token_hash_key UNIQUE (token_hash);


--
-- Name: web_users web_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.web_users
    ADD CONSTRAINT web_users_pkey PRIMARY KEY (id);


--
-- Name: web_users web_users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.web_users
    ADD CONSTRAINT web_users_username_key UNIQUE (username);


--
-- Name: whatsapp_geciken_haric whatsapp_geciken_haric_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whatsapp_geciken_haric
    ADD CONSTRAINT whatsapp_geciken_haric_pkey PRIMARY KEY (musteri_id);


--
-- Name: banka_hareketleri_referans_no_uidx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX banka_hareketleri_referans_no_uidx ON public.banka_hareketleri USING btree (referans_no) WHERE ((referans_no IS NOT NULL) AND (btrim(referans_no) <> ''::text));


--
-- Name: idx_contracts_musteri; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contracts_musteri ON public.contracts USING btree (musteri_id);


--
-- Name: idx_customers_bizim_hesap; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customers_bizim_hesap ON public.customers USING btree (bizim_hesap);


--
-- Name: idx_customers_durum; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customers_durum ON public.customers USING btree (durum);


--
-- Name: idx_customers_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customers_is_active ON public.customers USING btree (is_active);


--
-- Name: idx_customers_musteri_no; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_customers_musteri_no ON public.customers USING btree (musteri_no);


--
-- Name: idx_customers_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customers_parent_id ON public.customers USING btree (parent_id);


--
-- Name: idx_devam_personel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_devam_personel ON public.devam_kayitlari USING btree (personel_id);


--
-- Name: idx_devam_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_devam_tarih ON public.devam_kayitlari USING btree (tarih);


--
-- Name: idx_faturalar_musteri_durum; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_faturalar_musteri_durum ON public.faturalar USING btree (musteri_id, durum);


--
-- Name: idx_faturalar_musteri_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_faturalar_musteri_id ON public.faturalar USING btree (musteri_id);


--
-- Name: idx_faturalar_musteri_id_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_faturalar_musteri_id_tarih ON public.faturalar USING btree (musteri_id, fatura_tarihi DESC);


--
-- Name: idx_faturalar_musteri_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_faturalar_musteri_tarih ON public.faturalar USING btree (musteri_id, fatura_tarihi);


--
-- Name: idx_faturalar_musteri_vade; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_faturalar_musteri_vade ON public.faturalar USING btree (musteri_id, vade_tarihi);


--
-- Name: idx_gib_portal_liste_fno; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gib_portal_liste_fno ON public.gib_portal_liste_satir USING btree (upper(btrim(COALESCE(fatura_no, ''::text))));


--
-- Name: idx_gib_portal_liste_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_gib_portal_liste_tarih ON public.gib_portal_liste_satir USING btree (fatura_tarihi);


--
-- Name: idx_hareket_personel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hareket_personel ON public.personel_hareketleri USING btree (personel_id);


--
-- Name: idx_hareket_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hareket_tarih ON public.personel_hareketleri USING btree (tarih);


--
-- Name: password_reset_tokens_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX password_reset_tokens_expires_at_idx ON public.password_reset_tokens USING btree (expires_at) WHERE (used_at IS NULL);


--
-- Name: password_reset_tokens_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX password_reset_tokens_user_id_idx ON public.password_reset_tokens USING btree (user_id);


--
-- Name: email_verification_tokens_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX email_verification_tokens_expires_at_idx ON public.email_verification_tokens USING btree (expires_at) WHERE (used_at IS NULL);


--
-- Name: email_verification_tokens_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX email_verification_tokens_user_id_idx ON public.email_verification_tokens USING btree (user_id);


--
-- Name: idx_installments_musteri_vade; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_installments_musteri_vade ON public.contract_installments USING btree (musteri_id, vade_tarihi);


--
-- Name: idx_ledger_parties_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_parties_is_active ON public.ledger_parties USING btree (is_active);


--
-- Name: idx_ledger_parties_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_parties_name ON public.ledger_parties USING btree (lower(TRIM(BOTH FROM name)));


--
-- Name: idx_ledger_tx_party_currency_occurred; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_tx_party_currency_occurred ON public.ledger_transactions USING btree (party_id, currency, occurred_at DESC);


--
-- Name: idx_ledger_tx_party_void; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_tx_party_void ON public.ledger_transactions USING btree (party_id, is_void);


--
-- Name: idx_ledger_groups_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_groups_is_active ON public.ledger_groups USING btree (is_active);


--
-- Name: idx_ledger_group_members_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_group_members_group ON public.ledger_group_members USING btree (group_id);


--
-- Name: idx_ledger_group_members_party; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_group_members_party ON public.ledger_group_members USING btree (party_id);


--
-- Name: idx_ledger_reminders_due_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_reminders_due_at ON public.ledger_reminders USING btree (due_at);


--
-- Name: idx_ledger_reminders_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_reminders_status ON public.ledger_reminders USING btree (status);


--
-- Name: idx_ledger_reminders_party; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_reminders_party ON public.ledger_reminders USING btree (party_id);


--
-- Name: idx_ledger_tx_attach_transaction; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_tx_attach_transaction ON public.ledger_transaction_attachments USING btree (transaction_id);


--
-- Name: idx_ledger_tx_attach_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_tx_attach_created_at ON public.ledger_transaction_attachments USING btree (created_at DESC);


--
-- Name: uq_ledger_tx_attach_one_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_ledger_tx_attach_one_active ON public.ledger_transaction_attachments USING btree (transaction_id) WHERE (is_deleted = false);


--
-- Name: idx_ledger_registered_assets_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_registered_assets_code ON public.ledger_registered_assets USING btree (code);


--
-- Name: idx_masraflar_durum_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masraflar_durum_created ON public.masraflar USING btree (durum, created_at DESC);


--
-- Name: idx_masraflar_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masraflar_tarih ON public.masraflar USING btree (tarih);


--
-- Name: idx_mukerrer_arsiv_batch_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mukerrer_arsiv_batch_created_at ON public.mukerrer_arsiv_batch USING btree (created_at DESC);


--
-- Name: idx_musteri_kyc_musteri_id_id_desc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_musteri_kyc_musteri_id_id_desc ON public.musteri_kyc USING btree (musteri_id, id DESC);


--
-- Name: idx_musteri_reel_donem_tutar_musteri_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_musteri_reel_donem_tutar_musteri_id ON public.musteri_reel_donem_tutar USING btree (musteri_id);


--
-- Name: idx_musteri_tahsilat_panel_detay_musteri_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_musteri_tahsilat_panel_detay_musteri_id ON public.musteri_tahsilat_panel_detay USING btree (musteri_id);


--
-- Name: idx_tahsilatlar_customer_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tahsilatlar_customer_tarih ON public.tahsilatlar USING btree (customer_id, tahsilat_tarihi);


--
-- Name: idx_tahsilatlar_fatura_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tahsilatlar_fatura_id ON public.tahsilatlar USING btree (fatura_id);


--
-- Name: idx_tahsilatlar_musteri_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tahsilatlar_musteri_id ON public.tahsilatlar USING btree (musteri_id);


--
-- Name: idx_tahsilatlar_musteri_id_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tahsilatlar_musteri_id_tarih ON public.tahsilatlar USING btree (musteri_id, tahsilat_tarihi DESC);


--
-- Name: idx_tahsilatlar_musteri_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tahsilatlar_musteri_tarih ON public.tahsilatlar USING btree (musteri_id, tahsilat_tarihi);


--
-- Name: idx_tahsilatlar_musteri_tutar_pos; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tahsilatlar_musteri_tutar_pos ON public.tahsilatlar USING btree (musteri_id, tutar) WHERE ((tutar IS NOT NULL) AND (tutar > (0)::double precision));


--
-- Name: idx_tediyeler_musteri_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tediyeler_musteri_id ON public.tediyeler USING btree (musteri_id);


--
-- Name: idx_tediyeler_musteri_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tediyeler_musteri_tarih ON public.tediyeler USING btree (musteri_id, tediye_tarihi DESC);


--
-- Name: ix_akbank_dekont_map_musteri; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_akbank_dekont_map_musteri ON public.akbank_dekont_musteri_map USING btree (musteri_id);


--
-- Name: ix_akbank_import_dosyalar_yuklenme; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_akbank_import_dosyalar_yuklenme ON public.akbank_import_dosyalar USING btree (yuklenme_tarihi DESC);


--
-- Name: ix_akbank_import_excel_binary_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_akbank_import_excel_binary_hash ON public.akbank_import_dosyalar USING hash (excel_binary);


--
-- Name: ix_customers_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_customers_name ON public.customers USING btree (name);


--
-- Name: ix_faturalar_fatura_tarihi_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_faturalar_fatura_tarihi_id ON public.faturalar USING btree (fatura_tarihi, id);


--
-- Name: ix_faturalar_musteri_ftarih_ettn; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_faturalar_musteri_ftarih_ettn ON public.faturalar USING btree (musteri_id, fatura_tarihi, id) WHERE (ettn IS NOT NULL);


--
-- Name: ix_faturalar_musteri_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_faturalar_musteri_id ON public.faturalar USING btree (musteri_id);


--
-- Name: ix_faturalar_odememis_musteri_vade; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_faturalar_odememis_musteri_vade ON public.faturalar USING btree (musteri_id, vade_tarihi) WHERE ((COALESCE(durum, ''::text) <> 'odendi'::text) AND (vade_tarihi IS NOT NULL));


--
-- Name: ix_faturalar_vade_tarihi_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_faturalar_vade_tarihi_id ON public.faturalar USING btree (vade_tarihi, id);


--
-- Name: ix_kargolar_musteri_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_kargolar_musteri_created ON public.kargolar USING btree (musteri_id, created_at DESC NULLS LAST);


--
-- Name: ix_offices_customer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_offices_customer_id ON public.offices USING btree (customer_id) WHERE (customer_id IS NOT NULL);


--
-- Name: ix_tahsilatlar_banka_referans_no; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tahsilatlar_banka_referans_no ON public.tahsilatlar USING btree (banka_referans_no) WHERE (banka_referans_no IS NOT NULL);


--
-- Name: ix_tahsilatlar_customer_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tahsilatlar_customer_tarih ON public.tahsilatlar USING btree (customer_id, tahsilat_tarihi DESC, id) WHERE (customer_id IS NOT NULL);


--
-- Name: ix_tahsilatlar_fatura_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tahsilatlar_fatura_id ON public.tahsilatlar USING btree (fatura_id) WHERE (fatura_id IS NOT NULL);


--
-- Name: ix_tahsilatlar_islem_grubu_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tahsilatlar_islem_grubu_id ON public.tahsilatlar USING btree (islem_grubu_id) WHERE (islem_grubu_id IS NOT NULL);


--
-- Name: ix_tahsilatlar_musteri_tarih; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tahsilatlar_musteri_tarih ON public.tahsilatlar USING btree (musteri_id, tahsilat_tarihi DESC NULLS LAST, id);


--
-- Name: ix_tahsilatlar_tahsilat_tarihi; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tahsilatlar_tahsilat_tarihi ON public.tahsilatlar USING btree (tahsilat_tarihi DESC NULLS LAST);


--
-- Name: uq_masraflar_fis_gorsel_hash_aktif; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_masraflar_fis_gorsel_hash_aktif ON public.masraflar USING btree (fis_gorsel_hash) WHERE ((fis_gorsel_hash IS NOT NULL) AND (durum = ANY (ARRAY['onay_bekliyor'::text, 'onaylandi'::text])));


--
-- Name: uq_personel_izin_otomatik_gun; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_personel_izin_otomatik_gun ON public.personel_izin USING btree (personel_id, baslangic_tarihi) WHERE (aciklama = 'Otomatik - QR'::text);


--
-- Name: uq_tediyeler_makbuz_no_trim; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_tediyeler_makbuz_no_trim ON public.tediyeler USING btree (TRIM(BOTH FROM makbuz_no)) WHERE ((makbuz_no IS NOT NULL) AND (TRIM(BOTH FROM makbuz_no) <> ''::text));


--
-- Name: faturalar trg_faturalar_update_balance; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_faturalar_update_balance AFTER INSERT OR DELETE OR UPDATE ON public.faturalar FOR EACH ROW EXECUTE FUNCTION public.fn_update_customer_balance();


--
-- Name: tahsilatlar trg_tahsilatlar_update_balance; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_tahsilatlar_update_balance AFTER INSERT OR DELETE OR UPDATE ON public.tahsilatlar FOR EACH ROW EXECUTE FUNCTION public.fn_update_customer_balance();


--
-- Name: auto_invoice_items auto_invoice_items_fatura_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_invoice_items
    ADD CONSTRAINT auto_invoice_items_fatura_id_fkey FOREIGN KEY (fatura_id) REFERENCES public.faturalar(id) ON DELETE SET NULL;


--
-- Name: auto_invoice_items auto_invoice_items_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_invoice_items
    ADD CONSTRAINT auto_invoice_items_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE SET NULL;


--
-- Name: auto_invoice_items auto_invoice_items_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_invoice_items
    ADD CONSTRAINT auto_invoice_items_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.auto_invoice_runs(id) ON DELETE CASCADE;


--
-- Name: banka_hareketleri banka_hareketleri_banka_hesap_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banka_hareketleri
    ADD CONSTRAINT banka_hareketleri_banka_hesap_id_fkey FOREIGN KEY (banka_hesap_id) REFERENCES public.banka_hesaplar(id) ON DELETE CASCADE;


--
-- Name: banka_hareketleri banka_hareketleri_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banka_hareketleri
    ADD CONSTRAINT banka_hareketleri_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE SET NULL;


--
-- Name: banka_hareketleri banka_hareketleri_tahsilat_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.banka_hareketleri
    ADD CONSTRAINT banka_hareketleri_tahsilat_id_fkey FOREIGN KEY (tahsilat_id) REFERENCES public.tahsilatlar(id) ON DELETE SET NULL;


--
-- Name: cari_belgeler cari_belgeler_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cari_belgeler
    ADD CONSTRAINT cari_belgeler_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: contract_installments contract_installments_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_installments
    ADD CONSTRAINT contract_installments_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id) ON DELETE CASCADE;


--
-- Name: contract_installments contract_installments_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_installments
    ADD CONSTRAINT contract_installments_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: contracts contracts_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: customer_financial_profile customer_financial_profile_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_financial_profile
    ADD CONSTRAINT customer_financial_profile_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: faturalandirilacak_hizmetler faturalandirilacak_hizmetler_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faturalandirilacak_hizmetler
    ADD CONSTRAINT faturalandirilacak_hizmetler_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id);


--
-- Name: iletisim_log iletisim_log_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.iletisim_log
    ADD CONSTRAINT iletisim_log_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: legal_cases legal_cases_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_cases
    ADD CONSTRAINT legal_cases_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id) ON DELETE SET NULL;


--
-- Name: legal_cases legal_cases_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.legal_cases
    ADD CONSTRAINT legal_cases_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: musteri_aylik_grid_cache musteri_aylik_grid_cache_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.musteri_aylik_grid_cache
    ADD CONSTRAINT musteri_aylik_grid_cache_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: musteri_reel_donem_tutar musteri_reel_donem_tutar_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.musteri_reel_donem_tutar
    ADD CONSTRAINT musteri_reel_donem_tutar_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: musteri_tahsilat_panel_detay musteri_tahsilat_panel_detay_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.musteri_tahsilat_panel_detay
    ADD CONSTRAINT musteri_tahsilat_panel_detay_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: personel_ozluk personel_ozluk_personel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_ozluk
    ADD CONSTRAINT personel_ozluk_personel_id_fkey FOREIGN KEY (personel_id) REFERENCES public.personel(id) ON DELETE CASCADE;


--
-- Name: personel_yetki personel_yetki_personel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personel_yetki
    ADD CONSTRAINT personel_yetki_personel_id_fkey FOREIGN KEY (personel_id) REFERENCES public.personel(id) ON DELETE CASCADE;


--
-- Name: potansiyel_musteriler potansiyel_musteriler_converted_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.potansiyel_musteriler
    ADD CONSTRAINT potansiyel_musteriler_converted_customer_id_fkey FOREIGN KEY (converted_customer_id) REFERENCES public.customers(id) ON DELETE SET NULL;


--
-- Name: randevular randevular_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.randevular
    ADD CONSTRAINT randevular_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: randevular randevular_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.randevular
    ADD CONSTRAINT randevular_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.randevular(id) ON DELETE SET NULL;


--
-- Name: tahsilatlar tahsilatlar_fatura_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tahsilatlar
    ADD CONSTRAINT tahsilatlar_fatura_id_fkey FOREIGN KEY (fatura_id) REFERENCES public.faturalar(id);


--
-- Name: tahsilatlar tahsilatlar_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tahsilatlar
    ADD CONSTRAINT tahsilatlar_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id);


--
-- Name: tediyeler tediyeler_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tediyeler
    ADD CONSTRAINT tediyeler_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id);


--
-- Name: password_reset_tokens password_reset_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: email_verification_tokens email_verification_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verification_tokens
    ADD CONSTRAINT email_verification_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: ledger_transactions ledger_transactions_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_transactions
    ADD CONSTRAINT ledger_transactions_party_id_fkey FOREIGN KEY (party_id) REFERENCES public.ledger_parties(id) ON DELETE RESTRICT;


--
-- Name: ledger_group_members ledger_group_members_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_group_members
    ADD CONSTRAINT ledger_group_members_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.ledger_groups(id) ON DELETE CASCADE;


--
-- Name: ledger_group_members ledger_group_members_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_group_members
    ADD CONSTRAINT ledger_group_members_party_id_fkey FOREIGN KEY (party_id) REFERENCES public.ledger_parties(id) ON DELETE RESTRICT;


--
-- Name: ledger_reminders ledger_reminders_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_reminders
    ADD CONSTRAINT ledger_reminders_party_id_fkey FOREIGN KEY (party_id) REFERENCES public.ledger_parties(id) ON DELETE CASCADE;


--
-- Name: ledger_transaction_attachments ledger_tx_attach_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_transaction_attachments
    ADD CONSTRAINT ledger_tx_attach_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.ledger_transactions(id) ON DELETE CASCADE;


--
-- Name: user_ui_preferences user_ui_preferences_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_ui_preferences
    ADD CONSTRAINT user_ui_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: whatsapp_geciken_haric whatsapp_geciken_haric_musteri_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whatsapp_geciken_haric
    ADD CONSTRAINT whatsapp_geciken_haric_musteri_id_fkey FOREIGN KEY (musteri_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict iYToLm6nKFhGrZhtmmtwTatUPR2IwYRRRMr6dovQVxKZadvgagJzZDkB0e1LoGV

