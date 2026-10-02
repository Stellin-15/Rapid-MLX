from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/capture_glm53_tensorfold_product.py"
SPEC = importlib.util.spec_from_file_location("glm53_product_capture", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
capture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = capture
SPEC.loader.exec_module(capture)


def _fake_probe(ports, _sanitizer):
    return {
        "captured_at_utc": "2026-10-01T00:00:00+00:00",
        "memory_pressure": {
            "available": True,
            "exit_code": 0,
            "stdout": "System-wide memory free percentage: 75%",
            "stderr": "",
        },
        "vm_stat": {
            "available": True,
            "exit_code": 0,
            "stdout": "Mach Virtual Memory Statistics: (page size of 16384 bytes)",
            "stderr": "",
        },
        "swap": {
            "available": True,
            "exit_code": 0,
            "stdout": "total = 0.00M  used = 0.00M  free = 0.00M",
            "stderr": "",
            "used_bytes": 0,
        },
        "load_average": {"one": 0.1, "five": 0.2, "fifteen": 0.3},
        "listeners": {str(port): capture.listener_open(port) for port in ports},
    }


def _write_fake_server(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """
            import argparse
            import hashlib
            import json
            import os
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            parser = argparse.ArgumentParser()
            parser.add_argument("--mode", choices=("product", "direct"), required=True)
            parser.add_argument("--port", type=int, required=True)
            args = parser.parse_args()

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def send_json(self, value):
                    body = json.dumps(value, separators=(",", ":")).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_GET(self):
                    if self.path == "/v1/models":
                        model = (
                            "glm5.3-flash-tensorfold"
                            if args.mode == "product"
                            else "glm53-tf-v06"
                        )
                        return self.send_json({"object":"list","data":[{"id":model}]})
                    if self.path == "/healthz":
                        return self.send_json({
                            "status":"ok",
                            "profile":{"id":"glm5.3-flash-tensorfold"},
                        })
                    if self.path == "/v1/status":
                        return self.send_json({
                            "status":"ready",
                            "model":"glm5.3-flash-tensorfold",
                            "profile":{
                                "id":"glm5.3-flash-tensorfold",
                                "models":{"target":{
                                    "repository":"Vontra/GLM-5.3-Flash-MLX-4bit-MTP",
                                    "revision":"76add2a341a1cd90ad0e86bb69839ea9c35827c6",
                                    "ready":True,
                                }},
                            },
                        })
                    self.send_error(404)

                def do_POST(self):
                    length = int(self.headers.get("content-length", "0"))
                    request = json.loads(self.rfile.read(length))
                    if args.mode == "product":
                        assert request["model"] == "glm5.3-flash-tensorfold"
                        assert request["reasoning_max_tokens"] == 256
                        assert "thinking_budget" not in request
                        token_ids = [11, 22, 33]
                        digest = hashlib.sha256(b"11,22,33").hexdigest()
                        with open(os.environ["RAPID_MLX_TENSORFOLD_AUDIT_PATH"], "w") as f:
                            f.write(json.dumps({
                                "request_id":"rapid-private-id",
                                "token_ids":token_ids,
                                "token_sha256":digest,
                            }) + "\\n")
                    else:
                        assert request["model"] == "glm53-tf-v06"
                        assert request["thinking_budget"] == 256
                    response = {
                        "id":"chatcmpl-fixture",
                        "choices":[{"index":0,"message":{
                            "role":"assistant",
                            "reasoning_content":"reason",
                            "content":"answer",
                        },"finish_reason":"length"}],
                        "usage":{"prompt_tokens":460,"completion_tokens":768,"total_tokens":1228},
                    }
                    if args.mode == "direct":
                        response["tensorfold"] = {"token_sha":"abcdef123456"}
                    self.send_json(response)

            print(
                "pid=12345 /Users/private/secret Authorization: Bearer super-secret "
                "https://private.example/path",
                flush=True,
            )
            ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
            """
        )
    )


def test_capture_contract_with_fake_product_and_direct_servers(tmp_path: Path) -> None:
    fake = tmp_path / "fake_server.py"
    _write_fake_server(fake)
    output = tmp_path / "artifacts"
    target = tmp_path / "snapshots" / ("76add2a341a1cd90ad0e86bb69839ea9c35827c6")
    target.mkdir(parents=True)

    def product_builder(port: int) -> list[str]:
        return [sys.executable, str(fake), "--mode", "product", "--port", str(port)]

    def direct_builder(port: int, _target: Path) -> list[str]:
        return [sys.executable, str(fake), "--mode", "direct", "--port", str(port)]

    manifest = capture.run_capture(
        output,
        target=target,
        load_timeout=10,
        request_timeout=10,
        product_builder=product_builder,
        direct_builder=direct_builder,
        probe=_fake_probe,
    )

    assert manifest["status"] == "complete"
    assert manifest["final_listeners_gone"] is True
    assert manifest["swap_gate"]["passed"] is True
    product = manifest["phases"]["product"]["completion"]
    assert product["full_token_ids"]["ids"] == [11, 22, 33]
    assert (
        product["full_token_ids"]["sha256"] == hashlib.sha256(b"11,22,33").hexdigest()
    )
    drafted = manifest["phases"]["direct"]["drafted"]
    assert drafted["full_token_ids"]["availability"] == "unavailable"
    assert drafted["full_token_ids"]["sha256"] is None
    assert drafted["opaque_token_fingerprint"] == {
        "value": "abcdef123456",
        "kind": "tensorfold_opaque_token_fingerprint",
        "is_sha256": False,
    }
    assert all(
        manifest["phases"]["direct"]["comparisons"]["direct_drafted_vs_serial"].values()
    )

    frozen = output / "requests/frozen-fixture.json"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == (
        capture.REQUIRED_FIXTURE_SHA256
    )
    product_request = json.loads(
        (output / "requests/product-submitted.json").read_text()
    )
    assert product_request["reasoning_max_tokens"] == 256
    assert "thinking_budget" not in product_request

    hashes = json.loads((output / capture.HASH_MAP_NAME).read_text())
    retained = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and path.name != capture.HASH_MAP_NAME
    }
    assert set(hashes["files"]) == retained
    capture.verify_hash_map(output)

    logs = "\n".join(
        path.read_text() for path in output.rglob("*.log") if path.is_file()
    )
    assert "super-secret" not in logs
    assert "/Users/private" not in logs
    assert "private.example" not in logs
    assert "pid=12345" not in logs
    assert "<redacted>" in logs
    assert "<private-path>" in logs
    assert "pid=<pid>" in logs


