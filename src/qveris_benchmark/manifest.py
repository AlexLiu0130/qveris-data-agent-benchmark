"""Manifest contracts that resolve safe model aliases to provider tools."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .contracts import AuthMode, Domain, PlanStatus, SemanticPlan


TOOL_MANIFEST_SCHEMA_VERSION = "tool-manifest.v1"


class UnknownToolAlias(KeyError):
    """Raised when a READY plan names no configured tool alias."""


class PlanManifestMismatch(ValueError):
    """Raised when a plan and its resolved manifest entry disagree."""


@dataclass(frozen=True)
class ToolManifestEntry:
    """Provider-facing metadata; tool_id is deliberately absent from SemanticPlan."""

    alias: str
    tool_id: str
    request_schema: Mapping[str, Any]
    response_schema: Mapping[str, Any]
    domain: Domain
    auth_mode: AuthMode

    def __post_init__(self) -> None:
        if not self.alias or not self.tool_id:
            raise ValueError("alias and tool_id must not be empty")
        if type(self.domain) is not Domain:
            raise TypeError("domain must be a Domain")
        if self.auth_mode is not AuthMode.BEARER:
            raise ValueError("runtime tools must use bearer authentication")
        if type(self.request_schema) is not dict or type(self.response_schema) is not dict:
            raise TypeError("schemas must be JSON objects")
        object.__setattr__(self, "request_schema", MappingProxyType(dict(self.request_schema)))
        object.__setattr__(self, "response_schema", MappingProxyType(dict(self.response_schema)))


@dataclass(frozen=True)
class Manifest:
    """Read-only alias registry for connector and runner implementations."""

    schema_version: str
    entries: Mapping[str, ToolManifestEntry]

    def __post_init__(self) -> None:
        if self.schema_version != TOOL_MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"unsupported runtime manifest schema: {self.schema_version}")
        copied = dict(self.entries)
        for alias, entry in copied.items():
            if type(entry) is not ToolManifestEntry:
                raise TypeError("runtime manifest entries must be ToolManifestEntry instances")
            if alias != entry.alias:
                raise ValueError("manifest key must match entry alias")
        object.__setattr__(self, "entries", MappingProxyType(copied))

    @classmethod
    def from_entries(
        cls, entries: Iterable[ToolManifestEntry], *, schema_version: str
    ) -> "Manifest":
        registry: dict[str, ToolManifestEntry] = {}
        for entry in entries:
            if type(entry) is not ToolManifestEntry:
                raise TypeError("runtime manifest entries must be ToolManifestEntry instances")
            if entry.alias in registry:
                raise ValueError(f"duplicate manifest alias: {entry.alias}")
            registry[entry.alias] = entry
        return cls(schema_version, registry)

    def resolve(self, alias: str) -> ToolManifestEntry:
        try:
            return self.entries[alias]
        except KeyError as exc:
            raise UnknownToolAlias(alias) from exc

    def entry_for(self, plan: SemanticPlan) -> ToolManifestEntry:
        if plan.status is not PlanStatus.READY or plan.tool_alias is None or plan.domain is None:
            raise PlanManifestMismatch("only a complete READY plan can resolve a tool")
        entry = self.resolve(plan.tool_alias)
        if entry.domain is not plan.domain:
            raise PlanManifestMismatch("plan domain does not match manifest domain")
        return entry
