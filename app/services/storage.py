"""Document source abstraction.

Today we read RFP documents from a local folder path. Tomorrow the same
interface will list/download blobs from Azure Blob storage. The rest of the
pipeline only ever sees local file paths, so swapping the backend is a matter
of adding a new ``DocumentSource`` implementation — nothing downstream changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".ppt"}


class DocumentSource(Protocol):
    """A source of RFP documents that can be materialised as local files."""

    def list_documents(self) -> list[Path]:
        """Return local paths to every supported document in the source."""
        ...


class FolderSource:
    """Reads documents from a folder path on the server's filesystem.

    Used for local development. The path must exist and be readable by the
    process running the app.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def validate(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"Folder does not exist: {self.path}")
        if not self.path.is_dir():
            raise NotADirectoryError(f"Not a folder: {self.path}")

    def list_documents(self) -> list[Path]:
        self.validate()
        docs = [
            p
            for p in sorted(self.path.glob("*"))
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        return docs


# ── Future: Azure Blob storage ────────────────────────────────────────────────
# class AzureBlobSource:
#     """Lists blobs under a prefix, downloads each to a local temp dir, and
#     returns the local paths. Implement when wiring up Azure deployment."""
#
#     def __init__(self, container: str, prefix: str, local_cache: Path):
#         ...
#
#     def list_documents(self) -> list[Path]:
#         ...


def get_source(folder_path: str) -> DocumentSource:
    """Factory. For now always returns a FolderSource; later inspect the input
    (e.g. an ``https://...blob.core.windows.net/...`` URL) to choose a backend."""
    return FolderSource(folder_path)
