from __future__ import annotations

from ..binstream import BinStream, TruncatedError

PC_MAGICS = frozenset({b"PC", b"PS"})

HEADER_MAGIC_SIZE = 2

def sniff_pc_ps(head: bytes) -> bool:
    return head[:HEADER_MAGIC_SIZE] in PC_MAGICS

def read_pc_ps(stream: BinStream) -> list[bytes]:
    magic = stream.bytes(HEADER_MAGIC_SIZE)
    if magic not in PC_MAGICS:
        raise ValueError(f"not a PC/PS container: magic {magic!r}")

    count = stream.u16()
    offsets = [stream.u32() for _ in range(count + 1)]

    total = len(stream)
    for i in range(count):
        if offsets[i] > offsets[i + 1]:
            raise ValueError(
                f"entry {i} offsets descend: {offsets[i]:#x} > {offsets[i + 1]:#x}"
            )
    if offsets and offsets[-1] > total:
        raise TruncatedError(
            f"container declares {offsets[-1]} bytes but the file has {total}"
        )

    return [stream.slice(offsets[i], offsets[i + 1]) for i in range(count)]

def non_empty(entries: list[bytes]) -> list[tuple[int, bytes]]:
    return [(i, data) for i, data in enumerate(entries) if data]
