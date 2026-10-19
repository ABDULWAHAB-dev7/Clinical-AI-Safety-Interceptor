"""Development server: HTTPS with a REQUIRED client certificate, terminated by uvicorn itself (PLAN 5.6).

    python scripts/dev_server.py --certs seed/certs --port 8443

Configure the rest through the INTERCEPTOR_* environment variables (see api/app.py). The registry file in seed/certs is
used unless INTERCEPTOR_MTLS_REGISTRY is set, and the JWT public key unless INTERCEPTOR_JWT_PUBLIC_KEY_FILE is set.

uvicorn does not hand the client certificate to the application, so this uses api.dev_tls.MtlsH11Protocol. Production
should terminate mTLS at a reverse proxy instead (api/mtls.py).
"""

import argparse
import os
import ssl
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from api.dev_tls import MtlsH11Protocol  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--certs", default=str(ROOT / "seed" / "certs"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args()
    certs = Path(args.certs)

    os.environ.setdefault("INTERCEPTOR_MTLS_REGISTRY", str(certs / "registry.json"))
    os.environ.setdefault("INTERCEPTOR_JWT_PUBLIC_KEY_FILE", str(certs / "jwt_public.pem"))

    uvicorn.run(
        "api.app:build_default_app",
        factory=True,
        host=args.host,
        port=args.port,
        http=MtlsH11Protocol,
        ssl_keyfile=str(certs / "server.key"),
        ssl_certfile=str(certs / "server.pem"),
        ssl_ca_certs=str(certs / "ca.pem"),
        ssl_cert_reqs=ssl.CERT_REQUIRED,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
