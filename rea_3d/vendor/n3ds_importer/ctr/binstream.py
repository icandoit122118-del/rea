from __future__ import annotations

import struct

class TruncatedError(EOFError):
    pass

_U8 = struct.Struct("<B")
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_I8 = struct.Struct("<b")
_I16 = struct.Struct("<h")
_I32 = struct.Struct("<i")
_F32 = struct.Struct("<f")

class BinStream:
    __slots__ = ("_data", "_pos")

    def __init__(self, data: bytes, offset: int = 0) -> None:
        self._data = data
        self._pos = offset

    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > len(self._data):
            raise TruncatedError(f"seek to {pos} outside buffer of {len(self._data)} bytes")
        self._pos = pos

    def skip(self, n: int) -> None:
        self.seek(self._pos + n)

    def align(self, n: int) -> None:
        remainder = self._pos % n
        if remainder:
            self.skip(n - remainder)

    def remaining(self) -> int:
        return len(self._data) - self._pos

    def __len__(self) -> int:
        return len(self._data)

    def _take(self, fmt: struct.Struct):
        end = self._pos + fmt.size
        if end > len(self._data):
            raise TruncatedError(
                f"need {fmt.size} bytes at {self._pos}, only {self.remaining()} remain"
            )
        value = fmt.unpack_from(self._data, self._pos)[0]
        self._pos = end
        return value

    def bytes(self, n: int) -> bytes:
        end = self._pos + n
        if end > len(self._data):
            raise TruncatedError(f"need {n} bytes at {self._pos}, only {self.remaining()} remain")
        chunk = self._data[self._pos:end]
        self._pos = end
        return chunk

    def slice(self, start: int, end: int) -> bytes:
        if start < 0 or end > len(self._data) or start > end:
            raise TruncatedError(
                f"slice [{start}:{end}] outside buffer of {len(self._data)} bytes"
            )
        return self._data[start:end]

    def magic(self, n: int) -> bytes:
        return self._data[self._pos:self._pos + n]

    def u8(self) -> int:
        return self._take(_U8)

    def u16(self) -> int:
        return self._take(_U16)

    def u32(self) -> int:
        return self._take(_U32)

    def i8(self) -> int:
        return self._take(_I8)

    def i16(self) -> int:
        return self._take(_I16)

    def i32(self) -> int:
        return self._take(_I32)

    def f32(self) -> float:
        return self._take(_F32)

    def string(self, n: int) -> str:
        return self.bytes(n).split(b"\x00", 1)[0].decode("utf-8", "replace").strip()

    def pstring(self, n: int) -> str:
        return self.bytes(n).split(b"\x00", 1)[0].decode("utf-8", "replace")

    def bstring(self) -> str:
        return self.pstring(self.u8())

    def istring(self) -> str:
        return self.pstring(self.i32())

    def vec(self, n: int) -> tuple:
        return tuple(self.f32() for _ in range(n))

    def cstring(self) -> str:
        end = self._data.find(b"\x00", self._pos)
        if end == -1:
            raise TruncatedError(f"unterminated string at {self._pos}")
        text = self._data[self._pos:end].decode("utf-8", "replace")
        self._pos = end + 1
        return text
