"""Grading: catch rate per rung per error class, and what the gate costs.

Two numbers, not one. A defense is only interesting as a pair:

  CATCH RATE        of the corrupted payloads carrying error class C, what
                    fraction did this rung reject?
  FALSE REJECTION   of the CLEAN payloads, what fraction did this rung reject
                    anyway? A gate that rejects everything has a perfect catch
                    rate and is worthless, and reporting catch rate alone hides
                    that. Every rung here is measured against clean documents
                    as well as corrupted ones.

Reported per rung so the curve can be read directly: where it goes flat, the
rung added nothing over the one below it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .errors import FAMILIES


@dataclass
class Cell:
    """One (rung, error class) cell."""

    seen: int = 0
    caught: int = 0

    @property
    def rate(self) -> float:
        return self.caught / self.seen if self.seen else 0.0


@dataclass
class Results:
    # rung -> error_class -> Cell
    by_class: dict[str, dict[str, Cell]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Cell)))
    # rung -> Cell over clean documents (caught == wrongly rejected)
    clean: dict[str, Cell] = field(default_factory=lambda: defaultdict(Cell))

    def record(self, rung: str, error_class: str | None, rejected: bool) -> None:
        if error_class is None:
            c = self.clean[rung]
            c.seen += 1
            c.caught += 1 if rejected else 0
        else:
            c = self.by_class[rung][error_class]
            c.seen += 1
            c.caught += 1 if rejected else 0

    def family_rate(self, rung: str, family: str) -> tuple[int, int]:
        """(caught, seen) aggregated over every class in `family`."""
        caught = seen = 0
        for cls, cell in self.by_class.get(rung, {}).items():
            if FAMILIES[cls] == family:
                caught += cell.caught
                seen += cell.seen
        return caught, seen

    def to_dict(self) -> dict:
        return {
            "by_class": {
                rung: {cls: {"seen": c.seen, "caught": c.caught,
                             "rate": round(c.rate, 4)}
                       for cls, c in classes.items()}
                for rung, classes in self.by_class.items()
            },
            "clean": {
                rung: {"seen": c.seen, "false_rejections": c.caught,
                       "false_rejection_rate": round(c.rate, 4)}
                for rung, c in self.clean.items()
            },
        }


def wilson(caught: int, seen: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Used instead of the normal approximation
    because several cells here are at 0/n or n/n, where the normal interval
    has zero width and is simply wrong."""
    if seen == 0:
        return (0.0, 1.0)
    p = caught / seen
    d = 1 + z * z / seen
    center = (p + z * z / (2 * seen)) / d
    half = z * ((p * (1 - p) / seen + z * z / (4 * seen * seen)) ** 0.5) / d
    return (max(0.0, center - half), min(1.0, center + half))


def table(results: Results, rungs: tuple[str, ...],
          classes: tuple[str, ...]) -> str:
    """Fixed-width catch-rate table. Rows are error classes, columns rungs."""
    w = max(len(c) for c in classes) + 2
    head = "error class".ljust(w) + "".join(r.split("_", 1)[0].rjust(6)
                                            for r in rungs)
    lines = [head, "-" * len(head)]
    for fam in ("structural", "value_inconsistent", "value_consistent"):
        lines.append(f"-- {fam}")
        for cls in classes:
            if FAMILIES[cls] != fam:
                continue
            row = cls.ljust(w)
            for r in rungs:
                cell = results.by_class.get(r, {}).get(cls)
                row += (f"{cell.rate * 100:5.0f}%" if cell and cell.seen
                        else "    -")
            lines.append(row)
    lines.append("-" * len(head))
    row = "false rejections".ljust(w)
    for r in rungs:
        c = results.clean.get(r)
        row += (f"{c.rate * 100:5.0f}%" if c and c.seen else "    -")
    lines.append(row)
    return "\n".join(lines)
