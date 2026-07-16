# Movie Organiser Skill — TODO

## Goal

Create one `movie-organizer` skill.

The skill identifies the correct movie, confirms the TMDB ID, shows a dry-run summary, waits for approval, and then runs four Python scripts.

The scripts do not identify the movie. They only perform deterministic work after the movie has been confirmed.

## Project structure

```text
movie-organizer/
├── SKILL.md
└── scripts/
    ├── tmdb_nfo.py
    ├── rename_media.py
    ├── subtitles.py
    └── move_movie.py
```

No CLI or package framework is required.

---

## Confirmed rules

- [x] Source: `/lib3/waiting/movie--`
- [x] Destination root: `/lib3/media/movie`
- [x] The AI identifies the movie.
- [x] Use the release year to distinguish movies with the same title.
- [x] Ask the user when identification is uncertain.
- [x] Confirm the TMDB ID before running scripts.
- [x] Fetch TMDB metadata using `zh-CN`.
- [x] Show a dry-run before making changes.
- [x] Require user approval after the dry-run.
- [x] Ask the user when a permission problem occurs.
- [x] Existing recognised artwork is replaced.
- [x] Replace artwork only after a valid replacement downloads successfully.
- [x] Taiwan movies go to `/cn`.
- [x] Hong Kong movies go to `/hk`.
- [x] English-language movies go to `/en`, including non-US English movies.
- [x] Co-productions are routed using the primary dialogue audio language.
- [x] English-language movies require a full English subtitle.
- [x] Non-English-language movies require full English and Simplified Chinese subtitles.
- [x] OpenSubtitles.com is the primary subtitle source.
- [x] SubDL is the fallback subtitle source.
- [x] Correctly tagged embedded subtitles count.
- [x] Prefer `.srt`.
- [x] Include up to 20 actors in `movie.nfo`.

---

# 1. `SKILL.md`

## Identify the movie

- [ ] Find a movie folder or loose movie file in `/lib3/waiting/movie--`.
- [ ] Inspect:
  - [ ] Folder name
  - [ ] Movie filename
  - [ ] Existing NFO
  - [ ] Embedded title and year
  - [ ] Runtime
  - [ ] Primary audio language
  - [ ] Edition information
- [ ] Search TMDB using the likely title and release year.
- [ ] Compare:
  - [ ] Localised title
  - [ ] Original title
  - [ ] English title
  - [ ] Release year
  - [ ] Runtime
  - [ ] Original language
  - [ ] Production country
- [ ] Select the correct movie.
- [ ] Ask the user when multiple candidates remain plausible.
- [ ] Confirm the final TMDB ID.

## Dry-run

- [ ] Show:
  - [ ] Identified movie and TMDB ID
  - [ ] Title, original title, English title and year
  - [ ] Primary audio language
  - [ ] Proposed folder name
  - [ ] Proposed movie filename
  - [ ] Required, existing and missing subtitle languages
  - [ ] Artwork that will be replaced
  - [ ] Proposed destination
- [ ] Ask for explicit approval.
- [ ] Do not perform write operations before approval.

## Run the scripts

After approval:

1. [ ] Run `tmdb_nfo.py`
2. [ ] Run `rename_media.py`
3. [ ] Run `subtitles.py`
4. [ ] Run `move_movie.py`

- [ ] Stop when a script fails.
- [ ] Report the failed step and error.
- [ ] Ask the user when permission is denied.
- [ ] Report the final destination after completion.

---

# 2. `tmdb_nfo.py`

## Purpose

Fetch the confirmed movie from TMDB, inspect the media, download artwork, and generate `movie.nfo`.

## Inputs

- [ ] Movie folder path
- [ ] Main video file path
- [ ] Confirmed TMDB ID
- [ ] `TMDB_BEARER_TOKEN`

## Metadata

- [ ] Fetch movie details using `zh-CN`.
- [ ] Use `en-US` when a required Chinese field is missing.
- [ ] Fetch:
  - [ ] Title
  - [ ] Original title
  - [ ] English title
  - [ ] Tagline
  - [ ] Plot
  - [ ] Year
  - [ ] Release date
  - [ ] Rating
  - [ ] Top 250, when available
  - [ ] Runtime
  - [ ] Certification
  - [ ] Genres
  - [ ] Language
  - [ ] Country
  - [ ] Studios
  - [ ] Tags
  - [ ] Movie set
  - [ ] Trailer
  - [ ] Actors
  - [ ] Crew
