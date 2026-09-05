FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28

COPY qveris_benchmark /app/qveris_benchmark
COPY sandbox-get-runtime-config.v1.json /app/sandbox-get-runtime-config.v1.json
ENV PYTHONPATH=/app PYTHONNOUSERSITE=1
ENTRYPOINT ["python", "-m", "qveris_benchmark.sandbox_get_entry"]
