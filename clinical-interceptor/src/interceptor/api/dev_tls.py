"""Development only: lets the application see the client certificate when uvicorn terminates mTLS itself.

uvicorn does not expose the peer certificate to ASGI applications. This protocol class adds it through the ASGI TLS
extension (scope["extensions"]["tls"]["client_cert_chain"]), which api/mtls.py reads. Use it with:

    uvicorn interceptor.api.app:build_default_app --factory --http interceptor.api.dev_tls:MtlsH11Protocol \\
        --ssl-keyfile server.key --ssl-certfile server.pem --ssl-ca-certs ca.pem --ssl-cert-reqs 2

It relies on a uvicorn internals detail (the ASGI scope is built in handle_events and the app task only starts after
handle_events returns), so re-check it when upgrading uvicorn. In production, terminate mTLS at a reverse proxy instead.
"""

import ssl

from uvicorn.protocols.http.h11_impl import H11Protocol


class MtlsH11Protocol(H11Protocol):
    def handle_events(self):
        super().handle_events()
        scope = getattr(self, "scope", None)
        if scope is None:
            return
        extensions = scope.setdefault("extensions", {})
        if "tls" in extensions:
            return
        ssl_object = self.transport.get_extra_info("ssl_object")
        if ssl_object is None:
            return
        der = ssl_object.getpeercert(binary_form=True)
        if der:
            extensions["tls"] = {"client_cert_chain": [ssl.DER_cert_to_PEM_cert(der)]}
