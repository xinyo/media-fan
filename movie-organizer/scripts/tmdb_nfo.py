#!/usr/bin/env python3
"""Fetch confirmed TMDB metadata, inspect a movie, and create Kodi NFO/artwork."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p/original"
SCHEMA_VERSION = 1
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm"}
LANGUAGE_ALIASES = {
    "chi": "zh", "zho": "zh", "cmn": "zh", "yue": "yue", "eng": "en",
    "jpn": "ja", "kor": "ko", "fra": "fr", "fre": "fr", "deu": "de",
    "ger": "de", "spa": "es", "por": "pt", "tha": "th", "vie": "vi",
    "ind": "id", "msa": "ms", "may": "ms", "fil": "tl", "tgl": "tl",
    "und": "", "unknown": "",
}


class OrganizerError(RuntimeError):
    """A safe, user-actionable organizer failure."""


def diagnostic(message: str) -> None:
    print(message, file=sys.stderr)


def normalize_language(value: Any) -> str:
    text = str(value or "").strip().replace("_", "-").lower()
    if not text:
        return ""
    text = LANGUAGE_ALIASES.get(text, text)
    if text.startswith("zh-hans") or text in {"chs", "zh-cn", "zh-sg"}:
        return "zh-CN"
    if text.startswith("zh-hant") or text in {"cht", "zh-tw", "zh-hk"}:
        return "zh-TW"
    return text.split("-", 1)[0]


def _http_json(url: str, token: str, timeout: int = 30) -> dict[str, Any]:
    separator = "&" if "?" in url else "?"
    url = f"{url}{separator}api_key={token}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "movie-organizer-skill/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace")
        raise OrganizerError(f"TMDB request failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OrganizerError(f"TMDB request failed: {exc}") from exc


def fetch_tmdb_movie(
    tmdb_id: int,
    token: str,
    request_json: Callable[[str, str], dict[str, Any]] = _http_json,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return zh-CN details, en-US details, and unfiltered image data."""
    append = "credits,release_dates,videos,keywords"
    common = {"append_to_response": append}
    zh_url = f"{TMDB_API}/movie/{tmdb_id}?" + urllib.parse.urlencode({**common, "language": "zh-CN"})
    en_url = f"{TMDB_API}/movie/{tmdb_id}?" + urllib.parse.urlencode({**common, "language": "en-US"})
    image_url = f"{TMDB_API}/movie/{tmdb_id}/images?" + urllib.parse.urlencode(
        {"include_image_language": "zh,en,null"}
    )
    zh = request_json(zh_url, token)
    en = request_json(en_url, token)
    images = request_json(image_url, token)
    if int(zh.get("id") or 0) != tmdb_id or int(en.get("id") or 0) != tmdb_id:
        raise OrganizerError("TMDB returned a movie whose ID does not match the confirmed ID")
    return zh, en, images


def _pick_text(primary: dict[str, Any], fallback: dict[str, Any], key: str) -> str:
    return str(primary.get(key) or fallback.get(key) or "").strip()


def _names(items: Iterable[dict[str, Any]]) -> list[str]:
    return [str(item.get("name", "")).strip() for item in items if item.get("name")]


def _us_certification(details: dict[str, Any]) -> str:
    results = details.get("release_dates", {}).get("results", [])
    us = next((x for x in results if x.get("iso_3166_1") == "US"), {})
    releases = us.get("release_dates", [])
    ordered = sorted(releases, key=lambda x: (x.get("type") != 3, x.get("type") or 99))
    return next((str(x.get("certification", "")).strip() for x in ordered if x.get("certification")), "")


def _trailer(details: dict[str, Any], fallback: dict[str, Any]) -> str:
    videos = list(details.get("videos", {}).get("results", [])) + list(
        fallback.get("videos", {}).get("results", [])
    )
    candidates = [
        v for v in videos
        if v.get("site") == "YouTube" and str(v.get("type", "")).lower() == "trailer"
    ]
    candidates.sort(key=lambda v: (not bool(v.get("official")), v.get("published_at", "")))
    key = candidates[0].get("key") if candidates else ""
    return f"https://www.youtube.com/watch?v={key}" if key else ""


