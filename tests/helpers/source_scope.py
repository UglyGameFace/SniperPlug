from __future__ import annotations

import ast
from pathlib import Path


def function_source(path: str, name: str) -> str:
    text = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])

    raise AssertionError(f"Function {name!r} not found in {path}")


def class_source(path: str, name: str) -> str:
    text = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])

    raise AssertionError(f"Class {name!r} not found in {path}")
