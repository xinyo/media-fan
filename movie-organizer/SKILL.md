---
name: movie-organizer
description: Safely identify, preview, rename, enrich, subtitle, and route movies from `$MOVIE_INPUT` into `$MOVIE_OUTPUT` by language.
---

# Movie Organizer

Organize exactly one waiting movie at a time. Treat identification and approval as agent decisions; use the scripts only for deterministic work after confirming a TMDB ID.

## Configuration

Set these environment variables (copy `.env.example` to `.env`):

| Variable | Default | Required |
|---|---|---|
| `MOVIE_INPUT` | `/mnt/lib3/waiting/movie--` | Path to unorganized movies |
| `MOVIE_OUTPUT` | `/mnt/lib3/media/movie` | Root for organized movie folders |
| `TMDB_API_KEY` | — | TMDB API key (get at themoviedb.org) |
| `OPENSUBTITLES_API_KEY` | — | For subtitle download (optional) |
| `SUB DL_API_KEY` | — | Fallback subtitle provider (optional) |

All scripts fall back to sensible defaults when variables are unset. Only `TMDB_API_KEY` is strictly required for metadata and artwork.

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
5. Require `python3`, `ffprobe`, and `TMDB_API_KEY`.

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
- `subtitles.py` requires a full English subtitle for English audio and full English plus Simplified Chinese for other audio. Forced/commentary tracks never satisfy a requirement; Traditional Chinese remains distinct. Search OpenSubtitles.com first and SubDL second.
- `move_movie.py` verifies the video, NFO, and required subtitles, refuses collisions, and keeps the source intact if a cross-filesystem copy fails.

