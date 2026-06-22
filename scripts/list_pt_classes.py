"""
Список имён классов в YOLO .pt (Ultralytics): читает data.pkl внутри zip, без torch.
"""
from __future__ import annotations

import argparse
import struct
import zipfile
from pathlib import Path
from typing import Optional, Tuple


def _read_pickle_short_string(blob: bytes, pos: int) -> Tuple[Optional[str], int]:
    if pos >= len(blob) or blob[pos] != ord("X") or pos + 5 > len(blob):
        return None, pos
    ln = struct.unpack_from("<I", blob, pos + 1)[0]
    if ln > 4096 or pos + 5 + ln > len(blob):
        return None, pos + 1
    raw = blob[pos + 5 : pos + 5 + ln]
    try:
        s = raw.decode("utf-8")
    except UnicodeDecodeError:
        s = raw.decode("latin-1")
    return s, pos + 5 + ln


def extract_class_names(sub: bytes) -> list[str]:
    start = sub.find(b"(K\x00")
    if start < 0:
        raise ValueError(r"не найдена таблица (K\x00)")

    pos = start + len(b"(K\x00")
    names: list[str] = []
    while pos < len(sub) and len(names) < 1024:
        s, new_pos = _read_pickle_short_string(sub, pos)
        if not s:
            break
        names.append(s)
        pos = new_pos
        if pos >= len(sub):
            break
        if sub[pos] == ord("r"):
            pos += 1
            while pos < len(sub) and sub[pos] not in (ord("K"), ord("X"), ord("u")):
                pos += 1
        if pos + 2 <= len(sub) and sub[pos] == ord("K") and sub[pos + 1] < 120:
            pos += 2
            continue
        if pos < len(sub) and sub[pos] == ord("u"):
            break

    return names


def main() -> None:
    p = argparse.ArgumentParser(description="Классы в Ultralytics YOLO .pt")
    p.add_argument("pt", type=Path)
    args = p.parse_args()
    path: Path = args.pt
    if not path.is_file():
        raise SystemExit(f"нет файла: {path}")

    with zipfile.ZipFile(path) as z:
        pkl_name = next((n for n in z.namelist() if n.endswith("data.pkl")), None)
        if not pkl_name:
            raise SystemExit("нет data.pkl в архиве")
        raw = z.read(pkl_name)

    i = raw.find(b"names")
    if i < 0:
        raise SystemExit("ключ names не найден")

    sub = raw[i : i + 12000]
    try:
        names = extract_class_names(sub)
    except ValueError as e:
        raise SystemExit(str(e)) from e

    if not names:
        raise SystemExit("не удалось извлечь имена")

    for n in names:
        print(n)


if __name__ == "__main__":
    main()
