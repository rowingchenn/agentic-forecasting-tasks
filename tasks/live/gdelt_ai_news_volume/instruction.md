Forecast how many news articles about artificial intelligence GDELT will list in its daily
event files for each of the next seven days. The Task runs every day and asks for the seven
UTC days after the run date; forecasts are due at 10:00 UTC on the run date. You have about
90 minutes.

## The target

The GDELT Project (https://www.gdeltproject.org/) monitors news media worldwide and codes the
events reported in each article. GDELT 1.0 publishes one event file for each UTC date,
`https://data.gdeltproject.org/events/YYYYMMDD.export.CSV.zip` with YYYYMMDD the date, listed
at https://data.gdeltproject.org/events/index.html. The zip holds one tab-separated text file
with one event per line; the 58th and last field of a line, SOURCEURL, is the web address of
the article the event was found in. A date's file holds the events GDELT added on that date;
GDELT 1.0 leaves out the news GDELT translates from other languages.

For each target date, the target is the number of distinct SOURCEURL values in the date's file
that are about artificial intelligence by this rule. Put the letters A to Z of the URL in lower
case, cut it at its first `?` or `#`, and remove the scheme and host: everything before the
first `/` that follows `://` (in a URL without `://`, everything before its first `/`); if no
`/` follows, nothing remains and the URL does not count. Split what remains at every run of
characters that are not a letter a to z or a digit 0 to 9, so that no piece is empty. The URL
counts if one of the pieces is `ai`, or a piece `artificial` is directly followed by a piece
`intelligence`. Lines that do not have exactly 58 fields are skipped, and a URL on several
lines counts once. For example, `https://example.com/tech/ai-chip-startup-raises-funds`,
`https://example.com/2026/11/06/artificial-intelligence-and-jobs/`,
`https://example.com/technology/ai/articles/12345.html` and
`https://example.com/artificial--intelligence-act` count. These do not:
`https://www.example.ai/markets` and `https://example.com/news?topic=ai` (the word is only in
the host or the query), `https://example.com/openai-releases-model` and
`https://example.com/he-said-yes` (no piece is exactly `ai`), and
`https://example.com/artificial-intelligences` (`intelligences` is not `intelligence`).

The target is thus the number of articles that GDELT coded at least one event from, as its
daily file lists them, not every article about AI published that day; how many articles GDELT
processes in a day moves it too. The rule reads only the web address: of 150 counted URLs read
one by one, 147 were about AI, and it also counts, for example, the artist Ai Weiwei and Air
India flight numbers such as AI 171.

A forecast is settled on the first file GDELT publishes for the date, normally at 07:00 UTC
the next day; GDELT has not changed its files after posting them. A date whose file is
missing, cannot be read, or has no line of 58 fields is not settled, never taken as zero:
GDELT posted no files for 2025-06-14 to 2025-07-01. A date still without a file more than 14
days later, once a later date's file has been read, is treated as having none, and a file
posted after that is not used. The run
date's own file comes out the morning after it and is not forecast; when the Task starts, the
newest file is the one for the day before the run date.

## Inputs and environment

`/home/user/input/task.json` gives the run date, the target dates (the run date plus
[1, 2, 3, 4, 5, 6, 7] days), the submission cutoff (10:00 UTC on the run date), and the
quantile levels [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].

You have unrestricted internet access. Apart from task.json, no data is provided: find the information you need yourself, using any sources, tools, software, or models.

## Output

Write `/home/user/output/forecast.json` in this form:

```text
{
  "run_date": "<run_date from task.json>",
  "forecasts": [
    {"target_date": "YYYY-MM-DD",
     "quantiles": {"0.1": <articles>, "0.2": <articles>, "0.3": <articles>,
                   "0.4": <articles>, "0.5": <articles>, "0.6": <articles>,
                   "0.7": <articles>, "0.8": <articles>, "0.9": <articles>}},
    ...
  ]
}
```

- Include each date in `target_dates` exactly once.
- `quantiles` uses exactly the levels in `quantile_levels`, written as shown ("0.1"
  through "0.9"). Values are non-negative numbers of articles and must not decrease as the
  level increases.

Each forecast is scored after GDELT publishes the date's file, with the continuous ranked
probability score (CRPS) of its quantiles, so both the center and the spread matter.

## Collected materials

Save copies of the external materials you found useful for this forecast, such as data you downloaded, articles, reports, or forecasts by others, as regular files (not symbolic links) under `/home/user/output/collected/`. Save only what you obtained from outside this environment, not your own code, analyses, or forecasts. List each saved file in `/home/user/output/collected/index.jsonl`, one JSON object per line: `{"path": "<path relative to collected/>", "source": "<URL you obtained it from>", "note": "<why it is useful>"}`. Write an empty `index.jsonl` if you saved nothing.
