# Movie Organizer

Identify, rename, enrich with TMDB metadata + artwork, download subtitles, and route movies into a language-sorted library.

Works with **Hermes Agent**, **Codex CLI**, **Claude Code**, or any AI coding agent that can run Python scripts.

## Quick start

### 1. Install the skill

Copy the `movie-organizer/` directory into your agent's skills folder:

```bash
# For Hermes Agent
cp -r movie-organizer ~/.hermes/skills/media/

# For Codex CLI — add to your project's skills/ or reference in AGENTS.md
```

### 2. Configure

```bash
cp movie-organizer/.env.example movie-organizer/.env
```

Edit `.env` with your paths and API keys:

```env
MOVIE_INPUT=/mnt/lib3/waiting/movie--
MOVIE_OUTPUT=/mnt/lib3/media/movie
TMDB_API_KEY=your_key_here
```

### 3. Put a movie in the input folder

Drop a movie folder or a loose video file into `$MOVIE_INPUT`.

### 4. Ask your agent

```
I put a movie in the input folder, please organize it
```

The agent will:
1. Inspect the movie (ffprobe + filename)
2. Search TMDB and confirm with you
3. Show a dry-run preview
4. After your approval, run the full pipeline

## Pipeline

```
tmdb_nfo.py      →  Fetch TMDB metadata, ffprobe media info,
                     write movie.nfo, download poster + fanart
rename_media.py  →  Rename folder, video, subtitles, artwork
subtitles.py     →  Download missing required subtitles
move_movie.py    →  Verify completenss, move to language library
```

Scripts communicate via JSON state piped through stdin/stdout — no temp files, no database.

## Language routing

Movies are routed to subdirectories under `$MOVIE_OUTPUT`:

| Condition | Destination |
|---|---|
| Hong Kong production | `hk/` |
| Chinese/Taiwan primary audio | `cn/` |
| English primary audio | `en/` |
| Japanese | `asia/Japan/` |
| Korean | `asia/Korea/` |
| Southeast Asian | `asia/South East Asia/` |
| Other | `other/` |

## Requirements

- Python 3.10+
- `ffprobe` (from ffmpeg)
- TMDB API key ([get one free](https://www.themoviedb.org/settings/api))
- Optional: OpenSubtitles.com and/or SubDL API keys for subtitle download

## Credits

Built for personal media library management. Inspired by Kodi naming conventions and the need for safe, review-before-apply workflows.
