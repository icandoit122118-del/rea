from __future__ import annotations

from dataclasses import dataclass

ATTRIBBUFFERS_FORMAT_LOW = 0x0201
ATTRIBBUFFERS_FORMAT_HIGH = 0x0202
ATTRIBBUFFER0_CONFIG1 = 0x0204
ATTRIBBUFFER0_CONFIG2 = 0x0205
INDEXBUFFER_CONFIG = 0x0227
NUMVERTICES = 0x0228
FIXEDATTRIB_INDEX = 0x0232
FIXEDATTRIB_DATA0 = 0x0233
FIXEDATTRIB_DATA1 = 0x0234
FIXEDATTRIB_DATA2 = 0x0235
VSH_NUM_ATTR = 0x0242
PRIMITIVE_CONFIG = 0x025E
VSH_ATTRIBUTES_PERMUTATION_LOW = 0x02BB
VSH_ATTRIBUTES_PERMUTATION_HIGH = 0x02BC

@dataclass(frozen=True)
class Command:
    register: int
    parameters: tuple
    mask: int

    @property
    def param(self) -> int:
        return self.parameters[0]

def decode_commands(words: list) -> list:
    out: list = []
    i = 0
    n = len(words)

    while i + 1 < n:
        parameter = words[i]
        command = words[i + 1]
        i += 2

        register = command & 0xFFFF
        mask = (command >> 16) & 0xF
        extra = (command >> 20) & 0x7FF
        consecutive = (command >> 31) != 0

        if consecutive:
            for step in range(extra + 1):
                out.append(Command(register + step, (parameter,), mask))
                if step < extra:
                    if i >= n:
                        break
                    parameter = words[i]
                    i += 1
        else:
            params = [parameter]
            for _ in range(extra):
                if i >= n:
                    break
                params.append(words[i])
                i += 1
            out.append(Command(register, tuple(params), mask))

        if i & 1:
            i += 1

    return out
