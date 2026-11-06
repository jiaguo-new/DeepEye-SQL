"""Frozen deterministic candidate-only selector. No labels or gold-derived inputs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re

SELECTOR_VERSION = "public-consensus-v3"


@dataclass
class Candidate:
    sql: str
    family: str
    template: str
    sample_index: int
    ok: bool
    official_key: str | None
    schema_view: str = "full"


@dataclass
class Decision:
    sql: str
    reason: str
    official_key: str | None
    support: int
    families: list[str]
    templates: list[str]

    def to_dict(self):
        return asdict(self)


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().lower()).rstrip(";").strip()


def _groups(candidates):
    groups = {}
    for c in candidates:
        if c.ok and c.official_key and c.sql:
            groups.setdefault(c.official_key, []).append(c)
    return groups


def _representative(members):
    return min(members, key=lambda c: (
        c.template != "standard", c.schema_view != "full", c.sample_index, _norm(c.sql), c.family
    ))


def _rank(members):
    return (
        len({c.family for c in members}),
        len({c.schema_view for c in members}),
        len({c.template for c in members}),
        len(members),
        sum(c.template == "standard" for c in members),
    )


def _best(groups):
    items = groups.items() if isinstance(groups, dict) else groups
    ordered = sorted(items, key=lambda item: (
        tuple(-x for x in _rank(item[1])), _norm(_representative(item[1]).sql)
    ))
    return ordered[0] if ordered else (None, [])


def select_candidate(candidates: list[Candidate], strategy: str = "standard_champion") -> Decision:
    if strategy not in {"standard_champion", "all_cluster"}:
        raise ValueError(f"Unknown public selection strategy: {strategy}")
    groups = _groups(candidates)
    if not groups:
        # Deterministic syntactic fallback; remains explicitly non-executable.
        fallback_pool = [c for c in candidates if c.sql] or candidates
        fallback = min(fallback_pool, key=lambda c: (
            c.template != "standard", c.schema_view != "full", c.sample_index, _norm(c.sql), c.family
        )) if fallback_pool else None
        if fallback is None:
            raise ValueError("No candidates")
        return Decision(fallback.sql, "no_executable_fallback", None, 0, [], [])

    standard = {k: v for k, v in groups.items() if any(c.template == "standard" for c in v)}
    if strategy == "all_cluster":
        champion_key, champion = _best(groups)
        rep = _representative(champion)
        return Decision(rep.sql, "all_cluster", champion_key, len(champion),
                        sorted({c.family for c in champion}), sorted({c.template for c in champion}))
    champion_key, champion = _best(standard or groups)
    rep = _representative(champion)
    decision = Decision(rep.sql, "standard_champion", champion_key, len(champion),
                        sorted({c.family for c in champion}), sorted({c.template for c in champion}))

    alternatives = []
    for key, members in groups.items():
        if key == champion_key:
            continue
        families = {c.family for c in members}
        templates = {c.template for c in members}
        views = {c.schema_view for c in members}
        if (len(families) >= 2 or len(views) >= 2) and len(templates) >= 2 and len(members) >= 3:
            alternatives.append((key, members))
    if alternatives:
        alt_key, alt = _best(alternatives)
        champion_families = {c.family for c in champion}
        champion_views = {c.schema_view for c in champion}
        if len(champion_families) < 2 and len(champion_views) < 2 and len(alt) >= len(champion) + 2:
            rep = _representative(alt)
            decision = Decision(rep.sql, "cross_source_consensus", alt_key, len(alt),
                                sorted({c.family for c in alt}), sorted({c.template for c in alt}))
    return decision
