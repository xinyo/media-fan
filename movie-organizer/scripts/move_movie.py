#!/usr/bin/env python3
"""Verify a completed movie and move it to its language library safely."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Callable
import xml.etree.ElementTree as ET


MOVIE_OUTPUT = os.environ.get("MOVIE_OUTPUT")
if not MOVIE_OUTPUT:
    raise ValueError("MOVIE_OUTPUT environment variable is required")
DEFAULT_DESTINATION = Path(MOVIE_OUTPUT)
SOUTHEAST_ASIAN = {"th", "vi", "id", "ms", "tl", "fil", "km", "my", "lo"}
CHINESE = {"zh", "zh-cn", "zh-tw", "cmn", "yue"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa"}


class OrganizerError(RuntimeError):
    pass


def canonical_language(value: Any) -> str:
    text = str(value or "").strip().replace("_", "-").casefold()
    aliases = {
        "eng": "en", "jpn": "ja", "kor": "ko", "chi": "zh", "zho": "zh",
        "chs": "zh-cn", "zh-hans": "zh-cn", "cht": "zh-tw", "zh-hant": "zh-tw",
        "tha": "th", "vie": "vi", "ind": "id", "msa": "ms", "may": "ms",
        "tgl": "tl", "fil": "tl", "khm": "km", "bur": "my", "mya": "my", "lao": "lo",
    }
    return aliases.get(text, text.split("-", 1)[0] if text else "")


def route_relative(movie: dict[str, Any]) -> Path | None:
    countries = {str(x).upper() for x in movie.get("production_countries", [])}
    language = canonical_language(movie.get("primary_audio_language"))
    if "HK" in countries:
        return Path("hk")
    if "TW" in countries or language in CHINESE:
        return Path("cn")
    if language == "en":
        return Path("en")
    if language == "ja":
        return Path("asia/Japan")
    if language == "ko":
        return Path("asia/Korea")
    if language in SOUTHEAST_ASIAN:
        return Path("asia/South East Asia")
    if language:
        return Path("other")
    return None


def _subtitle_language(path: Path) -> tuple[str, bool, bool]:
    tokens = [x.casefold() for x in re.split(r"[._ -]+", path.stem) if x]
    language = ""
    for index, token in enumerate(tokens):
        pair = f"{token}-{tokens[index + 1]}" if index + 1 < len(tokens) else ""
        if pair in {"zh-cn", "zh-hans"}:
            language = "zh-CN"; break
        if pair in {"zh-tw", "zh-hant"}:
            language = "zh-TW"; break
        if token in {"en", "eng", "english"}:
            language = "en"; break
        if token in {"chs", "sc"}:
            language = "zh-CN"; break
        if token in {"cht", "tc"}:
            language = "zh-TW"; break
    forced = any(x in {"forced", "foreign", "foreignparts"} for x in tokens)
    commentary = any("comment" in x for x in tokens)
    return language, forced, commentary


def _validate_nfo(path: Path, tmdb_id: Any) -> None:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise OrganizerError(f"movie.nfo is missing or invalid: {exc}") from exc
    if root.tag != "movie":
        raise OrganizerError("movie.nfo root element is not <movie>")
    ids = [node.text for node in root.findall("uniqueid") if node.get("type") == "tmdb"]
    if str(tmdb_id) not in ids:
        raise OrganizerError("movie.nfo does not contain the confirmed TMDB ID")


def verify_complete(state: dict[str, Any], mode: str) -> None:
    folder, video = Path(state["movie_folder"]), Path(state["video_file"])
    required = list(state.get("subtitles", {}).get("required", []))
    if not required:
        raise OrganizerError("primary dialogue language is unknown; review is required before moving")
    if mode == "dry-run":
        satisfied = set(state.get("subtitles", {}).get("satisfied_after_apply", []))
        missing = [x for x in required if x not in satisfied]
        if missing:
            raise OrganizerError("dry-run still lacks required subtitles: " + ", ".join(missing))
        return
    if not folder.is_dir():
        raise OrganizerError(f"movie folder does not exist: {folder}")
    if not video.is_file():
        raise OrganizerError(f"main movie file does not exist: {video}")
    try:
        video.relative_to(folder)
    except ValueError as exc:
        raise OrganizerError("main movie file is outside the movie folder") from exc
    _validate_nfo(folder / "movie.nfo", state.get("movie", {}).get("tmdb_id"))
    present = set()
    for path in folder.iterdir():
        if path.is_file() and path.suffix.casefold() in SUBTITLE_EXTENSIONS:
            language, forced, commentary = _subtitle_language(path)
            if language and not forced and not commentary:
                present.add(language)
    missing = [language for language in required if language not in present]
    if missing:
        raise OrganizerError("required full subtitles are missing: " + ", ".join(missing))


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_copy(source: Path, copied: Path) -> None:
    source_files = {p.relative_to(source): p for p in source.rglob("*") if p.is_file()}
    copied_files = {p.relative_to(copied): p for p in copied.rglob("*") if p.is_file()}
    if source_files.keys() != copied_files.keys():
        raise OrganizerError("cross-filesystem copy has a different file set")
    for relative, source_path in source_files.items():
        target_path = copied_files[relative]
        if source_path.stat().st_size != target_path.stat().st_size:
            raise OrganizerError(f"cross-filesystem copy size mismatch: {relative}")
        if _file_hash(source_path) != _file_hash(target_path):
            raise OrganizerError(f"cross-filesystem copy checksum mismatch: {relative}")


def safe_move(
    source: Path,
    destination: Path,
    rename: Callable[[Path, Path], None] = os.rename,
    copytree: Callable[..., Any] = shutil.copytree,
) -> None:
    if destination.exists():
        raise OrganizerError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        rename(source, destination)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    temporary = destination.parent / f".{destination.name}.movie-organizer-{os.getpid()}"
    if temporary.exists():
        raise OrganizerError(f"temporary cross-filesystem destination already exists: {temporary}")
    try:
        copytree(source, temporary, copy_function=shutil.copy2)
        verify_copy(source, temporary)
        os.rename(temporary, destination)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise
    try:
        shutil.rmtree(source)
    except OSError as exc:
        raise OrganizerError(
            f"copy completed at {destination}, but source cleanup failed; keep both and remove {source} only after manual verification: {exc}"
        ) from exc


def move_state(state: dict[str, Any], mode: str, destination_root: Path = DEFAULT_DESTINATION) -> dict[str, Any]:
    if state.get("schema_version") != 1:
        raise OrganizerError("unsupported shared state schema")
    route = route_relative(state.get("movie", {}))
    if route is None:
        state.setdefault("proposal", {})["destination"] = None
        state.setdefault("warnings", []).append("Destination requires review because primary dialogue audio is unknown")
        if mode == "apply":
            raise OrganizerError("primary dialogue audio is unknown; review is required before moving")
        return state
    source = Path(state["movie_folder"])
    destination = destination_root / route / source.name
    if destination.exists():
        raise OrganizerError(f"destination collision: {destination}")
    verify_complete(state, mode)
    if mode == "apply":
        safe_move(source, destination)
        relative_video = Path(state["video_file"]).relative_to(source)
        state["movie_folder"] = os.fspath(destination)
        state["video_file"] = os.fspath(destination / relative_video)
        state.setdefault("nfo", {})["path"] = os.fspath(destination / "movie.nfo")
        for item in state.get("artwork", {}).get("planned", []):
            old = Path(item["path"])
            try: item["path"] = os.fspath(destination / old.relative_to(source))
            except ValueError: pass
        for key in ("external_existing", "downloaded"):
            for item in state.get("subtitles", {}).get(key, []):
                old = Path(str(item.get("path") or ""))
                try: item["path"] = os.fspath(destination / old.relative_to(source))
                except ValueError: pass
        state.setdefault("completed_steps", []).append("move_movie")
    state["mode"] = mode
    state.setdefault("operations", []).append({"action": "move", "source": os.fspath(source), "target": os.fspath(destination), "status": "applied" if mode == "apply" else "planned"})
    state.setdefault("proposal", {})["destination"] = os.fspath(destination)
    state["final_destination"] = os.fspath(destination) if mode == "apply" else None
    return state


def _load_state() -> dict[str, Any]:
    try: return json.load(sys.stdin)
    except json.JSONDecodeError as exc: raise OrganizerError(f"stdin is not valid shared JSON state: {exc}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "apply"), required=True)
    parser.add_argument("--destination-root", default=os.fspath(DEFAULT_DESTINATION))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        json.dump(move_state(_load_state(), args.mode, Path(args.destination_root)), sys.stdout, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (OrganizerError, OSError, ValueError) as exc:
        print(f"move_movie: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