- [ ] Limit actors to 20.
- [ ] Include directors, writers, producers and composers when available.

## Media inspection

- [ ] Use `ffprobe`.
- [ ] Read:
  - [ ] Resolution
  - [ ] Video codec
  - [ ] HDR type
  - [ ] Runtime
  - [ ] Audio streams
  - [ ] Primary audio language
  - [ ] Embedded subtitle streams and languages
- [ ] Normalise:
  - [ ] AVC → `h264`
  - [ ] HEVC → `h265`
  - [ ] AV1 → `av1`
  - [ ] UHD → `4K`
  - [ ] Full HD → `1080p`
  - [ ] HD → `720p`
  - [ ] Dolby Vision → `dv`
  - [ ] HDR10 → `hdr10`
  - [ ] HDR10+ → `hdr10plus`
  - [ ] HLG → `hlg`

## Artwork

- [ ] Download one poster.
- [ ] Poster preference:
  1. Original movie language
  2. Simplified Chinese
  3. Language-neutral
  4. Highest-rated available
- [ ] Download one main fanart image.
- [ ] Download up to four extra fanart images.
- [ ] Download when available:
  - [ ] Banner
  - [ ] Clearart
  - [ ] Thumb
  - [ ] Clearlogo
  - [ ] Disc art
  - [ ] Keyart
- [ ] Save as:

```text
poster.{ext}
fanart.{ext}
banner.{ext}
clearart.{ext}
thumb.{ext}
clearlogo.{ext}
discart.{ext}
keyart.{ext}
extrafanart/fanart1.{ext}
extrafanart/fanart2.{ext}
extrafanart/fanart3.{ext}
extrafanart/fanart4.{ext}
```

- [ ] Download replacements to temporary files.
- [ ] Validate images before replacing existing artwork.
- [ ] Ignore unavailable optional artwork.

## NFO

- [ ] Generate UTF-8 `movie.nfo`.
- [ ] Include:
  - [ ] Title
  - [ ] Original title
  - [ ] English title
  - [ ] Tagline
  - [ ] Plot
  - [ ] Year
  - [ ] Release date
  - [ ] Rating
  - [ ] Top 250
  - [ ] Runtime
  - [ ] Certification
  - [ ] Genres
  - [ ] Language
  - [ ] Country
  - [ ] Studios
  - [ ] Tags
  - [ ] Movie set
  - [ ] Trailer
  - [ ] Actors
  - [ ] Crew
  - [ ] Artwork references
  - [ ] TMDB ID
  - [ ] IMDb ID
  - [ ] Video details
  - [ ] Audio details
  - [ ] Subtitle details
  - [ ] Resolution
  - [ ] Codec
  - [ ] HDR
  - [ ] Edition, when known
  - [ ] Media source, when known
- [ ] Validate the XML before saving.
- [ ] Return a shared movie metadata dictionary.

Suggested function:

```python
def fetch_movie_and_create_nfo(
    movie_folder: str,
    video_file: str,
    tmdb_id: int,
) -> dict:
    ...
```

---

# 3. `rename_media.py`

## Purpose

Rename the movie folder, main video, existing subtitles and artwork.

## Folder name

```text
{title} {originalTitle} ({year})
```

- [ ] Omit `originalTitle` when it matches `title`.
- [ ] Remove filesystem-unsafe characters.
- [ ] Remove duplicate spaces.

## Movie filename

```text
{title} {originalTitle} ({year}) - {edition} {resolution} {videoCodec} {mediaSource} {hdr}.{extension}
```

- [ ] Omit unknown values.
- [ ] Omit `originalTitle` when it matches `title`.
- [ ] Omit `-` when no optional technical values remain.
- [ ] Preserve the original video extension.
- [ ] Do not transcode.

Examples:

```text
Inception (2010) - 1080p h264 bluray.mkv
Blade Runner (1982) - Final Cut 4K h265 bluray hdr10.mkv
千与千寻 千と千尋の神隠し (2001) - 1080p h264 bluray.mkv
```

## Existing subtitles

- [ ] Rename using the final movie filename stem.
- [ ] Preserve `.en`, `.zh-CN` and `.zh-TW`.
- [ ] Preserve `.forced` and `.sdh`.
- [ ] Leave uncertain subtitles unchanged and report them.

## Artwork

- [ ] Ensure artwork uses the required filenames.
- [ ] Preserve the correct extension.

Suggested function:

```python
def rename_movie_files(
    movie_folder: str,
    movie: dict,
) -> dict:
    ...
```

---

