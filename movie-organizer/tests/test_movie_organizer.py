from __future__ import annotations

import argparse
import copy
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"movie_organizer_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


tmdb = load_script("tmdb_nfo")
rename = load_script("rename_media")
subtitles = load_script("subtitles")
move = load_script("move_movie")


def sample_details(language: str = "zh") -> dict:
    localized = language == "zh"
    return {
        "id": 129,
        "imdb_id": "tt0245429",
        "title": "千与千寻" if localized else "Spirited Away",
        "original_title": "千と千尋の神隠し",
        "tagline": "隧道的另一边" if localized else "The tunnel led Chihiro to a mysterious town.",
        "overview": "少女进入神灵世界。" if localized else "A girl enters a spirit world.",
        "release_date": "2001-07-20",
        "vote_average": 8.5,
        "vote_count": 17000,
        "runtime": 125,
        "original_language": "ja",
        "genres": [{"name": "动画" if localized else "Animation"}],
        "production_countries": [{"iso_3166_1": "JP", "name": "日本" if localized else "Japan"}],
        "production_companies": [{"name": "Studio Ghibli"}],
        "belongs_to_collection": None,
        "keywords": {"keywords": [{"name": "spirit"}]},
        "videos": {"results": [{"site": "YouTube", "type": "Trailer", "official": True, "key": "abc"}]},
        "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [{"type": 3, "certification": "PG"}]}]},
        "credits": {
            "cast": [{"name": f"Actor {i}", "character": "Role", "order": i} for i in range(25)],
            "crew": [
                {"name": "Hayao Miyazaki", "job": "Director", "department": "Directing"},
                {"name": "Hayao Miyazaki", "job": "Screenplay", "department": "Writing"},
                {"name": "Toshio Suzuki", "job": "Producer", "department": "Production"},
                {"name": "Joe Hisaishi", "job": "Original Music Composer", "department": "Sound"},
            ],
        },
    }


def sample_movie(primary: str = "ja", countries=None) -> dict:
    return {
        "tmdb_id": 129, "imdb_id": "tt0245429", "title": "千与千寻",
        "original_title": "千と千尋の神隠し", "english_title": "Spirited Away", "year": 2001,
        "primary_audio_language": primary, "production_countries": countries or ["JP"],
        "edition": "", "resolution": "1080p", "video_codec": "h264", "media_source": "bluray", "hdr": "",
    }


def sample_state(folder: Path, video: Path, primary: str = "en") -> dict:
    return {
        "schema_version": 1, "mode": "dry-run", "source": {"original_folder": str(folder), "original_video": str(video), "loose": False, "original_release_name": video.name},
        "movie_folder": str(folder), "video_file": str(video), "movie": sample_movie(primary),
        "media": {"primary_audio_language": primary, "subtitle_streams": []},
        "artwork": {"planned": [], "existing_replaced": []}, "nfo": {"path": str(folder / "movie.nfo"), "planned": True, "written": False},
        "subtitles": {"embedded": []}, "operations": [], "completed_steps": [], "warnings": [], "proposal": {},
    }


def png(width: int = 120, height: int = 180) -> bytes:
    # Signature and IHDR are sufficient for the script's signature/dimension validation.
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return output.getvalue()


ENGLISH_SRT = b"1\n00:00:01,000 --> 00:00:03,000\nThis is a complete English subtitle line.\n"
CHINESE_SRT = "1\n00:00:01,000 --> 00:00:03,000\n这是一个电影字幕。\n".encode()
TRADITIONAL_SRT = "1\n00:00:01,000 --> 00:00:03,000\n這是電影裡的對話與聲音。\n".encode()


