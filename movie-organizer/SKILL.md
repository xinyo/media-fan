---
name: movie-organizer
description: Identify and organize movies from `$MOVIE_INPUT` into `$MOVIE_OUTPUT` by language. Inspect a movie with TMDB + ffprobe, present the proposed changes, and execute the full pipeline on approval.
---

# Movie Organizer

Organize exactly one waiting movie at a time. Identify it, present the plan, and run the scripts only after explicit user approval.

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

- Make no filesystem changes before explicit user approval.
- Keep credentials in environment variables. Never print, persist, or place them in command arguments.
- Pass state between scripts via temp files (`/tmp/movie_state.json`). Do not use piped subshells — they truncate large JSON.
- Stop on the first failing script. Report its stderr, completed changes, current paths, and a safe recovery command. Do not move an incomplete movie.
- Ask before retrying a permission failure. Ask for review if the primary dialogue language is unknown.

## Inspect and identify

1. Select the next folder or loose video in `$MOVIE_INPUT`; never batch movies.
2. Inspect folder and filenames, existing NFO data, `ffprobe` streams and format tags, runtime, edition/source hints, and the first default non-commentary audio stream.
3. Search TMDB using likely title and release year. Compare localized, original, and English titles, year, runtime, original language, and production countries.
4. If multiple candidates remain plausible, show the evidence and ask the user to choose.
5. If the audio stream has no language tag (common with scene releases), infer it from the movie's production countries and original language. Present your inference to the user for confirmation.
6. Require `python3`, `ffprobe`, and `TMDB_API_KEY`.

## Present and approve

Present one complete proposal containing:

- confirmed TMDB title, original and English titles, year, and ID;
- primary dialogue language (with inference reasoning if untagged);
- production countries;
- proposed folder and movie filename;
- existing subtitle files found in the folder;
- required vs existing subtitle languages and how missing ones will be obtained;
- artwork replacements (poster, fanart, extrafanart, clearlogo);
- final routed destination; and
- all warnings.

Then ask: **"Should I execute these changes?"**

A prior TMDB ID confirmation is not apply approval. Wait for an explicit "yes" / "do it" / "go ahead".

## Execute

After approval, inject the audio language if it was inferred, then run each script in `--mode apply`, passing state through a temp file:

```bash
python3 scripts/tmdb_nfo.py --mode apply --folder "$FOLDER" --video "$VIDEO" --tmdb-id "$TMDB_ID" > /tmp/movie_state.json 2>/dev/null || exit
python3 -c "..." < /tmp/movie_state.json > /tmp/movie_state.json.tmp && mv /tmp/movie_state.json.tmp /tmp/movie_state.json  # inject language if needed
python3 scripts/rename_media.py --mode apply < /tmp/movie_state.json > /tmp/movie_state.json.tmp && mv /tmp/movie_state.json.tmp /tmp/movie_state.json || exit
python3 scripts/subtitles.py --mode apply < /tmp/movie_state.json > /tmp/movie_state.json.tmp && mv /tmp/movie_state.json.tmp /tmp/movie_state.json || exit
python3 scripts/move_movie.py --mode apply < /tmp/movie_state.json || exit
```

Do not rerun already completed apply steps blindly. Use the returned paths and filesystem inspection to resume safely. Report the final destination only after `move_movie.py` succeeds.

For a loose video (no parent folder), create a work folder first, copy the video and any matching subtitles into it, then run the pipeline above against that folder.

## Script guarantees

- `tmdb_nfo.py` fetches confirmed TMDB metadata in `zh-CN` with `en-US` fallback, inspects streams with `ffprobe`, validates XML, and atomically replaces supported TMDB artwork. TMDB supplies only posters, backdrops, and logos; do not invent unsupported art types.
- `rename_media.py` preflights every collision, preserves the video extension and subtitle language/forced/SDH tags, and leaves uncertain subtitles unchanged.
- `subtitles.py` requires a full English subtitle for English audio and full English plus Simplified Chinese for other audio. Forced/commentary tracks never satisfy a requirement; Traditional Chinese remains distinct. Search OpenSubtitles.com first, SubDL second, and SubHD (scraped) third as a last resort. When all three providers fail, see `references/subhd-subtitle-extraction.md` for manual SubHD download as a fallback.
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
`subtitles.py` reads `OPENSUBTITLES_API_KEY` (plural) and `SUBDL_API_KEY`. If you set `OPENSUBTITLE_API_KEY` (singular, no trailing S) in `.env`, the script **silently ignores it** — the key is never read and no error is raised. Always double-check the exact env var name matches what the script reads.

When neither key is configured, `subtitles.py` still tries **SubHD** (scraped, no API key needed) as a last resort. Only when SubHD also fails does it raise a hard error. If the user wants to skip subtitles entirely:
1. Rename any existing external subtitle file to match the video stem (e.g. `视频名.en.srt`)
2. Manually inject `subtitles.required = ["en"]` into the state to satisfy `move_movie.py`
3. Skip the `subtitles.py` step entirely

### SubHD scraped subtitles may have imprecise timestamps
SubHD's embedded format uses `[hh:mm:ss]` without milliseconds. The script converts these to `hh:mm:ss,000`, which means subtitle timing is accurate to the second, not millisecond. This is usually fine for playback but may feel slightly off for fast-paced dialogue.

### Foreign subtitle filenames won't auto-match
Subtitles named after the English title (e.g. `With.Or.Without.You.1992.DVDRip.srt`) won't be recognized when the video file uses the Chinese title. Rename them to `{video_stem}.{lang}.srt` before staging.

### Loose video needs a work folder
A loose (unfoldered) video must be staged into a sibling work folder before running the pipeline:
1. Create the work folder: `mkdir -p "{parent}/{final_folder_name}"`
2. Copy video + matched subtitles into it
3. Run the pipeline against the staged folder
4. After rename + move, the work folder is gone

## Reference files

(Reference docs with environment-specific paths and credentials are kept locally under `~/.hermes/skills/media/movie-organizer/references/` and are not included in the public repo.)