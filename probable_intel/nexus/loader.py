from __future__ import annotations

import logging
import warnings
from pathlib import Path

from .errors import NEXUSError, NEXUSWarning
from .parser import NexusParser
from .spec import ApparatusSpec
from .validator import ApparatusValidator

log = logging.getLogger(__name__)


class NexusLoader:
    def __init__(self) -> None:
        self._parser = NexusParser()
        self._validator = ApparatusValidator()

    def load(self, path: Path | str) -> ApparatusSpec:
        path = Path(path)
        if not path.exists():
            raise NEXUSError(f"apparatus file not found: {path}")

        spec = self._parser.parse_file(path)
        self._validator.validate(spec)  # NEXUSWarnings propagate to caller

        log.info("loaded apparatus %r from %s (%d nodes)", spec.name, path, len(spec.nodes))
        return spec

    def load_directory(self, directory: Path | str) -> list[ApparatusSpec]:
        directory = Path(directory)
        specs = []
        for nx_file in sorted(directory.glob("*.nx")):
            try:
                specs.append(self.load(nx_file))
            except NEXUSError as e:
                log.error("failed to load %s: %s", nx_file, e)
        return specs