def _crew(details: dict[str, Any], fallback: dict[str, Any]) -> dict[str, list[str]]:
    rows = details.get("credits", {}).get("crew") or fallback.get("credits", {}).get("crew", [])
    mapping = {"directors": [], "writers": [], "producers": [], "composers": []}
    for row in rows:
        job = str(row.get("job", "")).lower()
        department = str(row.get("department", "")).lower()
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        if job == "director":
            key = "directors"
        elif job in {"writer", "screenplay", "story", "teleplay", "adaptation"} or department == "writing":
            key = "writers"
        elif "producer" in job:
            key = "producers"
        elif job in {"original music composer", "composer"}:
            key = "composers"
        else:
            continue
        if name not in mapping[key]:
            mapping[key].append(name)
    return mapping


def build_movie_metadata(
    tmdb_id: int,
    zh: dict[str, Any],
    en: dict[str, Any],
) -> dict[str, Any]:
    release_date = str(zh.get("release_date") or en.get("release_date") or "")
    actors_source = zh.get("credits", {}).get("cast") or en.get("credits", {}).get("cast", [])
    actors = []
    for person in actors_source[:20]:
        name = str(person.get("name", "")).strip()
        if name:
            actors.append({
                "name": name,
                "role": str(person.get("character", "")).strip(),
                "order": int(person.get("order") or 0),
                "thumb": f"{TMDB_IMAGE}{person['profile_path']}" if person.get("profile_path") else "",
            })
    keyword_rows = zh.get("keywords", {}).get("keywords") or en.get("keywords", {}).get("keywords", [])
    collection = zh.get("belongs_to_collection") or en.get("belongs_to_collection") or {}
    movie = {
        "tmdb_id": tmdb_id,
        "imdb_id": str(zh.get("imdb_id") or en.get("imdb_id") or ""),
        "title": _pick_text(zh, en, "title"),
        "original_title": _pick_text(zh, en, "original_title"),
        "english_title": _pick_text(en, zh, "title"),
        "tagline": _pick_text(zh, en, "tagline"),
        "plot": _pick_text(zh, en, "overview"),
        "release_date": release_date,
        "year": int(release_date[:4]) if re.match(r"^\d{4}", release_date) else None,
        "rating": float(zh.get("vote_average") or en.get("vote_average") or 0),
        "votes": int(zh.get("vote_count") or en.get("vote_count") or 0),
        "runtime": int(zh.get("runtime") or en.get("runtime") or 0),
        "certification": _us_certification(zh) or _us_certification(en),
        "genres": _names(zh.get("genres") or en.get("genres", [])),
        "original_language": normalize_language(zh.get("original_language") or en.get("original_language")),
        "production_countries": [x.get("iso_3166_1") for x in (zh.get("production_countries") or en.get("production_countries", [])) if x.get("iso_3166_1")],
        "countries": _names(zh.get("production_countries") or en.get("production_countries", [])),
        "studios": _names(zh.get("production_companies") or en.get("production_companies", [])),
        "tags": _names(keyword_rows),
        "set": {"name": str(collection.get("name", "")), "tmdb_id": collection.get("id")} if collection else {},
        "trailer": _trailer(zh, en),
        "actors": actors,
        **_crew(zh, en),
    }
    if not movie["title"] or not movie["year"]:
        raise OrganizerError("TMDB metadata is missing the title or release year")
    return movie


