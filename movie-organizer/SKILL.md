---
name: movie-organizer
description: Safely identify, preview, rename, enrich, subtitle, and route movies from `$MOVIE_INPUT` into `$MOVIE_OUTPUT` by language. Use when a movie needs organizing — create Kodi-compatible TMDB metadata and artwork, normalize movie and subtitle names, obtain required English or Simplified Chinese subtitles, or move a completed movie into the language library after an explicitly approved dry run.
---

# Movie Organizer

Organize exactly one waiting movie at a time. Treat identification and approval as agent decisions; use the scripts only for deterministic work after confirming a TMDB ID.

## Configuration

Set these environment variables before running scripts (the agent reads them from `~/.hermes/.env`):

| Variable | Default | Purpose |
|---|---|---|
| `MOVIE_INPUT` | `/mnt/lib3/waiting/movie--` | Source directory for unorganized movies |
| `MOVIE_OUTPUT` | `/mnt/lib3/media/movie` | Root directory for organized movies (subdirs: en/, cn/, hk/, asia/, other/) |
| `TMDB_API_KEY` | — | TMDB API key (required — get at themoviedb.org/settings/api) |
| `OPENSUBTITLES_API_KEY` | — | Subtitle download (optional) |
| `SUBDL_API_KEY` | — | Fallback subtitle provider (optional) |

When unset, all scripts fall back to the defaults listed above. Only `TMDB_API_KEY` is strictly required.

## Safety contract

- Make no filesystem changes before explicit user approval of the complete dry-run proposal.
- Keep credentials in environment variables. Never print, persist, or place them in command arguments.
- Carry script state in a quoted shell variable or a direct stdin/stdout pipeline. Do not create a state file.
- Stop on the first failing script. Report its stderr, completed changes, current paths, and a safe recovery command. Do not move an incomplete movie.
- Ask before retrying a permission failure. Ask for review if the primary dialogue language is unknown.

## Inspect and identify

1. Select the next folder or loose video in `$MOVIE_INPUT`; never batch movies.
2. Inspect folder and filenames, existing NFO data, `ffprobe` streams and format tags, runtime, edition/source hints, and the first default non-commentary audio stream.
3. Search TMDB using likely title and release year. Compare localized, original, and English titles, year, runtime, original language, and production countries.
4. If multiple candidates remain plausible, show the evidence and ask the user to choose. Always show the final title, year, and TMDB ID and obtain confirmation before invoking scripts.
5. Require `python3`, `ffprobe`, and `TMDB_API_KEY`. Missing subtitle provider credentials are allowed only when no subtitle download is needed.

## Preview

For a folder movie, run the scripts from this skill directory with state held only in memory:

```bash
set -o pipefail
state="$(python3 scripts/tmdb_nfo.py --mode dry-run --folder "$folder" --video "$video" --tmdb-id "$tmdb_id")" || exit
state="$(printf '%s' "$state" | python3 scripts/rename_media.py --mode dry-run)" || exit
state="$(printf '%s' "$state" | python3 scripts/subtitles.py --mode dry-run)" || exit
state="$(printf '%s' "$state" | python3 scripts/move_movie.py --mode dry-run)" || exit
printf '%s\n' "$state"
```

For a loose video, pass `--loose` to `tmdb_nfo.py`. Dry-run paths model a sibling work folder without creating it. After approval, stage only the video and sidecars whose basenames clearly match it in that sibling folder; leave uncertain files untouched. Then rerun apply using the staged folder without `--loose`.

Present one complete approval request containing:

- confirmed TMDB title, original and English titles, year, and ID;
- primary dialogue language and production countries;
- proposed folder and movie filename;
- required, existing, and missing subtitle languages, selected providers, and uncertain subtitle files;
- poster, fanart/extrafanart, and clearlogo replacements;
- final routed destination; and
- all warnings.

Confirm that the user approves these exact changes. A prior TMDB confirmation is not apply approval.

## Apply

After approval, stage a loose movie as described above. Run each script separately, preserving JSON in memory so the failed step is unambiguous:

