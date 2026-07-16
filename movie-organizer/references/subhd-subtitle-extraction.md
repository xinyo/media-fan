# SubHD Subtitle Extraction

SubHD (subhd.tv / subhd.me) is a Chinese subtitle site that hosts subtitles for movies and TV shows. Unlike OpenSubtitles or SubDL, SubHD does not provide a public API — subtitles are embedded directly in the HTML page.

## How SubHD Stores Subtitles

SubHD embeds the full subtitle text in a `data-content` attribute on the subtitle detail page (`/a/{code}`). The attribute sits inside a `<div>` or `<span>` element near the bottom of the page.

## Custom Data Format

The embedded format is **not** standard SRT/ASS. SubHD uses one of two variants:

**Variant A (with line numbers):**
```
[00:11:21]
253|你能对着我再说一遍吗？
254|Can you say that to me？
255|
256|[00:11:23]
257|- 对着你 对着芭芭拉？ - 对
258|- Uh, what, to-to Barbara? - Yeah.
```

**Variant B (bare, no line numbers):**
```
[00:11:21]
你能对着我再说一遍吗？
Can you say that to me?

[00:11:23]
- 对着你 对着芭芭拉？ - 对
- Uh, what, to-to Barbara? - Yeah.
```

Pattern:
- **Timestamp lines**: `[hh:mm:ss]` — standalone or prefixed with a line number like `256|[00:11:23]`
- **Text lines (Variant A)**: `number|text` where `number` is an internal line counter and `text` is the actual subtitle text
- **Text lines (Variant B)**: plain text, no line number prefix
- **Empty separators**: blank lines or `number|` with nothing after the pipe
- **HTML entities**: Text may contain HTML entities like `&#39;` (apostrophe) that need unescaping
- **Bilingual**: Chinese and English lines alternate under each timestamp

## Extraction Recipe

```bash
# 1. Fetch the subtitle detail page (use the /a/{code} URL from the movie's subtitle listing)
curl -sL -o /tmp/subhd_detail.html "https://subhd.tv/a/tSOfPy" \
  -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

# 2. Extract the data-content attribute
grep -oP 'data-content="\K[^"]*' /tmp/subhd_detail.html > /tmp/raw_sub.txt
```

## Python Conversion to SRT

```python
import html, re

with open('/tmp/raw_sub.txt') as f:
    raw = html.unescape(f.read())

lines = raw.strip().split('\n')
entries = []
current_ts = None
current_lines = []

for line in lines:
    line = line.strip()
    if not line:
        continue
    # Match timestamp (with or without line-number prefix)
    ts_match = re.match(r'(?:\d+\|)?\[(\d{2}:\d{2}:\d{2})\].*', line)
    if ts_match:
        if current_ts and current_lines:
            entries.append((current_ts, current_lines))
        current_ts = ts_match.group(1)
        current_lines = []
        continue
    # Match data line
    data_match = re.match(r'\d+\|(.+)', line)
    if data_match:
        content = data_match.group(1).strip()
        if content:
            current_lines.append(content)

if current_ts and current_lines:
    entries.append((current_ts, current_lines))

def sec2ts(s):
    h, m, sec = s//3600, (s%3600)//60, s%60
    return f"{h:02d}:{m:02d}:{sec:02d},000"

def ts2sec(ts):
    h,m,s = map(int, ts.split(':'))
    return h*3600+m*60+s

srt = []
for i, (ts, lines) in enumerate(entries):
    start = ts2sec(ts)
    end = ts2sec(entries[i+1][0]) if i+1 < len(entries) else start+3
    if end <= start: end = start+3
    srt.append(f"{i+1}\n{sec2ts(start)} --> {sec2ts(end)}\n{chr(10).join(lines)}\n")

with open('/tmp/subtitle.srt', 'w') as f:
    f.write(''.join(srt))
```

## When to Use SubHD

SubHD is useful when:
- OpenSubtitles and SubDL don't have the subtitle
- The movie is Chinese-language and SubHD has better coverage
- The movie is very recent (SubHD often gets community-translated subtitles quickly)
- You need bilingual (Chinese + English) subtitles

Since July 2026, the `subtitles.py` script includes a `SubHDClient` that automates the
entire flow below (search → fetch → convert). Use the extraction recipe and Python
conversion steps above **only** when the script's SubHD scrape fails — typically because
the site layout changed or the movie wasn't found by search.

## Script Integration Notes

### ID system
SubHD uses **Douban subject IDs** for its movie detail page URLs (`/d/{douban_id}`), not TMDB IDs.
The coincidence of `36235977` being the same on both TMDB and Douban for Backrooms (2026) was
accidental. The script searches by title + year and resolves the correct movie page from results.

### Search strategy
The `SubHDClient.search()` method:
1. Builds a query from the Chinese TMDB title + year (falls back to English title)
2. Fetches `subhd.tv/search/{query}` 
3. Finds the correct movie page by locating the poster `<img>` link inside a `.pics` div
   (this avoids sidebar "popular" widgets that also contain `/d/{id}` links for unrelated titles)
4. Fetches the movie detail page and extracts all subtitle entries
5. IMDb ID search (`subhd.tv/search/tt26657236`) does **not** work — SubHD does not index by IMDb ID

### HTML parsing fragility
The regex-based HTML parsing targets these patterns:
- Movie ID: `<a href='/d/{id}'> <div class="pics"><img src="...poster/...">`
- Subtitle code: `<a class="link-dark" href="/a/{code}">release name</a>`
- Language tags: `<span class="p-1 fw-bold">简体</span>`
- Format: `<span class="p-1 text-secondary">SRT</span>`
- Download count: `<div class="px-3 py-2 text-end text-secondary">{n}</div>`

If SubHD changes any of these CSS classes or DOM structure, the search or parsing will fail
gracefully (return empty candidates) and fall through to the manual steps above.

### Two data-content variants
The `_convert_to_srt()` method auto-detects which format the subtitle uses:
- **Variant A** (with line numbers like `253|text`) — detected by scanning for `\d+\|` pattern
- **Variant B** (bare text) — plain text lines between timestamp headers

Both convert to standard SRT with `hh:mm:ss,000` timestamps. SubHD's native format lacks
milliseconds, so all converted subtitles have `,000` — timing is second-accurate only.

## Limitations

- No API — must scrape HTML
- Requires a movie-specific URL (search subhd.tv first)
- The embedded format lacks milliseconds (timestamps are `[hh:mm:ss]` only)
- Some subtitles are marked as "in progress" (渐进式发布) — check the description
- SubHD may block requests without a proper User-Agent
- The site has alternative domains: subhd.me, subhd.one, subhdtw.com
