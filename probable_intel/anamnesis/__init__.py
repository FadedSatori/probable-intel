"""Anamnesis — a second brain built on probable-intel.

Phase 0: a Markdown vault plus a deterministic `pi brain` CLI that Claude Code
skills call for the mechanical steps (hashing, logging, linting, searching).
Design: docs/second-brain/DESIGN.md
"""
from .vault import Vault, VaultNotFound

__all__ = ["Vault", "VaultNotFound"]
