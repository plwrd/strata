"""CRDT machinery for collaborative layers (M9, ADR-0006).

Yjs semantics via ``pycrdt`` (Rust ``y-crdt`` bindings), wire-compatible with the
JS Yjs the renderer's editor speaks. The authoritative document lives here, on
the Python side, because sealing an update requires the layer key — which never
leaves the process.
"""
