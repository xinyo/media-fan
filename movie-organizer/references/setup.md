# Movie Organizer — Setup & Reference

## Environment Variables

| Variable | Required | Default | Source | Used By |
|----------|----------|---------|--------|---------|
| `MOVIE_INPUT` | No | `/mnt/lib3/waiting/movie--` | `.env` | Agent reads this for source path |
| `MOVIE_OUTPUT` | No | `/mnt/lib3/media/movie` | `.env` | `move_movie.py` destination root |
| `TMDB_API_KEY` | Yes | — | `~/.hermes/.env` | `tmdb_nfo.py` |
| `OPENSUBTITLES_API_KEY` | Optional | — | `~/.hermes/.env` | `subtitles.py` (OpenSubtitles.com) |
| `OPENSUBTITLES_USERNAME` | Optional | — | `~/.hermes/.env` | `subtitles.py` (OpenSubtitles.com) |
| `OPENSUBTITLES_PASSWORD` | Optional | — | `~/.hermes/.env` | `subtitles.py` (OpenSubtitles.com) |
| `SUBDL_API_KEY` | Optional | — | `~/.hermes/.env` | `subtitles.py` (SubDL fallback) |

## TMDB Auth

The scripts use `TMDB_API_KEY` passed as `?api_key=` query parameter (v3 auth).
Do NOT set `TMDB_BEARER_TOKEN` — v4 Bearer tokens are not used.

The key is: `8df6a7ffd027e73c1a3ca7c564bc94d5` (from ~/.hermes/.env)

## Paths

| Role | Default Path | Configurable Via |
|------|-------------|-----------------|
| Source (waiting) | `/mnt/lib3/waiting/movie--/` | `$MOVIE_INPUT` env var |
| Destination root | `/mnt/lib3/media/movie/` | `$MOVIE_OUTPUT` env var |
| Skill scripts | `~/.hermes/skills/media/movie-organizer/scripts/` | — |

Both default paths are on the same CIFS mount (`//10.155.129.107/lib3` → `/mnt/lib3`),
so `os.rename()` is atomic (same filesystem). If you change `MOVIE_OUTPUT` to a
different filesystem, `move_movie.py` will fall back to copy+verify+delete.

## Routing Rules

From `move_movie.py` `route_relative()`:

| Condition | Subdirectory |
|-----------|-------------|
| `production_countries` includes `HK` | `hk/` |
| `production_countries` includes `TW` OR language is Chinese | `cn/` |
| Primary audio is `en` (English) | `en/` |
| Primary audio is `ja` (Japanese) | `asia/Japan/` |
| Primary audio is `ko` (Korean) | `asia/Korea/` |
| Primary audio is Southeast Asian (th, vi, id, ms, tl, km, my, lo) | `asia/South East Asia/` |
| Other known language | `other/` |
| Unknown language | Blocks move — requires user review |

## Subtitle Requirements

From `subtitles.py` `required_languages()`:

| Primary Audio | Required Subtitles |
|---------------|-------------------|
| English | `en` |
| Non-English (e.g. Cantonese, Japanese, Korean) | `en` + `zh-CN` |

## Subtitle Search Order

OpenSubtitles.com (via `OPENSUBTITLES_API_KEY`) → SubDL (via `SUBDL_API_KEY`) → SubHD (scraped, no key needed)

SubHD (subhd.tv) is a Chinese subtitle community with no public API. The `SubHDClient` scrapes the movie page HTML, extracts subtitle listings, and converts the embedded `data-content` format to standard SRT. It requires no API key and is always available as a last resort.

## Known Issues

- **MKV audio language tags often missing** on Chinese-scene releases. ffprobe returns
  empty language for audio streams. Manually inject the correct language into the
  JSON state before passing to `subtitles.py` / `move_movie.py`.
- **`OPENSUBTITLES_API_KEY` env var name mismatch**: The script reads `OPENSUBTITLES_API_KEY` (plural, with trailing S) but the `.env` may contain `OPENSUBTITLE_API_KEY` (singular, no S). If the key is set under the wrong name, subtitle downloads silently fall through to SubDL instead. Use `grep OPENSUBTITLE ~/.hermes/.env` to check both spellings.

- **No subtitle API keys configured** causes `subtitles.py` to raise a hard error.
  If the user agrees to skip subtitles, bypass the step and inject `required: ["en"]`
  into the state for `move_movie.py`.
- **External subtitle filenames in English** won't auto-match a Chinese-named video.
  The `_recognized_subtitles()` function checks `basename.startswith(video_stem)`.
  Rename to `{video_stem}.{lang}.srt` before staging.
