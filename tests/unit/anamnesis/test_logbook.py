from __future__ import annotations

import pytest

from probable_intel.anamnesis import Vault
from probable_intel.anamnesis.capture import capture_bytes
from probable_intel.anamnesis.logbook import (
    LOG_ENTRY,
    append_episodes,
    append_log,
    iter_episodes,
    recent_log,
)


def test_log_entries_use_grepable_prefix(vault: Vault):
    entry = append_log(vault, "ingest", "  Some   Title ", "details here")
    assert LOG_ENTRY.match(entry)
    append_log(vault, "lint", "weekly")
    assert [(op, t) for _, op, t in recent_log(vault)] == [("ingest", "Some Title"), ("lint", "weekly")]
    with pytest.raises(ValueError):
        append_log(vault, "explode", "x")


def test_episodes_append_with_ids_and_inherited_tier(vault: Vault):
    item, _ = capture_bytes(vault, b"source", title="s", trust_tier="T1")
    written = append_episodes(vault, [
        {"proposition": "A did B in 2025.", "source": item.ref, "priority": "high",
         "anchor": {"exact": "did B"}},
        {"proposition": "User said C.", "source": "session"},
    ])
    assert written[0]["trust_tier"] == "T1" and written[0]["id"] != written[1]["id"]
    assert written[1]["priority"] == "medium" and "trust_tier" not in written[1]
    assert [e["proposition"] for e in iter_episodes(vault)] == ["A did B in 2025.", "User said C."]


@pytest.mark.parametrize("bad", [
    {"proposition": "", "source": "session"},
    {"proposition": "x", "source": "raw:000000000000"},
    {"proposition": "x", "source": "the internet"},
    {"proposition": "x", "source": "session", "priority": "urgent"},
    {"proposition": "x", "source": "session", "trust_tier": "T7"},
    {"proposition": "x", "source": "session", "anchor": {"prefix": "no exact"}},
    {"proposition": "x" * 601, "source": "session"},
])
def test_invalid_batches_write_nothing(vault: Vault, bad):
    with pytest.raises(ValueError):
        append_episodes(vault, [{"proposition": "fine", "source": "session"}, bad])
    assert iter_episodes(vault) == []
