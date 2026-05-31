#!/usr/bin/env python3
"""
Transcribe all audio/video files across Epstein datasets using faster-whisper on GPU.

Handles: mp4, avi, m4a, wav, mov, mp3, m4v, wmv, vob, opus
Outputs: transcripts.db (SQLite) with full text + timestamped segments
Skips files already transcribed (incremental).
Detects silence/noise-only files quickly and skips them.

Usage:
    python3 tools/transcribe_media.py                    # all datasets
    python3 tools/transcribe_media.py --datasets 9 10    # specific datasets
    python3 tools/transcribe_media.py --max-duration 300  # skip files > 5 min
"""

import argparse
import glob
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import json

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _find_data_dir():
    """Find the directory containing the database files."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        return os.environ["EPSTEIN_DATA_DIR"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(repo_root, "transcripts.db")):
        return repo_root
    if os.path.exists(os.path.join(os.getcwd(), "transcripts.db")):
        return os.getcwd()
    parent = os.path.dirname(os.getcwd())
    for name in os.listdir(parent):
        candidate = os.path.join(parent, name, "transcripts.db")
        if os.path.exists(candidate):
            return os.path.join(parent, name)
    return os.getcwd()

BASE_DIR = _find_data_dir()
DB_PATH = os.path.join(BASE_DIR, "transcripts.db")
LOG_PATH = os.path.join(BASE_DIR, "transcription.log")

MEDIA_EXTENSIONS = {".mp4", ".avi", ".m4a", ".wav", ".mov", ".mp3", ".m4v", ".wmv", ".vob", ".opus", ".ts"}
EFTA_RE = re.compile(r"(EFTA\d{8,})")

# Skip transcription for files where Whisper produces only silence/noise
SILENCE_PATTERNS = {"you", "you you", "...", "", "Thank you."}
MIN_MEANINGFUL_WORDS = 5  # transcript needs at least this many words to be kept

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="a"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("transcribe")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transcripts (
            id INTEGER PRIMARY KEY,
            efta_number TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT,
            duration_secs REAL,
            language TEXT,
            language_prob REAL,
            transcript TEXT,
            word_count INTEGER,
            dataset_source TEXT,
            transcribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(efta_number)
        );
        CREATE INDEX IF NOT EXISTS idx_tr_efta ON transcripts(efta_number);
        CREATE INDEX IF NOT EXISTS idx_tr_dataset ON transcripts(dataset_source);

        CREATE TABLE IF NOT EXISTS transcript_segments (
            id INTEGER PRIMARY KEY,
            efta_number TEXT NOT NULL,
            segment_id INTEGER,
            start_time REAL,
            end_time REAL,
            text TEXT,
            FOREIGN KEY (efta_number) REFERENCES transcripts(efta_number)
        );
        CREATE INDEX IF NOT EXISTS idx_seg_efta ON transcript_segments(efta_number);

        CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
            efta_number,
            transcript,
            content=transcripts,
            content_rowid=id
        );
    """)
    conn.commit()
    return conn


def get_already_transcribed(conn: sqlite3.Connection) -> set:
    cursor = conn.execute("SELECT efta_number FROM transcripts")
    return {row[0] for row in cursor.fetchall()}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
def find_media_files(datasets: list[int] | None = None) -> list[tuple[str, str, str]]:
    """Returns list of (file_path, efta_number, dataset_label)."""
    results = []

    if datasets:
        dirs = []
        for ds in datasets:
            # Check multiple possible directory names
            for pattern in [f"dataset{ds}", f"dataset{ds}_full"]:
                d = os.path.join(BASE_DIR, "datasets", pattern)
                if os.path.isdir(d):
                    dirs.append((d, f"ds{ds}"))
    else:
        dirs = []
        for d in sorted(glob.glob(os.path.join(BASE_DIR, "datasets", "dataset*"))):
            if os.path.isdir(d) and not d.endswith("_old_partial"):
                ds_name = os.path.basename(d)
                ds_num = re.search(r'\d+', ds_name)
                label = f"ds{ds_num.group()}" if ds_num else ds_name
                dirs.append((d, label))

    for ds_dir, ds_label in dirs:
        for root, _, files in os.walk(ds_dir):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in MEDIA_EXTENSIONS:
                    fpath = os.path.join(root, fname)
                    m = EFTA_RE.search(fname)
                    efta = m.group(1) if m else os.path.splitext(fname)[0]
                    results.append((fpath, efta, ds_label))

    return sorted(set(results), key=lambda x: x[1])


