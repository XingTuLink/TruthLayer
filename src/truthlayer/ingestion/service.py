"""DocumentIngestionService (#54).

Orchestrates: discover → hash → parse → normalize → chunk → persist, with
file-hash idempotency (#35), authority from config, and version-chain
links from explicit mapping only (#8).

The service flushes but never commits; the caller (CLI / future API) owns
the transaction boundary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from truthlayer.config import TruthLayerConfig
from truthlayer.db.orm import Chunk, Document, DocumentGroup, Workspace
from truthlayer.domain.enums import DocumentStatus
from truthlayer.domain.errors import TruthLayerError
from truthlayer.ingestion.chunking import ChunkSpec, chunk_blocks
from truthlayer.ingestion.discovery import SourceFile, discover_files
from truthlayer.ingestion.hashing import (
    EMPTY_CONTENT_HASH,
    hash_blocks,
    hash_bytes,
)
from truthlayer.ingestion.normalize import normalize_text
from truthlayer.ingestion.parsers import get_parser
from truthlayer.ingestion.version_chain import resolve_version_links
from truthlayer.providers.parser_protocol import ParsedBlock


@dataclass
class IngestionResult:
    workspace_name: str
    discovered: int = 0
    parsed: int = 0
    unchanged: int = 0
    failed: int = 0
    chunks: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


class DocumentIngestionService:
    def __init__(
        self,
        session: Session,
        config: TruthLayerConfig,
        base_dir: Path,
    ) -> None:
        self.session = session
        self.config = config
        self.base_dir = Path(base_dir)

    # -- public API ---------------------------------------------------------

    def run(self) -> IngestionResult:
        result = IngestionResult(workspace_name=self.config.workspace.name)
        workspace = self._get_or_create_workspace()

        files, warnings = discover_files(
            self.base_dir, self.config.sources, self.config.ignore
        )
        result.warnings.extend(warnings)
        result.discovered = len(files)

        existing_by_hash = self._load_existing_documents(workspace.id)
        docs_by_relpath: dict[str, Document] = {}

        for item in files:
            self._ingest_one(
                workspace.id, item, existing_by_hash, docs_by_relpath, result
            )

        self.session.flush()
        self._apply_version_links(workspace.id, docs_by_relpath, result)
        self.session.flush()
        return result

    # -- steps --------------------------------------------------------------

    def _get_or_create_workspace(self) -> Workspace:
        workspace = self.session.execute(
            select(Workspace).where(Workspace.name == self.config.workspace.name)
        ).scalar_one_or_none()
        if workspace is None:
            workspace = Workspace(name=self.config.workspace.name)
            self.session.add(workspace)
            self.session.flush()
        return workspace

    def _load_existing_documents(
        self, workspace_id: uuid.UUID
    ) -> dict[str, Document]:
        documents = self.session.execute(
            select(Document).where(Document.workspace_id == workspace_id)
        ).scalars().all()
        return {document.file_hash: document for document in documents}

    def _ingest_one(
        self,
        workspace_id: uuid.UUID,
        item: SourceFile,
        existing_by_hash: dict[str, Document],
        docs_by_relpath: dict[str, Document],
        result: IngestionResult,
    ) -> None:
        try:
            raw = item.absolute_path.read_bytes()
        except OSError as exc:
            result.failed += 1
            result.errors.append((item.relative_path, f"unreadable: {exc}"))
            return

        file_hash = hash_bytes(raw)
        document = existing_by_hash.get(file_hash)

        if document is not None and document.status == DocumentStatus.PARSED:
            result.unchanged += 1
            docs_by_relpath[item.relative_path] = document
            return

        if document is None:
            document = Document(
                workspace_id=workspace_id,
                filename=item.absolute_path.name,
                file_hash=file_hash,
                content_hash=EMPTY_CONTENT_HASH,
                authority_score=item.authority,
                status=DocumentStatus.PENDING,
                metadata_jsonb={
                    "relative_path": item.relative_path,
                    "source_type": item.source_type,
                },
            )
            self.session.add(document)
            self.session.flush()
        else:
            # Retry path: previous attempt failed; clear stale chunks.
            self.session.execute(
                delete(Chunk).where(Chunk.document_id == document.id)
            )

        try:
            blocks = self._parse_and_normalize(item)
            chunk_specs = chunk_blocks(blocks)
        except TruthLayerError as exc:
            self._mark_failed(document, item, str(exc), result)
            docs_by_relpath[item.relative_path] = document
            return
        except Exception as exc:  # parser library failures
            message = f"{type(exc).__name__}: {exc}"
            self._mark_failed(
                document, item, f"parser failure: {message}", result
            )
            docs_by_relpath[item.relative_path] = document
            return

        document.content_hash = hash_blocks(blocks)
        document.parsed_at = datetime.now(timezone.utc)
        document.status = DocumentStatus.PARSED
        document.authority_score = item.authority
        document.metadata_jsonb = {
            "relative_path": item.relative_path,
            "source_type": item.source_type,
        }

        for spec in chunk_specs:
            self.session.add(self._build_chunk(document.id, spec))

        result.parsed += 1
        result.chunks += len(chunk_specs)
        docs_by_relpath[item.relative_path] = document
        existing_by_hash[file_hash] = document

    def _parse_and_normalize(
        self, item: SourceFile
    ) -> list[ParsedBlock]:
        parser = get_parser(item.absolute_path)
        blocks = parser.parse(item.absolute_path)
        normalized: list[ParsedBlock] = []
        for block in blocks:
            text = normalize_text(block.text)
            if text:
                normalized.append(block.model_copy(update={"text": text}))
        return normalized

    @staticmethod
    def _build_chunk(document_id: uuid.UUID, spec: ChunkSpec) -> Chunk:
        return Chunk(
            document_id=document_id,
            chunk_index=spec.chunk_index,
            text=spec.text,
            token_count=spec.token_count,
            page_start=spec.page_start,
            page_end=spec.page_end,
            metadata_jsonb={"block_indexes": list(spec.block_indexes)},
        )

    def _mark_failed(
        self,
        document: Document,
        item: SourceFile,
        message: str,
        result: IngestionResult,
    ) -> None:
        document.status = DocumentStatus.FAILED
        document.content_hash = EMPTY_CONTENT_HASH
        document.parsed_at = None
        document.metadata_jsonb = {
            "relative_path": item.relative_path,
            "source_type": item.source_type,
            "error": message,
        }
        result.failed += 1
        result.errors.append((item.relative_path, message))

    def _apply_version_links(
        self,
        workspace_id: uuid.UUID,
        docs_by_relpath: dict[str, Document],
        result: IngestionResult,
    ) -> None:
        if not self.config.version_mapping:
            return

        links, warnings = resolve_version_links(
            self.config.version_mapping, set(docs_by_relpath)
        )
        result.warnings.extend(warnings)

        groups: dict[str, DocumentGroup] = {}
        for link in links:
            if link.group not in groups:
                group = self.session.execute(
                    select(DocumentGroup).where(
                        DocumentGroup.workspace_id == workspace_id,
                        DocumentGroup.name == link.group,
                    )
                ).scalar_one_or_none()
                if group is None:
                    group = DocumentGroup(
                        workspace_id=workspace_id, name=link.group
                    )
                    self.session.add(group)
                    self.session.flush()
                groups[link.group] = group

            document = docs_by_relpath[link.relative_path]
            document.document_group_id = groups[link.group].id
            document.version_label = link.version_label
            if link.supersedes_path is not None:
                previous = docs_by_relpath[link.supersedes_path]
                document.previous_version_id = previous.id
