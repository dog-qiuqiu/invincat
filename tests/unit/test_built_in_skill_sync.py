from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "invincat_cli" / "built_in_skills"
OFFICE_SKILLS = ("docx", "pptx", "xlsx")

SHARED_OFFICE_SCRIPT_PATHS = (
    "scripts/office/helpers/merge_runs.py",
    "scripts/office/helpers/simplify_redlines.py",
    "scripts/office/pack.py",
    "scripts/office/soffice.py",
    "scripts/office/unpack.py",
    "scripts/office/validate.py",
    "scripts/office/validators/__init__.py",
    "scripts/office/validators/base.py",
    "scripts/office/validators/docx.py",
    "scripts/office/validators/pptx.py",
    "scripts/office/validators/redlining.py",
)

SHARED_SCHEMA_PATHS = tuple(
    path.relative_to(SKILLS / "docx").as_posix()
    for path in sorted((SKILLS / "docx" / "scripts" / "office" / "schemas").rglob("*"))
    if path.is_file()
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_shared_office_skill_scripts_stay_in_sync() -> None:
    for relative in SHARED_OFFICE_SCRIPT_PATHS:
        hashes = {
            skill: _sha256(SKILLS / skill / relative)
            for skill in OFFICE_SKILLS
        }

        assert len(set(hashes.values())) == 1, f"{relative} differs: {hashes}"


def test_shared_office_skill_schemas_stay_in_sync() -> None:
    assert SHARED_SCHEMA_PATHS

    for relative in SHARED_SCHEMA_PATHS:
        hashes = {
            skill: _sha256(SKILLS / skill / relative)
            for skill in OFFICE_SKILLS
        }

        assert len(set(hashes.values())) == 1, f"{relative} differs: {hashes}"
