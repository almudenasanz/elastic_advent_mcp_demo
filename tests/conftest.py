import logging

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture(scope="session")
def telemetry():
    spans = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    trace.set_tracer_provider(provider)
    reader = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    logs = InMemoryLogRecordExporter()
    log_provider = LoggerProvider()
    log_provider.add_log_record_processor(SimpleLogRecordProcessor(logs))
    handler = LoggingHandler(logger_provider=log_provider)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield spans, reader, logs
    root.removeHandler(handler)
    log_provider.shutdown()
    provider.shutdown()


@pytest.fixture(autouse=True)
def clear_telemetry(telemetry):
    telemetry[0].clear()
    telemetry[2].clear()
