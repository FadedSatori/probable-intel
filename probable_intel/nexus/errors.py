class NEXUSError(Exception):
    """Fatal error in a NEXUS apparatus definition — apparatus cannot be loaded."""

    def __init__(self, message: str, apparatus_name: str = "", line: int = 0) -> None:
        self.apparatus_name = apparatus_name
        self.line = line
        loc = f"{apparatus_name}:{line}" if apparatus_name and line else apparatus_name or ""
        super().__init__(f"[{loc}] {message}" if loc else message)


class NEXUSWarning(UserWarning):
    """Non-fatal issue in a NEXUS apparatus definition."""
