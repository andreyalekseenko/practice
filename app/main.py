from __future__ import annotations

import base64
import io
import json
import os
import random
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from app.perf import PerfCollector, current_perf, route_from_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
DB_PATH = PROJECT_ROOT / "db" / "sets.db"
DB_BUSY_TIMEOUT_SEC = 30.0
DATA_DIR = PROJECT_ROOT / "data"
CANDIDATE_DIR = DATA_DIR / "dataset_candidates"
CANDIDATE_IMAGE_DIR = CANDIDATE_DIR / "images"
EXPORT_DIR = DATA_DIR / "exports"
PREVIEW_DIR = DATA_DIR / "request_preview_images"
TRAINING_RUNS_DIR = DATA_DIR / "training_runs"
RUNTIME_SETTINGS_FILE = Path(os.environ.get("RUNTIME_SETTINGS_FILE", str(DATA_DIR / "runtime_settings.json")))


def _bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except Exception:
        return default


SETTINGS: dict[str, Any] = {
    "DETECT_SCORE_THRESHOLD": _float_env("DETECT_SCORE_THRESHOLD", 0.7),
    "OLD_DETECT_SCORE_THRESHOLD": _float_env("OLD_DETECT_SCORE_THRESHOLD", 0.7),
    "DETECT_NMS_IOU": _float_env("DETECT_NMS_IOU", 0.4),
    "DETECT_DIFF_IOU_THRESHOLD": _float_env("DETECT_DIFF_IOU_THRESHOLD", 0.5),
    "SMB_INGEST_SCORE_THRESHOLD": _float_env("SMB_INGEST_SCORE_THRESHOLD", 0.9),
    "SMB_INGEST_OLD_SCORE_THRESHOLD": _float_env("SMB_INGEST_OLD_SCORE_THRESHOLD", 0.9),
    "SMB_COMPRESS_ENABLED": _bool_env("SMB_COMPRESS_ENABLED", True),
    "SMB_FIX_EXIF_ORIENTATION": _bool_env("SMB_FIX_EXIF_ORIENTATION", True),
    "SMB_COMPRESS_MAX_SIDE": _int_env("SMB_COMPRESS_MAX_SIDE", 1024),
    "SMB_COMPRESS_JPEG_QUALITY": _int_env("SMB_COMPRESS_JPEG_QUALITY", 50),
    "DETECT_IMAGE_SIZE": _int_env("DETECT_IMAGE_SIZE", 1024),
    "MODEL_PATH": os.environ.get("MODEL_PATH", "model_data/best_openvino_model"),
    "EXPORT_ZIP_TTL_HOURS": _int_env("EXPORT_ZIP_TTL_HOURS", 24),
    "DATASET_AUTOSAVE_ENABLED": _bool_env("DATASET_AUTOSAVE_ENABLED", False),
    "REQUEST_PREVIEW_SAVE_ENABLED": _bool_env("REQUEST_PREVIEW_SAVE_ENABLED", True),
    "DATASET_SCORE_THRESHOLD": _float_env("DATASET_SCORE_THRESHOLD", 0.5),
    "DATASET_AUTOSAVE_CLASS_IDS": None,
    "OLD_DETECT_ENABLED": _bool_env("OLD_DETECT_ENABLED", False),
    "TRAINING_RUNS_DIR": str(TRAINING_RUNS_DIR.relative_to(PROJECT_ROOT)),
    "RUNTIME_SETTINGS_FILE": str(RUNTIME_SETTINGS_FILE.relative_to(PROJECT_ROOT)) if RUNTIME_SETTINGS_FILE.is_relative_to(PROJECT_ROOT) else str(RUNTIME_SETTINGS_FILE),
    "PROJECT_TIMEZONE": os.environ.get("PROJECT_TIMEZONE", "Europe/Moscow"),
    "DEFAULT_RELABEL_THRESHOLD": 0.99,
    "DEFAULT_RELABEL_LIMIT": 0,
    "DEFAULT_EXPORT_LIMIT": 3000,
    "DEFAULT_TRAIN_PCT": 70.0,
    "DEFAULT_VAL_PCT": 20.0,
    "DEFAULT_TEST_PCT": 10.0,
    "DEFAULT_TRAIN_EPOCHS": 30,
    "DEFAULT_TRAIN_BATCH": 4,
    "DEFAULT_TRAIN_LIMIT": 0,
    "DEFAULT_SMB_INGEST_LIMIT": 20,
    "DEFAULT_SMB_INGEST_USE_LIMIT": True,
    "DEFAULT_SMB_INGEST_DRY_RUN": True,
    "SMB_SAVE_RESULT_ENABLED": _bool_env("SMB_SAVE_RESULT_ENABLED", True),
}

MODEL_LOCK = threading.Lock()
MODEL_CACHE: dict[str, Any] = {"path": None, "model": None}
JOBS: dict[str, dict[str, Any]] = {"relabel": {}, "train": {}, "smb": {}}
JOBS_LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def rel_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except Exception:
        return str(path)


def abs_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def resolve_model_load_path(path: Path) -> Path:
    """Ultralytics OpenVINO: каталог должен заканчиваться на *_openvino_model."""
    path = path.resolve()
    if not path.is_dir():
        return path
    if path.name.endswith("_openvino_model") and (path / "best.xml").is_file():
        return path
    nested = path / "best_openvino_model"
    if nested.is_dir() and (nested / "best.xml").is_file():
        return nested.resolve()
    if (path / "best.xml").is_file() and (path / "best.bin").is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                f"OpenVINO model folder must be named *_openvino_model (got {path.name!r}). "
                f"Rename to e.g. best_openvino_model or set MODEL_PATH to such directory."
            ),
        )
    return path


