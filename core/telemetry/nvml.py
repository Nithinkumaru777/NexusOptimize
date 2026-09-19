"""GPU telemetry through NVML (nvidia-ml-py) - replaces nvidia-smi polling.

One NVML init per process; every read degrades to "GPU not detected" instead
of raising, so telemetry keeps running on machines without an NVIDIA GPU.
"""

import threading

import pynvml

_lock = threading.Lock()
_handle = None
_init_failed = False

_EMPTY = {"gpu_name": "GPU not detected", "vram_percent": 0.0, "vram_used_mb": None,
          "vram_total_mb": None, "gpu_util": None, "gpu_temp_c": None}


def _get_handle():
    global _handle, _init_failed
    with _lock:
        if _handle is None and not _init_failed:
            try:
                pynvml.nvmlInit()
                _handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            except pynvml.NVMLError as e:
                print(f"NVML unavailable: {e}")
                _init_failed = True
        return _handle


def get_gpu_stats():
    h = _get_handle()
    if h is None:
        return dict(_EMPTY)
    try:
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        return {
            "gpu_name": pynvml.nvmlDeviceGetName(h),
            "vram_percent": round(100 * mem.used / mem.total, 1),
            "vram_used_mb": mem.used // 2**20,
            "vram_total_mb": mem.total // 2**20,
            "gpu_util": pynvml.nvmlDeviceGetUtilizationRates(h).gpu,
            "gpu_temp_c": pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU),
        }
    except pynvml.NVMLError as e:
        print(f"NVML read failed: {e}")
        return dict(_EMPTY)
