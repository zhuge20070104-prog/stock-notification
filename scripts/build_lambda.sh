#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT/build/lambda"
ZIP="$ROOT/build/lambda.zip"

rm -rf "$BUILD_DIR" "$ZIP"
mkdir -p "$BUILD_DIR"

# 1. handlers + shared lib
cp "$ROOT/src/fetcher.py" "$ROOT/src/indicators.py" "$ROOT/src/lookup.py" "$ROOT/src/movers.py" "$ROOT/src/notifier.py" "$ROOT/src/store.py" "$BUILD_DIR/"
cp "$ROOT/backend/api_handler.py" "$ROOT/backend/monitor_handler.py" "$BUILD_DIR/"

# 2. Install deps as Linux wheels (Lambda runs amazonlinux2 / x86_64).
#    在 WSL 上本来就是 Linux，但显式指定平台可以确保 wheel 一致。
pip install \
  -r "$ROOT/backend/requirements.txt" \
  -t "$BUILD_DIR" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --upgrade \
  --quiet

# 3. Trim cruft to keep zip slim
#    注意: 不能删 *.dist-info —— curl_cffi 等包在 import 时会用
#    importlib.metadata.version() 读自己的版本，缺 dist-info 就抛
#    "No package metadata was found for curl_cffi"。
find "$BUILD_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -name "*.pyc" -delete 2>/dev/null || true

# 4. Zip (Lambda 要求文件在 zip 根目录，不能套子目录)
( cd "$BUILD_DIR" && zip -rq "$ZIP" . )

size_mb=$(du -m "$ZIP" | cut -f1)
echo "built $ZIP (${size_mb}M)"
