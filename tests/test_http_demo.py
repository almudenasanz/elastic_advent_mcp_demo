"""Verify real MCP/HTTP boundaries and EDOT export of all three signals."""

import asyncio
import gzip
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from santa_mcp.traffic import run


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(port, process, logfile):
    with httpx.Client(trust_env=False, timeout=0.5) as client:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError(logfile.read_text())
            try:
                if client.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.1)
    raise AssertionError(f"Service did not become ready: {logfile.read_text()}")


def attributes(items):
    return {item.key: item.value.string_value for item in items}


def test_http_elastic_adapter_and_otlp_export(tmp_path):
    payloads = {"traces": [], "metrics": [], "logs": []}
    types = {
        "traces": ExportTraceServiceRequest,
        "metrics": ExportMetricsServiceRequest,
        "logs": ExportLogsServiceRequest,
    }

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            signal = self.path.split("/")[-1]
            payloads[signal].append(types[signal].FromString(body))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *args):
            pass

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    workshop_port, mcp_port, elastic_port = (free_port() for _ in range(3))
    executables = Path(sys.executable).parent
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "ELASTIC"))},
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{collector.server_port}",
        "OTEL_EXPORTER_OTLP_HEADERS": "",
        "OTEL_METRIC_EXPORT_INTERVAL": "200",
        "OTEL_BSP_SCHEDULE_DELAY": "200",
        "OTEL_BLRP_SCHEDULE_DELAY": "200",
        "OTEL_TRACES_SAMPLER": "always_on",
        "ELASTICSEARCH_API_KEY": "test-search-key",
        "ELASTICSEARCH_URL": f"http://127.0.0.1:{elastic_port}",
        "WORKSHOP_URL": f"http://127.0.0.1:{workshop_port}",
        "PAINT_DELAY_SECONDS": "0.1",
    }
    children = []
    handles = []
    try:
        configurations = [
            (
                [sys.executable, "tests/fake_elasticsearch.py", str(elastic_port)],
                elastic_port,
                "test-elastic",
                False,
            ),
            ([str(executables / "santa-workshop")], workshop_port, "santa-workshop-api", True),
            (
                [str(executables / "santa-mcp"), "--port", str(mcp_port)],
                mcp_port,
                "santa-mcp",
                True,
            ),
        ]
        for command, port, name, instrument in configurations:
            logfile = tmp_path / f"{name}.log"
            handle = logfile.open("w")
            handles.append(handle)
            if instrument:
                # Use the shipped defaults and preserve the test's exported endpoint.
                # Blank connection fields in the sample must not override shell exports.
                command = [
                    sys.executable,
                    "-m",
                    "dotenv",
                    "-f",
                    ".env.example",
                    "run",
                    "--no-override",
                    "--",
                    str(executables / "opentelemetry-instrument"),
                    *command,
                ]
            child = subprocess.Popen(
                command,
                env={**environment, "PORT": str(port), "OTEL_SERVICE_NAME": name},
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
            children.append(child)
            wait_ready(port, child, logfile)
            if name == "test-elastic":
                # Both seeding and runtime searches must work with the same ES key.
                seeded = subprocess.run(
                    [str(executables / "santa-setup-elastic")],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert seeded.returncode == 0, seeded.stderr
                assert "Verified all three toys" in seeded.stdout
        records = asyncio.run(run(f"http://127.0.0.1:{mcp_port}/mcp"))
        assert len(records) == 10
        assert sum(r["outcome"] == "success" for r in records) == 9
        assert sum(r["outcome"] == "error" for r in records) == 1
    finally:
        for child in reversed(children):
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for handle in handles:
            handle.close()
        collector.shutdown()
        collector.server_close()
        thread.join()

    spans = [
        (attributes(resource.resource.attributes).get("service.name"), span)
        for request in payloads["traces"]
        for resource in request.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    tool_spans = [s for service, s in spans if s.name == "tools:build_present"]
    assert len(tool_spans) == 3
    train_trace = next(
        s.trace_id
        for s in tool_spans
        if attributes(s.attributes).get("mcp.outcome") == "success"
        and any(
            attributes(p.attributes).get("santa.toy.id") == "wooden-train"
            for _, p in spans
            if p.trace_id == s.trace_id
        )
    )
    assert any(
        service == "santa-workshop-api" and s.name == "workshop.paint" and s.trace_id == train_trace
        for service, s in spans
    )
    assert any(
        service == "santa-mcp"
        and s.name == "knowledge.get_toy_instructions"
        and s.trace_id == train_trace
        for service, s in spans
    )
    assert any(attributes(s.attributes).get("mcp.outcome") == "error" for _, s in spans)
    logs = [
        log
        for request in payloads["logs"]
        for resource in request.resource_logs
        for scope in resource.scope_logs
        for log in scope.log_records
    ]
    paint_log = next(
        log
        for log in logs
        if log.trace_id == train_trace and "Paint booth started" in log.body.string_value
    )
    # Custom fields must survive the real EDOT/OTLP path with their original types.
    paint_attrs = {item.key: item.value for item in paint_log.attributes}
    assert paint_attrs["event.name"].string_value == "workshop.paint.started"
    assert paint_attrs["paint.booth.id"].string_value == "north-pole-1"
    assert paint_attrs["paint.delay_seconds"].WhichOneof("value") == "double_value"
    assert paint_attrs["paint.delay_seconds"].double_value == 0.1
    assert paint_attrs["paint.simulated_slowdown"].WhichOneof("value") == "bool_value"
    assert paint_attrs["paint.simulated_slowdown"].bool_value is True
    built_log = next(
        log
        for log in logs
        if log.trace_id == train_trace and "Present built" in log.body.string_value
    )
    built_attrs = {item.key: item.value for item in built_log.attributes}
    assert built_attrs["event.name"].string_value == "workshop.present_built"
    assert built_attrs["santa.parts.required"].WhichOneof("value") == "int_value"
    assert built_attrs["santa.parts.required"].int_value == 7
    assert any(attributes(log.attributes).get("mcp.outcome") == "error" for log in logs)
    metric_names = {
        metric.name
        for request in payloads["metrics"]
        for resource in request.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert {"mcp.requests", "mcp.request.duration"} <= metric_names