```bash
state="$(python3 scripts/tmdb_nfo.py --mode apply --folder "$folder" --video "$video" --tmdb-id "$tmdb_id")" || exit
state="$(printf '%s' "$state" | python3 scripts/rename_media.py --mode apply)" || exit
state="$(printf '%s' "$state" | python3 scripts/subtitles.py --mode apply)" || exit
state="$(printf '%s' "$state" | python3 scripts/move_movie.py --mode apply)" || exit
printf '%s\n' "$state"
```

Do not rerun already completed apply steps blindly. Use the returned paths and filesystem inspection to resume safely. Report the final destination only after `move_movie.py` succeeds.

## Script guarantees

- `tmdb_nfo.py` fetches confirmed TMDB metadata in `zh-CN` with `en-US` fallback, inspects streams with `ffprobe`, validates XML, and atomically replaces supported TMDB artwork. TMDB supplies only posters, backdrops, and logos; do not invent unsupported art types.
- `rename_media.py` preflights every collision, preserves the video extension and subtitle language/forced/SDH tags, and leaves uncertain subtitles unchanged.
- `subtitles.py` requires a full English subtitle for English audio and full English plus Simplified Chinese for other audio. Forced/commentary tracks never satisfy a requirement; Traditional Chinese remains distinct. Search OpenSubtitles.com first, SubDL second, and SubHD (scraped) third as a last resort. When all three providers fail, SubHD can be downloaded manually — see the local `~/.hermes/skills/media/movie-organizer/references/subhd-subtitle-extraction.md` reference.
- `move_movie.py` verifies the video, NFO, and required subtitles, refuses collisions, and keeps the source intact if a cross-filesystem copy fails.

## Pitfalls

### TMDB auth uses `TMDB_API_KEY` (query param), not Bearer token
The scripts were originally written for `TMDB_BEARER_TOKEN` (v4 JWT). They have been patched to use `?api_key=` query param auth with `TMDB_API_KEY`. Do not set `TMDB_BEARER_TOKEN`. The key lives in `~/.hermes/.env`.

### MKV files often lack audio language tags
Chinese-scene MKV files commonly have untagged audio streams (no `language` field in ffprobe). This causes `primary_audio_language` to be empty and blocks `move_movie.py`. Workaround: inject the correct language into the JSON state before passing to `subtitles.py` and `move_movie.py`:
```python
s["movie"]["primary_audio_language"] = "yue"  # or "cmn", "en", etc.
```

### Subtitle API keys may not be configured or may have wrong env var names
`subtitles.py` reads `OPENSUBTITLES_API_KEY` (plural) and `SUBDL_API_KEY`. If you set `OPENSUBTITLE_API_KEY` (singular, no trailing S) in `.env`, the script **silently ignores it** — the key is never read and no error is raised. Always double-check the exact env var name matches what the script reads (lines 406-409 of `scripts/subtitles.py`).

When neither key is configured, `subtitles.py` still tries **SubHD** (scraped, no API key needed) as a last resort. Only when SubHD also fails does it raise a hard error. If the user wants to skip subtitles entirely:
1. Rename any existing external subtitle file to match the video stem (e.g. `视频名.en.srt`)
2. Manually inject `subtitles.required = ["en"]` into the state to satisfy `move_movie.py`
3. Skip the `subtitles.py` step entirely

### SubHD scraped subtitles may have imprecise timestamps
SubHD's embedded format uses `[hh:mm:ss]` without milliseconds. The script converts these to `hh:mm:ss,000`, which means subtitle timing is accurate to the second, not millisecond. This is usually fine for playback but may feel slightly off for fast-paced dialogue.

### Foreign subtitle filenames won't auto-match
Subtitles named after the English title (e.g. `With.Or.Without.You.1992.DVDRip.srt`) won't be recognized when the video file uses the Chinese title. Rename them to `{video_stem}.{lang}.srt` before staging.

### Loose video workflow
A loose (unfoldered) video must be staged into a sibling work folder before apply:
1. Create the work folder: `mkdir -p "{parent}/{final_folder_name}"`
2. Copy video + matched subtitles into it
3. Run `tmdb_nfo.py` and `rename_media.py` on the staged folder **without** `--loose`
4. After rename, the work folder is ready for `move_movie.py`

## Reference files

(Reference docs with environment-specific paths and credentials are kept locally under `~/.hermes/skills/media/movie-organizer/references/` and are not included in the public repo.)