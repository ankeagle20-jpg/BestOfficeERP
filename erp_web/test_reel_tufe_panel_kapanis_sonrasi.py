# -*- coding: utf-8 -*-
"""Z=1 pasif/iptal: kapanış/son_borc sonrası panel satırları korunur."""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import routes.giris_routes as gr


class TestPanelKapanisSonrasiKoruma(unittest.TestCase):
    def setUp(self):
        self.mid = 900393
        self.son = date(2025, 1, 1)
        self.post_a = "2025-02-01"
        self.post_b = "2025-03-01"
        self.pre = "2024-12-01"
        self.existing = {
            self.pre: {"aylik": 500.0, "tahsil": 500.0, "kalan": 0.0, "tahsil_tarih": "2024-12-05"},
            self.post_a: {"aylik": 577.0, "tahsil": 577.0, "kalan": 0.0, "tahsil_tarih": "2025-02-10"},
            self.post_b: {"aylik": 577.0, "tahsil": 0.0, "kalan": 577.0, "tahsil_tarih": ""},
        }
        self.payload = {
            "aylar": [
                {"yil": 2024, "ay": 12, "brut_tutar_kdv": 510.0, "odenen_tutar_kdv": 0},
                {"yil": 2025, "ay": 2, "brut_tutar_kdv": 577.0, "odenen_tutar_kdv": 0},
                {"yil": 2025, "ay": 3, "brut_tutar_kdv": 577.0, "odenen_tutar_kdv": 0},
            ]
        }
        self.n_assert = 0

    def _count(self):
        self.n_assert += 1

    def _run(self, *, z1=True, aktif=False, existing=None, payload=None, tahsil_map=None):
        existing = existing if existing is not None else self.existing
        payload = payload if payload is not None else self.payload
        tahsil_map = tahsil_map if tahsil_map is not None else {self.pre: 510.0}
        son = None if aktif else self.son
        kyc = {"durum": "aktif" if aktif else "pasif", "iptal": False}

        with patch.object(gr, "_reel_overlay_a_dar_ez_kyc", return_value=z1), patch.object(
            gr, "_aylik_grid_son_borc_for_mid", return_value=(son, kyc)
        ), patch.object(gr, "_load_musteri_panel_by_iso", return_value=dict(existing)), patch.object(
            gr, "_aylik_tahsil_tutar_map", return_value=dict(tahsil_map)
        ), patch.object(gr, "_ekstre_tahsil_rows_for_musteri", return_value=[]), patch.object(
            gr, "_ekstre_tahsil_batch_maps_from_rows", return_value={"marker": {}, "pay": {}}
        ):
            return gr._panel_by_iso_from_tahsil_map(
                self.mid, payload, tahsilat_tarihi=None, trust_grid_odenen=False
            )

    def test_z1_pasif_post_tahsilli_ve_tahsilsiz_birebir(self):
        by_iso = self._run()
        self._count()
        self.assertIn(self.post_a, by_iso)
        self._count()
        self.assertEqual(by_iso[self.post_a]["tahsil"], 577.0)
        self._count()
        self.assertEqual(by_iso[self.post_a]["aylik"], 577.0)
        self._count()
        self.assertEqual(by_iso[self.post_b]["tahsil"], 0.0)
        self._count()
        self.assertEqual(by_iso[self.post_b]["kalan"], 577.0)
        self._count()
        self.assertEqual(by_iso[self.post_a], self.existing[self.post_a])
        self._count()
        self.assertEqual(by_iso[self.post_b], self.existing[self.post_b])
        self.assertGreater(self.n_assert, 0)

    def test_z1_pasif_post_ay_silinmez_eklenmez(self):
        # Grid'de post yok; existing'de var → korunur. Yeni post eklenmez.
        payload = {"aylar": [{"yil": 2024, "ay": 12, "brut_tutar_kdv": 510.0}]}
        by_iso = self._run(payload=payload)
        self._count()
        self.assertIn(self.post_a, by_iso)
        self._count()
        self.assertIn(self.post_b, by_iso)
        self._count()
        self.assertNotIn("2025-04-01", by_iso)
        self.assertGreater(self.n_assert, 0)

    def test_aktif_etkilenmez(self):
        by_iso = self._run(aktif=True)
        self._count()
        # Aktifte koruma yok: post aylar grid/map'ten yeniden yazılır veya düşer
        self.assertNotEqual(by_iso.get(self.post_a), self.existing[self.post_a])
        self._count()
        # Pre ay Z=1 mantığıyla güncellenir
        self.assertAlmostEqual(float(by_iso[self.pre]["aylik"]), 510.0, places=2)
        self.assertGreater(self.n_assert, 0)

    def test_z0_ayni(self):
        by_iso = self._run(z1=False)
        self._count()
        # Z=0: koruma uygulanmaz; post tahsil map yoksa 0'a düşer / silinir
        cell_a = by_iso.get(self.post_a) or {}
        self.assertNotEqual(float(cell_a.get("tahsil") or 0), 577.0)
        self.assertGreater(self.n_assert, 0)

    def test_kapanis_oncesi_z1_beklenen(self):
        by_iso = self._run()
        self._count()
        self.assertAlmostEqual(float(by_iso[self.pre]["aylik"]), 510.0, places=2)
        self._count()
        self.assertAlmostEqual(float(by_iso[self.pre]["tahsil"]), 510.0, places=2)
        self.assertGreater(self.n_assert, 0)

    def test_mutasyon_kural_kaldirilinca_fail(self):
        """Taban reproduksiyon: koruma identity olunca post tahsil budanır → senaryo FAIL."""
        real = gr._panel_apply_pasif_kapanis_sonrasi_koruma

        def _identity(mid, by_iso, existing):
            return by_iso if isinstance(by_iso, dict) else {}

        with patch.object(gr, "_panel_apply_pasif_kapanis_sonrasi_koruma", side_effect=_identity):
            by_iso = self._run()
        self._count()
        # Eski hatalı davranış: post tahsil 577→0 veya satır kaybı
        broken = (by_iso.get(self.post_a) or {}).get("tahsil")
        self.assertTrue(
            broken is None or abs(float(broken) - 577.0) > 0.02,
            msg="mutasyon: eski kod post tahsili bozmalı",
        )
        # Gerçek kural ile düzgün
        by_ok = self._run()
        self._count()
        self.assertEqual(by_ok[self.post_a]["tahsil"], 577.0)
        self.assertGreater(self.n_assert, 0)
        # fonksiyon hâlâ mevcut
        self.assertTrue(callable(real))


if __name__ == "__main__":
    unittest.main()
