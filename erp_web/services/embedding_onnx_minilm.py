# -*- coding: utf-8 -*-
"""
Çok dilli MiniLM benzeri cümle embedding’i — ONNX Runtime (CUDA / DirectML / OpenVINO / CPU).

Klasör yapısı (EMBEDDING_ONNX_DIR):
  model.onnx
  tokenizer.json

Oluşturmak için: python scripts/export_multilingual_minilm_onnx.py
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

_log = logging.getLogger(__name__)

_EMB: "MiniLmOnnxEmbedder | None" = None


def embedding_model_dir() -> Path | None:
    raw = (os.environ.get("EMBEDDING_ONNX_DIR") or "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_dir() else None
    # Varsayılan: repo içi (gitignore)
    here = Path(__file__).resolve().parent.parent / "models" / "multilingual-minilm-l12-onnx"
    return here if here.is_dir() else None


class MiniLmOnnxEmbedder:
    """Mean pooling + L2 normalize; batch ile GPU/NPU’ya gönderilebilir."""

    def __init__(self, model_dir: Path):
        from tokenizers import Tokenizer

        self.model_dir = model_dir
        onnx_path = model_dir / "model.onnx"
        if not onnx_path.is_file():
            cands = sorted(model_dir.glob("*.onnx"), key=lambda p: p.stat().st_size, reverse=True)
            if not cands:
                raise FileNotFoundError(f"Klasörde .onnx yok: {model_dir}")
            onnx_path = cands[0]
        tok_path = model_dir / "tokenizer.json"
        if not tok_path.is_file():
            raise FileNotFoundError(f"tokenizer.json yok: {tok_path}")

        self.tokenizer = Tokenizer.from_file(str(tok_path))
        self.max_length = int(os.environ.get("EMBEDDING_MAX_LENGTH", "128"))
        self.tokenizer.enable_truncation(max_length=self.max_length)
        self.tokenizer.enable_padding(length=self.max_length, pad_id=0, pad_type_id=0)

        from utils.compute_device import create_onnx_inference_session

        try:
            self.session = create_onnx_inference_session(str(onnx_path))
        except Exception as e:
            _log.warning("ONNX oturumu (tercih edilen sağlayıcılar) açılamadı, CPU deneniyor: %s", e)
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(
                str(onnx_path),
                sess_options=so,
                providers=["CPUExecutionProvider"],
            )

        self._out_name = self._pick_output_name()

    def _pick_output_name(self) -> str:
        outs = self.session.get_outputs()
        for o in outs:
            shp = o.shape
            if len(shp) == 3:
                return o.name
        if outs:
            return outs[0].name
        raise RuntimeError("ONNX çıktısı bulunamadı")

    def _encode_batch(self, texts: list[str]) -> tuple[np.ndarray, np.ndarray]:
        enc = self.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        return ids, mask

    def _run(self, input_ids: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        feeds: dict[str, Any] = {}
        for inp in self.session.get_inputs():
            nm = inp.name
            low = nm.lower()
            if low == "token_type_ids":
                feeds[nm] = np.zeros(input_ids.shape, dtype=np.int64)
            elif "attention" in low and "mask" in low:
                feeds[nm] = attention_mask
            elif low in ("input_ids", "x"):
                feeds[nm] = input_ids
            elif "input" in low and "mask" not in low and "type" not in low:
                feeds[nm] = input_ids
        if len(feeds) < len(self.session.get_inputs()):
            for inp in self.session.get_inputs():
                if inp.name not in feeds:
                    low = inp.name.lower()
                    if "attention" in low:
                        feeds[inp.name] = attention_mask
                    elif "type" in low:
                        feeds[inp.name] = np.zeros(input_ids.shape, dtype=np.int64)
                    else:
                        feeds[inp.name] = input_ids
        out = self.session.run([self._out_name], feeds)[0]
        return np.asarray(out, dtype=np.float32)

    @staticmethod
    def _mean_pool(last_hidden: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        m = attention_mask.astype(np.float32)[:, :, None]
        summed = (last_hidden * m).sum(axis=1)
        denom = m.sum(axis=1).clip(min=1e-9)
        v = summed / denom
        nrm = np.linalg.norm(v, axis=1, keepdims=True).clip(min=1e-9)
        return (v / nrm).astype(np.float32)

    def embed_texts(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        texts = [str(t or "")[:2000] for t in texts]
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        chunks: list[np.ndarray] = []
        bs = max(1, int(batch_size))
        for i in range(0, len(texts), bs):
            part = texts[i : i + bs]
            ids, mask = self._encode_batch(part)
            hidden = self._run(ids, mask)
            chunks.append(self._mean_pool(hidden, mask))
        return np.vstack(chunks) if chunks else np.zeros((0, 1), dtype=np.float32)

    def rank_query(
        self,
        query: str,
        labels: list[str],
        ids: list[int],
        topk: int = 5,
    ) -> list[dict[str, Any]]:
        if not labels or not ids or len(labels) != len(ids):
            return []
        qv = self.embed_texts([query], batch_size=1)[0]
        cv = self.embed_texts(labels, batch_size=32)
        sims = cv @ qv
        k = min(topk, len(sims))
        idx = np.argsort(-sims)[:k]
        out = []
        for j in idx:
            out.append({
                "musteri_id": int(ids[int(j)]),
                "score": float(sims[int(j)]),
                "label": labels[int(j)][:200],
            })
        return out


def get_embedder() -> MiniLmOnnxEmbedder | None:
    global _EMB
    if _EMB is not None:
        return _EMB
    d = embedding_model_dir()
    if d is None:
        _log.debug("EMBEDDING_ONNX_DIR tanımlı değil veya varsayılan model klasörü yok.")
        return None
    try:
        _EMB = MiniLmOnnxEmbedder(d)
        _log.info("ONNX embedding yüklendi: %s", d)
        return _EMB
    except Exception as e:
        _log.warning("ONNX embedding yüklenemedi: %s", e)
        return None


def embedding_available() -> bool:
    return get_embedder() is not None
