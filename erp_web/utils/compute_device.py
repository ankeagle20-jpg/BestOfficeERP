# -*- coding: utf-8 -*-
"""
Hızlandırıcı tespiti ve ONNX Runtime oturumu (NVIDIA GPU / Intel NPU / DirectML).

Önemli: Bu ERP’deki ağırlık (Flask, PostgreSQL, Excel, metin tabanlı Akbank eşleştirme)
CPU + bellek bağlıdır; GPU veya NPU otomatik olarak bu işleri hızlandırmaz. Donanım
yalnızca burada **ONNX modeli** çalıştırdığınızda (ör. ileride embedding / sınıflandırma)
devreye girer.

Kurulum (birini seçin; hepsi aynı anda genelde kurulmaz):
  • NVIDIA (CUDA):  pip install onnxruntime-gpu
  • Windows GPU (DML, RTX dahil): pip install onnxruntime-directml
  • Intel NPU/GPU (OpenVINO): resmi Intel / onnxruntime-openvino paketine göre kurun

Ortam:
  ORT_PREFERRED=cuda|dml|openvino|cpu   — öncelik (varsayılan: otomatik sıra)
  OPENVINO_DEVICE=NPU|GPU|CPU         — OpenVINOExecutionProvider için (varsa)
  USE_CUDA=1, USE_DIRECTML=1, USE_INTEL_NPU=1 — ORT_PREFERRED ile aynı yönde ipucu

Kullanım:
  from utils.compute_device import create_onnx_inference_session
  sess = create_onnx_inference_session("model.onnx")
"""
from __future__ import annotations

import logging
import os
from typing import Any

_log = logging.getLogger(__name__)


def _torch_cuda_summary() -> dict[str, Any]:
    out: dict[str, Any] = {"available": False, "device_name": None}
    try:
        import torch

        out["available"] = bool(torch.cuda.is_available())
        if out["available"]:
            out["device_name"] = torch.cuda.get_device_name(0)
    except Exception:
        pass
    return out


def _onnx_available_providers() -> list[str]:
    try:
        import onnxruntime as ort

        return list(ort.get_available_providers())
    except Exception:
        return []


def preferred_onnx_providers() -> list[str]:
    """
    ONNX Runtime sağlayıcı sırası: CUDA → DirectML → OpenVINO → CPU.
    ORT_PREFERRED veya USE_* ile zorlanabilir.
    """
    avail = set(_onnx_available_providers())
    if not avail:
        return ["CPUExecutionProvider"]

    pref = (os.environ.get("ORT_PREFERRED") or "").strip().lower()
    force_cuda = os.environ.get("USE_CUDA", "").strip().lower() in ("1", "true", "yes", "on")
    force_dml = os.environ.get("USE_DIRECTML", "").strip().lower() in ("1", "true", "yes", "on")
    force_ov = os.environ.get("USE_INTEL_NPU", "").strip().lower() in ("1", "true", "yes", "on")

    chosen: list[str] = []

    def add(name: str) -> None:
        if name in avail and name not in chosen:
            chosen.append(name)

    if pref == "cpu":
        add("CPUExecutionProvider")
        return chosen or ["CPUExecutionProvider"]

    if pref == "cuda" or force_cuda:
        add("CUDAExecutionProvider")
    if pref == "dml" or force_dml:
        add("DmlExecutionProvider")
    if pref == "openvino" or force_ov:
        add("OpenVINOExecutionProvider")

    if not chosen:
        add("CUDAExecutionProvider")
        add("DmlExecutionProvider")
        add("OpenVINOExecutionProvider")

    add("CPUExecutionProvider")
    return chosen


def onnx_provider_options(providers: list[str]) -> list[dict[str, Any]]:
    """OpenVINO için cihaz tipi (NPU/GPU/CPU); diğerleri boş."""
    opts: list[dict[str, Any]] = []
    dev = (os.environ.get("OPENVINO_DEVICE") or "NPU").strip().upper()
    for p in providers:
        if p == "OpenVINOExecutionProvider":
            opts.append({"device_type": dev})
        else:
            opts.append({})
    return opts


def create_onnx_inference_session(
    model_path: str,
    *,
    providers: list[str] | None = None,
    sess_options: Any | None = None,
):
    """
    ONNX model yükler; NVIDIA / DirectML / Intel NPU mümkünse otomatik seçilir.
    onnxruntime yüklü değilse ImportError.
    """
    import onnxruntime as ort

    prov = providers if providers is not None else preferred_onnx_providers()
    popts = onnx_provider_options(prov)
    kwargs: dict[str, Any] = {"providers": prov, "provider_options": popts}
    if sess_options is not None:
        kwargs["sess_options"] = sess_options
    return ort.InferenceSession(model_path, **kwargs)


def accelerator_summary() -> dict[str, Any]:
    """Teşhis / API için özet (şifre veya model yolu içermez)."""
    onnx_p = _onnx_available_providers()
    return {
        "onnxruntime_providers": onnx_p,
        "preferred_onnx_order": preferred_onnx_providers(),
        "torch_cuda": _torch_cuda_summary(),
    }


def log_startup_accelerators() -> None:
    """Uygulama açılışında bir kez (log + konsol; yerel MSI testinde görünür)."""
    try:
        s = accelerator_summary()
        line = (
            f"[compute] ONNX: {s.get('onnxruntime_providers')} | "
            f"tercih: {s.get('preferred_onnx_order')} | "
            f"torch_cuda: {s.get('torch_cuda')}"
        )
        print(line)
        _log.info(line)
    except Exception as e:
        _log.debug("accelerator_summary: %s", e)
