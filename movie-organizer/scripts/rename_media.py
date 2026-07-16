#!/usr/bin/env python3
"""Rename a confirmed movie, recognized sidecars, and supported artwork safely."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable
import unicodedata


SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa"}
LANGUAGE_TAGS = {
    "en": "en", "eng": "en", "english": "en",
    "zh": "zh", "zho": "zh", "chi": "zh", "chs": "zh-CN",
    "zh-cn": "zh-CN", "zh-hans": "zh-CN", "sc": "zh-CN",
    "cht": "zh-TW", "zh-tw": "zh-TW", "zh-hant": "zh-TW", "tc": "zh-TW",
}


class OrganizerError(RuntimeError):
    pass


def sanitize_component(value: Any, fallback: str = "Movie") -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = re.sub(r"[\x00-\x1f\x7f/:*?\"<>|\\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = re.sub(r"\.{2,}", ".", text)
    if text in {"", ".", ".."}:
        text = fallback
    return text[:240].rstrip(" .")


def _same_title(left: str, right: str) -> bool:
    def key(value: str) -> str:
        return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())
    return bool(left and right and key(left) == key(right))


def base_movie_name(movie: dict[str, Any]) -> str:
    title = sanitize_component(movie.get("title"))
    original = sanitize_component(movie.get("original_title"), "") if movie.get("original_title") else ""
    names = title if not original or _same_title(title, original) else f"{title} {original}"
    return sanitize_component(f"{names} ({movie.get('year')})")


def movie_filename(movie: dict[str, Any], extension: str) -> str:
    base = base_movie_name(movie)
    fields = [
        movie.get("edition"), movie.get("resolution"), movie.get("video_codec"),
        movie.get("media_source"), movie.get("hdr"),
    ]
    technical = " ".join(sanitize_component(x, "") for x in fields if str(x or "").strip())
    stem = f"{base} - {technical}" if technical else base
    extension = extension if extension.startswith(".") else f".{extension}"
    return f"{sanitize_component(stem)}{extension.lower()}"


def subtitle_tags(path: Path, video_stem: str) -> tuple[list[str] | None, str | None]:
    """Return canonical tags, or (None, reason) when the sidecar is uncertain."""
    if path.suffix.lower() not in SUBTITLE_EXTENSIONS:
        return None, "unsupported subtitle extension"
    stem = path.stem
    lower = stem.casefold()
    video_key = video_stem.casefold()
    if lower.startswith(video_key):
        suffix = stem[len(video_stem):]
    elif re.match(r"^(?:movie|subtitle|sub)[._ -]", lower):
        suffix = re.sub(r"^(?:movie|subtitle|sub)", "", stem, flags=re.I)
    else:
        suffix = stem
    tokens = [x for x in re.split(r"[._ -]+", suffix.strip("._ -")) if x]
    folded = [x.casefold() for x in tokens]
    language = None
    for index, token in enumerate(folded):
        pair = f"{token}-{folded[index + 1]}" if index + 1 < len(folded) else ""
        if pair in LANGUAGE_TAGS:
            language = LANGUAGE_TAGS[pair]
            break
        if token in LANGUAGE_TAGS:
            language = LANGUAGE_TAGS[token]
            break
    if not language:
        return None, "language tag could not be determined"
    tags = [language]
    if any(token in {"forced", "foreign", "foreignparts"} for token in folded):
        tags.append("forced")
    if any(token in {"sdh", "hi", "hearingimpaired"} for token in folded):
        tags.append("sdh")
    return tags, None


def _recognized_subtitles(folder: Path, old_video: Path, loose: bool) -> tuple[list[tuple[Path, list[str]]], list[dict[str, str]]]:
    recognized, uncertain = [], []
    candidates = sorted(folder.iterdir()) if folder.exists() else []
    for path in candidates:
        if not path.is_file() or path.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        tags, reason = subtitle_tags(path, old_video.stem)
        basename_match = path.stem.casefold().startswith(old_video.stem.casefold())
        if loose and not basename_match:
            uncertain.append({"path": os.fspath(path), "reason": "loose sidecar basename does not clearly match the video"})
        elif tags is None:
            uncertain.append({"path": os.fspath(path), "reason": reason or "uncertain subtitle"})
        else:
            recognized.append((path, tags))
    return recognized, uncertain


def _artwork_renames(folder: Path, target_folder: Path) -> list[tuple[Path, Path]]:
    aliases = {
        "poster": "poster", "movie-poster": "poster", "folder": "poster",
        "fanart": "fanart", "backdrop": "fanart", "movie-fanart": "fanart",
        "clearlogo": "clearlogo", "logo": "clearlogo",
    }
    renames = []
    if not folder.exists():
        return renames
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        canonical = aliases.get(path.stem.casefold())
        if canonical:
            renames.append((path, target_folder / f"{canonical}{path.suffix.lower()}"))
    extra = folder / "extrafanart"
    if extra.is_dir():
        for path in sorted(extra.iterdir()):
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                renames.append((path, target_folder / "extrafanart" / path.name))
    return renames


def _collision_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path))).casefold()


def check_collisions(renames: Iterable[tuple[Path, Path]]) -> None:
    pairs = [(source, target) for source, target in renames if source != target]
    source_keys = {_collision_key(source) for source, _ in pairs}
    seen: dict[str, Path] = {}
    for source, target in pairs:
        key = _collision_key(target)
        if key in seen and _collision_key(seen[key]) != _collision_key(source):
            raise OrganizerError(f"multiple files would target {target}")
        seen[key] = source
        if target.exists() and key not in source_keys:
            raise OrganizerError(f"rename target already exists: {target}")


def apply_renames(renames: Iterable[tuple[Path, Path]]) -> None:
    """Use a two-phase rename so cycles/case-only changes cannot overwrite data."""
    pairs = [(source, target) for source, target in renames if source != target and source.exists()]
    check_collisions(pairs)
    staged: list[tuple[Path, Path]] = []
    try:
        for source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{source.name}.", suffix=".rename", dir=source.parent)
            os.close(fd)
            os.unlink(temp_name)
            temporary = Path(temp_name)
            source.rename(temporary)
            staged.append((temporary, target))
        for temporary, target in staged:
            temporary.rename(target)
    except BaseException:
        for temporary, target in reversed(staged):
            if temporary.exists():
                original = next((s for s, t in pairs if t == target), None)
                if original and not original.exists():
                    temporary.rename(original)
        raise


def _load_state() -> dict[str, Any]:
    try:
        state = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise OrganizerError(f"stdin is not valid shared JSON state: {exc}") from exc
    if state.get("schema_version") != 1 or not isinstance(state.get("movie"), dict):
        raise OrganizerError("stdin does not contain supported movie-organizer state")
    return state


def rename_state(state: dict[str, Any], mode: str) -> dict[str, Any]:
    old_folder = Path(state["movie_folder"])
    old_video = Path(state["video_file"])
    loose = bool(state.get("source", {}).get("loose"))
    final_folder_name = base_movie_name(state["movie"])
    target_folder = old_folder / final_folder_name if loose else old_folder.parent / final_folder_name
    target_video = target_folder / movie_filename(state["movie"], old_video.suffix)
    scan_folder = old_folder
    recognized, uncertain = _recognized_subtitles(scan_folder, old_video, loose)

    renames: list[tuple[Path, Path]] = [(old_video, target_video)]
    for path, tags in recognized:
        tag_text = ".".join(tags)
        renames.append((path, target_folder / f"{target_video.stem}.{tag_text}{path.suffix.lower()}"))
    renames.extend(_artwork_renames(scan_folder, target_folder))
    nfo_source = Path(state.get("nfo", {}).get("path", old_folder / "movie.nfo"))
    renames.append((nfo_source, target_folder / "movie.nfo"))
    check_collisions(renames)
    if not loose and target_folder != old_folder and target_folder.exists():
        raise OrganizerError(f"destination folder already exists: {target_folder}")

    if mode == "apply":
        if loose:
            raise OrganizerError("loose movies must be staged in a sibling work folder before apply")
        file_renames = [(source, old_folder / target.relative_to(target_folder)) for source, target in renames]
        apply_renames(file_renames)
        if target_folder != old_folder:
            old_folder.rename(target_folder)

    # Update planned paths consistently in dry-run and apply.
    path_map = {_collision_key(source): target for source, target in renames}
    for item in state.get("artwork", {}).get("planned", []):
        old = Path(item["path"])
        item["path"] = os.fspath(path_map.get(_collision_key(old), target_folder / old.relative_to(old_folder)))
    state["movie_folder"] = os.fspath(target_folder)
    state["video_file"] = os.fspath(target_video)
    state.setdefault("nfo", {})["path"] = os.fspath(target_folder / "movie.nfo")
    state["mode"] = mode
    state.setdefault("subtitles", {})["external_existing"] = [
        {"path": os.fspath(target), "original_path": os.fspath(source), "language": tags[0], "forced": "forced" in tags, "sdh": "sdh" in tags, "commentary": False, "source": "external"}
        for (source, tags) in recognized
        for target in [target_folder / f"{target_video.stem}.{'.'.join(tags)}{source.suffix.lower()}"]
    ]
    state["subtitles"]["uncertain"] = uncertain
    operations = [
        {"action": "rename", "source": os.fspath(source), "target": os.fspath(target), "status": "applied" if mode == "apply" else "planned"}
        for source, target in renames if source != target
    ]
    if target_folder != old_folder and not loose:
        operations.append({"action": "rename-folder", "source": os.fspath(old_folder), "target": os.fspath(target_folder), "status": "applied" if mode == "apply" else "planned"})
    state.setdefault("operations", []).extend(operations)
    if mode == "apply":
        state.setdefault("completed_steps", []).append("rename_media")
    proposal = state.setdefault("proposal", {})
    proposal.update({
        "folder_name": final_folder_name,
        "movie_filename": target_video.name,
        "movie_folder": os.fspath(target_folder),
        "uncertain_subtitles": uncertain,
    })
    return state


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "apply"), required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        json.dump(rename_state(_load_state(), args.mode), sys.stdout, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (OrganizerError, OSError, ValueError) as exc:
        print(f"rename_media: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
