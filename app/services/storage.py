"""Document source abstraction.

FolderSource reads from a local path.
AzureBlobSource lists blobs in a container, downloads them to a local cache dir,
and returns the local paths — the rest of the pipeline is unchanged.

Switch between them by setting AZURE_STORAGE_CONNECTION_STRING and
AZURE_STORAGE_CONTAINER_NAME in .env; get_source() auto-detects.
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
    """Reads documents from a folder path on the server's filesystem."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def validate(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"Folder does not exist: {self.path}")
        if not self.path.is_dir():
            raise NotADirectoryError(f"Not a folder: {self.path}")

    def list_documents(self) -> list[Path]:
        self.validate()
        return [
            p
            for p in sorted(self.path.glob("*"))
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]


class AzureBlobSource:
    """Download RFP blobs from Azure Blob Storage to a local cache dir.

    Already-downloaded blobs are reused unless the blob has been modified
    since the last download (checked via ETag stored in a sidecar file).

    Set in .env:
        AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
        AZURE_STORAGE_CONTAINER_NAME=rfp_docs
    """

    def __init__(
        self,
        connection_string: str,
        container: str,
        prefix: str = "",
        local_cache: Path | None = None,
    ):
        self._conn_str = connection_string
        self._container = container
        self._prefix = prefix
        self._cache_dir = local_cache or Path("data/blob_cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def list_documents(self) -> list[Path]:
        from azure.storage.blob import BlobServiceClient  # lazy import

        service = BlobServiceClient.from_connection_string(self._conn_str)
        container_client = service.get_container_client(self._container)

        local_paths: list[Path] = []
        for blob in container_client.list_blobs(name_starts_with=self._prefix):
            blob_name: str = blob.name
            if not any(blob_name.lower().endswith(ext) for ext in SUPPORTED_EXTENSIONS):
                continue

            # Use just the filename (strip any virtual folder prefix)
            local_file = self._cache_dir / Path(blob_name).name
            etag_file  = local_file.with_suffix(local_file.suffix + ".etag")

            # Re-download if file missing or ETag changed
            current_etag = blob.etag or ""
            cached_etag  = etag_file.read_text(encoding="utf-8").strip() if etag_file.exists() else ""

            if not local_file.exists() or current_etag != cached_etag:
                blob_client = container_client.get_blob_client(blob_name)
                with open(local_file, "wb") as f:
                    f.write(blob_client.download_blob().readall())
                etag_file.write_text(current_etag, encoding="utf-8")

            local_paths.append(local_file)

        return sorted(local_paths)


def get_source(folder_path: str = "") -> DocumentSource:
    """Return the appropriate DocumentSource.

    If AZURE_STORAGE_CONNECTION_STRING + AZURE_STORAGE_CONTAINER_NAME are both
    set in the environment/config, blob mode is used and folder_path is ignored.
    Otherwise falls back to FolderSource(folder_path).
    """
    from app.config import get_settings
    settings = get_settings()
    if settings.blob_mode:
        return AzureBlobSource(
            connection_string=settings.azure_storage_connection_string,
            container=settings.azure_storage_container_name,
            local_cache=settings.blob_cache_dir,
        )
    return FolderSource(folder_path)
