#!/usr/bin/env python3
"""Find, validate, normalize, and install required movie subtitles."""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any, Callable, Iterable
import urllib.error
import urllib.parse
import urllib.request
import zipfile


ALLOWED_EXTENSIONS = {".srt", ".ass", ".ssa"}
EXECUTABLE_EXTENSIONS = {".exe", ".com", ".bat", ".cmd", ".ps1", ".sh", ".js", ".jar", ".scr", ".msi", ".dll"}
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
MAX_EXTRACTED_BYTES = 20 * 1024 * 1024
USER_AGENT = "movie-organizer-skill/1.0"
LANGUAGE_ALIASES = {
    "eng": "en", "english": "en", "en": "en",
    "chi": "zh", "zho": "zh", "zh": "zh", "chs": "zh-CN",
    "zh-cn": "zh-CN", "zh-hans": "zh-CN", "sc": "zh-CN",
    "cht": "zh-TW", "zh-tw": "zh-TW", "zh-hant": "zh-TW", "tc": "zh-TW",
}


class OrganizerError(RuntimeError):
    pass


def canonical_language(value: Any) -> str:
    text = str(value or "").strip().replace("_", "-").casefold()
    return LANGUAGE_ALIASES.get(text, text.split("-", 1)[0] if text else "")


def required_languages(primary_audio: str) -> list[str]:
    language = canonical_language(primary_audio)
    if not language:
        return []
    return ["en"] if language == "en" else ["en", "zh-CN"]


def _external_tags(path: Path) -> dict[str, Any]:
    tokens = [x.casefold() for x in re.split(r"[._ -]+", path.stem) if x]
    language = ""
    for index, token in enumerate(tokens):
        pair = f"{token}-{tokens[index + 1]}" if index + 1 < len(tokens) else ""
        if pair in LANGUAGE_ALIASES:
            language = LANGUAGE_ALIASES[pair]
            break
        if token in LANGUAGE_ALIASES:
            language = LANGUAGE_ALIASES[token]
            break
    return {
        "path": os.fspath(path), "language": language,
        "forced": any(x in {"forced", "foreign", "foreignparts"} for x in tokens),
        "sdh": any(x in {"sdh", "hi", "hearingimpaired"} for x in tokens),
        "commentary": any("comment" in x for x in tokens),
        "source": "external",
    }


def discover_external(folder: Path) -> list[dict[str, Any]]:
    if not folder.is_dir():
        return []
    return [_external_tags(path) for path in sorted(folder.iterdir()) if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS]


