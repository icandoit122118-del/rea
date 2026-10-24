from __future__ import annotations

import os

EXTENSIONS = (".png", ".tga", ".dds", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
SUBFOLDERS = ("", "images", "textures", "tex", "Textures", "Images")


def stem(name: str) -> str:
    tail = name.replace("\\", "/").rsplit("/", 1)[-1]
    return os.path.splitext(tail)[0].lower()


def index_folder(folder: str) -> dict:
    found: dict = {}
    if not folder or not os.path.isdir(folder):
        return found
    for entry in sorted(os.listdir(folder)):
        path = os.path.join(folder, entry)
        if not os.path.isfile(path):
            continue
        base, extension = os.path.splitext(entry)
        if extension.lower() not in EXTENSIONS:
            continue
        found.setdefault(base.lower(), path)
    return found


def search_paths(roots) -> list:
    paths = []
    for root in roots:
        if not root:
            continue
        for sub in SUBFOLDERS:
            candidate = os.path.join(root, sub) if sub else root
            if os.path.isdir(candidate) and candidate not in paths:
                paths.append(candidate)
    return paths


def resolve(names, roots) -> dict:
    available: dict = {}
    for folder in search_paths(roots):
        for key, path in index_folder(folder).items():
            available.setdefault(key, path)

    resolved = {}
    for name in names:
        path = available.get(stem(name))
        if path is not None:
            resolved[name] = path
    return resolved
