from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY_DOCS = (
    ROOT / "README.md",
    ROOT / "EXPERIMENT_REPORT.md",
)
CURRENT_DOCS = tuple(
    path
    for path in (ROOT / "docs").rglob("*.md")
    if "archive" not in path.parts
)
CURRENT_SPECS = tuple((ROOT / "specs" / "018-forumtwin-v2").rglob("*.md"))

AMBIGUOUS_TICK = re.compile(r"(?<![A-Za-z0-9_-])T\d+(?:[–—-]T?\d+)?(?![A-Za-z0-9_-])")
REQUIRED_DOCS = {
    "README.md",
    "EXPERIMENT_REPORT.md",
    "docs/USER_GUIDE.md",
    "docs/ARCHITECTURE_AND_CODE.md",
    "docs/DATA_PIPELINE.md",
    "docs/UPSTREAM_COMPARISON.md",
    "specs/018-forumtwin-v2/TECHNICAL_SPEC.md",
    "specs/018-forumtwin-v2/IMPLEMENTATION_STATUS.md",
    "specs/018-forumtwin-v2/ACCEPTANCE_MATRIX.md",
}


def prose_without_code(markdown: str) -> str:
    without_fences = re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)
    return re.sub(r"`[^`]*`", "", without_fences)


def main() -> int:
    errors: list[str] = []
    for relative in sorted(REQUIRED_DOCS):
        if not (ROOT / relative).is_file():
            errors.append(f"missing required document: {relative}")

    for path in (*ENTRY_DOCS, *CURRENT_DOCS, *CURRENT_SPECS):
        prose = prose_without_code(path.read_text(encoding="utf-8"))
        for match in AMBIGUOUS_TICK.finditer(prose):
            line = prose.count("\n", 0, match.start()) + 1
            errors.append(
                f"{path.relative_to(ROOT)}:{line}: ambiguous '{match.group(0)}'; "
                "write 'Tick N' or use a RUN-* identifier"
            )

    if errors:
        print("DOC_NAMING_FAILED")
        for error in errors:
            print(error)
        return 1

    print(
        "DOC_NAMING_OK "
        f"checked={len(ENTRY_DOCS) + len(CURRENT_DOCS) + len(CURRENT_SPECS)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
