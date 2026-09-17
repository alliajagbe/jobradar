"""Build a tailored variant from a compact spec.

Usage:
  .venv/bin/python scripts/make_variant.py spec.json

A full variant is about a hundred lines of YAML, most of it the same every time:
the ten experience bullets and four projects in a fixed order, each naming the
master bullet it came from. Hand-writing that per application is where a typo
becomes a wrong `from:` and the anti-fabrication check starts comparing against
the wrong source.

So a spec carries only what actually varies per job, and this fills in the rest
from the master:

  {"slug": ..., "company": ..., "title": ..., "url": ...,
   "summary": "three rendered lines",
   "skills": {"skl.analytics": [...], ...},      # order of keys is render order
   "bullets": {"exp.ff.b1": "re-angled text"}}   # omitted bullets keep master text
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.resume.model import load_master                  # noqa: E402
from jobradar.resume.paths import resolve                       # noqa: E402

ORDER = ["exp.wfu", "exp.castlery", "exp.benori", "exp.founderforward", "exp.sbsf"]


def _q(text: str) -> str:
    return json.dumps(" ".join(str(text).split()), ensure_ascii=False)


def build(spec: dict) -> str:
    master = load_master()
    out: list[str] = ["schema: 1"]
    out.append(f'job: {{id: "link:{spec["slug"]}", company: {_q(spec["company"])}, '
               f'title: {_q(spec["title"])}, jd_sha: link}}')
    for line in (spec.get("note") or "").splitlines():
        if line.strip():
            out.append(f"# {line.strip()}")
    out.append("summary: >-")
    for chunk in _wrap(spec["summary"], 86):
        out.append(f"  {chunk}")
    out.append("skills:")
    pools = {g["id"]: set(g["items"]) for g in master.raw["skills"]}
    for gid, items in spec["skills"].items():
        bad = [i for i in items if i not in pools.get(gid, set())]
        if bad:
            raise SystemExit(f"{gid}: {bad} are not in the master pool")
        out.append(f"  - id: {gid}")
        out.append(f"    items: [{', '.join(_q(i) for i in items)}]")
    out.append("experience:")
    for role_id in ORDER:
        role = master.role(role_id)
        out.append(f"  - role: {role_id}")
        out.append("    bullets:")
        for node in role["bullets"]:
            bid = node["id"]
            text = spec.get("bullets", {}).get(bid)
            if text:
                out.append(f"      - {{from: {bid}, text: {_q(text)}}}")
            else:
                out.append(f"      - {{from: {bid}}}")
    out.append("projects:")
    for pid in spec.get("projects") or [p["id"] for p in master.raw["projects"]]:
        proj = master.project(pid)
        bid = proj["bullet"]["id"]
        text = spec.get("bullets", {}).get(bid)
        inner = f"{{from: {bid}" + (f", text: {_q(text)}}}" if text else "}")
        out.append(f"  - {{id: {pid}, bullet: {inner}}}")
    return "\n".join(out) + "\n"


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = " ".join(text.split()).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    spec = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    path = resolve("variants", f"{spec['slug']}.yaml", create_parent=True)
    path.write_text(build(spec), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