class TmdbTests(unittest.TestCase):
    def test_localized_fallback_and_people(self):
        zh, en = sample_details("zh"), sample_details("en")
        zh["overview"] = ""
        movie = tmdb.build_movie_metadata(129, zh, en)
        self.assertEqual(movie["title"], "千与千寻")
        self.assertEqual(movie["plot"], "A girl enters a spirit world.")
        self.assertEqual(movie["certification"], "PG")
        self.assertEqual(len(movie["actors"]), 20)
        self.assertEqual(movie["directors"], ["Hayao Miyazaki"])
        self.assertEqual(movie["composers"], ["Joe Hisaishi"])

    def test_confirmed_id_mismatch_is_rejected(self):
        def request(url, token):
            data = sample_details("zh")
            data["id"] = 999
            return data
        with self.assertRaisesRegex(tmdb.OrganizerError, "does not match"):
            tmdb.fetch_tmdb_movie(129, "secret", request)

    def test_ffprobe_fixture_normalization_and_commentary(self):
        media = tmdb.inspect_media(Path("ignored.mkv"), ROOT / "tests/fixtures/ffprobe_movie.json")
        self.assertEqual((media["resolution"], media["video_codec"], media["hdr"]), ("4K", "h265", "dv"))
        self.assertEqual(media["primary_audio_language"], "ja")
        self.assertTrue(media["audio_streams"][1]["commentary"])
        self.assertEqual(media["subtitle_streams"][0]["language"], "en")
        self.assertTrue(media["subtitle_streams"][1]["forced"])

    def test_artwork_only_uses_tmdb_supported_types(self):
        images = {
            "posters": [{"file_path": "/p.jpg", "iso_639_1": "ja", "vote_average": 8}],
            "backdrops": [{"file_path": f"/b{i}.jpg", "vote_average": 10-i} for i in range(7)],
            "logos": [{"file_path": "/l.png", "iso_639_1": "zh", "vote_average": 7}],
        }
        rows = tmdb.plan_artwork(sample_movie(), images, Path("/movie"))
        self.assertEqual([x["kind"] for x in rows].count("extrafanart"), 4)
        self.assertEqual({x["kind"] for x in rows}, {"poster", "fanart", "extrafanart", "clearlogo"})

    def test_invalid_replacement_keeps_existing_art(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "poster.jpg"
            target.write_bytes(b"old artwork")
            installed, warnings = tmdb.install_artwork([{"kind": "poster", "url": "x", "path": str(target)}], lambda _: b"not an image")
            self.assertEqual(installed, [])
            self.assertTrue(warnings)
            self.assertEqual(target.read_bytes(), b"old artwork")

    def test_valid_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "poster.png"
            target.write_bytes(b"old")
            installed, warnings = tmdb.install_artwork([{"kind": "poster", "url": "x", "path": str(target)}], lambda _: png())
            self.assertEqual(installed, [str(target)])
            self.assertFalse(warnings)
            self.assertEqual(target.read_bytes(), png())
            self.assertFalse(list(Path(temp).glob("*.tmp")))

    def test_valid_replacement_removes_obsolete_extension(self):
        with tempfile.TemporaryDirectory() as temp:
            old = Path(temp) / "poster.jpg"; old.write_bytes(b"old")
            target = Path(temp) / "poster.png"
            installed, warnings = tmdb.install_artwork([{"kind": "poster", "url": "x", "path": str(target)}], lambda _: png())
            self.assertFalse(warnings)
            self.assertEqual(installed, [str(target)])
            self.assertFalse(old.exists())
            self.assertTrue(target.exists())

    def test_nfo_is_valid_utf8_xml_and_has_streams(self):
        movie = tmdb.build_movie_metadata(129, sample_details("zh"), sample_details("en"))
        media = tmdb.inspect_media(Path("ignored.mkv"), ROOT / "tests/fixtures/ffprobe_movie.json")
        data = tmdb.build_nfo_xml(movie, media, [])
        root = tmdb.ET.fromstring(data)
        self.assertEqual(root.findtext("title"), "千与千寻")
        self.assertEqual(root.findtext("uniqueid"), "129")
        self.assertEqual(len(root.findall("actor")), 20)
        self.assertEqual(root.findtext("fileinfo/streamdetails/video/codec"), "h265")

    def test_dry_run_has_no_file_changes(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"TMDB_API_KEY": "secret"}):
            folder = Path(temp) / "release"; folder.mkdir()
            video = folder / "Movie.2001.mkv"; video.write_bytes(b"video")
            before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
            args = argparse.Namespace(mode="dry-run", folder=str(folder), video=str(video), tmdb_id=129, loose=False, ffprobe_json=str(ROOT / "tests/fixtures/ffprobe_movie.json"))
            with mock.patch.object(tmdb, "fetch_tmdb_movie", return_value=(sample_details("zh"), sample_details("en"), {})):
                state = tmdb.create_state(args)
            after = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertFalse(state["nfo"]["written"])


