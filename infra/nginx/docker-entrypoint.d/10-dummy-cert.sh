#!/bin/sh
set -eu

CERT_DIR=/etc/letsencrypt/live/huadingai.cn
FULLCHAIN="$CERT_DIR/fullchain.pem"
PRIVKEY="$CERT_DIR/privkey.pem"

if [ -f "$FULLCHAIN" ] && [ -f "$PRIVKEY" ]; then
  exit 0
fi

mkdir -p "$CERT_DIR"
openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
  -subj "/CN=huadingai.cn" \
  -keyout "$PRIVKEY" \
  -out "$FULLCHAIN"
