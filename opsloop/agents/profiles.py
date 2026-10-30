"""Specialist profiles (YAML, own-built). The coding agent runs with one of these; its write
scope is enforced in code, both while the agent works and again by the diff policy."""

from dataclasses import dataclass
from pathlib import Path

import yaml

PROFILE_DIR = Path(__file__).parent / "specialists"


@dataclass(frozen=True)
class SpecialistProfile:
    name: str
    wing: str
    instructions: str
    allowed_write_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    tools: tuple[str, ...]
    runbooks: tuple[str, ...]
    max_files_changed: int


def load_profile(name: str, directory: Path = PROFILE_DIR) -> SpecialistProfile:
    data = yaml.safe_load((directory / f"{name}.yaml").read_text(encoding="utf-8"))
    return SpecialistProfile(
        name=data["name"],
        wing=data["wing"],
        instructions=data["instructions"],
        allowed_write_paths=tuple(data["allowed_write_paths"]),
        forbidden_paths=tuple(data["forbidden_paths"]),
        tools=tuple(data["tools"]),
        runbooks=tuple(data.get("runbooks", [])),
        max_files_changed=int(data["max_files_changed"]),
    )


def load_all(directory: Path = PROFILE_DIR) -> dict[str, SpecialistProfile]:
    return {p.stem: load_profile(p.stem, directory) for p in sorted(directory.glob("*.yaml"))}


def specialist_for(category: str, profiles: dict[str, SpecialistProfile] | None = None) -> str:
    """Specialist chosen from the root-cause category; generic when none matches."""
    profiles = profiles or load_all()
    for key, prof in profiles.items():
        if prof.wing == category:
            return key
    return "generic_specialist"
