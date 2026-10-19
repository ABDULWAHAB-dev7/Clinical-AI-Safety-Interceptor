#!/usr/bin/env bash
# Generates TEST certificates and keys into seed/certs/ (gitignored). Never use these anywhere real.
#
#   ca.pem / ca.key                      a test CA
#   server.pem / server.key              server certificate for localhost and 127.0.0.1
#   client-registered.pem / .key         a client certificate that IS in registry.json
#   client-unregistered.pem / .key       a client certificate signed by the same CA but NOT in the registry
#   registry.json                        {"clients": [{"client_id", "fingerprint_sha256"}]} for the registered client
#   jwt_private.pem / jwt_public.pem     an EC P-256 key pair for signing and verifying test bearer tokens
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: stop it rewriting the /CN=... subjects

OUT="${1:-$(dirname "$0")/../seed/certs}"
mkdir -p "$OUT"
cd "$OUT"

openssl req -x509 -newkey rsa:2048 -nodes -days 30 -keyout ca.key -out ca.pem -subj "/CN=interceptor-test-ca" 2>/dev/null

openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=localhost" 2>/dev/null
printf "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n" > server.ext
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 30 -out server.pem -extfile server.ext 2>/dev/null

for name in client-registered client-unregistered; do
  openssl req -newkey rsa:2048 -nodes -keyout "$name.key" -out "$name.csr" -subj "/CN=$name" 2>/dev/null
  printf "extendedKeyUsage=clientAuth\n" > client.ext
  openssl x509 -req -in "$name.csr" -CA ca.pem -CAkey ca.key -CAcreateserial -days 30 -out "$name.pem" -extfile client.ext 2>/dev/null
done

FINGERPRINT=$(openssl x509 -in client-registered.pem -noout -fingerprint -sha256 | sed 's/.*=//; s/://g' | tr 'A-F' 'a-f')
printf '{"clients": [{"client_id": "ehr-gateway-test", "fingerprint_sha256": "%s"}]}\n' "$FINGERPRINT" > registry.json

openssl ecparam -name prime256v1 -genkey -noout -out jwt_private.pem 2>/dev/null
openssl ec -in jwt_private.pem -pubout -out jwt_public.pem 2>/dev/null

rm -f ./*.csr ./*.ext ./*.srl
echo "test certificates written to $(pwd)"
echo "registered client fingerprint: $FINGERPRINT"
