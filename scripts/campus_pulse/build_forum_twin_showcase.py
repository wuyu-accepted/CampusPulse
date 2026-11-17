"""Build the public, body-minimized ForumTwin hero showcase artifact.

The input is an immutable authorized-live hero result.  Only the validated
simulation result, aggregate usage, and digest identities are retained; the
authorization object, transport diagnostics, prompts, and Provider bodies are
not copied into the public frontend asset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sha256-output", required=True, type=Path)
    args = parser.parse_args()

    source_bytes = args.input.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    result = source["result"]
    if result.get("schema_version") != "campus-pulse-forum-hero-result-v1":
        raise SystemExit("unexpected ForumTwin hero schema")
    if result.get("end_tick") != 23 or set(result.get("branches", {})) != {"Natural", "D"}:
        raise SystemExit("showcase requires paired Natural/D T0-T23 result")
    if result.get("emulator_public_messages") != 0:
        raise SystemExit("public emulator message count must be zero")

    public = {
        "schema_version": "campus-pulse-forum-hero-showcase-v1",
        "source_artifact_sha256": source["artifact_sha256"],
        "source_file_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "authorization_fingerprint": source["authorization_fingerprint"],
        "execution_provenance": "authorized_live_llm",
        "publication_eligible": False,
        "disclosure": (
            "真实 LLM T0-T23 单场景 paired hero pilot；用于产品演示和机制审计，"
            "不是双场景八种子正式总体结果。"
        ),
        "live_usage": source["live_usage"],
        "result": result,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_bytes = (json.dumps(public, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    args.output.write_bytes(output_bytes)
    output_sha256 = hashlib.sha256(output_bytes).hexdigest()
    args.sha256_output.parent.mkdir(parents=True, exist_ok=True)
    args.sha256_output.write_text(output_sha256 + "\n", encoding="ascii")
    print(output_sha256)


if __name__ == "__main__":
    main()
