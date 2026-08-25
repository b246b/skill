#!/usr/bin/env python3
"""Generate or edit images with the HoseHub image API."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


API_BASE = "https://ai.qhose.net/v1/images"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_TIMEOUT_SECONDS = 300
MAX_ERROR_BODY_CHARS = 2000


class HoseHubError(RuntimeError):
    """A sanitized error safe to display to the user."""


def _require_api_key() -> str:
    api_key = os.environ.get("HOSEHUBAPI_KEY", "").strip()
    if not api_key:
        raise HoseHubError(
            "HOSEHUBAPI_KEY is not set. Set it in the local environment before calling the API."
        )
    return api_key


def _request_json(
    url: str,
    *,
    api_key: str,
    body: bytes,
    content_type: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": "hosehub-image-skill/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read(MAX_ERROR_BODY_CHARS).decode("utf-8", errors="replace")
        error_body = error_body.replace(api_key, "[REDACTED]")
        raise HoseHubError(f"HoseHub API returned HTTP {exc.code}: {error_body}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        if isinstance(exc, urllib.error.URLError):
            reason = exc.reason
        else:
            reason = "request timed out"
        raise HoseHubError(f"Could not reach the HoseHub API: {reason}") from None

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HoseHubError("HoseHub API returned a non-JSON response.") from exc
    if not isinstance(payload, dict):
        raise HoseHubError("HoseHub API returned an unexpected JSON value.")
    return payload


def _multipart_body(fields: dict[str, str], images: list[Path]) -> tuple[bytes, str]:
    boundary = f"----hosehub-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    for image_path in images:
        mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
        safe_name = image_path.name.replace('"', "_")
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="image[]"; '
                    f'filename="{safe_name}"\r\n'
                ).encode(),
                f"Content-Type: {mime_type}\r\n\r\n".encode(),
                image_path.read_bytes(),
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def generate_request(
    *,
    api_key: str,
    model: str,
    prompt: str,
    size: str,
    n: int,
    api_base: str = API_BASE,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    body = json.dumps(
        {"model": model, "prompt": prompt, "size": size, "n": n},
        ensure_ascii=False,
    ).encode("utf-8")
    return _request_json(
        f"{api_base}/generations",
        api_key=api_key,
        body=body,
        content_type="application/json",
        timeout=timeout,
    )


def edit_request(
    *,
    api_key: str,
    model: str,
    prompt: str,
    images: list[Path],
    size: str | None,
    n: int | None,
    api_base: str = API_BASE,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    fields = {"model": model, "prompt": prompt}
    if size:
        fields["size"] = size
    if n is not None:
        fields["n"] = str(n)
    body, content_type = _multipart_body(fields, images)
    return _request_json(
        f"{api_base}/edits",
        api_key=api_key,
        body=body,
        content_type=content_type,
        timeout=timeout,
    )


def _extension_from_url(url: str) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".png"


def _download_https(url: str, timeout: int) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise HoseHubError("HoseHub returned a non-HTTPS image URL; refusing to download it.")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise HoseHubError(f"Could not download a generated image: {exc.reason}") from None


def save_outputs(
    payload: dict[str, Any],
    *,
    output_dir: Path,
    prefix: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[Path]:
    items = payload.get("data")
    if not isinstance(items, list) or not items:
        raise HoseHubError("HoseHub response did not contain any image data.")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saved: list[Path] = []

    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise HoseHubError(f"Image result {index} has an unexpected format.")
        if isinstance(item.get("b64_json"), str):
            try:
                content = base64.b64decode(item["b64_json"], validate=True)
            except (ValueError, TypeError) as exc:
                raise HoseHubError(f"Image result {index} contains invalid base64 data.") from exc
            extension = ".png"
        elif isinstance(item.get("url"), str):
            content = _download_https(item["url"], timeout)
            extension = _extension_from_url(item["url"])
        else:
            raise HoseHubError(f"Image result {index} contains neither b64_json nor url.")

        if not content:
            raise HoseHubError(f"Image result {index} is empty.")
        output_path = output_dir / f"{prefix}-{timestamp}-{index:02d}{extension}"
        collision_index = 1
        while output_path.exists():
            output_path = output_dir / (
                f"{prefix}-{timestamp}-{index:02d}-{collision_index}{extension}"
            )
            collision_index += 1
        output_path.write_bytes(content)
        saved.append(output_path.resolve())

    return saved


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _existing_image(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"image file does not exist: {path}")
    return path


def _filename_prefix(value: str) -> str:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise argparse.ArgumentTypeError("must be a plain filename prefix without directories")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--prompt", required=True)
        subparser.add_argument("--model", default=DEFAULT_MODEL)
        subparser.add_argument("--output-dir", type=Path, required=True)
        subparser.add_argument(
            "--filename-prefix", type=_filename_prefix, default="hosehub-image"
        )
        subparser.add_argument("--timeout", type=_positive_int, default=DEFAULT_TIMEOUT_SECONDS)

    generate = subparsers.add_parser("generate", help="Generate one or more images")
    add_common(generate)
    generate.add_argument("--size", default="1024x1024")
    generate.add_argument("--n", type=_positive_int, default=1)

    edit = subparsers.add_parser("edit", help="Edit one or more input images")
    add_common(edit)
    edit.add_argument("--image", action="append", type=_existing_image, required=True)
    edit.add_argument("--size")
    edit.add_argument("--n", type=_positive_int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        api_key = _require_api_key()
        if args.operation == "generate":
            payload = generate_request(
                api_key=api_key,
                model=args.model,
                prompt=args.prompt,
                size=args.size,
                n=args.n,
                timeout=args.timeout,
            )
        else:
            payload = edit_request(
                api_key=api_key,
                model=args.model,
                prompt=args.prompt,
                images=args.image,
                size=args.size,
                n=args.n,
                timeout=args.timeout,
            )
        paths = save_outputs(
            payload,
            output_dir=args.output_dir.expanduser().resolve(),
            prefix=args.filename_prefix,
            timeout=args.timeout,
        )
    except HoseHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
