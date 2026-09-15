"""Loading the master, resolving a variant against it.

The master is the only source of truth for content. A variant may reorder,
select and rephrase; it may never introduce an employer, a title, a location, a
date, a number or a tool. Most of that is enforced structurally rather than by
validation: a variant has no fields for employer, title, location or date, so
changing one is not something the format can express.

Every bullet a variant carries must name the master bullet it came from. That
`from:` is what makes the anti-fabrication check possible at all, and it is why
a bullet without one is a hard error rather than a new bullet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .paths import ResumeError, master_path


@dataclass(frozen=True)
class Bullet:
    id: str
    text: str
    verb_class: str
    metrics: tuple[str, ...]
    tools: tuple[str, ...]
    entities: tuple[str, ...] = ()


@dataclass
class Master:
    raw: dict
    bullets: dict[str, Bullet] = field(default_factory=dict)

    @property
    def identity(self) -> dict:
        return self.raw["identity"]

    def role(self, role_id: str) -> dict:
        for role in self.raw["experience"]:
            if role["id"] == role_id:
                return role
        raise ResumeError(f"unknown experience id {role_id!r}")

    def project(self, proj_id: str) -> dict:
        for proj in self.raw["projects"]:
            if proj["id"] == proj_id:
                return proj
        raise ResumeError(f"unknown project id {proj_id!r}")

    def skill_group(self, group_id: str) -> dict:
        for group in self.raw["skills"]:
            if group["id"] == group_id:
                return group
        raise ResumeError(f"unknown skill group id {group_id!r}")


def _mk_bullet(node: dict) -> Bullet:
    facts = node.get("facts") or {}
    return Bullet(
        id=node["id"],
        text=" ".join(node["text"].split()),
        verb_class=node.get("verb_class", ""),
        metrics=tuple(str(m) for m in facts.get("metrics") or ()),
        tools=tuple(facts.get("tools") or ()),
        entities=tuple(facts.get("entities") or ()),
    )


def load_master(path: Path | None = None) -> Master:
    path = path or master_path()
    if not path.exists():
        raise ResumeError(
            f"no master resume at {path}. Create it there, outside the repository; "
            f"see jobradar/resume/paths.py for why it must not live in the repo."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    master = Master(raw=raw)

    def add(node: dict) -> None:
        bullet = _mk_bullet(node)
        if bullet.id in master.bullets:
            # A duplicate id makes the anti-fabrication check compare a rewritten
            # bullet against the wrong source, which is worse than no check.
            raise ResumeError(f"duplicate bullet id {bullet.id!r} in {path}")
        master.bullets[bullet.id] = bullet
        for alt in node.get("alts") or ():
            add(alt)

    for role in raw["experience"]:
        for node in role["bullets"]:
            add(node)
    for proj in raw["projects"]:
        add(proj["bullet"])
    add(raw["summary"] | {"verb_class": "summary"})
    return master


def default_document(master: Master) -> dict:
    """The master rendered with nothing tailored. Used by `baseline`."""
    return {
        "identity": master.identity,
        "summary": " ".join(master.raw["summary"]["text"].split()),
        "education": master.raw["education"],
        "skills": [{**g, "items": g.get("default", g["items"])} for g in master.raw["skills"]],
        "experience": master.raw["experience"],
        "projects": master.raw["projects"],
    }


def resolve_variant(master: Master, variant: dict) -> dict:
    """Fold a variant onto the master into a renderable document."""
    doc_skills = []
    chosen = variant.get("skills") or []
    if chosen and len(chosen) != 5:
        raise ResumeError(f"a variant must name exactly five skill groups, got {len(chosen)}")
    for entry in chosen or [{"id": g["id"]} for g in master.raw["skills"]]:
        group = master.skill_group(entry["id"])
        items = entry.get("items") or group.get("default") or group["items"]
        pool = set(group["items"])
        extra = [i for i in items if i not in pool]
        if extra:
            raise ResumeError(
                f"skill group {group['id']}: {extra} are not in the master pool. "
                f"Skills may be reordered and selected, never invented."
            )
        doc_skills.append({**group, "items": list(items)})

    doc_exp = []
    for entry in variant.get("experience") or []:
        role = master.role(entry["role"])
        bullets = []
        for b in entry.get("bullets") or []:
            if "from" not in b:
                raise ResumeError(
                    f"a bullet in {entry['role']} has no `from:`. Every bullet must name "
                    f"the master bullet it derives from; that is what makes the "
                    f"anti-fabrication check possible."
                )
            src = master.bullets.get(b["from"])
            if src is None:
                raise ResumeError(f"unknown master bullet id {b['from']!r}")
            text = " ".join((b.get("text") or src.text).split())
            bullets.append({"id": src.id, "text": text, "source": src})
        doc_exp.append({**role, "bullets": bullets})

    doc_proj = []
    for entry in variant.get("projects") or []:
        pid = entry["id"] if isinstance(entry, dict) else entry
        proj = master.project(pid)
        node = entry.get("bullet") if isinstance(entry, dict) else None
        src = master.bullets[proj["bullet"]["id"]] if node is None else master.bullets[node["from"]]
        text = " ".join(((node or {}).get("text") or src.text).split())
        doc_proj.append({**proj, "bullet": {"id": src.id, "text": text, "source": src}})

    return {
        "identity": master.identity,
        "summary": " ".join((variant.get("summary") or master.raw["summary"]["text"]).split()),
        "education": master.raw["education"],
        "skills": doc_skills,
        "experience": doc_exp or master.raw["experience"],
        "projects": doc_proj or master.raw["projects"],
    }