class RenameTests(unittest.TestCase):
    def test_sanitizes_names_and_deduplicates_title(self):
        movie = sample_movie()
        movie.update({"title": "Bad: Movie?", "original_title": "Bad Movie"})
        self.assertEqual(rename.base_movie_name(movie), "Bad Movie (2001)")

    def test_filename_preserves_extension(self):
        name = rename.movie_filename(sample_movie(), ".MKV")
        self.assertTrue(name.endswith("1080p h264 bluray.mkv"))

    def test_subtitle_tags_preserve_language_forced_sdh(self):
        tags, reason = rename.subtitle_tags(Path("Film.zh-TW.forced.sdh.ass"), "Film")
        self.assertIsNone(reason)
        self.assertEqual(tags, ["zh-TW", "forced", "sdh"])

    def test_uncertain_subtitle_is_left_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "release"; folder.mkdir()
            video = folder / "Film.mkv"; video.write_bytes(b"v")
            uncertain = folder / "random.srt"; uncertain.write_text("x")
            state = sample_state(folder, video)
            result = rename.rename_state(state, "dry-run")
            self.assertEqual(result["proposal"]["uncertain_subtitles"][0]["path"], str(uncertain))
            self.assertTrue(uncertain.exists())

    def test_collision_is_detected_before_any_rename(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "a"; target = Path(temp) / "b"
            source.write_text("a"); target.write_text("b")
            with self.assertRaisesRegex(rename.OrganizerError, "already exists"):
                rename.check_collisions([(source, target)])
            self.assertEqual(source.read_text(), "a")
            self.assertEqual(target.read_text(), "b")

    def test_dry_run_and_apply_propose_same_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "release"; folder.mkdir()
            video = folder / "Film.mkv"; video.write_bytes(b"v")
            (folder / "Film.en.sdh.srt").write_bytes(ENGLISH_SRT)
            (folder / "movie.nfo").write_text("<movie/>")
            dry = rename.rename_state(copy.deepcopy(sample_state(folder, video)), "dry-run")
            applied = rename.rename_state(copy.deepcopy(sample_state(folder, video)), "apply")
            self.assertEqual(dry["proposal"]["movie_folder"], applied["proposal"]["movie_folder"])
            self.assertEqual(dry["proposal"]["movie_filename"], applied["proposal"]["movie_filename"])
            self.assertTrue(Path(applied["video_file"]).exists())
            self.assertTrue(any(Path(applied["movie_folder"]).glob("*.en.sdh.srt")))


class FakeProvider:
    def __init__(self, payload=ENGLISH_SRT, language="en", provider="OpenSubtitles"):
        self.payload, self.language, self.provider = payload, language, provider

    def search(self, movie, release_name, language):
        return [{
            "provider": self.provider, "file_id": 1, "file_name": f"release.{language}.srt",
            "release_name": release_name, "language": language, "tmdb_id": movie["tmdb_id"],
            "imdb_id": movie["imdb_id"], "year": movie["year"], "format": "srt",
            "sdh": False, "forced": False, "commentary": False, "downloads": 1000, "rating": 9,
        }]

    def fetch(self, candidate):
        return self.payload


class SubtitleTests(unittest.TestCase):
    def test_required_language_rules(self):
        self.assertEqual(subtitles.required_languages("eng"), ["en"])
        self.assertEqual(subtitles.required_languages("jpn"), ["en", "zh-CN"])
        self.assertEqual(subtitles.required_languages(""), [])

    def test_forced_and_commentary_do_not_count_sdh_does(self):
        rows = [
            {"language": "en", "forced": True},
            {"language": "zh-CN", "commentary": True},
            {"language": "en", "sdh": True},
            {"language": "zh-TW"},
        ]
        valid = subtitles.valid_full_subtitles(rows)
        self.assertIn("en", valid)
        self.assertNotIn("zh-CN", valid)
        self.assertIn("zh-TW", valid)

    def test_generic_chinese_tag_does_not_count_as_simplified(self):
        valid = subtitles.valid_full_subtitles([{"language": "chi", "forced": False}])
        self.assertIn("zh", valid)
        self.assertNotIn("zh-CN", valid)

    def test_standard_is_preferred_over_sdh(self):
        valid = subtitles.valid_full_subtitles([{"language": "en", "sdh": True}, {"language": "en", "sdh": False}])
        self.assertFalse(valid["en"][0]["sdh"])

    def test_ranking_rewards_exact_ids_release_year_srt(self):
        good = {"tmdb_id": 129, "imdb_id": "tt0245429", "year": 2001, "release_name": "Film.1080p", "file_name": "Film.srt", "format": "srt", "language": "en", "downloads": 100}
        bad = {"tmdb_id": 999, "year": 1999, "file_name": "Other.ass", "format": "ass", "language": "en", "sdh": True}
        self.assertGreater(subtitles.rank_candidate(good, sample_movie(), "Film.1080p.mkv", "en"), subtitles.rank_candidate(bad, sample_movie(), "Film.1080p.mkv", "en"))

    def test_archive_traversal_is_rejected(self):
        with self.assertRaisesRegex(subtitles.OrganizerError, "unsafe path"):
            subtitles.normalize_download(zip_bytes({"../escape.srt": ENGLISH_SRT}), "en", "x.zip")

    def test_archive_executable_is_rejected(self):
        with self.assertRaisesRegex(subtitles.OrganizerError, "executable"):
            subtitles.normalize_download(zip_bytes({"good.srt": ENGLISH_SRT, "run.exe": b"MZ"}), "en", "x.zip")

    def test_invalid_timestamp_is_rejected(self):
        with self.assertRaisesRegex(subtitles.OrganizerError, "timestamp"):
            subtitles.normalize_download(b"No timestamps here, only English words.", "en", "x.srt")

    def test_encoding_is_normalized_to_utf8(self):
        text = "1\n00:00:01,000 --> 00:00:02,000\nCaf\u00e9 is an English subtitle sentence.\n"
        data, extension = subtitles.normalize_download(text.encode("cp1252"), "en", "x.srt")
        self.assertEqual(extension, ".srt")
        self.assertIn("Caf\u00e9", data.decode("utf-8"))

    def test_traditional_chinese_does_not_satisfy_simplified(self):
        with self.assertRaisesRegex(subtitles.OrganizerError, "heuristics"):
            subtitles.normalize_download(TRADITIONAL_SRT, "zh-CN", "x.srt")
        normalized, _ = subtitles.normalize_download(CHINESE_SRT, "zh-CN", "x.srt")
        self.assertIn("这是", normalized.decode())

    def test_dry_run_searches_but_does_not_download_or_write(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); video = folder / "Film.mkv"; video.write_bytes(b"v")
            provider = FakeProvider()
            provider.fetch = mock.Mock(side_effect=AssertionError("dry-run downloaded"))
            result = subtitles.ensure_state(sample_state(folder, video, "en"), "dry-run", [provider])
            self.assertEqual(result["subtitles"]["missing_required"], ["en"])
            self.assertEqual(len(result["subtitles"]["planned_downloads"]), 1)
            provider.fetch.assert_not_called()
            self.assertEqual([p.name for p in folder.iterdir()], ["Film.mkv"])

    def test_apply_downloads_and_normalizes(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); video = folder / "Film.mkv"; video.write_bytes(b"v")
            result = subtitles.ensure_state(sample_state(folder, video, "en"), "apply", [FakeProvider()])
            target = Path(result["subtitles"]["downloaded"][0]["path"])
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), ENGLISH_SRT)

    def test_invalid_existing_external_does_not_count(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); video = folder / "Film.mkv"; video.write_bytes(b"v")
            invalid = folder / "Film.en.srt"; invalid.write_text("not a subtitle")
            state = sample_state(folder, video, "en")
            state["subtitles"]["external_existing"] = [{"path": str(invalid), "language": "en", "forced": False, "commentary": False, "source": "external"}]
            result = subtitles.ensure_state(state, "dry-run", [FakeProvider()])
            self.assertEqual(result["subtitles"]["missing_required"], ["en"])
            self.assertTrue(any("Ignored invalid external subtitle" in x for x in result["warnings"]))

    def test_opensubtitles_http_response_is_normalized(self):
        def request(url, headers, data=None):
            self.assertNotIn("secret", url)
            return {"data": [{"attributes": {
                "language": "en", "release": "Film.2001.1080p", "hearing_impaired": False,
                "download_count": 500, "feature_details": {"tmdb_id": 129, "imdb_id": 245429, "year": 2001},
                "files": [{"file_id": 7, "file_name": "Film.2001.srt"}],
            }}]}
        client = subtitles.OpenSubtitlesClient("secret", request_json=request)
        rows = client.search(sample_movie(), "Film.2001.1080p.mkv", "en")
        self.assertEqual(rows[0]["provider"], "OpenSubtitles")
        self.assertEqual(rows[0]["file_id"], 7)
        self.assertEqual(rows[0]["language"], "en")

    def test_invalid_primary_provider_download_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); video = folder / "Film.mkv"; video.write_bytes(b"v")
            first = FakeProvider(b"invalid", provider="OpenSubtitles")
            second = FakeProvider(ENGLISH_SRT, provider="SubDL")
            result = subtitles.ensure_state(sample_state(folder, video, "en"), "apply", [first, second])
            self.assertEqual(result["subtitles"]["downloaded"][0]["source"], "SubDL")

    def test_missing_provider_is_clear_error(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); video = folder / "Film.mkv"; video.write_bytes(b"v")
            with self.assertRaisesRegex(subtitles.OrganizerError, "no OPENSUBTITLES"):
                subtitles.ensure_state(sample_state(folder, video, "en"), "dry-run", [])


