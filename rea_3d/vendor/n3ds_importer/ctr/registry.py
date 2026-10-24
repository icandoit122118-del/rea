from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .binstream import BinStream

class Kind(Enum):
    COMPRESSION = "compression"
    CONTAINER = "container"
    MODEL = "model"
    TEXTURE = "texture"
    ANIMATION = "animation"

class UnknownFormat(Exception):
    pass

@dataclass(frozen=True)
class Reader:
    name: str
    kind: Kind
    sniff: Callable[[bytes], bool]
    load: Callable[[BinStream], Any]

@dataclass(frozen=True)
class Node:
    path: tuple
    kind: Kind | None
    reader_name: str | None
    data: bytes
    error: str | None = None

    @property
    def label(self) -> str:
        if self.error:
            return f"{self.reader_name or '?'} (failed: {self.error})"
        return self.reader_name or "unidentified"

_READERS: list[Reader] = []

_SNIFF_WINDOW = 16

def register(reader: Reader) -> None:
    _READERS.append(reader)

def identify(data: bytes) -> Reader | None:
    head = data[:_SNIFF_WINDOW]
    for reader in _READERS:
        try:
            if reader.sniff(head):
                return reader
        except Exception:
            continue
    return None

@dataclass
class _Snapshot:
    readers: list = field(default_factory=list)

    def restore(self) -> None:
        _READERS[:] = self.readers

def registry_snapshot() -> _Snapshot:
    return _Snapshot(list(_READERS))

def walk(data: bytes, max_depth: int = 8) -> list[Node]:
    nodes: list[Node] = []
    _walk_into(data, (), 0, max_depth, nodes)
    return nodes

def _walk_into(data: bytes, path: tuple, depth: int, max_depth: int, out: list) -> None:
    if not data:
        return

    reader = identify(data)

    if reader is not None and depth < max_depth and reader.kind in (
        Kind.COMPRESSION,
        Kind.CONTAINER,
    ):
        try:
            produced = reader.load(BinStream(data))
        except Exception as exc:
            out.append(
                Node(path=path, kind=None, reader_name=reader.name,
                     data=data, error=f"{type(exc).__name__}: {exc}")
            )
            return

        if reader.kind is Kind.COMPRESSION:
            _walk_into(produced, path, depth + 1, max_depth, out)
        else:
            for index, entry in enumerate(produced):
                _walk_into(entry, path + (index,), depth + 1, max_depth, out)
        return

    out.append(
        Node(
            path=path,
            kind=reader.kind if reader else None,
            reader_name=reader.name if reader else None,
            data=data,
        )
    )