def load_runtime_settings() -> None:
    if not RUNTIME_SETTINGS_FILE.is_file():
        return
    try:
        data = json.loads(RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if isinstance(data, dict):
        SETTINGS.update(data)


def save_runtime_settings() -> None:
    RUNTIME_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    persisted = {k: v for k, v in SETTINGS.items() if k != "AVAILABLE_MODELS"}
    RUNTIME_SETTINGS_FILE.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")


def init_dirs() -> None:
    for p in [DB_PATH.parent, DATA_DIR, CANDIDATE_IMAGE_DIR, EXPORT_DIR, PREVIEW_DIR, TRAINING_RUNS_DIR]:
        p.mkdir(parents=True, exist_ok=True)


def init_sqlite_pragmas(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=30000")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=DB_BUSY_TIMEOUT_SEC)
    con.row_factory = sqlite3.Row
    init_sqlite_pragmas(con)
    return con


def db_retry(fn, *, attempts: int = 5):
    """Повтор при sqlite3 database is locked (пик process_link + UI)."""
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last_err = e
            if "locked" not in str(e).lower() or attempt >= attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    if last_err:
        raise last_err


def init_db() -> None:
    init_dirs()
    con = db()
    try:
        con.execute(
            """CREATE TABLE IF NOT EXISTS api_request_log (
                id TEXT PRIMARY KEY, date TEXT, method TEXT, path TEXT, "query" TEXT,
                status_code INTEGER, duration_ms REAL, client_ip TEXT, user_agent TEXT,
                request_size INTEGER, response_size INTEGER, extra_json TEXT
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS request_history (
                id TEXT PRIMARY KEY, date TEXT, event TEXT, rule TEXT, login TEXT, user_agent TEXT, "keys" TEXT
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS dataset_samples (
                id TEXT PRIMARY KEY, created_at TEXT, source_type TEXT, source_path TEXT,
                image_name TEXT, local_image_path TEXT, width INTEGER, height INTEGER,
                status TEXT, model_name TEXT, conf REAL, imgsz INTEGER, api_log_id TEXT,
                extra_json TEXT
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS dataset_annotations (
                id TEXT PRIMARY KEY, sample_id TEXT, class_id INTEGER, class_name TEXT,
                conf REAL, x0 REAL, y0 REAL, x1 REAL, y1 REAL, status TEXT,
                FOREIGN KEY(sample_id) REFERENCES dataset_samples(id)
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS smb_ingest_log (
                source_path TEXT PRIMARY KEY, processed_at TEXT, status TEXT, sample_id TEXT,
                old_count INTEGER, current_count INTEGER, annotation_source TEXT, error TEXT, extra_json TEXT
            )"""
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_api_request_log_date ON api_request_log(date DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_api_request_log_path_date ON api_request_log(path, date DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_dataset_samples_status_created ON dataset_samples(status, created_at DESC)")
        init_sqlite_pragmas(con)
        con.commit()
    finally:
        con.close()


def parse_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_class_ids_env(name: str) -> list[int] | None:
    val = os.environ.get(name)
    if val is None or not str(val).strip():
        return None
    out: list[int] = []
    for part in str(val).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return sorted(set(out))


def normalize_dataset_autosave_class_ids(raw: Any) -> list[int]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",") if p.strip()]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def dataset_autosave_allowed_class_ids() -> set[int] | None:
    """None — все классы (ключ не задан в SETTINGS). Иначе только перечисленные id (пустой set — ничего)."""
    if "DATASET_AUTOSAVE_CLASS_IDS" not in SETTINGS or SETTINGS.get("DATASET_AUTOSAVE_CLASS_IDS") is None:
        return None
    return set(normalize_dataset_autosave_class_ids(SETTINGS.get("DATASET_AUTOSAVE_CLASS_IDS")))


def filter_detections_for_dataset_autosave(detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Порог DATASET_SCORE_THRESHOLD + фильтр по DATASET_AUTOSAVE_CLASS_IDS."""
    thr = float(SETTINGS["DATASET_SCORE_THRESHOLD"])
    keep = [d for d in detections if float(d.get("conf", 0)) >= thr]
    allowed = dataset_autosave_allowed_class_ids()
    if allowed is not None:
        if not allowed:
            return []
        keep = [d for d in keep if int(d.get("class_id", 0)) in allowed]
    return keep


def active_model_classes() -> list[dict[str, Any]]:
    path = resolve_model_load_path(abs_path(str(SETTINGS["MODEL_PATH"])))
    meta_path = path / "metadata.yaml"
    if meta_path.is_file():
        try:
            import yaml

            data = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            names = data.get("names") or {}
            if isinstance(names, dict):
                return [{"id": int(k), "name": str(v)} for k, v in sorted(names.items(), key=lambda kv: int(kv[0]))]
        except Exception:
            pass
    try:
        model = get_model()
        names = getattr(model, "names", None) or {}
        if isinstance(names, dict):
            return [{"id": int(k), "name": str(v)} for k, v in sorted(names.items(), key=lambda kv: int(kv[0]))]
    except Exception:
        pass
    names_file = PROJECT_ROOT / "class_data" / "class.names"
    if names_file.is_file():
        names = [x.strip() for x in names_file.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [{"id": i, "name": n} for i, n in enumerate(names)]
    return [{"id": 0, "name": "container"}]


def list_models() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    model_root = PROJECT_ROOT / "model_data"
    for p in sorted(model_root.glob("*.pt")):
        out.append({"label": p.name, "path": rel_path(p), "type": "pt"})
    for meta in sorted(model_root.rglob("metadata.yaml")):
        d = meta.parent
        out.append({"label": str(d.relative_to(model_root)), "path": rel_path(d), "type": "openvino"})
    return out


def settings_payload() -> dict[str, Any]:
    s = dict(SETTINGS)
    s["AVAILABLE_MODELS"] = list_models()
    s["MODEL_CLASSES"] = active_model_classes()
    ids = SETTINGS.get("DATASET_AUTOSAVE_CLASS_IDS")
    s["DATASET_AUTOSAVE_CLASS_IDS"] = None if ids is None else normalize_dataset_autosave_class_ids(ids)
    return s


def get_model():
    from ultralytics import YOLO

    path = resolve_model_load_path(abs_path(str(SETTINGS["MODEL_PATH"])))
    if not path.exists():
        fallback = PROJECT_ROOT / "model_data" / "garbage.pt"
        if fallback.exists():
            path = fallback
        else:
            raise HTTPException(status_code=503, detail=f"model not found: {path}")
    with MODEL_LOCK:
        if MODEL_CACHE["path"] != str(path):
            MODEL_CACHE["model"] = YOLO(str(path))
            MODEL_CACHE["path"] = str(path)
        return MODEL_CACHE["model"]


def image_from_bytes(raw: bytes) -> np.ndarray:
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="cannot decode image")
    return img


def encode_jpeg(img: np.ndarray, quality: int = 92) -> bytes:
    ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise HTTPException(status_code=500, detail="cannot encode image")
    return enc.tobytes()


def storage_jpeg_quality() -> int:
    """Качество JPEG для превью, датасета и сохранённого результата на SMB (аналог ffmpeg -q)."""
    q = int(SETTINGS.get("SMB_COMPRESS_JPEG_QUALITY", 50))
    return max(40, min(100, q))


def image_from_bytes_exif(raw: bytes) -> np.ndarray:
    """Декодирование PNG/JPEG/etc., с поправкой EXIF orientation при включённом SMB_FIX_EXIF_ORIENTATION."""
    if not SETTINGS.get("SMB_FIX_EXIF_ORIENTATION", True):
        return image_from_bytes(raw)
    try:
        from PIL import Image, ImageOps

        pil = Image.open(io.BytesIO(raw))
        pil = ImageOps.exif_transpose(pil)
        if pil.mode != "RGB":
            pil = pil.convert("RGB")
        arr = np.asarray(pil)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    except Exception:
        return image_from_bytes(raw)


def resize_max_side_bgr(img_bgr: np.ndarray, max_side: int) -> np.ndarray:
    ms = max(64, int(max_side))
    h, w = img_bgr.shape[:2]
    m = max(h, w)
    if m <= ms:
        return img_bgr
    scale = ms / m
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    return cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA)


def prepare_image_before_detection(raw: bytes) -> tuple[np.ndarray, dict[str, Any]]:
    """Перед распознаванием: EXIF при необходимости, затем ограничение длинной стороны SMB_COMPRESS_MAX_SIDE."""
    img = image_from_bytes_exif(raw)
    h0, w0 = img.shape[:2]
    meta: dict[str, Any] = {"decoded_width": w0, "decoded_height": h0}
    if SETTINGS.get("SMB_COMPRESS_ENABLED", True):
        ms = int(SETTINGS["SMB_COMPRESS_MAX_SIDE"])
        img = resize_max_side_bgr(img, ms)
    meta["detect_width"], meta["detect_height"] = int(img.shape[1]), int(img.shape[0])
    meta["storage_jpeg_quality"] = storage_jpeg_quality()
    return img, meta


def save_preview_image_bgr(img_bgr: np.ndarray, request_id: str, *, force_cache: bool = False) -> str:
    """Превью на диск (качество SMB_COMPRESS_JPEG_QUALITY).

    Если force_cache=True — пишем всегда (кэш по запросу журнала /api/request-log/{id}/image из SMB).
    Иначе — только при REQUEST_PREVIEW_SAVE_ENABLED (превью в момент обработки запроса).
    """
    if not force_cache and not SETTINGS.get("REQUEST_PREVIEW_SAVE_ENABLED", True):
        return ""
    path = PREVIEW_DIR / f"{request_id}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_jpeg(img_bgr, storage_jpeg_quality()))
    return rel_path(path)


def run_detection(img_bgr: np.ndarray, conf: float | None = None, imgsz: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    t0 = time.perf_counter()
    conf = float(SETTINGS["DETECT_SCORE_THRESHOLD"] if conf is None else conf)
    imgsz = int(SETTINGS["DETECT_IMAGE_SIZE"] if imgsz is None else imgsz)
    model = get_model()
    result = model.predict(img_bgr, conf=conf, iou=float(SETTINGS["DETECT_NMS_IOU"]), imgsz=imgsz, verbose=False)[0]
    detections: list[dict[str, Any]] = []
    h, w = img_bgr.shape[:2]
    names = getattr(result, "names", None) or getattr(model, "names", {}) or {}
    for box in result.boxes:
        xyxy = [float(v) for v in box.xyxy[0].tolist()]
        cls = int(box.cls[0].item()) if box.cls is not None else 0
        score = float(box.conf[0].item()) if box.conf is not None else 0.0
        area = max(0.0, xyxy[2] - xyxy[0]) * max(0.0, xyxy[3] - xyxy[1])
        detections.append(
            {
                "class_id": cls,
                "name": str(names.get(cls, "container") if isinstance(names, dict) else "container"),
                "conf": round(score, 3),
                "xyxy": [round(v, 2) for v in xyxy],
                "area_px": round(area, 2),
                "area_pct": round(area / max(1, w * h) * 100, 3),
            }
        )
    process = {
        "model_name": Path(str(MODEL_CACHE["path"] or SETTINGS["MODEL_PATH"])).name,
        "model_path": rel_path(Path(str(MODEL_CACHE["path"] or SETTINGS["MODEL_PATH"]))),
        "conf": conf,
        "imgsz": imgsz,
        "width": w,
        "height": h,
        "inference_ms": round((time.perf_counter() - t0) * 1000, 2),
    }
    return detections, process


def legacy_result(detections: list[dict[str, Any]], orientation: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    orientation = orientation or {"view": None, "val": None, "error": ["not found exif for image"], "state": False}
    out: list[dict[str, Any]] = [{"count": len(detections), "orientation": orientation}]
    for d in detections:
        x0, y0, x1, y1 = d["xyxy"]
        out.append({"key": float(d.get("class_id", 0)), "ratio": round(float(d.get("conf", 0)), 3), "x0": int(round(x0)), "x1": int(round(x1)), "y0": int(round(y0)), "y1": int(round(y1))})
    return out


def save_candidate(
    img_bgr: np.ndarray,
    detections: list[dict[str, Any]],
    *,
    source_type: str,
    source_path: str,
    image_name: str,
    conf: float,
    imgsz: int,
    api_log_id: str | None = None,
    extra: dict[str, Any] | None = None,
    status: str = "candidate",
) -> str | None:
    if not detections:
        return None
    sample_id = str(uuid.uuid4())
    image_path = CANDIDATE_IMAGE_DIR / f"{sample_id}.jpg"
    image_path.write_bytes(encode_jpeg(img_bgr, storage_jpeg_quality()))
    h, w = img_bgr.shape[:2]
    model_name = Path(str(MODEL_CACHE["path"] or SETTINGS["MODEL_PATH"])).name
    con = db()
    try:
        con.execute(
            """INSERT INTO dataset_samples
               (id, created_at, source_type, source_path, image_name, local_image_path, width, height, status, model_name, conf, imgsz, api_log_id, extra_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sample_id, now_iso(), source_type, source_path, image_name, rel_path(image_path), w, h, status, model_name, conf, imgsz, api_log_id, json.dumps(extra or {}, ensure_ascii=False)),
        )
        for d in detections:
            x0, y0, x1, y1 = d["xyxy"]
            con.execute(
                """INSERT INTO dataset_annotations
                   (id, sample_id, class_id, class_name, conf, x0, y0, x1, y1, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), sample_id, int(d.get("class_id", 0)), str(d.get("name", "container")), float(d.get("conf", 0)), x0, y0, x1, y1, status),
            )
        con.commit()
    finally:
        con.close()
    return sample_id


def update_sample_status(sample_id: str, status: str) -> dict[str, Any]:
    con = db()
    try:
        con.execute("UPDATE dataset_samples SET status=? WHERE id=?", (status, sample_id))
        con.execute("UPDATE dataset_annotations SET status=? WHERE sample_id=?", (status, sample_id))
        con.commit()
    finally:
        con.close()
    return {"id": sample_id, "status": status}


def delete_samples(where: str, params: tuple[Any, ...]) -> dict[str, Any]:
    con = db()
    deleted_files = 0
    try:
        rows = con.execute(f"SELECT id, local_image_path FROM dataset_samples WHERE {where}", params).fetchall()
        ids = [r["id"] for r in rows]
        for r in rows:
            p = abs_path(r["local_image_path"])
            if p.is_file():
                p.unlink()
                deleted_files += 1
        if ids:
            placeholders = ",".join("?" for _ in ids)
            con.execute(f"DELETE FROM dataset_annotations WHERE sample_id IN ({placeholders})", ids)
            con.execute(f"DELETE FROM dataset_samples WHERE id IN ({placeholders})", ids)
            con.commit()
    finally:
        con.close()
    return {"deleted_samples": len(ids), "deleted_files": deleted_files, "counts": dataset_counts()}


def dataset_counts() -> dict[str, int]:
    con = db()
    try:
        return {r["status"]: int(r["cnt"]) for r in con.execute("SELECT status, COUNT(*) AS cnt FROM dataset_samples GROUP BY status")}
    finally:
        con.close()


def smb_unc_candidates(link_photo: str) -> list[str]:
    raw = (link_photo or "").strip().strip('"')
    raw = raw.replace("\\/", "/").replace("smb://", "\\\\")
    raw = raw.replace("/", "\\") if raw.startswith("\\\\") else raw
    server = os.environ.get("SMB_SERVER", "fs.mag-rf.ru")
    if raw.startswith("\\\\"):
        unc = raw
    elif raw.startswith("/"):
        parts = raw.strip("/").split("/", 1)
        if len(parts) == 2:
            unc = f"\\\\{server}\\{parts[0]}\\{parts[1].replace('/', '\\')}"
        else:
            unc = f"\\\\{server}\\{raw.strip('/').replace('/', '\\')}"
    else:
        unc = raw
    variants = {unc}
    for a, b in [("\u00a0", " "), ("\u202f", " "), (" ", "\u00a0"), (" ", "\u202f")]:
        variants.add(unc.replace(a, b))
    return list(variants)


def smb_register_session() -> None:
    import smbclient

    username = os.environ.get("SMB_USERNAME")
    password = os.environ.get("SMB_PASSWORD")
    domain = os.environ.get("SMB_DOMAIN")
    server = os.environ.get("SMB_SERVER", "fs.mag-rf.ru")
    if not username or not password:
        return
    session_user = username
    if domain and "\\" not in username and "@" not in username:
        session_user = f"{domain}\\{username}"
    smbclient.register_session(server, username=session_user, password=password)


def smb_read(link_photo: str) -> bytes:
    try:
        import smbclient

        smb_register_session()
        errors = []
        for unc in smb_unc_candidates(link_photo):
            try:
                with smbclient.open_file(unc, mode="rb") as f:
                    return f.read()
            except Exception as e:
                errors.append(f"{unc}: {e}")
        raise FileNotFoundError("; ".join(errors[-3:]))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"not found link {link_photo}: {e}") from e


def smb_write_bytes(destination: str, data: bytes) -> str:
    import smbclient

    smb_register_session()
    errors: list[str] = []
    for unc in smb_unc_candidates(destination):
        try:
            pw = PureWindowsPath(unc)
            parent_s = str(pw.parent)
            if parent_s and parent_s != unc:
                smbclient.makedirs(parent_s, exist_ok=True)
            with smbclient.open_file(unc, mode="wb") as f:
                f.write(data)
            return unc
        except Exception as e:
            errors.append(f"{unc}: {e}")
    raise OSError("; ".join(errors) if errors else "smb write failed")


def resolve_smb_result_save_path(payload: dict[str, Any], link_photo: str) -> str | None:
    """Цель записи: явный путь из JSON иначе тот же UNC что и link_photo (перезапись исходника сжатым JPEG)."""
    for key in ("save_result_photo", "link_photo_result", "save_annotated_photo", "result_photo_path"):
        v = payload.get(key)
        if v is not None and str(v).strip():
            return str(v).strip().strip('"')
    lp = (link_photo or "").strip().strip('"')
    return lp or None


def try_save_smb_processed_photo(payload: dict[str, Any], link_photo: str, img_bgr: np.ndarray) -> dict[str, Any]:
    """Перезапись на SMB сжатым кадром (без рамок). Отключить полностью: SMB_SAVE_RESULT_ENABLED=false.

    Возвращает поля для extra_json: smb_on_disk_status (written|disabled|no_path|error), при ошибке smb_save_error.
    """
    info: dict[str, Any] = {}
    lp = (link_photo or "").strip().strip('"')
    if not SETTINGS.get("SMB_SAVE_RESULT_ENABLED", True):
        if lp:
            info["smb_on_disk_status"] = "disabled"
        return info
    dest = resolve_smb_result_save_path(payload, link_photo)
    if not dest:
        info["smb_on_disk_status"] = "no_path"
        return info
    try:
        data = encode_jpeg(img_bgr, storage_jpeg_quality())
        smb_write_bytes(dest, data)
        info["smb_saved_result"] = dest
        info["smb_on_disk_status"] = "written"
        info["smb_written_jpeg_bytes"] = len(data)
        info["smb_written_detect_w"] = int(img_bgr.shape[1])
        info["smb_written_detect_h"] = int(img_bgr.shape[0])
    except Exception as e:
        info["smb_save_error"] = str(e)
        info["smb_on_disk_status"] = "error"
    return info


class ApiLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.api_log_id = request_id
        request.state.api_extra = {}
        body = await request.body()

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, receive)
        request.state.api_log_id = request_id
        request.state.api_extra = {}
        perf = PerfCollector(route_from_path(request.url.path))
        token = current_perf.set(perf)
        start = time.perf_counter()
        status_code = 500
        response_size = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            response_size = response.headers.get("content-length")
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            extra = getattr(request.state, "api_extra", {}) or {}
            perf_data = perf.finalize()
            extra["perf"] = perf_data
            try:
                def _write_log() -> None:
                    con = db()
                    try:
                        con.execute(
                            """INSERT OR REPLACE INTO api_request_log
                               (id, date, method, path, "query", status_code, duration_ms, client_ip, user_agent, request_size, response_size, extra_json)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                request_id,
                                now_iso(),
                                request.method,
                                request.url.path,
                                request.url.query,
                                status_code,
                                duration_ms,
                                request.client.host if request.client else "",
                                request.headers.get("user-agent", ""),
                                len(body) if body else None,
                                int(response_size) if response_size and response_size.isdigit() else None,
                                json.dumps(extra, ensure_ascii=False) if extra else None,
                            ),
                        )
                        con.commit()
                    finally:
                        con.close()

                db_retry(_write_log)
            except Exception:
                pass
            current_perf.reset(token)


_env_class_ids = _parse_class_ids_env("DATASET_AUTOSAVE_CLASS_IDS")
if _env_class_ids is not None:
    SETTINGS["DATASET_AUTOSAVE_CLASS_IDS"] = _env_class_ids

load_runtime_settings()
if SETTINGS.get("DATASET_AUTOSAVE_CLASS_IDS") is not None:
    SETTINGS["DATASET_AUTOSAVE_CLASS_IDS"] = normalize_dataset_autosave_class_ids(SETTINGS["DATASET_AUTOSAVE_CLASS_IDS"])
init_db()
app = FastAPI(title="Garbage detection", version="0.1.0")
app.add_middleware(ApiLogMiddleware)


@app.get("/")
def root_redirect():
    return RedirectResponse(url="/index.html")


@app.get("/index.html")
def index_html():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/app.js")
def app_js():
    return FileResponse(str(STATIC_DIR / "app.js"), media_type="text/javascript")


@app.get("/style.css")
def style_css():
    return FileResponse(str(STATIC_DIR / "style.css"), media_type="text/css")


@app.get("/manifest.webmanifest")
def manifest_webmanifest():
    return FileResponse(str(STATIC_DIR / "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(str(STATIC_DIR / "sw.js"), media_type="application/javascript", headers={"Cache-Control": "no-cache"})


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


@app.post("/api/predict")
async def predict(request: Request, file: UploadFile, conf: float = Query(default=0.7, ge=0.01, le=0.999)):
    raw = await file.read()
    img, preprocess = prepare_image_before_detection(raw)
    save_preview_image_bgr(img, request.state.api_log_id)
    detections, process = run_detection(img, conf=conf)
    request.state.api_extra.update({"image_name": file.filename, "image_preprocess": preprocess, "result_count": len(detections), "detections": detections, **process})
    if SETTINGS.get("DATASET_AUTOSAVE_ENABLED"):
        keep = filter_detections_for_dataset_autosave(detections)
        sample_id = save_candidate(img, keep, source_type="predict", source_path=file.filename or "", image_name=file.filename or "upload.jpg", conf=conf, imgsz=process["imgsz"], api_log_id=request.state.api_log_id, extra={"current_detections": detections, "dataset_autosave_detections": keep})
        request.state.api_extra["dataset_sample_id"] = sample_id
    return {"detections": detections, "process": process, "image": {"width": process["width"], "height": process["height"], "filename": file.filename}}


@app.post("/api/request-log/cleanup-previews")
def cleanup_request_previews(days: int = Query(default=14, ge=1, le=3650)):
    """Удаляет JPEG в data/request_preview_images: по дате записи в журнале или по mtime (если строки журнала нет)."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=int(days))
    deleted = 0
    errors: list[str] = []
    con = db()
    try:
        for path in PREVIEW_DIR.glob("*.jpg"):
            log_id = path.stem
            try:
                row = con.execute("SELECT date FROM api_request_log WHERE id=?", (log_id,)).fetchone()
                if row:
                    try:
                        d = datetime.fromisoformat(str(row["date"]))
                    except ValueError:
                        continue
                    if d >= cutoff:
                        continue
                else:
                    fm = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)
                    if fm >= cutoff:
                        continue
                path.unlink()
                deleted += 1
            except OSError as e:
                errors.append(f"{path.name}: {e}")
        return {"deleted": deleted, "older_than_days": days, "errors": errors}
    finally:
        con.close()


@app.get("/api/request-history")
def api_request_history(
    limit: int = Query(default=10, ge=1, le=200),
    kind: str = Query(default="all"),
    client_ip: str | None = Query(default=None),
    path_contains: str | None = Query(default=None),
):
    con = db()
    try:
        where: list[str] = []
        params: list[Any] = []
        if kind == "1c":
            where.append("path = ?")
            params.append("/api/v1/projects/process_link")
        elif kind == "predict":
            where.append("path = ?")
            params.append("/api/predict")
        elif kind == "ui":
            where.append("path NOT IN (?, ?)")
            params.extend(["/api/v1/projects/process_link", "/api/predict"])
        if client_ip:
            where.append("client_ip = ?")
            params.append(client_ip)
        if path_contains:
            where.append("path LIKE ?")
            params.append(f"%{path_contains}%")
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = con.execute(
            f"""SELECT id, date, method, path, "query", status_code, duration_ms, client_ip, user_agent,
                      request_size, response_size, extra_json
               FROM api_request_log {where_sql} ORDER BY date DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            item["extra"] = parse_json(item.pop("extra_json", None))
            items.append(item)
        return {"items": items}
    finally:
        con.close()


@app.get("/api/request-log/{log_id}")
def api_request_log_item(log_id: str):
    con = db()
    try:
        row = con.execute("SELECT * FROM api_request_log WHERE id=?", (log_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="log not found")
        item = dict(row)
        item["extra"] = parse_json(item.pop("extra_json", None))
        return item
    finally:
        con.close()


@app.get("/api/request-log/{log_id}/image")
def api_request_log_image(log_id: str):
    path = PREVIEW_DIR / f"{log_id}.jpg"
    if path.is_file():
        return FileResponse(str(path))
    con = db()
    try:
        row = con.execute("SELECT extra_json FROM api_request_log WHERE id=?", (log_id,)).fetchone()
    finally:
        con.close()
    if not row:
        raise HTTPException(status_code=404, detail="log not found")

    extra = parse_json(row["extra_json"]) or {}
    link_photo = extra.get("link_photo")
    if link_photo:
        try:
            raw = smb_read(str(link_photo))
            img_reload, prep_reload = prepare_image_before_detection(raw)
            save_preview_image_bgr(img_reload, log_id, force_cache=True)
            if path.is_file():
                return FileResponse(str(path))
        except Exception as e:
            detail = getattr(e, "detail", None) or str(e)
            raise HTTPException(status_code=404, detail=f"preview image not found and SMB reload failed: {detail}") from e

    sample_id = extra.get("dataset_sample_id")
    if sample_id:
        con = db()
        try:
            sample = con.execute("SELECT local_image_path FROM dataset_samples WHERE id=?", (sample_id,)).fetchone()
        finally:
            con.close()
        if sample:
            sample_path = abs_path(sample["local_image_path"])
            if sample_path.is_file():
                return FileResponse(str(sample_path))

    raise HTTPException(status_code=404, detail="preview image not found for this API log")


@app.get("/api/dataset/candidates")
def dataset_candidates(status: str = "candidate", limit: int = Query(default=50, ge=1, le=500)):
    con = db()
    try:
        where = "" if status == "all" else "WHERE status=?"
        params: tuple[Any, ...] = () if status == "all" else (status,)
        rows = con.execute(
            f"""SELECT * FROM dataset_samples {where} ORDER BY created_at DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            item["extra"] = parse_json(item.pop("extra_json", None)) or {}
            anns = con.execute("SELECT * FROM dataset_annotations WHERE sample_id=? ORDER BY id", (item["id"],)).fetchall()
            item["annotations"] = [
                {**dict(a), "xyxy": [a["x0"], a["y0"], a["x1"], a["y1"]]} for a in anns
            ]
            items.append(item)
        allowed = dataset_autosave_allowed_class_ids()
        return {
            "items": items,
            "counts": dataset_counts(),
            "autosave_enabled": SETTINGS["DATASET_AUTOSAVE_ENABLED"],
            "autosave_class_ids": None if allowed is None else sorted(allowed),
            "autosave_all_classes": allowed is None,
            "model_classes": active_model_classes(),
        }
    finally:
        con.close()


@app.get("/api/dataset/sample/{sample_id}/image")
def dataset_sample_image(sample_id: str):
    con = db()
    try:
        row = con.execute("SELECT local_image_path FROM dataset_samples WHERE id=?", (sample_id,)).fetchone()
    finally:
        con.close()
    if not row:
        raise HTTPException(status_code=404, detail="sample not found")
    p = abs_path(row["local_image_path"])
    if not p.is_file():
        raise HTTPException(status_code=404, detail="image file not found")
    return FileResponse(str(p))


@app.post("/api/dataset/sample/{sample_id}/approve")
def dataset_sample_approve(sample_id: str):
    return update_sample_status(sample_id, "approved")


@app.post("/api/dataset/sample/{sample_id}/reject")
def dataset_sample_reject(sample_id: str):
    return update_sample_status(sample_id, "rejected")


@app.post("/api/dataset/delete-candidates")
def dataset_delete_candidates():
    return delete_samples("status=?", ("candidate",))


@app.post("/api/dataset/delete-labeled")
def dataset_delete_labeled():
    return delete_samples("status IN ('approved','rejected')", ())


@app.post("/api/dataset/cleanup")
def dataset_cleanup():
    ttl = int(SETTINGS["EXPORT_ZIP_TTL_HOURS"]) * 3600
    now = time.time()
    deleted = 0
    for p in list(EXPORT_DIR.glob("*.zip")):
        if now - p.stat().st_mtime > ttl:
            p.unlink()
            deleted += 1
    return {"deleted_exports": deleted}


def split_items(items: list[sqlite3.Row], train_pct: float, val_pct: float, test_pct: float):
    items = list(items)
    random.Random(42).shuffle(items)
    total = len(items)
    train_n = int(total * train_pct / 100)
    val_n = int(total * val_pct / 100)
    return {"train": items[:train_n], "val": items[train_n : train_n + val_n], "test": items[train_n + val_n :]}


def yolo_line(a: sqlite3.Row, w: int, h: int) -> str:
    x0, y0, x1, y1 = float(a["x0"]), float(a["y0"]), float(a["x1"]), float(a["y1"])
    cx = ((x0 + x1) / 2) / w
    cy = ((y0 + y1) / 2) / h
    bw = (x1 - x0) / w
    bh = (y1 - y0) / h
    return f"{int(a['class_id'])} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


@app.post("/api/dataset/export")
async def dataset_export(request: Request):
    payload = await request.json() if request.headers.get("content-length") else {}
    limit = payload.get("limit") or int(SETTINGS["DEFAULT_EXPORT_LIMIT"]) or None
    train_pct = float(payload.get("train_pct", SETTINGS["DEFAULT_TRAIN_PCT"]))
    val_pct = float(payload.get("val_pct", SETTINGS["DEFAULT_VAL_PCT"]))
    test_pct = float(payload.get("test_pct", SETTINGS["DEFAULT_TEST_PCT"]))
    zip_name = f"dataset_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    query = f"train_pct={train_pct}&val_pct={val_pct}&test_pct={test_pct}"
    if limit:
        query = f"limit={int(limit)}&{query}"
    return {"zip_name": zip_name, "download_url": f"/api/dataset/export/{zip_name}?{query}", "split": {"train_pct": train_pct, "val_pct": val_pct, "test_pct": test_pct}}


@app.get("/api/dataset/export/{zip_name}")
def dataset_export_download(zip_name: str, limit: int | None = Query(default=None, ge=1, le=100000), train_pct: float = Query(default=70.0, ge=0, le=100), val_pct: float = Query(default=20.0, ge=0, le=100), test_pct: float = Query(default=10.0, ge=0, le=100)):
    if not zip_name.endswith(".zip"):
        raise HTTPException(status_code=400, detail="zip_name must end with .zip")
    con = db()
    try:
        sql = "SELECT * FROM dataset_samples WHERE status='approved' ORDER BY created_at DESC"
        params: tuple[Any, ...] = ()
        if limit:
            sql += " LIMIT ?"
            params = (limit,)
        samples = con.execute(sql, params).fetchall()
        split = split_items(samples, train_pct, val_pct, test_pct)
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("data.yaml", "path: .\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n  0: container\n")
            for part, rows in split.items():
                for s in rows:
                    img = abs_path(s["local_image_path"])
                    if not img.is_file():
                        continue
                    anns = con.execute("SELECT * FROM dataset_annotations WHERE sample_id=? AND status='approved'", (s["id"],)).fetchall()
                    stem = f"{s['id']}.jpg"
                    z.write(img, f"images/{part}/{stem}")
                    z.writestr(f"labels/{part}/{s['id']}.txt", "\n".join(yolo_line(a, int(s["width"]), int(s["height"])) for a in anns) + ("\n" if anns else ""))
        mem.seek(0)
        return StreamingResponse(mem, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{zip_name}"'})
    finally:
        con.close()


@app.get("/api/settings")
def api_settings():
    return {"settings": settings_payload()}


@app.post("/api/settings")
async def api_settings_update(request: Request):
    payload = await request.json()
    if isinstance(payload, dict):
        if "DATASET_AUTOSAVE_CLASS_IDS" in payload:
            payload = dict(payload)
            payload["DATASET_AUTOSAVE_CLASS_IDS"] = normalize_dataset_autosave_class_ids(payload["DATASET_AUTOSAVE_CLASS_IDS"])
        SETTINGS.update(payload)
        save_runtime_settings()
        with MODEL_LOCK:
            MODEL_CACHE["path"] = None
            MODEL_CACHE["model"] = None
    return {"settings": settings_payload()}


@app.get("/api/models")
def api_models():
    return {"items": list_models(), "active": SETTINGS["MODEL_PATH"]}


def start_job(kind: str, target, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    job = {"id": job_id, "status": "queued", "created_at": now_iso(), "updated_at": now_iso(), "payload": payload, "progress": {"done": 0, "total": 0}, "result": None, "error": ""}
    with JOBS_LOCK:
        JOBS[kind][job_id] = job
    threading.Thread(target=target, args=(job_id, payload), daemon=True).start()
    return job


def update_job(kind: str, job_id: str, **changes) -> None:
    with JOBS_LOCK:
        job = JOBS[kind].get(job_id)
        if job:
            job.update(changes)
            job["updated_at"] = now_iso()


def get_job(kind: str, job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS[kind].get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def relabel_worker(job_id: str, payload: dict[str, Any]) -> None:
    threshold = float(payload.get("threshold", SETTINGS["DEFAULT_RELABEL_THRESHOLD"]))
    limit = payload.get("limit") or int(SETTINGS["DEFAULT_RELABEL_LIMIT"]) or None
    update_job("relabel", job_id, status="running")
    con = db()
    approved = 0
    try:
        sql = "SELECT id FROM dataset_samples WHERE status='candidate' ORDER BY created_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = con.execute(sql).fetchall()
        total = len(rows)
        for i, row in enumerate(rows, 1):
            anns = con.execute("SELECT MAX(conf) AS mx FROM dataset_annotations WHERE sample_id=?", (row["id"],)).fetchone()
            if anns and anns["mx"] is not None and float(anns["mx"]) >= threshold:
                update_sample_status(row["id"], "approved")
                approved += 1
            update_job("relabel", job_id, progress={"done": i, "total": total}, result={"approved": approved})
        update_job("relabel", job_id, status="completed", result={"approved": approved, "total": total})
    except Exception as e:
        update_job("relabel", job_id, status="failed", error=str(e))
    finally:
        con.close()


@app.post("/api/dataset/relabel-candidates")
async def dataset_relabel_candidates(request: Request):
    payload = await request.json() if request.headers.get("content-length") else {}
    return start_job("relabel", relabel_worker, payload)


@app.get("/api/dataset/relabel-candidates/{job_id}")
def dataset_relabel_candidates_status(job_id: str):
    return get_job("relabel", job_id)


def train_worker(job_id: str, payload: dict[str, Any]) -> None:
    update_job("train", job_id, status="running")
    try:
        # Rebuild a YOLO dataset archive/run folder. Real training can be started manually
        # from this folder; this keeps the API non-destructive after source recovery.
        run_dir = TRAINING_RUNS_DIR / job_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "params.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        update_job("train", job_id, status="completed", result={"run_dir": rel_path(run_dir), "note": "training source restored; dataset prep recorded"})
    except Exception as e:
        update_job("train", job_id, status="failed", error=str(e))


@app.get("/api/dataset/train")
def dataset_train_jobs():
    return {"items": list(JOBS["train"].values())}


@app.post("/api/dataset/train")
async def dataset_train(request: Request):
    payload = await request.json() if request.headers.get("content-length") else {}
    return start_job("train", train_worker, payload)


@app.get("/api/dataset/train/{job_id}")
def dataset_train_status(job_id: str):
    return get_job("train", job_id)


def ingest_worker(job_id: str, payload: dict[str, Any]) -> None:
    dirs = payload.get("directories") or payload.get("dirs") or []
    limit = None if payload.get("no_limit") else (payload.get("limit") or int(SETTINGS["DEFAULT_SMB_INGEST_LIMIT"]))
    dry_run = bool(payload.get("dry_run", SETTINGS["DEFAULT_SMB_INGEST_DRY_RUN"]))
    update_job("smb", job_id, status="running")
    try:
        import smbclient

        files: list[str] = []
        for d in dirs:
            for unc in smb_unc_candidates(str(d)):
                try:
                    for name in smbclient.listdir(unc):
                        if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp")):
                            files.append(unc.rstrip("\\") + "\\" + name)
                    break
                except Exception:
                    continue
        if limit:
            files = files[: int(limit)]
        added = 0
        errors = 0
        for i, path in enumerate(files, 1):
            try:
                if not dry_run:
                    raw = smb_read(path)
                    img, _prep = prepare_image_before_detection(raw)
                    dets, proc = run_detection(img, conf=float(SETTINGS["SMB_INGEST_SCORE_THRESHOLD"]))
                    keep = filter_detections_for_dataset_autosave(dets)
                    if keep:
                        save_candidate(img, keep, source_type="smb_ingest", source_path=path, image_name=Path(path).name, conf=proc["conf"], imgsz=proc["imgsz"], extra={"current_detections": dets, "dataset_autosave_detections": keep, "annotation_source": "current"})
                        added += 1
                update_job("smb", job_id, progress={"done": i, "total": len(files)}, result={"scanned": i, "added": added, "errors": errors, "dry_run": dry_run})
            except Exception:
                errors += 1
        update_job("smb", job_id, status="completed", result={"scanned": len(files), "added": added, "errors": errors, "dry_run": dry_run})
    except Exception as e:
        update_job("smb", job_id, status="failed", error=str(e))


@app.get("/api/dataset/ingest-smb")
def dataset_ingest_smb_jobs():
    return {"items": list(JOBS["smb"].values())}


@app.post("/api/dataset/ingest-smb")
async def dataset_ingest_smb(request: Request):
    payload = await request.json() if request.headers.get("content-length") else {}
    return start_job("smb", ingest_worker, payload)


@app.get("/api/dataset/ingest-smb/{job_id}")
def dataset_ingest_smb_status(job_id: str):
    return get_job("smb", job_id)


@app.get("/api/legacy-request-history")
def legacy_request_history(limit: int = Query(default=20, ge=1, le=500), rule: str | None = None):
    con = db()
    try:
        where = ""
        params: list[Any] = []
        if rule:
            where = "WHERE rule LIKE ?"
            params.append(f"%{rule}%")
        rows = con.execute(f'SELECT * FROM request_history {where} ORDER BY date DESC LIMIT ?', (*params, limit)).fetchall()
        return {"items": [dict(r) for r in rows]}
    finally:
        con.close()


@app.get("/api/v1/base-class/get_base_classes")
def get_base_classes():
    names_file = PROJECT_ROOT / "class_data" / "class.names"
    names = [x.strip() for x in names_file.read_text(encoding="utf-8").splitlines() if x.strip()] if names_file.is_file() else ["container"]
    return {"error": "", "status": True, "result": [{"key": float(i), "name": name} for i, name in enumerate(names)]}


@app.post("/api/v1/projects/process_link")
async def process_link(request: Request):
    payload = await request.json()
    link = payload.get("link_photo") or payload.get("path") or payload.get("url") or ""
    login = payload.get("login") or "superuser"

    def _write_history() -> None:
        con = db()
        try:
            con.execute(
                "INSERT OR REPLACE INTO request_history (id, date, event, rule, login, user_agent, \"keys\") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), now_iso(), "request", "/api/v1/projects/process_link", login, request.headers.get("user-agent", ""), json.dumps(payload, ensure_ascii=False)),
            )
            con.commit()
        finally:
            con.close()

    db_retry(_write_history)
    try:
        raw = smb_read(link)
        img, preprocess_meta = prepare_image_before_detection(raw)
        save_preview_image_bgr(img, request.state.api_log_id)
        dets, proc = run_detection(img, conf=float(SETTINGS["DETECT_SCORE_THRESHOLD"]))
        result = legacy_result(dets)
        extra = {
            "login": login,
            "link_photo": link,
            "legacy_status": True,
            "result_count": len(dets),
            "legacy_result": result,
            "current_detections": dets,
            "image_preprocess": preprocess_meta,
            **proc,
        }
        extra.update(try_save_smb_processed_photo(payload, link, img))
        if SETTINGS.get("DATASET_AUTOSAVE_ENABLED"):
            keep = filter_detections_for_dataset_autosave(dets)
            sample_id = save_candidate(img, keep, source_type="process_link", source_path=link, image_name=Path(link.replace("\\", "/")).name, conf=float(SETTINGS["DATASET_SCORE_THRESHOLD"]), imgsz=proc["imgsz"], api_log_id=request.state.api_log_id, extra={"current_detections": dets, "dataset_autosave_detections": keep, "old_detections": [], "annotation_source": "current"})
            extra["dataset_sample_id"] = sample_id
        request.state.api_extra.update(extra)
        out = {"error": "", "result": result, "status": True}
        if extra.get("smb_saved_result"):
            out["saved_result_photo"] = extra["smb_saved_result"]
        if extra.get("smb_save_error"):
            out["save_photo_error"] = extra["smb_save_error"]
        return out
    except Exception as e:
        msg = str(e)
        request.state.api_extra.update({"login": login, "link_photo": link, "legacy_status": False, "legacy_error": msg})
        return {"error": msg, "result": [{"count": 0, "orientation": {"view": None, "val": None, "error": [msg], "state": False}}], "status": False}


@app.post("/api/v1/projects/process_photo")
async def process_photo(request: Request):
    payload = await request.json()
    raw_b64 = payload.get("photo") or payload.get("image") or payload.get("file") or ""
    try:
        raw = base64.b64decode(raw_b64, validate=False)
        img, preprocess_meta = prepare_image_before_detection(raw)
        save_preview_image_bgr(img, request.state.api_log_id)
        dets, proc = run_detection(img, conf=float(SETTINGS["DETECT_SCORE_THRESHOLD"]))
        result = legacy_result(dets)
        extra = {"legacy_status": True, "result_count": len(dets), "legacy_result": result, "current_detections": dets, "image_preprocess": preprocess_meta, **proc}
        extra.update(try_save_smb_processed_photo(payload, "", img))
        request.state.api_extra.update(extra)
        out = {"error": "", "result": result, "status": True}
        if extra.get("smb_saved_result"):
            out["saved_result_photo"] = extra["smb_saved_result"]
        if extra.get("smb_save_error"):
            out["save_photo_error"] = extra["smb_save_error"]
        return out
    except Exception as e:
        msg = str(e)
        request.state.api_extra.update({"legacy_status": False, "legacy_error": msg})
        return {"error": msg, "result": [{"count": 0, "orientation": {"view": None, "val": None, "error": [msg], "state": False}}], "status": False}


@app.get("/api/metrics/latency-samples")
def api_latency_samples(limit: int = Query(200, ge=1, le=500), route: str | None = Query(None, description="Подстрока в path (например process_link или predict).")):
    con = db()
    try:
        where = 'WHERE extra_json IS NOT NULL AND extra_json LIKE \'%"perf"%\''
        params: list[Any] = []
        if route:
            where += " AND path LIKE ?"
            params.append(f"%{route}%")
        rows = con.execute(f"SELECT id, date, path, status_code, duration_ms, extra_json FROM api_request_log {where} ORDER BY date DESC LIMIT ?", (*params, limit)).fetchall()
        items = []
        for r in rows:
            extra = parse_json(r["extra_json"]) or {}
            items.append({"id": r["id"], "date": r["date"], "path": r["path"], "status_code": r["status_code"], "duration_ms": r["duration_ms"], "perf": extra.get("perf") or {}})
        return {"items": items}
    finally:
        con.close()


@app.get("/api/metrics/recognition-speed")
def recognition_speed_metrics(
    kind: str = Query(default="1c"),
    hours: int = Query(default=24, ge=1, le=720),
    bucket_minutes: int = Query(default=15, ge=1, le=1440),
    metric: str = Query(default="duration_ms"),
):
    path_map = {
        "1c": ["/api/v1/projects/process_link"],
        "predict": ["/api/predict"],
        "all": ["/api/v1/projects/process_link", "/api/predict", "/api/v1/projects/process_photo"],
    }
    paths = path_map.get(kind, path_map["1c"])
    since_ts = time.time() - hours * 3600
    placeholders = ",".join("?" for _ in paths)
    con = db()
    try:
        rows = con.execute(
            f"""SELECT date, path, duration_ms, extra_json
                FROM api_request_log
                WHERE path IN ({placeholders}) AND status_code BETWEEN 200 AND 299
                ORDER BY date ASC""",
            tuple(paths),
        ).fetchall()
    finally:
        con.close()

    buckets: dict[int, list[float]] = {}
    slowest: dict[str, Any] | None = None
    fastest: dict[str, Any] | None = None
    values: list[float] = []
    bucket_sec = bucket_minutes * 60

    for row in rows:
        try:
            dt = datetime.fromisoformat(str(row["date"]).replace("Z", ""))
            ts = dt.replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            continue
        if ts < since_ts:
            continue
        extra = parse_json(row["extra_json"]) or {}
        if metric == "inference_ms":
            value = extra.get("inference_ms") or (extra.get("perf") or {}).get("inference_ms")
        else:
            value = row["duration_ms"]
        try:
            value = float(value)
        except Exception:
            continue
        if value < 0:
            continue
        bucket = int(ts // bucket_sec) * bucket_sec
        buckets.setdefault(bucket, []).append(value)
        values.append(value)
        point = {"date": row["date"], "path": row["path"], "value_ms": round(value, 2), "result_count": extra.get("result_count"), "image_name": extra.get("image_name") or Path(str(extra.get("link_photo") or "")).name}
        if slowest is None or value > slowest["value_ms"]:
            slowest = point
        if fastest is None or value < fastest["value_ms"]:
            fastest = point

    points = []
    for bucket in sorted(buckets):
        vals = buckets[bucket]
        avg = sum(vals) / len(vals)
        points.append(
            {
                "bucket_start": datetime.fromtimestamp(bucket, tz=timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
                "count": len(vals),
                "min_ms": round(min(vals), 2),
                "avg_ms": round(avg, 2),
                "max_ms": round(max(vals), 2),
            }
        )

    summary = {
        "count": len(values),
        "min_ms": round(min(values), 2) if values else None,
        "avg_ms": round(sum(values) / len(values), 2) if values else None,
        "max_ms": round(max(values), 2) if values else None,
        "slowest": slowest,
        "fastest": fastest,
    }
    return {"kind": kind, "hours": hours, "bucket_minutes": bucket_minutes, "metric": metric, "summary": summary, "points": points}

