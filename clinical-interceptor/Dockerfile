# Interceptor API image. Migrations run at start-up (docker/entrypoint.sh) before the server.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Only what the service reads at run time: code, policy, knowledge, schema, migrations and the FHIR fixtures.
COPY src ./src
COPY nli_service ./nli_service
COPY config ./config
COPY knowledge ./knowledge
COPY db ./db
COPY migrations ./migrations
COPY alembic.ini ./
COPY fixtures ./fixtures
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN useradd --create-home --uid 10001 interceptor && chown -R interceptor /app
USER interceptor

# src for the interceptor package, and the project root for the nli_service package, which the local NLI scorer
# lives in.
ENV PYTHONPATH=/app/src:/app
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
