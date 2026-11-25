"""Which references a .ma scene uses, and whether each is the latest
version — read straight from the Maya ASCII text, no Maya needed.

A .ma file declares its references near the top, before the first
createNode, as MEL `file` commands:

    file -rdi 1 -ns "chr" -rfn "chrRN" -typ "mayaAscii" "C:/.../chr_rig_v003.ma";   (nested, depth > 1)
    file -r -ns "chr" -dr 1 -rfn "chrRN" -typ "mayaAscii" "C:/.../chr_rig_v003.ma";   (top level)

so only that header is read. A path can be absolute, project-relative
(the repo root — batch/launcher Maya sessions setProject there), contain
$ENV vars, carry a "{n}" copy-number suffix, or be another machine's
absolute path to the same repo — resolve_reference_path() tries each.

Two version conventions are recognized (version_info()):
- version folder:  .../<name>/v003/<file>   (MayaPublisher's own publish layout)
- version in name: .../chr_rig_v003.ma       (work files)
"latest" is the highest number next to it on disk right now.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

LATEST = "latest"
OUTDATED = "outdated"
MISSING = "missing"
UNVERSIONED = "unversioned"

# Only createNode ends the header — `requires` lines can come before the
# `file -r` lines (Maya writes plugin requires like "fbxmaya" first).
_HEADER_END = ("createNode ",)
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_COPY_NUMBER = re.compile(r"\{\d+\}$")
_VERSION_FOLDER = re.compile(r"^v(\d+)$", re.IGNORECASE)
# "v<digits>" as its own "_"/"."/"-"-separated token, so "prev002" isn't a version.
_VERSION_IN_NAME = re.compile(r"^(?P<prefix>(?:.*[_.\-])?)v(?P<num>\d+)(?P<rest>(?:[_.\-].*)?)$", re.IGNORECASE)


@dataclass
class ReferenceInfo:
    namespace: str
    ref_node: str
    raw_path: str
    nested: bool
    rdi_depth: str = "0"  # "0" = a "-r" load line; else the "-rdi" depth
    resolved_path: str | None = None
    rebased: bool = False  # found by re-rooting another machine's path onto this workspace
    version: int | None = None
    latest_version: int | None = None
    latest_path: str | None = None
    status: str = UNVERSIONED


@dataclass
class SceneReport:
    scene_path: str
    references: list[ReferenceInfo] = field(default_factory=list)
    error: str | None = None

    def count(self, status: str) -> int:
        return sum(1 for ref in self.references if ref.status == status)


def _unquote(text: str) -> str:
    return text.replace('\\"', '"').replace("\\\\", "\\")


def _flag_value(statement: str, flag: str) -> str:
    match = re.search(rf"(?:^|\s){re.escape(flag)}\s+\"((?:[^\"\\]|\\.)*)\"", statement)
    return _unquote(match.group(1)) if match else ""


def read_reference_statements(scene_path: str) -> list[ReferenceInfo]:
    """Every `file -r`/`file -rdi` command in the scene's header, in file order."""
    references = []
    statement = None
    with open(scene_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if statement is None:
                if stripped.startswith(_HEADER_END):
                    break
                if not stripped.startswith("file "):
                    continue
                statement = stripped
            else:
                statement += " " + stripped
            if not statement.endswith(";"):
                continue
            tokens = statement.split()
            is_reference = "-r" in tokens or "-rdi" in tokens or "-reference" in tokens
            quoted = _QUOTED.findall(statement)
            if is_reference and quoted:
                depth = tokens[tokens.index("-rdi") + 1] if "-rdi" in tokens else "0"
                references.append(
                    ReferenceInfo(
                        namespace=_flag_value(statement, "-ns"),
                        ref_node=_flag_value(statement, "-rfn"),
                        raw_path=_COPY_NUMBER.sub("", _unquote(quoted[-1])),
                        nested=depth not in ("0", "1"),
                        rdi_depth=depth,
                    )
                )
            statement = None

    # Maya writes each top-level reference twice: a "-rdi 1" declaration
    # (ahead of its nested "-rdi 2+" children) and the "-r" load line. Keep
    # the "-r" one so a reference isn't listed/counted twice.
    loaded_nodes = {ref.ref_node for ref in references if ref.rdi_depth == "0"}
    return [ref for ref in references if not (ref.rdi_depth == "1" and ref.ref_node in loaded_nodes)]


def resolve_reference_path(raw_path: str, scene_path: str, project_root: str | None, workspace_root: str | None):
    """(path on this machine or None, rebased?)."""
    expanded = os.path.expandvars(raw_path)
    path = Path(expanded)
    candidates = []
    if path.is_absolute():
        candidates.append(path)
    else:
        if project_root:
            candidates.append(Path(project_root) / path)
        candidates.append(Path(scene_path).parent / path)
    for candidate in candidates:
        if candidate.is_file():
            return os.path.normpath(str(candidate)), False

    # Another machine's (or an old drive's) absolute path to the same file,
    # e.g. D:/OtherUser/storage/MerderaProject/KafkaProj/publish/x.ma or
    # G:/My Drive/Projects/KafkaProj/publish/x.ma: re-root the longest
    # suffix that exists under this workspace or this repo — same idea as
    # Explorer's breadcrumb _resolve_typed_path. At least folder + file, so a
    # bare common file name can't match by accident.
    bases = [Path(root) for root in (workspace_root, project_root) if root]
    if bases and path.is_absolute():
        parts = path.parts[1:]
        for start in range(len(parts) - 1):
            for base in bases:
                candidate = base.joinpath(*parts[start:])
                if candidate.is_file():
                    return os.path.normpath(str(candidate)), True
    return None, False


class _DirCache:
    """os.listdir per folder, once per scan — many references share folders."""

    def __init__(self) -> None:
        self._entries: dict[str, list[str]] = {}

    def list(self, folder: str) -> list[str]:
        key = os.path.normcase(folder)
        if key not in self._entries:
            try:
                self._entries[key] = os.listdir(folder)
            except OSError:
                self._entries[key] = []
        return self._entries[key]


def _swap_version_in_name(name: str, old: int, new: int) -> str | None:
    match = _VERSION_IN_NAME.match(Path(name).stem)
    if not match or int(match.group("num")) != old:
        return None
    digits = len(match.group("num"))
    letter = Path(name).stem[len(match.group("prefix"))]  # keep "v" vs "V"
    return f"{match.group('prefix')}{letter}{new:0{digits}d}{match.group('rest')}{Path(name).suffix}"


def version_info(path: str, cache: _DirCache) -> tuple[int | None, int | None, str | None]:
    """(version, latest_version, latest_path) — all None if the path follows
    neither convention. latest_path is the newest equivalent file, or the
    newest version folder itself if no file in it clearly matches."""
    p = Path(path)

    folder_match = _VERSION_FOLDER.match(p.parent.name)
    if folder_match:
        version = int(folder_match.group(1))
        base = str(p.parent.parent)
        versions = {}
        for name in cache.list(base):
            match = _VERSION_FOLDER.match(name)
            if match and os.path.isdir(os.path.join(base, name)):
                versions[int(match.group(1))] = name
        latest = max(versions, default=version)
        if latest <= version:
            return version, version, path
        latest_dir = os.path.join(base, versions[latest])
        entries = cache.list(latest_dir)
        for candidate in (p.name, _swap_version_in_name(p.name, version, latest)):
            if candidate and candidate in entries:
                return version, latest, os.path.join(latest_dir, candidate)
        same_type = [e for e in entries if e.lower().endswith(p.suffix.lower())]
        return version, latest, os.path.join(latest_dir, same_type[0]) if len(same_type) == 1 else latest_dir

    name_match = _VERSION_IN_NAME.match(p.stem)
    if name_match:
        version = int(name_match.group("num"))
        sibling = re.compile(
            rf"^{re.escape(name_match.group('prefix'))}v(\d+){re.escape(name_match.group('rest'))}{re.escape(p.suffix)}$",
            re.IGNORECASE,
        )
        best_version, best_name = version, p.name
        for name in cache.list(str(p.parent)):
            match = sibling.match(name)
            if match and int(match.group(1)) > best_version:
                best_version, best_name = int(match.group(1)), name
        return version, best_version, str(p.parent / best_name)

    return None, None, None


def scan_scene(scene_path: str, project_root: str | None = None, workspace_root: str | None = None, cache: _DirCache | None = None) -> SceneReport:
    """Reads one scene's references and classifies each. Never raises —
    an unreadable scene lands in .error."""
    cache = cache or _DirCache()
    report = SceneReport(scene_path=scene_path)
    try:
        report.references = read_reference_statements(scene_path)
    except OSError as exc:
        report.error = str(exc)
        return report

    for ref in report.references:
        ref.resolved_path, ref.rebased = resolve_reference_path(ref.raw_path, scene_path, project_root, workspace_root)
        if ref.resolved_path is None:
            ref.status = MISSING
            continue
        ref.version, ref.latest_version, ref.latest_path = version_info(ref.resolved_path, cache)
        if ref.version is None:
            ref.status = UNVERSIONED
        elif ref.latest_version is not None and ref.latest_version > ref.version:
            ref.status = OUTDATED
        else:
            ref.status = LATEST
    return report


def new_dir_cache() -> _DirCache:
    return _DirCache()


# ------------------------------------------------------------
# UPDATING REFERENCES (rewriting the .ma text)
# ------------------------------------------------------------
_FILE_TYPES = {".ma": "mayaAscii", ".mb": "mayaBinary", ".fbx": "FBX"}
_QUOTED_SPAN = re.compile(r'"(?:[^"\\]|\\.)*"')
_TYPE_FLAG = re.compile(r'(-typ\s+)"(?:[^"\\]|\\.)*"')


def can_update(ref: ReferenceInfo) -> bool:
    """An outdated top-level reference whose latest version is a real file
    (not just the newest vNNN folder). Nested references come from the
    referenced file itself — update that file instead."""
    return ref.status == OUTDATED and not ref.nested and bool(ref.latest_path) and os.path.isfile(ref.latest_path)


def path_for_scene(new_path: str, project_root: str | None) -> str:
    """How a reference path is written into a scene: relative to the repo
    root (the Maya project UkoreHub sets) when it's inside it, so the scene
    works on every machine; absolute otherwise. Always forward slashes."""
    path = os.path.normpath(new_path)
    if project_root:
        try:
            relative = os.path.relpath(path, project_root)
            if not relative.startswith(".."):
                return relative.replace("\\", "/")
        except ValueError:
            pass
    return path.replace("\\", "/")


def _mel_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def update_references(scene_path: str, new_paths: dict[str, str], project_root: str | None = None) -> int:
    """Points each reference node named in `new_paths` ({ref_node: new
    file}) at its new file by rewriting the scene's own `file -r` /
    `file -rdi 1` lines — what Replace Reference + save would do, minus
    opening Maya. Everything else in the file stays byte-for-byte the same,
    except the stale "-rdi 2+" lines under a replaced reference (its old
    nested references), which are dropped: Maya re-reads nested references
    from the new file when the scene is opened. Returns how many reference
    nodes were changed; writes nothing if that's 0. Written via a temp file
    + os.replace, so a failure never leaves a half-written scene."""
    with open(scene_path, "rb") as f:
        data = f.read()
    # latin-1 maps every byte to one char, so untouched bytes round-trip
    # exactly whatever the file's real encoding is.
    text = data.decode("latin-1")
    header_end = text.find("\ncreateNode ")
    header_end = len(text) if header_end < 0 else header_end + 1
    lines = text[:header_end].splitlines(keepends=True)

    out: list[str] = []
    changed: set[str] = set()
    dropping_children_of = None
    index = 0
    while index < len(lines):
        if not lines[index].strip().startswith("file "):
            out.append(lines[index])
            index += 1
            continue
        end = index
        while not lines[end].rstrip().endswith(";") and end + 1 < len(lines):
            end += 1
        raw = "".join(lines[index:end + 1])
        index = end + 1

        tokens = raw.split()
        depth = tokens[tokens.index("-rdi") + 1] if "-rdi" in tokens else ("0" if "-r" in tokens else None)
        ref_node = _flag_value(raw, "-rfn")
        if depth == "1" or depth == "0":
            dropping_children_of = None
        if depth not in (None, "0", "1") and dropping_children_of is not None:
            continue
        if depth in ("0", "1") and ref_node in new_paths:
            spans = list(_QUOTED_SPAN.finditer(raw))
            old = _unquote(spans[-1].group(0)[1:-1])
            copy_number = _COPY_NUMBER.search(old)
            new = new_paths[ref_node].encode("utf-8").decode("latin-1") + (copy_number.group(0) if copy_number else "")
            raw = raw[: spans[-1].start()] + _mel_quote(new) + raw[spans[-1].end():]
            new_type = _FILE_TYPES.get(Path(new_paths[ref_node]).suffix.lower())
            if new_type:
                raw = _TYPE_FLAG.sub(lambda m: m.group(1) + _mel_quote(new_type), raw, count=1)
            changed.add(ref_node)
            if depth == "1":
                dropping_children_of = ref_node
        out.append(raw)

    if not changed:
        return 0
    new_data = ("".join(out) + text[header_end:]).encode("latin-1")
    tmp_path = scene_path + ".ukore_tmp"
    with open(tmp_path, "wb") as f:
        f.write(new_data)
    os.replace(tmp_path, scene_path)
    return len(changed)
