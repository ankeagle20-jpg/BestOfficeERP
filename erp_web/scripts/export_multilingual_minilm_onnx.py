#!/usr/bin/env python3
"""
Bir kerelik ONNX dışa aktarma (çok dilli MiniLM, Türkçe dekont metni için uygun).

Kurulum:
  pip install optimum[onnxruntime] transformers torch

Çalıştır (erp_web klasöründen):
  python scripts/export_multilingual_minilm_onnx.py

Çıktı:
  erp_web/models/multilingual-minilm-l12-onnx/
    model.onnx, tokenizer.json, config.json, ...

Sonra .env:
  EMBEDDING_ONNX_DIR=C:/yol/BestOfficeERP/erp_web/models/multilingual-minilm-l12-onnx
  AKBANK_EMBED_PROTOTYPE=1
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        from optimum.onnxruntime import ORTModelForFeatureExtraction
        from transformers import AutoTokenizer
    except ImportError:
        print(
            "Eksik paket: pip install optimum[onnxruntime] transformers torch",
            file=sys.stderr,
        )
        return 1

    model_id = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    root = Path(__file__).resolve().parent.parent
    out = root / "models" / "multilingual-minilm-l12-onnx"
    out.mkdir(parents=True, exist_ok=True)
    print("Dışa aktarılıyor:", model_id)
    print("Hedef:", out)
    model = ORTModelForFeatureExtraction.from_pretrained(model_id, export=True)
    model.save_pretrained(out)
    tok = AutoTokenizer.from_pretrained(model_id)
    tok.save_pretrained(out)
    print("Tamam. EMBEDDING_ONNX_DIR=", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
