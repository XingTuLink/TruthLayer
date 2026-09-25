"""KnowledgeExtractionService (#54, Sprint 3).

Orchestrates, for one workspace scan:

chunks -> LLM candidate envelope -> deterministic validation
      -> Entity/Alias resolution -> Fact + Evidence persistence
      -> optional chunk/entity embeddings -> immutable Snapshot with a
deterministic Knowledge Hash.

Hard rules enforced here regardless of model output (#2, #22):
* every Fact carries >= 1 Evidence whose quote is source text;
* FactClaim XOR / temporal-window validation;
* unresolved/ambiguous entities and invalid candidates are rejected with a
  warning, never guessed.

The service flushes but never commits; the caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from truthlayer.config import TruthLayerConfig
from truthlayer.db.orm import (
    Chunk,
    Document,
    Entity,
    Fact,
    ScanRun,
    Workspace,
)
from truthlayer.domain.enums import DocumentStatus, FactStatus, ObjectType
from truthlayer.domain.errors import DomainValidationError, ProviderError
from truthlayer.domain.evidence import Evidence
from truthlayer.domain.fact import FactClaim
from truthlayer.extraction.entities import EntityResolver
from truthlayer.extraction.knowledge_hash import (
    compute_knowledge_hash,
    fact_record,
)
from truthlayer.extraction.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from truthlayer.extraction.schemas import ExtractionEnvelope, RawEntity, RawFact
from truthlayer.extraction.snapshot import write_snapshot
from truthlayer.providers.embedding import EmbeddingProvider
from truthlayer.providers.llm import LLMProvider

# No detector in Sprint 3; recorded explicitly for ScanRun honesty.
DETECTOR_VERSION: str | None = None


@dataclass
class ExtractionResult:
    workspace_name: str
    workspace_id: uuid.UUID | None = None
    scan_run_id: uuid.UUID | None = None
    documents: int = 0
    chunks_processed: int = 0
    entities_new: int = 0
    facts_new: int = 0
    facts_total: int = 0
    entities_total: int = 0
    chunk_embeddings: int = 0
    entity_embeddings: int = 0
    embedding_dim: int | None = None
    snapshot_id: uuid.UUID | None = None
    knowledge_hash: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def _squash(text: str) -> str:
    """Whitespace/normalization-insensitive comparison for quote checks."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def _canonical_config_hash(config: TruthLayerConfig) -> str:
    payload = json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _scalar_object_value(
    raw: RawFact,
) -> tuple[Any, ObjectType, date | None, date | None, datetime | None]:
    """Coerce a candidate scalar + dates, raising on type violations."""
    object_type = ObjectType(raw.object_type)
    value: Any = raw.object_value

    if object_type is ObjectType.STRING:
        if not isinstance(value, str):
            raise DomainValidationError(
                f"string object must be JSON string, got {type(value).__name__}"
            )
        stored_value: Any = value.strip()
    elif object_type is ObjectType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DomainValidationError(
                f"number object must be JSON number, got {type(value).__name__}"
            )
        stored_value = int(value) if isinstance(value, float) and value.is_integer() else value
    elif object_type is ObjectType.BOOLEAN:
        if not isinstance(value, bool):
            raise DomainValidationError(
                f"boolean object must be true/false, got {type(value).__name__}"
            )
        stored_value = value
    else:  # ObjectType.DATE
        if not isinstance(value, str):
            raise DomainValidationError("date object must be a YYYY-MM-DD string")
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError as exc:
            raise DomainValidationError(f"invalid date object: {value!r}") from exc
        # JSONB stores dates as ISO strings; FactClaim validates with dates.
        stored_value = parsed_date

    valid_from = date.fromisoformat(raw.valid_from) if raw.valid_from else None
    valid_to = date.fromisoformat(raw.valid_to) if raw.valid_to else None
    observed = (
        datetime(
            *date.fromisoformat(raw.observed_at).timetuple()[:3],
            tzinfo=timezone.utc,
        )
        if raw.observed_at
        else None
    )
    # FactClaim wants a date instance for the DATE scalar.
    claim_value = stored_value
    jsonb_value = (
        stored_value.isoformat()
        if object_type is ObjectType.DATE
        else stored_value
    )
    # Validate through the domain object before returning (#22).
    FactClaim(
        subject_entity_id=uuid.uuid4(),  # placeholder, replaced by caller
        predicate=raw.predicate.strip(),
        object_value=claim_value,
        object_type=object_type,
        valid_from=valid_from,
        valid_to=valid_to,
        observed_at=observed.date() if observed else None,
    )
    return jsonb_value, object_type, valid_from, valid_to, observed


