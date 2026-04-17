"""mock_vllm.py — Minimal vLLM-compatible mock server.

Simulates vLLM /health and /v1/chat/completions endpoints.
For HA testing: run 2 instances on different ports, put nginx in front.

Usage:
  python mock_vllm.py --port 8001 --id mock-A
  python mock_vllm.py --port 8002 --id mock-B
"""
from __future__ import annotations
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_handler(instance_id: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs): pass

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Mock-Instance", instance_id)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "instance": instance_id}).encode())
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path.startswith("/v1/chat/completions"):
                length = int(self.headers.get("Content-Length", 0))
                _ = self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Mock-Instance", instance_id)
                self.end_headers()
                # vLLM-compatible response format
                resp = {
                    "id": f"mock-{int(time.time()*1000)}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "mock-14b",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps({"mock": True, "instance": instance_id, "nodes": []})},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }
                self.wfile.write(json.dumps(resp).encode())
            else:
                self.send_response(404)
                self.end_headers()
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--id", required=True)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(args.id))
    print(f"mock vLLM '{args.id}' listening on :{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
