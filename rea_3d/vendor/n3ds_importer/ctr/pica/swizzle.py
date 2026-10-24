from __future__ import annotations

TILE = 8
TILE_TEXELS = TILE * TILE

def morton_index(px: int, py: int) -> int:
    return (
        (px & 1)
        | ((py & 1) << 1)
        | ((px & 2) << 1)
        | ((py & 2) << 2)
        | ((px & 4) << 2)
        | ((py & 4) << 3)
    )

def _build_tile_order() -> tuple:
    order = [0] * TILE_TEXELS
    for py in range(TILE):
        for px in range(TILE):
            order[morton_index(px, py)] = py * TILE + px
    return tuple(order)

TILE_ORDER = _build_tile_order()

def tiled_to_linear_index(x: int, y: int, width: int) -> int:
    tiles_across = width // TILE
    tile_index = (y // TILE) * tiles_across + (x // TILE)
    return tile_index * TILE_TEXELS + morton_index(x % TILE, y % TILE)

def deswizzle(texels: list, width: int, height: int) -> list:
    if width % TILE or height % TILE:
        raise ValueError(
            f"3DS textures are tiled {TILE}x{TILE}; {width}x{height} is not a multiple"
        )
    expected = width * height
    if len(texels) != expected:
        raise ValueError(f"expected {expected} texels for {width}x{height}, got {len(texels)}")

    out = [None] * expected
    for y in range(height):
        row = y * width
        for x in range(width):
            out[row + x] = texels[tiled_to_linear_index(x, y, width)]
    return out
