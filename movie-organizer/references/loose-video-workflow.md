# Loose Video Workflow — Step by Step

Use this when the source is a single loose `.mkv`/`.mp4` file (not inside a folder)
in `$MOVIE_INPUT` (default: `/mnt/lib3/waiting/movie--/`).

## 1. Identify

- Run `ffprobe` on the video to get codec, resolution, audio streams, duration
- Search TMDB using the filename as the title hint
- Confirm with user: correct TMDB ID, primary audio language

## 2. Rename External Subtitle (if any)

Subtitles with an English-titled filename won't auto-match a Chinese-named video.

```bash
mv "With.Or.Without.You.1992.DVDRip.srt" "明月照尖东.en.srt"
```

Recognized patterns: `{stem}.{lang}.srt`, `{stem}.{lang}.forced.srt`,
`{stem}.{lang}.sdh.srt` where lang is `en`, `zh-CN`, `zh-TW`.

## 3. Stage (dry-run first, then apply)

Determine the final folder name from `base_movie_name()`. Create a work folder:

```bash
mkdir -p "$MOVIE_INPUT/{final_folder_name}"
cp "$MOVIE_INPUT/{video}.mkv"  "$MOVIE_INPUT/{final_folder_name}/"
cp "$MOVIE_INPUT/{video}.en.srt" "$MOVIE_INPUT/{final_folder_name}/"
```

## 4. Run the Pipeline

### Dry-run
Run each script in sequence piping JSON state. Use the **staged folder** (no `--loose`):

```python
state = run_script("tmdb_nfo", "--mode", "dry-run", "--folder", WORK_FOLDER, "--video", VIDEO, "--tmdb-id", str(TMDB_ID))
state = run_script("rename_media", "--mode", "dry-run", stdin=state)
state = run_script("subtitles", "--mode", "dry-run", stdin=state)
state = run_script("move_movie", "--mode", "dry-run", stdin=state)
```

### Handle missing audio language
If `primary_audio_language` is empty (untagged MKV audio streams), inject it:

```python
s["movie"]["primary_audio_language"] = "yue"  # Cantonese
state = json.dumps(s, ensure_ascii=False)
```

### Handle missing subtitle API keys
If subtitles fails with "no API key configured" and user says skip:

```python
s["subtitles"] = s.get("subtitles", {})
s["subtitles"]["required"] = ["en"]
s["subtitles"]["existing"] = ["en"]
s["subtitles"]["missing"] = []
state = json.dumps(s, ensure_ascii=False)
```

### Apply
Same sequence with `--mode apply`. Present the final destination to the user.

## 5. Cleanup

After the movie is moved to `$MOVIE_OUTPUT/{route}/`, the work folder
is gone (moved atomically). Remove remaining source originals:

```bash
rm "$MOVIE_INPUT/{video}.mkv"
rm "$MOVIE_INPUT/{video}.en.srt"
```

## Concrete Example (明月照尖东, 1992)

```
Source:          $MOVIE_INPUT/明月照尖东.mkv
                 $MOVIE_INPUT/With.Or.Without.You.1992.DVDRip.srt
TMDB ID:         261196
Audio:           yue (Cantonese) — manually injected
Production:      HK
Destination:     $MOVIE_OUTPUT/hk/明月照尖东 明月照尖東 (1992)/
Final filename:  明月照尖东 明月照尖東 (1992) - 720p h264.mkv
Subtitle:        明月照尖东 明月照尖東 (1992) - 720p h264.en.srt
Artwork:         poster.jpg, fanart.jpg
NFO:             movie.nfo
```
