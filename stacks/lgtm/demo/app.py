# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fastapi",
#     "uvicorn",
#     "opentelemetry-sdk",
#     "opentelemetry-exporter-otlp-proto-http",
#     "opentelemetry-instrumentation-fastapi",
# ]
# ///
"""Tiny demo app that emits all three signals to the LGTM stack.

    uv run demo/app.py            # then: curl localhost:8900/work

Every log line carries trace_id= so Grafana's derived field can jump from a log
straight to its trace. That round trip is the whole point of the stack; if it
breaks, this app is the smallest thing that proves it.
"""

import logging
import os
import random
import time

import uvicorn
from fastapi import FastAPI, HTTPException
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
RESOURCE = Resource.create({"service.name": "demo-app", "service.version": "0.1.0"})

trace.set_tracer_provider(TracerProvider(resource=RESOURCE))
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{OTLP}/v1/traces"))
)

metrics.set_meter_provider(
    MeterProvider(
        resource=RESOURCE,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{OTLP}/v1/metrics"),
                export_interval_millis=10_000,
            )
        ],
    )
)

_logs = LoggerProvider(resource=RESOURCE)
_logs.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{OTLP}/v1/logs"))
)


class TraceContextFilter(logging.Filter):
    """Put trace_id in the message text, not just the OTLP record.

    Loki keeps the OTLP trace_id as structured metadata, but printing it in the
    line means the regex derived field works too -- and it makes `docker logs`
    readable when you are debugging why nothing arrived.
    """

    def filter(self, record):
        ctx = trace.get_current_span().get_span_context()
        record.trace_id = f"{ctx.trace_id:032x}" if ctx.is_valid else "-"
        return True


handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(levelname)s trace_id=%(trace_id)s %(message)s"))
log = logging.getLogger("demo")
log.setLevel(logging.INFO)
log.addFilter(TraceContextFilter())
log.addHandler(handler)
log.addHandler(LoggingHandler(logger_provider=_logs))

tracer = trace.get_tracer("demo")
work_counter = metrics.get_meter("demo").create_counter(
    "demo_work_total", description="completed /work calls"
)

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)


@app.get("/")
def root():
    return {"ok": True, "otlp": OTLP}


@app.get("/work")
def work():
    """Nested spans + a log + a metric, with a 1-in-6 failure for error rates."""
    with tracer.start_as_current_span("fetch") as span:
        delay = random.uniform(0.01, 0.2)
        span.set_attribute("db.rows", random.randint(1, 500))
        time.sleep(delay)
        log.info("fetched rows in %.0fms", delay * 1000)

    with tracer.start_as_current_span("render"):
        time.sleep(random.uniform(0.01, 0.05))
        if random.randint(1, 6) == 1:
            log.error("render failed")
            raise HTTPException(status_code=500, detail="render failed")

    work_counter.add(1)
    log.info("work complete")
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8900)
