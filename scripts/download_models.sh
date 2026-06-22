#!/usr/bin/env bash
#
# Скачивает веса моделей с публичной ссылки Яндекс.Диска и распаковывает в model_data/.
# Веса не хранятся в git (большие бинарники) — этот скрипт восстанавливает их.
#
# Использование:
#   scripts/download_models.sh "https://disk.yandex.ru/d/XXXXXXXX"
# или:
#   YANDEX_PUBLIC_URL="https://disk.yandex.ru/d/XXXXXXXX" scripts/download_models.sh
#
set -euo pipefail

# Ссылка по умолчанию — публичный архив с весами на Яндекс.Диске.
DEFAULT_URL="https://disk.yandex.ru/d/H99ovrbBNjnzAg"
PUBLIC_URL="${1:-${YANDEX_PUBLIC_URL:-$DEFAULT_URL}}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="https://cloud-api.yandex.net/v1/disk/public/resources/download"

echo "1/3 Запрашиваю прямую ссылку для скачивания у API Яндекс.Диска..."
HREF="$(curl -fsS -G "$API" --data-urlencode "public_key=${PUBLIC_URL}" \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["href"])')"

TMP="$(mktemp --suffix=.zip)"
trap 'rm -f "$TMP"' EXIT

echo "2/3 Скачиваю архив с весами..."
curl -fL --progress-bar -o "$TMP" "$HREF"

echo "3/3 Распаковываю в ${ROOT} ..."
unzip -o "$TMP" -d "$ROOT" >/dev/null

echo "Готово. Веса моделей восстановлены в model_data/."