# ---------------------------------------------------------------------------
# Duration check via ffprobe
# ---------------------------------------------------------------------------
def get_duration(file_path: str) -> float | None:
    """Get media duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------
def has_audio_stream(file_path: str) -> bool:
    """Check if a media file contains an audio stream."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", file_path],
            capture_output=True, text=True, timeout=10
        )
        return bool(result.stdout.strip())
    except Exception:
        return True  # assume yes if check fails


def transcribe_file(model, file_path: str) -> dict:
    """Transcribe a single media file. Returns dict with results."""
    result = {
        "transcript": "",
        "segments": [],
        "language": None,
        "language_prob": 0.0,
        "duration": None,
        "word_count": 0,
        "is_silence": False,
        "error": None,
    }

    if not has_audio_stream(file_path):
        result["error"] = "no audio stream (video-only)"
        return result

    try:
        segments_iter, info = model.transcribe(
            file_path,
            beam_size=5,
            vad_filter=True,  # Skip silence automatically
            vad_parameters=dict(
                min_silence_duration_ms=1000,
                speech_pad_ms=200,
            ),
        )

        result["language"] = info.language
        result["language_prob"] = info.language_probability
        result["duration"] = info.duration

        all_segments = []
        all_text_parts = []

        for seg in segments_iter:
            text = seg.text.strip()
            if text:
                all_segments.append({
                    "id": seg.id,
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": text,
                })
                all_text_parts.append(text)

        full_text = " ".join(all_text_parts)
        result["transcript"] = full_text
        result["segments"] = all_segments
        result["word_count"] = len(full_text.split())

        # Check if it's just silence/noise
        unique_words = set(full_text.lower().split())
        if result["word_count"] < MIN_MEANINGFUL_WORDS or unique_words <= {"you", "the", "a", ""}:
            result["is_silence"] = True

    except Exception as exc:
        result["error"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Transcribe Epstein media files")
    parser.add_argument("--datasets", nargs="+", type=int, default=None,
                        help="Dataset numbers to process (default: all)")
    parser.add_argument("--max-duration", type=float, default=7200,
                        help="Skip files longer than N seconds (default: 7200 = 2hr)")
    parser.add_argument("--model-size", default="large-v3",
                        help="Whisper model size (default: large-v3)")
    parser.add_argument("--batch-size", type=int, default=1,
                        help="Files to commit per batch")
    parser.add_argument("--file-list", type=str, default=None,
                        help="Path to file with one media path per line (from prescreen_media.py)")
    args = parser.parse_args()

    log.info("=" * 70)
    log.info("Media Transcription Pipeline")
    log.info("=" * 70)

    # Find media files
    if args.file_list:
        log.info("Loading file list from %s...", args.file_list)
        media_files = []
        with open(args.file_list) as f:
            for line in f:
                fpath = line.strip()
                if not fpath or not os.path.isfile(fpath):
                    continue
                fname = os.path.basename(fpath)
                m = EFTA_RE.search(fname)
                efta = m.group(1) if m else os.path.splitext(fname)[0]
                # Determine dataset label from path
                ds_match = re.search(r'dataset(\d+)', fpath)
                ds_label = f"ds{ds_match.group(1)}" if ds_match else "unknown"
                media_files.append((fpath, efta, ds_label))
        log.info("Loaded %d files from priority list", len(media_files))
    else:
        log.info("Scanning for media files (datasets=%s)...", args.datasets or "all")
        media_files = find_media_files(args.datasets)
        log.info("Found %d media files", len(media_files))

    if not media_files:
        log.info("No media files found. Exiting.")
        return

    # Show breakdown
    ext_counts = {}
    for fp, _, _ in media_files:
        ext = os.path.splitext(fp)[1].lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
        log.info("  %s: %d files", ext, count)

    # Open DB and check what's already done
    conn = init_db()
    already_done = get_already_transcribed(conn)
    new_files = [(fp, efta, ds) for fp, efta, ds in media_files if efta not in already_done]
    log.info("Already transcribed: %d | New: %d", len(already_done), len(new_files))

    if not new_files:
        log.info("Nothing new to transcribe. Done.")
        conn.close()
        return

    # Load Whisper model on GPU
    log.info("Loading whisper model '%s' on GPU...", args.model_size)
    t0 = time.time()
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model_size, device="cuda", compute_type="float16")
    log.info("  Model loaded in %.1fs", time.time() - t0)

    # Process files
    total = len(new_files)
    done = 0
    skipped_duration = 0
    skipped_silence = 0
    errors = 0
    total_audio_secs = 0
    t_start = time.time()

    cursor = conn.cursor()

    for file_path, efta, ds_label in new_files:
        done += 1
        ext = os.path.splitext(file_path)[1].lower()

        # Check duration
        duration = get_duration(file_path)
        if duration and duration > args.max_duration:
            log.info("[%d/%d] SKIP %s (%.0fs > max %.0fs)", done, total, efta, duration, args.max_duration)
            skipped_duration += 1
            # Still record it so we don't re-check
            cursor.execute("""
                INSERT OR IGNORE INTO transcripts
                (efta_number, file_path, file_type, duration_secs, transcript, word_count, dataset_source)
                VALUES (?, ?, ?, ?, '[SKIPPED: exceeds max duration]', 0, ?)
            """, (efta, file_path, ext, duration, ds_label))
            continue

        # Transcribe
        t0 = time.time()
        result = transcribe_file(model, file_path)
        elapsed = time.time() - t0

        if result["error"]:
            errors += 1
            log.warning("[%d/%d] ERROR %s: %s", done, total, efta, result["error"])
            cursor.execute("""
                INSERT OR IGNORE INTO transcripts
                (efta_number, file_path, file_type, duration_secs, transcript, word_count, dataset_source)
                VALUES (?, ?, ?, ?, ?, 0, ?)
            """, (efta, file_path, ext, duration, f'[ERROR: {result["error"][:200]}]', ds_label))
            continue

        if result["is_silence"]:
            skipped_silence += 1
            transcript_text = result["transcript"] or "[SILENCE/NOISE ONLY]"
        else:
            transcript_text = result["transcript"]
            if result["duration"]:
                total_audio_secs += result["duration"]

        # Insert transcript
        cursor.execute("""
            INSERT OR IGNORE INTO transcripts
            (efta_number, file_path, file_type, duration_secs, language, language_prob,
             transcript, word_count, dataset_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (efta, file_path, ext, result["duration"], result["language"],
              result["language_prob"], transcript_text, result["word_count"], ds_label))

        # Insert segments
        for seg in result["segments"]:
            cursor.execute("""
                INSERT INTO transcript_segments
                (efta_number, segment_id, start_time, end_time, text)
                VALUES (?, ?, ?, ?, ?)
            """, (efta, seg["id"], seg["start"], seg["end"], seg["text"]))

        # Update FTS
        row_id = cursor.lastrowid
        if row_id and not result["is_silence"]:
            cursor.execute("""
                INSERT INTO transcripts_fts(rowid, efta_number, transcript)
                VALUES (?, ?, ?)
            """, (row_id, efta, transcript_text))

        # Progress log
        speed = f"{result['duration']/elapsed:.1f}x realtime" if result["duration"] and elapsed > 0 else "?"
        words_preview = transcript_text[:80].replace('\n', ' ') if transcript_text else ""

        if done % 5 == 0 or done <= 10:
            wall_elapsed = time.time() - t_start
            rate = done / wall_elapsed if wall_elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            log.info(
                "[%d/%d] %s | %s | %.0fs | %d words | %s | ETA %.0fs | %s",
                done, total, efta, ext, result["duration"] or 0,
                result["word_count"], speed, eta, words_preview[:60]
            )

        # Batch commit
        if done % 10 == 0:
            conn.commit()

    conn.commit()
    wall_time = time.time() - t_start

    log.info("=" * 70)
    log.info("TRANSCRIPTION COMPLETE")
    log.info("=" * 70)
    log.info("Files processed:    %d", done)
    log.info("Skipped (duration): %d", skipped_duration)
    log.info("Skipped (silence):  %d", skipped_silence)
    log.info("Errors:             %d", errors)
    log.info("Total audio:        %.0f sec (%.1f hours)", total_audio_secs, total_audio_secs / 3600)
    log.info("Wall time:          %.0f sec (%.1f min)", wall_time, wall_time / 60)
    if total_audio_secs > 0 and wall_time > 0:
        log.info("Overall speed:      %.1fx realtime", total_audio_secs / wall_time)

    # Stats
    cursor.execute("SELECT COUNT(*) FROM transcripts WHERE word_count > 0")
    log.info("Total transcripts with content: %d", cursor.fetchone()[0])
    cursor.execute("SELECT SUM(word_count) FROM transcripts")
    log.info("Total words transcribed: %d", cursor.fetchone()[0] or 0)

    conn.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