class MoveTests(unittest.TestCase):
    def test_all_routing_branches_and_precedence(self):
        cases = [
            ("en", ["HK", "US"], "hk"), ("en", ["TW"], "cn"), ("zh", ["CN"], "cn"),
            ("en", ["GB"], "en"), ("ja", ["US"], "asia/Japan"), ("ko", ["KR"], "asia/Korea"),
            ("th", ["TH"], "asia/South East Asia"), ("fr", ["FR"], "other"), ("", ["US"], None),
        ]
        for language, countries, expected in cases:
            with self.subTest(language=language, countries=countries):
                actual = move.route_relative(sample_movie(language, countries))
                self.assertEqual(str(actual) if actual else None, expected)

    def test_destination_conflict_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); folder = root / "source"; folder.mkdir(); video = folder / "Film.mkv"; video.write_bytes(b"v")
            state = sample_state(folder, video, "en")
            state["subtitles"].update({"required": ["en"], "satisfied_after_apply": ["en"]})
            destination = root / "library/en/source"; destination.mkdir(parents=True)
            with self.assertRaisesRegex(move.OrganizerError, "collision"):
                move.move_state(state, "dry-run", root / "library")

    def test_unknown_audio_requests_review_without_move(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "Film.mkv"; video.write_bytes(b"v")
            state = sample_state(root, video, "")
            result = move.move_state(state, "dry-run", root / "library")
            self.assertIsNone(result["proposal"]["destination"])
            self.assertTrue(root.exists())

    def test_cross_filesystem_copy_failure_keeps_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; source.mkdir(); (source / "movie.mkv").write_bytes(b"movie")
            destination = root / "library/movie"
            def cross_device(src, dst):
                raise OSError(errno.EXDEV, "cross-device")
            def broken_copy(src, dst, **kwargs):
                Path(dst).mkdir(); (Path(dst) / "partial").write_bytes(b"x"); raise OSError("copy failed")
            with self.assertRaisesRegex(OSError, "copy failed"):
                move.safe_move(source, destination, rename=cross_device, copytree=broken_copy)
            self.assertTrue((source / "movie.mkv").exists())
            self.assertFalse(destination.exists())
            self.assertFalse(list((root / "library").glob(".*movie-organizer*")))

    def test_cross_filesystem_success_verifies_then_removes_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; source.mkdir(); (source / "movie.mkv").write_bytes(b"movie")
            destination = root / "library/movie"
            def cross_device(src, dst):
                raise OSError(errno.EXDEV, "cross-device")
            move.safe_move(source, destination, rename=cross_device)
            self.assertFalse(source.exists())
            self.assertEqual((destination / "movie.mkv").read_bytes(), b"movie")

    def test_apply_verifies_nfo_and_subtitle_then_moves(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "Movie (2001)"; source.mkdir()
            video = source / "Movie (2001).mkv"; video.write_bytes(b"movie")
            (source / "Movie (2001).en.srt").write_bytes(ENGLISH_SRT)
            (source / "movie.nfo").write_text("<?xml version='1.0'?><movie><uniqueid type='tmdb'>129</uniqueid></movie>")
            state = sample_state(source, video, "en")
            state["subtitles"].update({"required": ["en"], "satisfied_after_apply": ["en"]})
            result = move.move_state(state, "apply", root / "library")
            self.assertTrue(Path(result["final_destination"]).is_dir())
            self.assertFalse(source.exists())


class FlowTests(unittest.TestCase):
    def test_representative_dry_run_is_read_only_and_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); folder = root / "raw"; folder.mkdir()
            video = folder / "Spirited.Away.2001.mkv"; video.write_bytes(b"video bytes")
            (folder / "Spirited.Away.2001.en.srt").write_bytes(ENGLISH_SRT)
            (folder / "Spirited.Away.2001.zh-CN.srt").write_bytes(CHINESE_SRT)
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            state = sample_state(folder, video, "ja")
            state = rename.rename_state(state, "dry-run")
            state = subtitles.ensure_state(state, "dry-run", [])
            state = move.move_state(state, "dry-run", root / "library")
            after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertIn("asia/Japan", state["proposal"]["destination"])
            self.assertEqual(state["proposal"]["missing_subtitles"], [])
            self.assertIn("movie_filename", state["proposal"])


if __name__ == "__main__":
    unittest.main()