def test_production_commands_pin_alias_and_direct_settings(tmp_path: Path) -> None:
    product = capture.default_product_command(18162)
    assert product[:3] == ["rapid-mlx", "serve", "glm5.3-flash-tensorfold"]
    assert product[-2:] == ["--port", "18162"]

    target = tmp_path / "target"
    direct = capture.default_direct_command(18163, target)
    assert direct[:2] == ["tensorfold", "serve"]
    assert direct[2] == str(target)
    assert direct[direct.index("--context") + 1] == "8192"
    assert direct[direct.index("--max-tokens") + 1] == "4096"
    assert direct[direct.index("--parallel") + 1] == "1"
    assert direct[direct.index("--mtp-drafts") + 1] == "3"
    assert direct[direct.index("--prefill-pass") + 1] == "8"
    assert direct[direct.index("--pass-cache-gib") + 1] == "16"
    assert "--no-update-check" in direct


def test_stream_request_records_first_visible_delta_ttft() -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n')
            self.wfile.flush()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n')
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = capture.time.monotonic_ns()
        result = capture.http_request(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            origin_ns=origin,
            payload=b'{"stream":true}',
            timeout=5,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.ttft_ns is not None and result.ttft_ns >= 0
    assert result.ttft_basis == "first_visible_sse_delta"
    assert result.started_offset_ns <= result.first_byte_offset_ns
    assert result.first_byte_offset_ns <= result.ended_offset_ns


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("used = 0.00M", 0),
        ("total = 4.00G used = 1.50G free = 2.50G", round(1.5 * 1024**3)),
        ("unparseable", None),
    ],
)
def test_parse_swap_used_bytes(text: str, expected: int | None) -> None:
    assert capture.parse_swap_used_bytes(text) == expected


def test_cli_refuses_to_resolve_model_without_command_lifetime_lock(
    tmp_path: Path,
) -> None:
    env = dict(os.environ)
    env.pop("RAPID_LARGE_MODEL_LOCK_HELD", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(tmp_path / "out")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "scripts/large-model-run.py" in result.stderr
    assert not (tmp_path / "out").exists()


def test_nonzero_swap_discards_before_starting_a_server(tmp_path: Path) -> None:
    started = []

    def swapped_probe(ports, sanitizer):
        result = _fake_probe(ports, sanitizer)
        result["swap"]["used_bytes"] = 1024
        result["swap"]["stdout"] = "total = 1.00M used = 0.00M free = 1.00M"
        return result

    def product_builder(_port: int) -> list[str]:
        started.append("product")
        return ["must-not-run"]

    target = tmp_path / "snapshots" / capture.model_contract()["target_revision"]
    target.mkdir(parents=True)
    output = tmp_path / "discarded"
    with pytest.raises(capture.CaptureError, match="pre-capture swap usage"):
        capture.run_capture(
            output,
            target=target,
            product_builder=product_builder,
            probe=swapped_probe,
        )

    assert started == []
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "invalid"
    assert manifest["swap_gate"]["passed"] is False
    capture.verify_hash_map(output)


def test_runtime_contract_requires_exact_noneditable_vcs_revision(monkeypatch) -> None:
    model = capture.model_contract()
    valid = {
        "installed": True,
        "version": "0.6.0",
        "editable": False,
        "vcs": {"type": "git", "commit_id": model["runtime_revision"]},
    }
    monkeypatch.setattr(capture, "package_provenance", lambda _name: valid)
    capture.require_qualified_runtime(model)

    monkeypatch.setattr(
        capture,
        "package_provenance",
        lambda _name: {**valid, "editable": True},
    )
    with pytest.raises(capture.CaptureError, match="exact qualified"):
        capture.require_qualified_runtime(model)


def test_sanitizer_redacts_json_credentials_private_urls_and_pids(
    tmp_path: Path,
) -> None:
    sanitizer = capture.Sanitizer(tmp_path)
    cleaned = sanitizer.text(
        '{"api_key":"secret-value","authorization":"Bearer credential",'
        '"pid":1234,"url":"https://private.invalid/path"}'
    )
    assert "secret-value" not in cleaned
    assert "credential" not in cleaned
    assert "1234" not in cleaned
    assert "private.invalid" not in cleaned
    assert cleaned.count("<redacted>") == 2
    assert "<pid>" in cleaned
    assert "<url>" in cleaned


def test_child_environment_is_offline_and_drops_credential_variables(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HF_TOKEN", "private")
    monkeypatch.setenv("SOME_API_KEY", "private")
    monkeypatch.setenv("NORMAL_SETTING", "retained")
    environment = capture.offline_child_environment()
    assert "HF_TOKEN" not in environment
    assert "SOME_API_KEY" not in environment
    assert environment["NORMAL_SETTING"] == "retained"
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