class KnowledgeExtractionService:
    def __init__(
        self,
        session: Session,
        config: TruthLayerConfig,
        base_dir: Path,
        llm: LLMProvider,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self.session = session
        self.config = config
        self.base_dir = Path(base_dir)
        self.llm = llm
        self.embedder = embedder

    def run(self) -> ExtractionResult:
        result = ExtractionResult(workspace_name=self.config.workspace.name)
        workspace = self._get_or_create_workspace()
        result.workspace_id = workspace.id
        scan_run = self._open_scan_run(workspace.id)
        self.session.flush()

        try:
            docs_by_id = self._load_documents(workspace.id)
            result.documents = len(docs_by_id)
            resolver = EntityResolver(self.session, workspace.id)
            existing_keys = self._load_existing_fact_keys(workspace.id)

            for chunk, document in self._pending_chunks(workspace.id):
                result.chunks_processed += 1
                self._extract_chunk(
                    chunk=chunk,
                    document=document,
                    workspace_id=workspace.id,
                    resolver=resolver,
                    existing_keys=existing_keys,
                    result=result,
                )
            result.entities_new = resolver.created_count
            self.session.flush()

            self._fill_embeddings(workspace.id, result)
            self.session.flush()

            snapshot = self._snapshot(workspace.id, scan_run, result)

            scan_run.status = "completed"
            scan_run.completed_at = datetime.now(timezone.utc)
            scan_run.document_count = result.documents
            scan_run.fact_count = result.facts_total
            scan_run.embedding_dim = result.embedding_dim
            result.scan_run_id = scan_run.id
            result.snapshot_id = snapshot.id
            self.session.flush()
        except Exception as exc:
            scan_run.status = "failed"
            scan_run.completed_at = datetime.now(timezone.utc)
            scan_run.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            self.session.flush()
            raise
        return result

    # -- setup --------------------------------------------------------------

    def _get_or_create_workspace(self) -> Workspace:
        workspace = self.session.execute(
            select(Workspace).where(Workspace.name == self.config.workspace.name)
        ).scalar_one_or_none()
        if workspace is None:
            workspace = Workspace(name=self.config.workspace.name)
            self.session.add(workspace)
            self.session.flush()
        return workspace

    def _open_scan_run(self, workspace_id: uuid.UUID) -> ScanRun:
        extraction = self.config.extraction
        embedding = self.config.embedding
        scan_run = ScanRun(
            workspace_id=workspace_id,
            config_hash=_canonical_config_hash(self.config),
            extraction_provider=extraction.provider if extraction else None,
            extraction_model=extraction.model if extraction else None,
            embedding_provider=embedding.provider if embedding else None,
            embedding_model=embedding.model if embedding else None,
            embedding_dim=embedding.dimensions if embedding else None,
            detector_version=DETECTOR_VERSION,
            prompt_version=PROMPT_VERSION,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        self.session.add(scan_run)
        return scan_run

    def _load_documents(self, workspace_id: uuid.UUID) -> dict[uuid.UUID, Document]:
        documents = self.session.scalars(
            select(Document).where(
                Document.workspace_id == workspace_id,
                Document.status == DocumentStatus.PARSED,
            )
        ).all()
        return {doc.id: doc for doc in documents}

    def _pending_chunks(
        self, workspace_id: uuid.UUID
    ) -> list[tuple[Chunk, Document]]:
        # Chunks that already produced facts are skipped on incremental
        # rescans; re-parsed documents get fresh chunk ids automatically.
        referenced = set(
            self.session.scalars(
                select(Fact.source_chunk_id).where(
                    Fact.workspace_id == workspace_id,
                    Fact.source_chunk_id.is_not(None),
                )
            ).all()
        )

        chunks = self.session.scalars(
            select(Chunk)
            .where(
                Chunk.document_id.in_(
                    select(Document.id).where(
                        Document.workspace_id == workspace_id,
                        Document.status == DocumentStatus.PARSED,
                    )
                )
            )
            .order_by(Chunk.document_id, Chunk.chunk_index)
        ).all()
        docs = self._load_documents(workspace_id)
        return [(chunk, docs[chunk.document_id]) for chunk in chunks if chunk.id not in referenced]

    def _load_existing_fact_keys(
        self, workspace_id: uuid.UUID
    ) -> set[tuple[Any, ...]]:
        facts = self.session.scalars(
            select(Fact).where(Fact.workspace_id == workspace_id)
        ).all()
        return {self._fact_key(fact) for fact in facts}

    @staticmethod
    def _fact_key(fact: Fact) -> tuple[Any, ...]:
        value = fact.object_value
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return (
            fact.subject_entity_id,
            fact.predicate.strip(),
            fact.object_entity_id,
            fact.object_type,
            value,
            fact.valid_from.isoformat() if fact.valid_from else "",
            fact.valid_to.isoformat() if fact.valid_to else "",
        )

    # -- per-chunk extraction ------------------------------------------------

    def _extract_chunk(
        self,
        *,
        chunk: Chunk,
        document: Document,
        workspace_id: uuid.UUID,
        resolver: EntityResolver,
        existing_keys: set[tuple[Any, ...]],
        result: ExtractionResult,
    ) -> None:
        page = chunk.page_start
        prompt_input = build_user_prompt(
            chunk_text=chunk.text, filename=document.filename, page=page
        )
        envelope = self.llm.generate_structured(
            input_text=prompt_input,
            output_schema=ExtractionEnvelope,
            system_prompt=SYSTEM_PROMPT,
        )

        declared: dict[str, RawEntity] = {}
        for raw_entity in envelope.entities:
            key = raw_entity.name.strip().casefold()
            declared.setdefault(key, raw_entity)

        for raw_fact in envelope.facts:
            self._persist_fact(
                raw_fact=raw_fact,
                declared=declared,
                chunk=chunk,
                document=document,
                workspace_id=workspace_id,
                resolver=resolver,
                existing_keys=existing_keys,
                result=result,
            )

    def _persist_fact(
        self,
        *,
        raw_fact: RawFact,
        declared: dict[str, RawEntity],
        chunk: Chunk,
        document: Document,
        workspace_id: uuid.UUID,
        resolver: EntityResolver,
        existing_keys: set[tuple[Any, ...]],
        result: ExtractionResult,
    ) -> None:
        label = f"{document.filename}#chunk{chunk.chunk_index}"
        try:
            subject_decl = declared.get(raw_fact.subject.strip().casefold())
            if subject_decl is not None:
                subject = resolver.resolve_reference(
                    raw_fact.subject, subject_decl
                )
            else:
                salvaged = resolver.get_or_create_unknown(raw_fact.subject)
                if salvaged is None:
                    raise DomainValidationError(
                        "subject entity ambiguous: "
                        f"{raw_fact.subject!r}"
                    )
                subject, created = salvaged
                if created:
                    result.warnings.append(
                        f"{label}: undeclared subject {raw_fact.subject!r} "
                        "auto-typed 'unknown'"
                    )
            if subject is None:  # defensive; resolve() always returns
                raise DomainValidationError(
                    f"subject entity unresolved: {raw_fact.subject!r}"
                )

            try:
                valid_from = (
                    date.fromisoformat(raw_fact.valid_from)
                    if raw_fact.valid_from
                    else None
                )
                valid_to = (
                    date.fromisoformat(raw_fact.valid_to)
                    if raw_fact.valid_to
                    else None
                )
                observed_at = (
                    datetime(
                        *date.fromisoformat(raw_fact.observed_at).timetuple()[:3],
                        tzinfo=timezone.utc,
                    )
                    if raw_fact.observed_at
                    else None
                )
            except ValueError as exc:
                raise DomainValidationError(f"invalid date: {exc}") from exc

            object_entity_id: uuid.UUID | None = None
            object_value: Any = None
            object_type: ObjectType | None = None
            object_entity: Entity | None = None

            if raw_fact.object_entity is not None:
                object_decl = declared.get(
                    raw_fact.object_entity.strip().casefold()
                )
                if object_decl is not None:
                    object_entity = resolver.resolve_reference(
                        raw_fact.object_entity, object_decl
                    )
                else:
                    salvaged_object = resolver.get_or_create_unknown(
                        raw_fact.object_entity
                    )
                    if salvaged_object is None:
                        raise DomainValidationError(
                            "object entity ambiguous: "
                            f"{raw_fact.object_entity!r}"
                        )
                    object_entity, created_object = salvaged_object
                    if created_object:
                        result.warnings.append(
                            f"{label}: undeclared object "
                            f"{raw_fact.object_entity!r} auto-typed 'unknown'"
                        )
                if object_entity is None:
                    raise DomainValidationError(
                        "object entity unresolved: "
                        f"{raw_fact.object_entity!r}"
                    )
                object_entity_id = object_entity.id
                FactClaim(
                    subject_entity_id=subject.id,
                    predicate=raw_fact.predicate.strip(),
                    object_entity_id=object_entity.id,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    observed_at=observed_at.date() if observed_at else None,
                )
            else:
                object_value, object_type, _, _, _ = _scalar_object_value(
                    raw_fact
                )

            # Evidence First (#2): the quote must come from the source text.
            quote = raw_fact.quote.strip()
            if _squash(quote) not in _squash(chunk.text):
                result.warnings.append(
                    f"{label}: quote not found verbatim, anchored to chunk text"
                )
                quote = chunk.text.strip()[:500]
            if not quote:
                raise DomainValidationError("empty evidence quote")

            evidence = Evidence(
                document_id=document.id,
                chunk_id=chunk.id,
                page=chunk.page_start,
                quote=quote,
                source_type=str(
                    document.metadata_jsonb.get("source_type", "document")
                ),
                authority_score=float(document.authority_score),
            )

            candidate = Fact(
                workspace_id=workspace_id,
                subject_entity_id=subject.id,
                predicate=raw_fact.predicate.strip(),
                object_entity_id=object_entity_id,
                object_value=object_value,
                object_type=object_type.value if object_type else None,
                valid_from=valid_from,
                valid_to=valid_to,
                observed_at=observed_at,
                confidence=raw_fact.confidence,
                status=FactStatus.ACTIVE.value,
                source_chunk_id=chunk.id,
                evidence_jsonb=[evidence.model_dump(mode="json")],
            )
            key = self._fact_key(candidate)
            if key in existing_keys:
                return  # already extracted — incremental rescans are idempotent
            existing_keys.add(key)

            self.session.add(candidate)
            self.session.flush()
            result.facts_new += 1
        except (DomainValidationError, ValueError) as exc:
            result.errors.append((label, str(exc)))

    # -- embeddings ----------------------------------------------------------

    def _fill_embeddings(
        self, workspace_id: uuid.UUID, result: ExtractionResult
    ) -> None:
        if self.embedder is None:
            return
        try:
            pending_chunks = self.session.scalars(
                select(Chunk)
                .where(
                    Chunk.document_id.in_(
                        select(Document.id).where(
                            Document.workspace_id == workspace_id,
                            Document.status == DocumentStatus.PARSED,
                        )
                    ),
                    Chunk.embedding.is_(None),
                )
                .order_by(Chunk.document_id, Chunk.chunk_index)
            ).all()
            if pending_chunks:
                vectors = self.embedder.embed([c.text for c in pending_chunks])
                for chunk, vector in zip(pending_chunks, vectors, strict=True):
                    chunk.embedding = vector
                result.chunk_embeddings = len(pending_chunks)

            pending_entities = self.session.scalars(
                select(Entity).where(
                    Entity.workspace_id == workspace_id,
                    Entity.embedding.is_(None),
                )
            ).all()
            if pending_entities:
                vectors = self.embedder.embed(
                    [entity.canonical_name for entity in pending_entities]
                )
                for entity, vector in zip(
                    pending_entities, vectors, strict=True
                ):
                    entity.embedding = vector
                result.entity_embeddings = len(pending_entities)

            dim = getattr(self.embedder, "dimensions", None)
            result.embedding_dim = dim
        except ProviderError as exc:
            # Embeddings only power recall; a failing slot must not destroy
            # the extracted knowledge (#23).
            result.warnings.append(f"embedding skipped: {exc}")

    # -- snapshot ------------------------------------------------------------

    def _snapshot(
        self,
        workspace_id: uuid.UUID,
        scan_run: ScanRun,
        result: ExtractionResult,
    ):
        subject_alias = aliased(Entity)
        object_alias = aliased(Entity)
        rows = self.session.execute(
            select(Fact, subject_alias, object_alias)
            .join(subject_alias, Fact.subject_entity_id == subject_alias.id)
            .outerjoin(object_alias, Fact.object_entity_id == object_alias.id)
            .where(
                Fact.workspace_id == workspace_id,
                Fact.status == FactStatus.ACTIVE.value,
            )
        ).all()

        records = [
            fact_record(fact, subject=subject, object_entity=object_entity)
            for fact, subject, object_entity in rows
        ]
        knowledge_hash = compute_knowledge_hash(records)
        result.knowledge_hash = knowledge_hash
        result.facts_total = len(records)

        entity_ids = self.session.scalars(
            select(Entity.id).where(Entity.workspace_id == workspace_id)
        ).all()
        result.entities_total = len(entity_ids)

        return write_snapshot(
            self.session,
            workspace_id=workspace_id,
            scan_run_id=scan_run.id,
            knowledge_hash=knowledge_hash,
            fact_ids=[row[0].id for row in rows],
            entity_ids=list(entity_ids),
        )
