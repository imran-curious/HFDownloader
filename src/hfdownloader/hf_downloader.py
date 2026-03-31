from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from huggingface_hub import HfApi, hf_hub_url


class DownloadCancelledError(RuntimeError):
    """Raised when the user cancels an in-progress download."""


@dataclass(frozen=True)
class ModelFile:
    filename: str
    size: int | None
    url: str


def save_model(path_name, tokenizer, model):
    """Save an already loaded tokenizer/model pair to disk."""
    path = Path(path_name).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(path)
    model.save_pretrained(path)
    return path.resolve()


def inspect_model(model_id, token=None, revision=None):
    """Return model files and metadata for a repository."""
    api = HfApi()
    info = api.model_info(
        model_id,
        revision=revision or None,
        files_metadata=True,
        token=token or None,
    )
    resolved_revision = getattr(info, "sha", None) or revision or "main"
    files = []
    for sibling in info.siblings:
        if not sibling.rfilename:
            continue
        files.append(
            ModelFile(
                filename=sibling.rfilename,
                size=getattr(sibling, "size", None),
                url=hf_hub_url(model_id, sibling.rfilename, revision=resolved_revision),
            )
        )
    return files


def suggest_models(query, limit=5):
    """Return nearby public model ids for a user query."""
    api = HfApi()
    return [model.id for model in api.list_models(search=query, limit=limit)]


def _build_headers(token):
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelledError("Download cancelled by user.")


def _wait_if_paused(pause_event, cancel_event):
    if pause_event is None:
        return
    while not pause_event.is_set():
        _check_cancel(cancel_event)
        time.sleep(0.1)


def _emit(progress_callback, event_type, payload):
    if progress_callback is not None:
        progress_callback(event_type, payload)


def download_model(
    model_id,
    path_name,
    token=None,
    revision=None,
    force_download=False,
    chunk_size=1024 * 256,
    progress_callback=None,
    pause_event=None,
    cancel_event=None,
):
    """Download a Hugging Face model repository into a local folder."""
    path = Path(path_name).expanduser()
    path.mkdir(parents=True, exist_ok=True)

    files = inspect_model(model_id, token=token, revision=revision)
    total_bytes = sum(file.size or 0 for file in files)
    _emit(
        progress_callback,
        "prepared",
        {
            "target": str(path.resolve()),
            "files": files,
            "file_count": len(files),
            "total_bytes": total_bytes,
        },
    )

    overall_downloaded = 0
    current_path = None
    current_completed = False
    current_expected_size = None

    with httpx.Client(
        follow_redirects=True,
        headers=_build_headers(token),
        timeout=None,
    ) as client:
        for index, remote_file in enumerate(files, start=1):
            _check_cancel(cancel_event)
            _wait_if_paused(pause_event, cancel_event)

            destination = path / remote_file.filename
            destination.parent.mkdir(parents=True, exist_ok=True)

            current_path = destination
            current_completed = False
            current_expected_size = remote_file.size

            if (
                not force_download
                and destination.exists()
                and remote_file.size is not None
                and destination.stat().st_size == remote_file.size
            ):
                overall_downloaded += remote_file.size
                current_completed = True
                _emit(
                    progress_callback,
                    "file_complete",
                    {
                        "filename": remote_file.filename,
                        "path": str(destination),
                        "file_index": index,
                        "file_count": len(files),
                        "file_size": remote_file.size,
                        "overall_downloaded": overall_downloaded,
                        "total_bytes": total_bytes,
                        "skipped": True,
                    },
                )
                continue

            if destination.exists():
                destination.unlink()

            _emit(
                progress_callback,
                "file_start",
                {
                    "filename": remote_file.filename,
                    "path": str(destination),
                    "file_index": index,
                    "file_count": len(files),
                    "file_size": remote_file.size,
                    "overall_downloaded": overall_downloaded,
                    "total_bytes": total_bytes,
                },
            )

            file_downloaded = 0
            last_emit_time = time.monotonic()
            last_emit_bytes = 0

            try:
                with client.stream("GET", remote_file.url) as response:
                    response.raise_for_status()
                    with destination.open("wb") as handle:
                        for chunk in response.iter_bytes(chunk_size=chunk_size):
                            _wait_if_paused(pause_event, cancel_event)
                            _check_cancel(cancel_event)
                            if not chunk:
                                continue
                            handle.write(chunk)
                            file_downloaded += len(chunk)

                            now = time.monotonic()
                            if now - last_emit_time >= 0.2:
                                interval = max(now - last_emit_time, 1e-6)
                                speed = (file_downloaded - last_emit_bytes) / interval
                                _emit(
                                    progress_callback,
                                    "progress",
                                    {
                                        "filename": remote_file.filename,
                                        "path": str(destination),
                                        "file_index": index,
                                        "file_count": len(files),
                                        "file_downloaded": file_downloaded,
                                        "file_size": remote_file.size,
                                        "overall_downloaded": overall_downloaded + file_downloaded,
                                        "total_bytes": total_bytes,
                                        "speed_bytes_per_sec": speed,
                                    },
                                )
                                last_emit_time = now
                                last_emit_bytes = file_downloaded
            except Exception:
                if destination.exists():
                    destination.unlink()
                raise

            overall_downloaded += file_downloaded
            current_completed = True
            _emit(
                progress_callback,
                "file_complete",
                {
                    "filename": remote_file.filename,
                    "path": str(destination),
                    "file_index": index,
                    "file_count": len(files),
                    "file_size": remote_file.size,
                    "overall_downloaded": overall_downloaded,
                    "total_bytes": total_bytes,
                    "skipped": False,
                },
            )

    if current_path is not None and not current_completed and current_path.exists():
        current_size = current_path.stat().st_size
        if current_expected_size is None or current_size != current_expected_size:
            current_path.unlink()

    return path.resolve()


def build_parser():
    parser = argparse.ArgumentParser(
        description="Download a Hugging Face model repository into a local folder."
    )
    parser.add_argument(
        "model",
        type=str,
        help="Model id, for example: cardiffnlp/twitter-roberta-base-sentiment",
    )
    parser.add_argument(
        "path_name",
        type=str,
        help="Folder where the model files should be downloaded.",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Optional Hugging Face token for gated/private models.",
    )
    parser.add_argument(
        "--revision",
        type=str,
        default=None,
        help="Optional branch, tag, or commit revision to download.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download files even if they already exist locally.",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    target = download_model(
        args.model,
        args.path_name,
        token=args.token,
        revision=args.revision,
        force_download=args.force_download,
    )
    print(f"Downloaded '{args.model}' to '{target}'.")


if __name__ == "__main__":
    main()
