# hinotes-exporter

Export your notes from [hinotes.hidock.com](https://hinotes.hidock.com) — both the AI summary and the full transcript — to local Markdown files, preserving your folder structure.

## How it works

- On first run, a browser window opens so you can log in and solve the CAPTCHA manually
- The access token is saved to `~/.hinotes_exporter/session.json` and reused on every subsequent run
- Notes are exported using the same Markdown export endpoint the website uses natively
- Each note is exported twice: the summary (`scope=summary`) and the transcript (`scope=transcription`)
- A `.manifest.json` file tracks the summary and transcript of each note separately — re-runs only fetch what's missing, so notes exported before transcript support get their transcripts backfilled

## Requirements

- Python 3.10+
- `pip install requests playwright`
- `playwright install chromium`

Or install from the requirements file:

```bash
pip install -r requirements-hinotes.txt
playwright install chromium
```

## Usage

**Export all notes:**
```bash
python hinotes_export.py --output ~/Downloads/hinotes
```

**Export a single folder:**
```bash
python hinotes_export.py --output ~/Downloads/hinotes --folder 1on1s
```

**Re-download everything (ignore already-downloaded notes):**
```bash
python hinotes_export.py --output ~/Downloads/hinotes --force
```

**Also export notes not assigned to any folder:**
```bash
python hinotes_export.py --output ~/Downloads/hinotes --include-unfiled
```

**Adjust timezone (default is UTC-7 / Pacific):**
```bash
python hinotes_export.py --output ~/Downloads/hinotes --tz-offset 0
```

## All options

| Flag | Default | Description |
|------|---------|-------------|
| `--output`, `-o` | *(required)* | Directory to save notes into |
| `--folder` | all folders | Only export notes from this folder (case-insensitive) |
| `--force`, `-f` | false | Re-download notes that were already saved |
| `--include-unfiled` | false | Also export notes with no folder (saved under `_unfiled/`) |
| `--tz-offset` | `420` | Timezone offset in minutes from UTC (420 = UTC-7) |
| `--delay` | `0.3` | Seconds between downloads to avoid rate-limiting |

## First run walkthrough

1. Run the script with your desired `--output` path
2. A Chromium browser window opens at the HiNotes login page
3. Log in with your email and password and complete the CAPTCHA
4. The window closes automatically once login succeeds
5. The script fetches your folders and starts downloading

On all future runs the saved token is used and no browser is needed. If the token expires, the browser will open again automatically.

## Notes

- Each note is saved as a `.md` file named after the note title (as provided by the server, which includes the date prefix, e.g. `2026-04-23 ETD Technical Sync.md`), with its transcript next to it as `<same name>.transcript.md`
- Notes with no transcript on the server are recorded as such in the manifest and not retried (use `--force` to retry)
- The `.manifest.json` in your output directory is updated after every note, so an interrupted run can be safely resumed by re-running the same command
- The script does not modify or delete any data on HiNotes — it is read-only