def valid_full_subtitles(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        language = canonical_language(row.get("language"))
        if not language or row.get("forced") or row.get("commentary"):
            continue
        result.setdefault(language, []).append(row)
    for language in result:
        result[language].sort(key=lambda row: bool(row.get("sdh")))
    return result


def _tokens(value: str) -> set[str]:
    return {x for x in re.split(r"[^a-z0-9]+", value.casefold()) if len(x) > 1}


def rank_candidate(candidate: dict[str, Any], movie: dict[str, Any], release_name: str, language: str) -> float:
    score = 0.0
    tmdb = str(candidate.get("tmdb_id") or "")
    imdb = str(candidate.get("imdb_id") or "").removeprefix("tt")
    expected_imdb = str(movie.get("imdb_id") or "").removeprefix("tt")
    if tmdb and tmdb == str(movie.get("tmdb_id")):
        score += 100
    if imdb and expected_imdb and imdb == expected_imdb:
        score += 100
    if candidate.get("year") and int(candidate["year"]) == int(movie.get("year") or 0):
        score += 25
    candidate_release = str(candidate.get("release_name") or candidate.get("file_name") or "")
    expected_tokens = _tokens(Path(release_name).stem)
    candidate_tokens = _tokens(candidate_release)
    if expected_tokens and candidate_tokens:
        score += 35 * len(expected_tokens & candidate_tokens) / len(expected_tokens)
    fmt = str(candidate.get("format") or Path(str(candidate.get("file_name") or "")).suffix.lstrip(".")).casefold()
    score += 15 if fmt == "srt" else 8 if fmt in {"ass", "ssa"} else -30
    score += -8 if candidate.get("sdh") else 5
    score += min(float(candidate.get("downloads") or 0) / 1000, 10)
    score += float(candidate.get("rating") or 0)
    if canonical_language(candidate.get("language")) != language:
        score -= 200
    if candidate.get("forced") or candidate.get("commentary"):
        score -= 200
    return round(score, 3)


def _json_request(
    url: str,
    headers: dict[str, str],
    data: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **headers}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace")
        raise OrganizerError(f"provider request failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OrganizerError(f"provider request failed: {exc}") from exc


def _download(url: str, headers: dict[str, str] | None = None, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_ARCHIVE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise OrganizerError(f"subtitle download failed: {exc}") from exc
    if len(data) > MAX_ARCHIVE_BYTES:
        raise OrganizerError("subtitle download exceeds 40 MiB")
    return data


class OpenSubtitlesClient:
    def __init__(self, api_key: str, username: str = "", password: str = "", request_json: Callable[..., dict[str, Any]] = _json_request, downloader: Callable[..., bytes] = _download):
        self.api_key, self.username, self.password = api_key, username, password
        self.request_json, self.downloader = request_json, downloader
        self.base_url = "https://api.opensubtitles.com/api/v1"
        self.token = ""

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Api-Key": self.api_key}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def login(self) -> None:
        if not self.username or not self.password or self.token:
            return
        data = self.request_json(f"{self.base_url}/login", {"Api-Key": self.api_key}, {"username": self.username, "password": self.password})
        self.token = str(data.get("token") or "")
        host = str(data.get("base_url") or "").strip().rstrip("/")
        if host:
            if not re.fullmatch(r"(?:vip-)?api\.opensubtitles\.com", host):
                raise OrganizerError("OpenSubtitles returned an unexpected API host")
            self.base_url = f"https://{host}/api/v1"

    def search(self, movie: dict[str, Any], release_name: str, language: str) -> list[dict[str, Any]]:
        params = {"languages": language.casefold(), "tmdb_id": movie.get("tmdb_id"), "order_by": "download_count", "order_direction": "desc"}
        if movie.get("imdb_id"):
            params["imdb_id"] = str(movie["imdb_id"]).removeprefix("tt")
        url = f"{self.base_url}/subtitles?{urllib.parse.urlencode(params)}"
        response = self.request_json(url, self.headers)
        candidates = []
        for item in response.get("data", []):
            attributes = item.get("attributes", {})
            feature = attributes.get("feature_details", {})
            files = attributes.get("files") or []
            for file_info in files:
                file_name = str(file_info.get("file_name") or "")
                candidates.append({
                    "provider": "OpenSubtitles", "file_id": file_info.get("file_id"),
                    "file_name": file_name, "release_name": attributes.get("release") or file_name,
                    "language": canonical_language(attributes.get("language")),
                    "tmdb_id": feature.get("tmdb_id"), "imdb_id": feature.get("imdb_id"), "year": feature.get("year"),
                    "format": Path(file_name).suffix.lstrip(".").casefold(),
                    "sdh": bool(attributes.get("hearing_impaired")), "forced": bool(attributes.get("foreign_parts_only")),
                    "commentary": "commentary" in str(attributes.get("release") or "").casefold(),
                    "downloads": attributes.get("download_count") or attributes.get("new_download_count") or 0,
                    "rating": attributes.get("ratings") or 0,
                })
        return candidates

    def fetch(self, candidate: dict[str, Any]) -> bytes:
        self.login()
        response = self.request_json(f"{self.base_url}/download", self.headers, {"file_id": candidate["file_id"]})
        link = str(response.get("link") or "")
        if not link.startswith("https://"):
            raise OrganizerError("OpenSubtitles returned an invalid download link")
        return self.downloader(link, {"User-Agent": USER_AGENT})


class SubDLClient:
    def __init__(self, api_key: str, request_json: Callable[..., dict[str, Any]] = _json_request, downloader: Callable[..., bytes] = _download):
        self.api_key, self.request_json, self.downloader = api_key, request_json, downloader

    def search(self, movie: dict[str, Any], release_name: str, language: str) -> list[dict[str, Any]]:
        language_code = "ZH" if language == "zh-CN" else "EN" if language == "en" else language.upper()
        params = {
            "api_key": self.api_key, "tmdb_id": movie.get("tmdb_id"), "imdb_id": movie.get("imdb_id"),
            "type": "movie", "year": movie.get("year"), "languages": language_code,
            "file_name": release_name, "subs_per_page": 30, "releases": 1, "hi": 1,
        }
        response = self.request_json("https://api.subdl.com/api/v1/subtitles?" + urllib.parse.urlencode(params), {})
        result_rows = response.get("results", [])
        feature = result_rows[0] if result_rows else {}
        candidates = []
        for row in response.get("subtitles", []):
            file_name = str(row.get("name") or Path(str(row.get("url") or "")).name)
            row_language = canonical_language(row.get("language") or language)
            if row_language == "zh" and language == "zh-CN":
                # SubDL can return the generic ZH code after a Simplified
                # Chinese-filtered query; apply mode still verifies the text.
                row_language = "zh-CN"
            candidates.append({
                "provider": "SubDL", "url": row.get("url"), "file_name": file_name,
                "release_name": row.get("release_name") or file_name,
                "language": row_language,
                "tmdb_id": feature.get("tmdb_id"), "imdb_id": feature.get("imdb_id"), "year": feature.get("year"),
                "format": str(row.get("format") or Path(file_name).suffix.lstrip(".")).casefold(),
                "sdh": bool(row.get("hi")), "forced": bool(row.get("forced")),
                "commentary": "commentary" in str(row.get("release_name") or "").casefold(),
                "downloads": row.get("downloads") or 0, "rating": row.get("rating") or 0,
            })
        return candidates

    def fetch(self, candidate: dict[str, Any]) -> bytes:
        relative = str(candidate.get("url") or "")
        if relative.startswith("https://dl.subdl.com/"):
            url = relative
        elif relative.startswith("/subtitle/"):
            url = "https://dl.subdl.com" + relative
        else:
            raise OrganizerError("SubDL returned an invalid download link")
        separator = "&" if "?" in url else "?"
        return self.downloader(f"{url}{separator}{urllib.parse.urlencode({'api_key': self.api_key})}", {"x-api-key": self.api_key})


class SubHDClient:
    """Scrape subtitles from subhd.tv (no API key needed) as a last-resort provider.

    SubHD is a Chinese subtitle community. It has no public API — subtitles are
    embedded directly in the HTML page inside a ``data-content`` attribute in a
    custom number-prefixed format that must be converted to standard SRT.

    The provider is always appended last, after OpenSubtitles and SubDL.
    """

    BASE_URL = "https://subhd.tv"
    SUBHD_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    def __init__(self) -> None:
        self._html_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # HTML helpers (no BeautifulSoup dependency)
    # ------------------------------------------------------------------

    def _fetch_html(self, url: str) -> str:
        if url in self._html_cache:
            return self._html_cache[url]
        req = urllib.request.Request(
            url, headers={"User-Agent": self.SUBHD_UA},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OrganizerError(f"SubHD request failed: {exc}") from exc
        text = raw.decode("utf-8", "replace")
        self._html_cache[url] = text
        return text

    @staticmethod
    def _extract_attr(html: str, attr: str) -> list[str]:
        """Extract values of ``attr="..."`` from a block of HTML."""
        return re.findall(rf'{re.escape(attr)}="([^"]*)"', html)

    @staticmethod
    def _subtitle_code_from_page(html: str) -> str | None:
        """Find the first ``/a/{code}`` link on a subtitle detail page."""
        codes = re.findall(r'href="/a/([a-zA-Z0-9]+)"', html)
        return codes[0] if codes else None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, movie: dict[str, Any], release_name: str, language: str) -> list[dict[str, Any]]:
        """Search subhd.tv for subtitles matching ``movie``.

        Strategy:
          1. Build a search query from the Chinese TMDB title + year.
          2. Fetch the search-results page.
          3. Locate the movie-detail page ``/d/{id}`` that best matches our title.
          4. Fetch the movie page and extract every subtitle listing.
          5. Filter by the requested language and return candidates.
        """
        # -- build search query -------------------------------------------
        year = movie.get("year", "")
        zh_title = (movie.get("title") or "").strip()
        en_title = (movie.get("original_title") or movie.get("title") or "").strip()
        query_candidates = []
        if zh_title:
            query_candidates.append(f"{zh_title} {year}".strip())
        if en_title and en_title != zh_title:
            query_candidates.append(f"{en_title} {year}".strip())
        if not query_candidates:
            query_candidates.append(release_name)

        # -- find the movie page on SubHD ---------------------------------
        movie_id = self._resolve_movie_id(query_candidates, release_name)
        if not movie_id:
            return []

        # -- fetch the movie detail page (all subtitles for this movie) ----
        try:
            movie_html = self._fetch_html(f"{self.BASE_URL}/d/{movie_id}")
        except OrganizerError:
            return []

        return self._parse_movie_subtitles(movie_html, language)

    def _resolve_movie_id(self, query_candidates: list[str], release_name: str) -> str | None:
        """Search SubHD and return the ``/d/{id}`` of the best-matching movie.

        The search-results page lists subtitle cards, each linked to a movie
        detail page via a poster image link.  Sidebar widgets on the same page
        also contain ``/d/{id}`` links for unrelated movies, so we only trust
        IDs that appear inside a search-result card (identified by a ``pics``
        div containing a poster ``<img>``).
        """
        for query in query_candidates:
            query_encoded = urllib.parse.quote(query)
            try:
                html = self._fetch_html(f"{self.BASE_URL}/search/{query_encoded}")
            except OrganizerError:
                continue

            # Find the movie ID from the poster link inside a result card.
            # Each card has: <a href='/d/{id}'> <div class="pics"><img src="...poster/...">
            match = re.search(
                r"href=['\"]/d/(\d+)['\"][^>]*>\s*<div class=\"pics\"",
                html,
            )
            if match:
                return match.group(1)

            # Fallback: any /d/ ID that appears right before a poster image
            match = re.search(
                r'href=["\']/d/(\d+)["\'][^>]*>.*?<img[^>]*poster/',
                html,
                re.DOTALL,
            )
            if match:
                return match.group(1)

        return None

    def _parse_movie_subtitles(self, html: str, language: str) -> list[dict[str, Any]]:
        """Parse subtitle entries from a movie detail page (``/d/{id}``)."""
        candidates: list[dict[str, Any]] = []

        # Each subtitle entry looks like:
        #   <div class="row pt-2 mb-2">
        #     <a class="link-dark" href="/a/tSOfPy">release name</a>
        #     <span>双语</span> <span>简体</span> <span>英语</span>
        #     <span class="text-secondary">SRT</span>
        #   </div>
        #
        # We split on the row delimiter and parse each block.

        blocks = re.split(r'<div class="row pt-2 mb-2">', html)
        for block in blocks:
            code_match = re.search(r'href="/a/([a-zA-Z0-9]+)"', block)
            if not code_match:
                continue
            code = code_match.group(1)

            # Release name
            rel_match = re.search(r'href="/a/[^"]*"[^>]*>([^<]+)</a>', block)
            release_name = rel_match.group(1).strip() if rel_match else ""

            # Format (.srt / .ass / .ssa)
            fmt_match = re.search(
                r'<span class="p-1 text-secondary">(\w+)</span>', block,
            )
            fmt = fmt_match.group(1).casefold() if fmt_match else "srt"

            # Download count
            dl_match = re.search(
                r'<div class="px-3 py-2 text-end text-secondary">(\d+)</div>',
                block,
            )
            downloads = int(dl_match.group(1)) if dl_match else 0

            # Language tags: the tags before the format span
            tags = re.findall(
                r'<span class="p-1 fw-bold">([^<]+)</span>', block,
            )
            tag_text = " ".join(tags)

            # Determine candidate language
            cand_lang = self._resolve_language(tag_text)

            # Skip if language doesn't match what we need
            if language == "zh-CN" and cand_lang not in ("zh-CN", "zh"):
                continue
            if language == "en" and cand_lang not in ("en",):
                continue

            candidates.append({
                "provider": "SubHD",
                "code": code,
                "file_name": f"{release_name}.{fmt}",
                "release_name": release_name,
                "language": cand_lang if cand_lang != "zh" else "zh-CN",
                "format": fmt,
                "downloads": downloads,
                "rating": 0.0,
                "sdh": False,
                "forced": False,
                "commentary": False,
                "tmdb_id": None,
                "imdb_id": None,
                "year": None,
                # Pre-populate score fields for rank_candidate
                "score": 0.0,
            })

        return candidates

    @staticmethod
    def _resolve_language(tag_text: str) -> str:
        """Infer canonical language from SubHD tag text."""
        t = tag_text.casefold()
        if "双语" in t:
            return "zh-CN"  # bilingual — default to Simplified Chinese
        if "简体" in t or "简" in t or "chs" in t:
            return "zh-CN"
        if "繁体" in t or "繁" in t or "cht" in t:
            return "zh-TW"
        if "英语" in t or "英文" in t or "en" in t:
            return "en"
        # Fallback: check for Chinese characters in the release name later
        return "zh-CN"

    # ------------------------------------------------------------------
    # Fetch & Convert
    # ------------------------------------------------------------------

    def fetch(self, candidate: dict[str, Any]) -> bytes:
        """Fetch the subtitle detail page, extract ``data-content``, and
        return valid SRT bytes."""
        code = str(candidate.get("code") or "")
        if not code:
            raise OrganizerError("SubHD candidate has no subtitle code")

        html = self._fetch_html(f"{self.BASE_URL}/a/{code}")

        # Extract the embedded data-content
        match = re.search(r'data-content="([^"]*)"', html)
        if not match:
            raise OrganizerError(
                f"SubHD page /a/{code} has no data-content attribute "
                "(site layout may have changed — see SKILL.md for manual fallback)"
            )

        raw_text = match.group(1)
        import html as html_lib
        raw_text = html_lib.unescape(raw_text)

        # Convert from SubHD's custom number|text format to SRT
        srt_bytes = self._convert_to_srt(raw_text)
        return srt_bytes

    @staticmethod
    def _convert_to_srt(raw: str) -> bytes:
        """Convert SubHD's embedded format to standard SRT and return bytes.

        SubHD uses two variants of the embedded format:

        **Variant A** (with line numbers)::

            [00:11:21]
            253|你能对着我再说一遍吗？
            254|Can you say that to me?
            255|
            256|[00:11:23]
            ...

        **Variant B** (bare, no line numbers)::

            [00:11:21]
            你能对着我再说一遍吗？
            Can you say that to me?

            [00:11:23]
            ...

        Both convert to standard SRT with ``hh:mm:ss,000`` timestamps.
        """
        lines = raw.strip().split("\n")
        has_number_prefix = any(
            re.match(r"\d+\|", line) for line in lines if line.strip()
        )

        entries: list[tuple[str, list[str]]] = []
        current_ts: str | None = None
        current_lines: list[str] = []

        if has_number_prefix:
            # Variant A: number|text format
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                ts_match = re.match(r"(?:\d+\|)?\[(\d{2}:\d{2}:\d{2})\].*", line)
                if ts_match:
                    if current_ts is not None and current_lines:
                        entries.append((current_ts, current_lines))
                    current_ts = ts_match.group(1)
                    current_lines = []
                    continue
                data_match = re.match(r"\d+\|(.+)", line)
                if data_match:
                    content = data_match.group(1).strip()
                    if content:
                        current_lines.append(content)
        else:
            # Variant B: bare format (no line numbers)
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                ts_match = re.match(r"\[(\d{2}:\d{2}:\d{2})\]", line)
                if ts_match:
                    if current_ts is not None and current_lines:
                        entries.append((current_ts, current_lines))
                    current_ts = ts_match.group(1)
                    current_lines = []
                else:
                    if current_ts is not None and line:
                        current_lines.append(line)

        if current_ts is not None and current_lines:
            entries.append((current_ts, current_lines))

        if not entries:
            raise OrganizerError(
                "SubHD subtitle data yielded zero entries after parsing"
            )

        def _ts_to_sec(ts: str) -> int:
            h, m, s = map(int, ts.split(":"))
            return h * 3600 + m * 60 + s

        def _sec_to_ts(sec: int) -> str:
            h = sec // 3600
            m = (sec % 3600) // 60
            s = sec % 60
            return f"{h:02d}:{m:02d}:{s:02d},000"

        srt_parts: list[str] = []
        for i, (ts, text_lines) in enumerate(entries):
            num = i + 1
            start_sec = _ts_to_sec(ts)
            if i + 1 < len(entries):
                end_sec = _ts_to_sec(entries[i + 1][0])
                if end_sec <= start_sec:
                    end_sec = start_sec + 3
            else:
                end_sec = start_sec + 3
            srt_parts.append(
                f"{num}\n{_sec_to_ts(start_sec)} --> {_sec_to_ts(end_sec)}\n"
                f"{chr(10).join(text_lines)}\n\n"
            )

        result = "".join(srt_parts).encode("utf-8")
        return result


def decode_subtitle(data: bytes, expected_language: str = "") -> str:
    if b"\x00" in data[:200] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise OrganizerError("subtitle contains unexpected NUL bytes")
    encodings = ["utf-8-sig"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.append("utf-16")
    # Single-byte Western text can look like valid East Asian text and vice versa;
    # use the provider's expected language only to choose decoding order, then
    # independently verify the decoded dialogue below.
    encodings.extend(["gb18030", "big5", "cp1252"] if expected_language == "zh-CN" else ["cp1252", "gb18030", "big5"])
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            if "\ufffd" not in text:
                return text
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise OrganizerError("subtitle text encoding is not recognized")


def validate_timestamps(text: str, extension: str) -> None:
    if extension == ".srt":
        matches = re.findall(r"(?m)^\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})", text)
        if not matches:
            raise OrganizerError("SRT subtitle has no valid timestamp range")
        for values in matches:
            if int(values[1]) > 59 or int(values[2]) > 59 or int(values[5]) > 59 or int(values[6]) > 59:
                raise OrganizerError("SRT subtitle contains an invalid timestamp")
    else:
        if not re.search(r"(?m)^Dialogue:\s*\d+,\d{1,2}:\d{2}:\d{2}[.:]\d{2},\d{1,2}:\d{2}:\d{2}[.:]\d{2},", text):
            raise OrganizerError("ASS/SSA subtitle has no valid Dialogue timestamps")


SIMPLIFIED_MARKERS = set("这为国们体后发里么时过个电影观众门开关听说话头万与东丝业严丧")
TRADITIONAL_MARKERS = set("這為國們體後發裡麼時過個電影觀眾門開關聽說話頭萬與東絲業嚴喪")


def language_matches(text: str, expected: str) -> bool:
    dialogue = re.sub(r"(?m)^\s*\d+\s*$|\d{1,2}:\d{2}:\d{2}[,.]\d{2,3}\s*-->.*$|<[^>]+>|\{[^}]+\}", " ", text)
    cjk = [c for c in dialogue if "\u3400" <= c <= "\u9fff"]
    latin = [c for c in dialogue if c.isascii() and c.isalpha()]
    if expected == "en":
        return len(latin) >= 8 and len(latin) >= len(cjk) * 2
    if expected == "zh-CN":
        simplified_only = SIMPLIFIED_MARKERS - TRADITIONAL_MARKERS
        traditional_only = TRADITIONAL_MARKERS - SIMPLIFIED_MARKERS
        simplified = sum(c in simplified_only for c in cjk)
        traditional = sum(c in traditional_only for c in cjk)
        return len(cjk) >= 4 and not (traditional >= 2 and simplified == 0)
    if expected == "zh-TW":
        simplified_only = SIMPLIFIED_MARKERS - TRADITIONAL_MARKERS
        traditional_only = TRADITIONAL_MARKERS - SIMPLIFIED_MARKERS
        simplified = sum(c in simplified_only for c in cjk)
        traditional = sum(c in traditional_only for c in cjk)
        return len(cjk) >= 4 and not (simplified >= 2 and traditional == 0)
    return True


def validate_external_row(row: dict[str, Any]) -> tuple[bool, str]:
    path = Path(str(row.get("path") or ""))
    if not path.is_file() and row.get("original_path"):
        path = Path(str(row["original_path"]))
    if not path.is_file():
        return False, "file does not exist"
    try:
        if path.stat().st_size > MAX_EXTRACTED_BYTES:
            raise OrganizerError("file exceeds 20 MiB")
        language = canonical_language(row.get("language"))
        text = decode_subtitle(path.read_bytes(), language).replace("\r\n", "\n").replace("\r", "\n")
        validate_timestamps(text, path.suffix.casefold())
        if not language_matches(text, language):
            raise OrganizerError(f"text heuristics do not match {language}")
        return True, ""
    except (OSError, OrganizerError) as exc:
        return False, str(exc)


def _safe_zip_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > 100:
        raise OrganizerError("subtitle archive contains too many entries")
    total = 0
    subtitles = []
    for info in infos:
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
            raise OrganizerError("subtitle archive contains an unsafe path")
        suffix = path.suffix.casefold()
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise OrganizerError("subtitle archive contains a symbolic link")
        if suffix in EXECUTABLE_EXTENSIONS:
            raise OrganizerError("subtitle archive contains an executable")
        total += info.file_size
        if total > MAX_EXTRACTED_BYTES:
            raise OrganizerError("subtitle archive expands beyond 20 MiB")
        if not info.is_dir() and suffix in ALLOWED_EXTENSIONS:
            subtitles.append(info)
    if not subtitles:
        raise OrganizerError("subtitle archive contains no supported subtitle")
    return subtitles


def normalize_download(data: bytes, expected_language: str, preferred_name: str = "") -> tuple[bytes, str]:
    files: list[tuple[str, bytes]] = []
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in _safe_zip_members(archive):
                    files.append((info.filename, archive.read(info)))
        except zipfile.BadZipFile as exc:
            raise OrganizerError("subtitle ZIP archive is corrupt") from exc
    else:
        suffix = Path(preferred_name).suffix.casefold()
        if suffix not in ALLOWED_EXTENSIONS:
            raise OrganizerError("raw subtitle has an unsupported extension")
        files.append((preferred_name, data))
    files.sort(key=lambda row: (Path(row[0]).suffix.casefold() != ".srt", preferred_name.casefold() not in row[0].casefold(), len(row[1])))
    failures = []
    for name, payload in files:
        extension = Path(name).suffix.casefold()
        try:
            text = decode_subtitle(payload, expected_language).replace("\r\n", "\n").replace("\r", "\n")
            validate_timestamps(text, extension)
            if not language_matches(text, expected_language):
                raise OrganizerError(f"text heuristics do not match {expected_language}")
            return (text.rstrip() + "\n").encode("utf-8"), extension
        except OrganizerError as exc:
            failures.append(f"{name}: {exc}")
    raise OrganizerError("no valid subtitle in download (" + "; ".join(failures[:3]) + ")")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try: os.unlink(temp_name)
        except FileNotFoundError: pass
        raise


def _providers() -> list[Any]:
    providers = []
    open_key = os.environ.get("OPENSUBTITLES_API_KEY", "")
    if open_key:
        providers.append(OpenSubtitlesClient(open_key, os.environ.get("OPENSUBTITLES_USERNAME", ""), os.environ.get("OPENSUBTITLES_PASSWORD", "")))
    subdl_key = os.environ.get("SUBDL_API_KEY", "")
    if subdl_key:
        providers.append(SubDLClient(subdl_key))
    # SubHD is always available (no API key required) as a last-resort scraper.
    providers.append(SubHDClient())
    return providers


def choose_candidates(providers: list[Any], movie: dict[str, Any], release_name: str, language: str) -> tuple[list[dict[str, Any]], Any | None, list[str]]:
    warnings = []
    for provider in providers:  # ordering is policy: OpenSubtitles, then SubDL
        try:
            rows = provider.search(movie, release_name, language)
        except OrganizerError as exc:
            warnings.append(f"{provider.__class__.__name__} search failed: {exc}")
            continue
        for row in rows:
            row["score"] = rank_candidate(row, movie, release_name, language)
        suitable = sorted((x for x in rows if x["score"] >= 20), key=lambda x: x["score"], reverse=True)
        if suitable:
            return suitable, provider, warnings
    return [], None, warnings


def ensure_state(state: dict[str, Any], mode: str, providers: list[Any] | None = None) -> dict[str, Any]:
    movie = state.get("movie", {})
    primary = movie.get("primary_audio_language") or state.get("media", {}).get("primary_audio_language") or ""
    required = required_languages(primary)
    folder = Path(state["movie_folder"])
    video = Path(state["video_file"])
    rows = list(state.get("subtitles", {}).get("embedded", []))
    external_rows = list(state.get("subtitles", {}).get("external_existing", []))
    if mode == "apply":
        external_rows.extend(discover_external(folder))
    # Deduplicate predicted/current paths, then validate external content rather
    # than trusting filename tags alone.
    unique_external = {}
    for row in external_rows:
        unique_external[(str(row.get("path")), canonical_language(row.get("language")))] = row
    for row in unique_external.values():
        valid, reason = validate_external_row(row)
        if valid:
            rows.append(row)
        else:
            state.setdefault("warnings", []).append(f"Ignored invalid external subtitle {row.get('path')}: {reason}")
    full = valid_full_subtitles(rows)
    existing = [language for language in required if full.get(language)]
    missing = [language for language in required if language not in existing]
    warnings = state.setdefault("warnings", [])
    if not primary:
        message = "Primary dialogue audio is unknown; subtitle requirements need review"
        if message not in warnings: warnings.append(message)
    provider_list = providers if providers is not None else _providers()
    if missing and not provider_list:
        raise OrganizerError("missing required subtitles and no OPENSUBTITLES_API_KEY or SUBDL_API_KEY is configured — SubHD scraped fallback also unavailable")

    planned, installed = [], []
    release_name = str(state.get("source", {}).get("original_release_name") or video.name)
    for language in missing:
        candidates, provider, provider_warnings = choose_candidates(provider_list, movie, release_name, language)
        warnings.extend(provider_warnings)
        if not candidates or provider is None:
            raise OrganizerError(f"no reliable {language} subtitle was found on OpenSubtitles.com, SubDL, or SubHD")
        selected = candidates[0]
        extension = Path(str(selected.get("file_name") or "")).suffix.casefold()
        if extension not in ALLOWED_EXTENSIONS:
            extension = ".srt"
        target = folder / f"{video.stem}.{language}{'.sdh' if selected.get('sdh') else ''}{extension}"
        item = {"language": language, "provider": selected["provider"], "score": selected["score"], "target": os.fspath(target), "file_name": selected.get("file_name"), "sdh": bool(selected.get("sdh"))}
        planned.append(item)
        if mode == "apply":
            last_error = None
            remaining = list(provider_list)
            while provider is not None and candidates:
                installed_this_language = False
                for candidate in candidates:
                    try:
                        payload = provider.fetch(candidate)
                        normalized, actual_extension = normalize_download(payload, language, str(candidate.get("file_name") or ""))
                        candidate_target = folder / f"{video.stem}.{language}{'.sdh' if candidate.get('sdh') else ''}{actual_extension}"
                        if candidate_target.exists():
                            raise OrganizerError(f"subtitle target already exists: {candidate_target}")
                        atomic_write(candidate_target, normalized)
                        item.update({"target": os.fspath(candidate_target), "file_name": candidate.get("file_name"), "score": candidate["score"], "provider": candidate["provider"], "sdh": bool(candidate.get("sdh"))})
                        installed.append({"path": os.fspath(candidate_target), "language": language, "forced": False, "sdh": bool(candidate.get("sdh")), "commentary": False, "source": candidate["provider"]})
                        installed_this_language = True
                        break
                    except OrganizerError as exc:
                        last_error = exc
                if installed_this_language:
                    break
                try:
                    provider_index = remaining.index(provider)
                    remaining = remaining[provider_index + 1:]
                except ValueError:
                    remaining = []
                candidates, provider, fallback_warnings = choose_candidates(remaining, movie, release_name, language)
                warnings.extend(fallback_warnings)
            else:
                raise OrganizerError(f"all selected {language} downloads failed validation: {last_error}")

    subtitle_state = state.setdefault("subtitles", {})
    subtitle_state.update({
        "required": required, "existing_required": existing, "missing_required": missing,
        "planned_downloads": planned, "downloaded": installed,
        "satisfied_after_apply": sorted(set(existing + [x["language"] for x in installed])) if mode == "apply" else sorted(set(existing + [x["language"] for x in planned])),
    })
    state["mode"] = mode
    state.setdefault("operations", []).extend({"action": "download-subtitle", "target": x["target"], "provider": x["provider"], "status": "applied" if mode == "apply" else "planned"} for x in planned)
    if mode == "apply": state.setdefault("completed_steps", []).append("subtitles")
    state.setdefault("proposal", {}).update({
        "required_subtitles": required, "existing_subtitles": existing, "missing_subtitles": missing,
        "subtitle_downloads": planned,
    })
    return state


def _load_state() -> dict[str, Any]:
    try: state = json.load(sys.stdin)
    except json.JSONDecodeError as exc: raise OrganizerError(f"stdin is not valid shared JSON state: {exc}") from exc
    if state.get("schema_version") != 1: raise OrganizerError("unsupported shared state schema")
    return state


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "apply"), required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        json.dump(ensure_state(_load_state(), args.mode), sys.stdout, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (OrganizerError, OSError, ValueError) as exc:
        print(f"subtitles: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