# 4. `subtitles.py`

## Purpose

Check existing subtitles and download missing required languages.

## Credentials

```text
OPENSUBTITLES_API_KEY
OPENSUBTITLES_USERNAME
OPENSUBTITLES_PASSWORD
SUBDL_API_KEY
```

## Requirements

- [ ] English primary audio requires a full English subtitle.
- [ ] Non-English primary audio requires:
  - [ ] Full English subtitle
  - [ ] Full Simplified Chinese subtitle

## Existing subtitles

- [ ] Check embedded and external subtitles.
- [ ] Accept correctly tagged embedded subtitles.
- [ ] Forced-only subtitles do not count.
- [ ] Commentary subtitles do not count.
- [ ] Prefer standard subtitles over SDH.
- [ ] Use SDH only when no standard subtitle is available.
- [ ] Do not download duplicate languages.

## Search order

For each missing language:

1. [ ] Search OpenSubtitles.com.
2. [ ] Search using:
   - [ ] IMDb ID
   - [ ] TMDB ID
   - [ ] Original release filename
   - [ ] Title and exact year
3. [ ] Search SubDL when OpenSubtitles has no suitable result.
4. [ ] Stop and report when no reliable subtitle is found.

## Validation

- [ ] Prefer `.srt`.
- [ ] Allow `.ass`, `.ssa`
- [ ] Confirm:
  - [ ] Timestamps are valid
  - [ ] Language is correct
- [ ] Convert text subtitles to UTF-8 when needed.
- [ ] Safely extract archives.
- [ ] Never execute downloaded files.

## Naming

```text
{movieStem}.en.srt
{movieStem}.zh-CN.srt
{movieStem}.en.sdh.srt
{movieStem}.zh-CN.sdh.srt
```

- [ ] A bilingual Simplified Chinese-English subtitle may satisfy the Chinese requirement when it contains complete Simplified Chinese lines.
- [ ] Traditional Chinese does not satisfy the Simplified Chinese requirement unless conversion is explicitly enabled.

Suggested function:

```python
def ensure_required_subtitles(
    movie_folder: str,
    video_file: str,
    movie: dict,
) -> dict:
    ...
```

---

# 5. `move_movie.py`

## Purpose

Move the completed movie folder to the correct library location.

## Routing

```text
Hong Kong production      → /lib3/media/movie/hk
Chinese primary audio     → /lib3/media/movie/cn
Taiwan movie              → /lib3/media/movie/cn
English primary audio     → /lib3/media/movie/en
Japanese primary audio    → /lib3/media/movie/asia/Japan
Korean primary audio      → /lib3/media/movie/asia/Korea
Southeast Asian language  → /lib3/media/movie/asia/South East Aisa
Other language            → /lib3/media/movie/other
```

## Rules

- [ ] Use the primary dialogue audio language.
- [ ] Ignore commentary tracks.
- [ ] Ask for review when the primary language cannot be determined.

## Checks

- [ ] Confirm all required subtitles are present.
- [ ] Confirm `movie.nfo` exists.
- [ ] Confirm the main movie file exists.
- [ ] Check whether the destination folder already exists.
- [ ] Never overwrite an existing destination.
- [ ] Move the complete folder.
- [ ] For cross-filesystem moves, copy first and remove the source only after success.
- [ ] Return the final destination path.

Suggested function:

```python
def move_movie_to_library(
    movie_folder: str,
    movie: dict,
    destination_root: str = "/lib3/media/movie",
) -> str:
    ...
```

---

# Shared movie data

```python
movie = {
    "tmdb_id": 129,
    "imdb_id": "tt0245429",
    "title": "千与千寻",
    "original_title": "千と千尋の神隠し",
    "english_title": "Spirited Away",
    "year": 2001,
    "primary_audio_language": "ja",
    "production_countries": ["JP"],
    "edition": None,
    "resolution": "1080p",
    "video_codec": "h264",
    "media_source": "bluray",
    "hdr": None,
}
```

---

# Final workflow

- [ ] Find the next movie in `/lib3/waiting/movie--`.
- [ ] Inspect the movie.
- [ ] Identify the correct TMDB movie.
- [ ] Ask the user when uncertain.
- [ ] Confirm the TMDB ID.
- [ ] Show the dry-run.
- [ ] Receive approval.
- [ ] Run `tmdb_nfo.py`.
- [ ] Run `rename_media.py`.
- [ ] Run `subtitles.py`.
- [ ] Run `move_movie.py`.
- [ ] Report the final destination.
