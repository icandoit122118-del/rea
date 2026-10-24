from __future__ import annotations

import struct
from dataclasses import dataclass

NODE_SIZE = 12


@dataclass(frozen=True)
class Node:
    reference_bit: int
    left: int
    right: int
    name: str


def read_cstring(data, at: int) -> str:
    if at <= 0 or at >= len(data):
        return ""
    end = data.find(b"\x00", at)
    if end == -1:
        end = len(data)
    return data[at:end].decode("utf-8", "replace")


def read_tree(data, at: int) -> list:
    if at <= 0 or at + NODE_SIZE > len(data):
        return []

    nodes = []
    index = 0
    highest = 0

    while index <= highest:
        offset = at + index * NODE_SIZE
        if offset + NODE_SIZE > len(data):
            break
        reference_bit, left, right, name_at = struct.unpack_from("<IHHI", data, offset)
        highest = max(highest, left, right)
        nodes.append(Node(reference_bit, left, right, read_cstring(data, name_at)))
        index += 1
        if index > 0xFFFF:
            break

    return nodes


def names(data, at: int) -> list:
    nodes = read_tree(data, at)
    return [node.name for node in nodes[1:]]