def inspect_media(video_file: Path, ffprobe_json: Path | None = None) -> dict[str, Any]:
    if ffprobe_json:
        try:
            data = json.loads(ffprobe_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OrganizerError(f"Cannot read ffprobe fixture: {exc}") from exc
    else:
        command = [
            "ffprobe", "-v", "error", "-show_format", "-show_streams",
            "-of", "json", os.fspath(video_file),
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise OrganizerError("ffprobe is required but was not found in PATH") from exc
        except subprocess.CalledProcessError as exc:
            raise OrganizerError(f"ffprobe failed: {exc.stderr.strip() or 'unknown error'}") from exc
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise OrganizerError("ffprobe returned invalid JSON") from exc

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise OrganizerError("ffprobe did not find a video stream")
    width, height = int(video_stream.get("width") or 0), int(video_stream.get("height") or 0)
    resolution = "4K" if width >= 3000 or height >= 1600 else "1080p" if width >= 1900 or height >= 1000 else "720p" if width >= 1200 or height >= 700 else f"{height}p" if height else ""
    codec_raw = str(video_stream.get("codec_name") or video_stream.get("codec_long_name") or "").lower()
    codec = "h265" if codec_raw in {"hevc", "h265"} else "h264" if codec_raw in {"avc", "h264"} else "av1" if codec_raw == "av1" else codec_raw
    combined = " ".join(str(x) for x in [
        video_stream.get("color_transfer"), video_stream.get("color_primaries"),
        video_stream.get("codec_tag_string"), video_stream.get("profile"),
        *video_stream.get("side_data_list", []), video_stream.get("tags", {}),
    ]).lower()
    if "dovi" in combined or "dolby vision" in combined:
        hdr = "dv"
    elif "smpte2094" in combined or "hdr10+" in combined or "hdr10 plus" in combined:
        hdr = "hdr10plus"
    elif "arib-std-b67" in combined or "hlg" in combined:
        hdr = "hlg"
    elif "smpte2084" in combined or "pq" in combined or "hdr10" in combined:
        hdr = "hdr10"
    else:
        hdr = ""

    audio = []
    for stream in streams:
        if stream.get("codec_type") != "audio":
            continue
        tags = stream.get("tags", {})
        title = str(tags.get("title") or tags.get("handler_name") or "")
        commentary = bool(re.search(r"commentary|director.?s? comments?|comment", title, re.I))
        audio.append({
            "index": stream.get("index"),
            "language": normalize_language(tags.get("language")),
            "codec": str(stream.get("codec_name") or "").lower(),
            "channels": int(stream.get("channels") or 0),
            "title": title,
            "default": bool(stream.get("disposition", {}).get("default")),
            "commentary": commentary,
        })
    dialogue = [x for x in audio if not x["commentary"]]
    primary = next((x for x in dialogue if x["default"] and x["language"]), None)
    primary = primary or next((x for x in dialogue if x["language"]), None)

    subtitles = []
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue
        tags = stream.get("tags", {})
        title = str(tags.get("title") or "")
        disposition = stream.get("disposition", {})
        subtitle_language = normalize_language(tags.get("language"))
        if subtitle_language == "zh":
            if re.search(r"simplified|简体|簡體|\bchs\b|hans", title, re.I):
                subtitle_language = "zh-CN"
            elif re.search(r"traditional|繁體|繁体|\bcht\b|hant", title, re.I):
                subtitle_language = "zh-TW"
        subtitles.append({
            "index": stream.get("index"),
            "language": subtitle_language,
            "codec": str(stream.get("codec_name") or "").lower(),
            "title": title,
            "forced": bool(disposition.get("forced") or re.search(r"\bforced\b", title, re.I)),
            "sdh": bool(disposition.get("hearing_impaired") or re.search(r"\b(?:sdh|hi)\b", title, re.I)),
            "commentary": bool(re.search(r"commentary|comment", title, re.I)),
        })
    format_data = data.get("format", {})
    duration = float(format_data.get("duration") or video_stream.get("duration") or 0)
    return {
        "resolution": resolution,
        "width": width,
        "height": height,
        "video_codec": codec,
        "hdr": hdr,
        "runtime_seconds": round(duration),
        "audio_streams": audio,
        "primary_audio_language": primary["language"] if primary else "",
        "primary_audio_stream": primary,
        "subtitle_streams": subtitles,
        "format_name": str(format_data.get("format_name") or ""),
        "format_tags": format_data.get("tags", {}),
    }


def infer_release_hints(path: Path) -> dict[str, str]:
    text = path.stem
    edition_match = re.search(r"\b(final cut|director'?s cut|extended(?: cut)?|theatrical cut|remastered|unrated)\b", text, re.I)
    source_match = re.search(r"\b(uhd[ ._-]?blu-?ray|blu-?ray|bdrip|web[ ._-]?dl|webrip|hdtv|dvd(?:rip)?)\b", text, re.I)
    source = (source_match.group(1).lower() if source_match else "").replace("-", "").replace("_", "").replace(" ", "")
    source = "bluray" if source in {"bluray", "bdrip", "uhdbluray"} else "web-dl" if source == "webdl" else source
    return {"edition": edition_match.group(1).title() if edition_match else "", "media_source": source}


def _image_score(row: dict[str, Any]) -> tuple[float, int]:
    return (float(row.get("vote_average") or 0), int(row.get("vote_count") or 0))


def _rank_posters(rows: list[dict[str, Any]], original_language: str) -> list[dict[str, Any]]:
    def priority(row: dict[str, Any]) -> tuple[int, float, int]:
        lang = normalize_language(row.get("iso_639_1"))
        bucket = 0 if lang == original_language else 1 if lang == "zh-CN" or row.get("iso_639_1") == "zh" else 2 if not lang else 3
        score = _image_score(row)
        return (bucket, -score[0], -score[1])
    return sorted(rows, key=priority)


def plan_artwork(movie: dict[str, Any], images: dict[str, Any], folder: Path) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    posters = _rank_posters(list(images.get("posters", [])), movie.get("original_language", ""))
    backdrops = sorted(images.get("backdrops", []), key=_image_score, reverse=True)
    logos = _rank_posters(list(images.get("logos", [])), movie.get("original_language", ""))

    def add(kind: str, row: dict[str, Any], relative: str) -> None:
        source = str(row.get("file_path") or "")
        if not source:
            return
        ext = Path(source).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            ext = ".jpg"
        target = folder / f"{relative}{ext}"
        planned.append({
            "kind": kind, "url": f"{TMDB_IMAGE}{source}", "path": os.fspath(target),
            "language": row.get("iso_639_1"), "width": row.get("width"), "height": row.get("height"),
        })

    if posters:
        add("poster", posters[0], "poster")
    for index, row in enumerate(backdrops[:5]):
        add("fanart" if index == 0 else "extrafanart", row, "fanart" if index == 0 else f"extrafanart/fanart{index}")
    if logos:
        add("clearlogo", logos[0], "clearlogo")
    return planned


def image_dimensions(data: bytes) -> tuple[str, int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return "png", width, height
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return "gif", width, height
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return "webp", width, height
        raise OrganizerError("unsupported WebP image header")
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            if marker in {0xD8, 0xD9}:
                offset += 2
                continue
            size = int.from_bytes(data[offset + 2:offset + 4], "big")
            if marker in set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0)):
                height = int.from_bytes(data[offset + 5:offset + 7], "big")
                width = int.from_bytes(data[offset + 7:offset + 9], "big")
                return "jpeg", width, height
            if size < 2:
                break
            offset += 2 + size
    raise OrganizerError("downloaded artwork is not a supported image")


def _download_bytes(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "movie-organizer-skill/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(40 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise OrganizerError(f"artwork download failed: {exc}") from exc


def atomic_replace_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def install_artwork(
    planned: list[dict[str, Any]],
    downloader: Callable[[str], bytes] = _download_bytes,
) -> tuple[list[str], list[str]]:
    installed, warnings = [], []
    for item in planned:
        target = Path(item["path"])
        try:
            data = downloader(item["url"])
            if len(data) > 40 * 1024 * 1024:
                raise OrganizerError("image exceeds 40 MiB limit")
            kind, width, height = image_dimensions(data)
            if width < 100 or height < 100:
                raise OrganizerError(f"image dimensions are implausible ({width}x{height})")
            expected = target.suffix.lower()
            if expected in {".jpg", ".jpeg"} and kind != "jpeg" or expected == ".png" and kind != "png" or expected == ".webp" and kind != "webp":
                raise OrganizerError(f"image signature does not match {expected}")
            atomic_replace_bytes(target, data)
            # A successful replacement retires alternate extensions for the
            # same recognized Kodi artwork name. Never do this before the new
            # file has been validated and atomically installed.
            for alternate in target.parent.glob(f"{target.stem}.*"):
                if alternate != target and alternate.is_file() and alternate.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                    alternate.unlink()
            installed.append(os.fspath(target))
        except (OrganizerError, OSError) as exc:
            warnings.append(f"Kept existing {item['kind']} because replacement was invalid: {exc}")
    return installed, warnings


def _add_text(parent: ET.Element, tag: str, value: Any) -> None:
    if value not in (None, "", [], {}):
        ET.SubElement(parent, tag).text = str(value)


def build_nfo_xml(movie: dict[str, Any], media: dict[str, Any], artwork: list[dict[str, Any]]) -> bytes:
    root = ET.Element("movie")
    for tag, key in [
        ("title", "title"), ("originaltitle", "original_title"), ("englishtitle", "english_title"),
        ("sorttitle", "title"), ("tagline", "tagline"), ("plot", "plot"), ("year", "year"),
        ("premiered", "release_date"), ("runtime", "runtime"), ("mpaa", "certification"),
        ("trailer", "trailer"),
    ]:
        _add_text(root, tag, movie.get(key))
    if movie.get("rating"):
        ratings = ET.SubElement(root, "ratings")
        rating = ET.SubElement(ratings, "rating", {"name": "tmdb", "max": "10", "default": "true"})
        _add_text(rating, "value", movie["rating"])
        _add_text(rating, "votes", movie.get("votes"))
    _add_text(root, "id", movie.get("imdb_id"))
    tmdb = ET.SubElement(root, "uniqueid", {"type": "tmdb", "default": "true"})
    tmdb.text = str(movie["tmdb_id"])
    if movie.get("imdb_id"):
        imdb = ET.SubElement(root, "uniqueid", {"type": "imdb", "default": "false"})
        imdb.text = movie["imdb_id"]
    for value in movie.get("genres", []): _add_text(root, "genre", value)
    for value in movie.get("countries", []): _add_text(root, "country", value)
    for value in movie.get("studios", []): _add_text(root, "studio", value)
    for value in movie.get("tags", []): _add_text(root, "tag", value)
    _add_text(root, "language", movie.get("original_language"))
    if movie.get("set"):
        set_node = ET.SubElement(root, "set")
        _add_text(set_node, "name", movie["set"].get("name"))
        _add_text(set_node, "overview", "")
    for name in movie.get("directors", []): _add_text(root, "director", name)
    for name in movie.get("writers", []): _add_text(root, "credits", name)
    for name in movie.get("producers", []): _add_text(root, "producer", name)
    for name in movie.get("composers", []): _add_text(root, "composer", name)
    for actor in movie.get("actors", [])[:20]:
        node = ET.SubElement(root, "actor")
        _add_text(node, "name", actor.get("name")); _add_text(node, "role", actor.get("role"))
        _add_text(node, "order", actor.get("order")); _add_text(node, "thumb", actor.get("thumb"))
    for item in artwork:
        kind = item["kind"]
        if kind in {"poster", "fanart", "clearlogo"}:
            thumb = ET.SubElement(root, "thumb", {"aspect": kind})
            thumb.text = Path(item["path"]).name
    fileinfo = ET.SubElement(root, "fileinfo")
    streamdetails = ET.SubElement(ET.SubElement(fileinfo, "streamdetails"), "video")
    _add_text(streamdetails, "codec", media.get("video_codec")); _add_text(streamdetails, "aspect", "")
    _add_text(streamdetails, "width", media.get("width")); _add_text(streamdetails, "height", media.get("height"))
    _add_text(streamdetails, "durationinseconds", media.get("runtime_seconds")); _add_text(streamdetails, "hdrtype", media.get("hdr"))
    details_parent = fileinfo.find("streamdetails")
    assert details_parent is not None
    for row in media.get("audio_streams", []):
        node = ET.SubElement(details_parent, "audio")
        _add_text(node, "codec", row.get("codec")); _add_text(node, "language", row.get("language")); _add_text(node, "channels", row.get("channels"))
    for row in media.get("subtitle_streams", []):
        node = ET.SubElement(details_parent, "subtitle")
        _add_text(node, "language", row.get("language"))
    _add_text(root, "edition", movie.get("edition")); _add_text(root, "source", movie.get("media_source"))
    ET.indent(root, space="  ")
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    ET.fromstring(data)
    return data + b"\n"


def _recognized_existing_artwork(folder: Path) -> list[str]:
    matches = []
    patterns = ["poster.*", "fanart.*", "clearlogo.*", "extrafanart/fanart*.*"]
    for pattern in patterns:
        matches.extend(os.fspath(x) for x in folder.glob(pattern) if x.is_file())
    return sorted(set(matches))


def create_state(args: argparse.Namespace) -> dict[str, Any]:
    folder = Path(args.folder).expanduser().resolve()
    video = Path(args.video).expanduser().resolve()
    if not folder.is_dir():
        raise OrganizerError(f"movie folder does not exist: {folder}")
    if not video.is_file():
        raise OrganizerError(f"movie file does not exist: {video}")
    try:
        video.relative_to(folder)
    except ValueError as exc:
        raise OrganizerError("movie file must be inside the supplied movie folder") from exc
    if video.suffix.lower() not in VIDEO_EXTENSIONS:
        raise OrganizerError(f"unsupported movie extension: {video.suffix}")
    if args.mode == "apply" and args.loose:
        raise OrganizerError("stage a loose movie in its sibling work folder before apply mode")
    token = os.environ.get("TMDB_API_KEY", "")
    if not token:
        raise OrganizerError("TMDB_API_KEY is required")
    zh, en, images = fetch_tmdb_movie(args.tmdb_id, token)
    movie = build_movie_metadata(args.tmdb_id, zh, en)
    media = inspect_media(video, Path(args.ffprobe_json) if args.ffprobe_json else None)
    hints = infer_release_hints(video)
    movie.update(hints)
    movie.update({
        "resolution": media["resolution"], "video_codec": media["video_codec"], "hdr": media["hdr"],
        "primary_audio_language": media["primary_audio_language"],
    })
    planned_artwork = plan_artwork(movie, images, folder)
    nfo_path = folder / "movie.nfo"
    warnings = []
    installed: list[str] = []
    if not media["primary_audio_language"]:
        warnings.append("Primary dialogue audio language could not be determined; review is required before moving")
    if args.mode == "apply":
        installed, art_warnings = install_artwork(planned_artwork)
        warnings.extend(art_warnings)
        available_artwork = [item for item in planned_artwork if Path(item["path"]).is_file()]
        atomic_replace_bytes(nfo_path, build_nfo_xml(movie, media, available_artwork))
    operations = [{"action": "write", "target": os.fspath(nfo_path), "status": "applied" if args.mode == "apply" else "planned"}]
    operations.extend({"action": "replace-artwork", "target": x["path"], "status": "applied" if x["path"] in installed else "planned" if args.mode == "dry-run" else "skipped"} for x in planned_artwork)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": args.mode,
        "source": {"original_folder": os.fspath(folder), "original_video": os.fspath(video), "loose": bool(args.loose), "original_release_name": video.name},
        "movie_folder": os.fspath(folder),
        "video_file": os.fspath(video),
        "movie": movie,
        "media": media,
        "artwork": {"planned": planned_artwork, "existing_replaced": _recognized_existing_artwork(folder)},
        "nfo": {"path": os.fspath(nfo_path), "planned": True, "written": args.mode == "apply"},
        "subtitles": {"embedded": media["subtitle_streams"]},
        "operations": operations,
        "completed_steps": ["tmdb_nfo"] if args.mode == "apply" else [],
        "warnings": warnings,
        "proposal": {
            "identified_movie": {k: movie.get(k) for k in ("title", "original_title", "english_title", "year", "tmdb_id")},
            "primary_audio_language": movie.get("primary_audio_language"),
            "production_countries": movie.get("production_countries", []),
            "artwork_replacements": [x["path"] for x in planned_artwork],
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "apply"), required=True)
    parser.add_argument("--folder", required=True, help="movie folder (or parent for --loose dry-run)")
    parser.add_argument("--video", required=True, help="main movie video")
    parser.add_argument("--tmdb-id", required=True, type=int, help="confirmed TMDB movie ID")
    parser.add_argument("--loose", action="store_true", help="model a loose movie during dry-run")
    parser.add_argument("--ffprobe-json", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        state = create_state(parse_args(argv))
        json.dump(state, sys.stdout, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (OrganizerError, OSError) as exc:
        diagnostic(f"tmdb_nfo: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
