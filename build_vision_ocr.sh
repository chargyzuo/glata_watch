#!/bin/bash
# 重新编译 macOS 原生 Vision OCR 二进制（换机器或系统升级后跑一次即可）
set -e
cd "$(dirname "$0")"
mkdir -p bin
swiftc -O vision_ocr.swift -o bin/vision_ocr
echo "built: $(pwd)/bin/vision_ocr"
