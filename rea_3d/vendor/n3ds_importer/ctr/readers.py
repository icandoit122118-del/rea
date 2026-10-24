from __future__ import annotations

from .compress.lz import decompress_lz, sniff_lz
from .container.pc_ps import read_pc_ps, sniff_pc_ps
from .formats.bch.header import sniff_bch
from .formats.gfl2.gfmodel import read_gfmodel, sniff_gfmodel
from .formats.gfl2.gftexture import read_gftexture, sniff_gftexture
from .registry import Kind, Reader, register

_REGISTERED = False

def register_builtin_readers() -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    register(Reader(
        name="LZ",
        kind=Kind.COMPRESSION,
        sniff=sniff_lz,
        load=lambda stream: decompress_lz(stream.slice(0, len(stream))),
    ))
    register(Reader(
        name="PC/PS",
        kind=Kind.CONTAINER,
        sniff=sniff_pc_ps,
        load=read_pc_ps,
    ))
    register(Reader(
        name="GFModel",
        kind=Kind.MODEL,
        sniff=sniff_gfmodel,
        load=read_gfmodel,
    ))
    register(Reader(
        name="BCH",
        kind=Kind.MODEL,
        sniff=sniff_bch,
        load=lambda stream: stream.slice(0, len(stream)),
    ))
    register(Reader(
        name="GFTexture",
        kind=Kind.TEXTURE,
        sniff=sniff_gftexture,
        load=read_gftexture,
    ))

    _REGISTERED = True
