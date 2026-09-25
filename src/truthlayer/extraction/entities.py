"""Deterministic entity resolution / alias matching (#12, #32).

Resolution ladder, all within one workspace:
1. exact canonical name + type
2. alias text + declared type
3. unique alias match when the type is undeclared
4. otherwise create a new entity

Normalization is deliberately conservative: NFC, whitespace collapse and
casefold. It never merges across different declared types and never guesses
when an alias is ambiguous.
"""

from __future__ import annotations

import unicodedata
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from truthlayer.db.orm import Entity, EntityAlias
from truthlayer.extraction.schemas import RawEntity


def normalize_name(value: str) -> str:
    """NFC + whitespace collapse + casefold."""
    return " ".join(unicodedata.normalize("NFC", value).split()).casefold()


def normalize_type(value: str) -> str:
    return value.strip().casefold()


#: Fallback type when a small model emits a fact without declaring the
#: entity in the envelope's entity list. Evidence is still mandatory and
#: ambiguity is still rejected; this only salvages otherwise-lost facts.
UNKNOWN_ENTITY_TYPE = "unknown"


class EntityResolver:
    def __init__(self, session: Session, workspace_id: uuid.UUID) -> None:
        self._session = session
        self._workspace_id = workspace_id
        self.created_count = 0
        self._entities: dict[tuple[str, str], Entity] = {}
        # normalized alias text -> {entity_id: Entity}
        self._aliases: dict[str, dict[uuid.UUID, Entity]] = {}
        # entity_id -> set of normalized alias texts already stored
        self._known_aliases: dict[uuid.UUID, set[str]] = {}
        self._load()

    def _load(self) -> None:
        entities = self._session.scalars(
            select(Entity).where(Entity.workspace_id == self._workspace_id)
        ).all()
        for entity in entities:
            self._index_entity(entity)

        aliases = self._session.scalars(
            select(EntityAlias)
            .join(Entity, EntityAlias.entity_id == Entity.id)
            .where(Entity.workspace_id == self._workspace_id)
        ).all()
        for alias in aliases:
            entity = self._entity_by_id(alias.entity_id)
            if entity is not None:
                self._register_alias_text(alias.alias_text, entity)

    def _entity_by_id(self, entity_id: uuid.UUID) -> Entity | None:
        for entity in self._entities.values():
            if entity.id == entity_id:
                return entity
        return None

    def _index_entity(self, entity: Entity) -> None:
        key = (
            normalize_name(entity.canonical_name),
            normalize_type(entity.entity_type),
        )
        self._entities.setdefault(key, entity)
        self._known_aliases.setdefault(entity.id, set())

    def _register_alias_text(self, text: str, entity: Entity) -> None:
        key = normalize_name(text)
        self._aliases.setdefault(key, {})[entity.id] = entity
        self._known_aliases.setdefault(entity.id, set()).add(key)

    def _add_alias(self, entity: Entity, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            return
        key = normalize_name(text)
        if key in self._known_aliases.get(entity.id, set()):
            return
        self._session.add(
            EntityAlias(entity_id=entity.id, alias_text=text)
        )
        self._register_alias_text(text, entity)

    def resolve(self, raw: RawEntity) -> Entity:
        """Get-or-create an entity for a declared LLM entity."""
        name = " ".join(raw.name.split())
        kind = raw.type.strip()
        key = (normalize_name(name), normalize_type(kind))

        entity = self._entities.get(key)
        if entity is None:
            entity = Entity(
                workspace_id=self._workspace_id,
                canonical_name=name,
                entity_type=kind,
            )
            self._session.add(entity)
            self._session.flush()
            self._index_entity(entity)
            self.created_count += 1
            self._add_alias(entity, name)

        # Surface forms seen in this document become aliases (idempotently).
        self._add_alias(entity, name)
        for alias in raw.aliases:
            self._add_alias(entity, alias)
        return entity

    def _global_matches(self, norm: str) -> dict[uuid.UUID, Entity]:
        """All entities reachable by canonical name or alias (any type)."""
        matches: dict[uuid.UUID, Entity] = {}
        for (candidate_name, _type), entity in self._entities.items():
            if candidate_name == norm:
                matches[entity.id] = entity
        matches.update(self._aliases.get(norm, {}))
        return matches

    def resolve_reference(
        self, name: str, declared: RawEntity | None
    ) -> Entity | None:
        """Resolve a declared reference; None = missing or ambiguous."""
        if declared is not None:
            return self.resolve(declared)

        matches = self._global_matches(normalize_name(name))
        if len(matches) == 1:
            return next(iter(matches.values()))
        return None  # unknown or ambiguous — do not guess (#12)

    def get_or_create_unknown(self, name: str) -> tuple[Entity, bool] | None:
        """Resolve an undeclared reference, salvaging it as type "unknown".

        Returns ``(entity, was_created)`` or None when the name is
        ambiguous across existing entities.
        """
        surface = " ".join(name.split())
        matches = self._global_matches(normalize_name(surface))
        if len(matches) == 1:
            return next(iter(matches.values())), False
        if len(matches) > 1:
            return None

        entity = Entity(
            workspace_id=self._workspace_id,
            canonical_name=surface,
            entity_type=UNKNOWN_ENTITY_TYPE,
        )
        self._session.add(entity)
        self._session.flush()
        self._index_entity(entity)
        self.created_count += 1
        self._add_alias(entity, surface)
        return entity, True
