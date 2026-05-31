#!/usr/bin/env python3
"""
Pre-screen media files to classify them before transcription.

Uses ffprobe to detect:
  - Audio stream presence
  - Duration (1-hour = surveillance)
  - Audio codec/bitrate
  - Resolution (fixed CCTV resolutions = surveillance)

Classifications:
  SPEECH_LIKELY  - audio-only or short video with speech indicators
  SURVEILLANCE   - 1-hour MP4/AVI with fixed surveillance resolution
  NO_AUDIO       - video file with no audio stream
  UNKNOWN        - needs manual check

Usage:
    python3 tools/prescreen_media.py                    # classify all remaining
    python3 tools/prescreen_media.py --transcribe       # classify then auto-transcribe SPEECH_LIKELY
    python3 tools/prescreen_media.py --stats             # just show classification stats
    python3 tools/prescreen_media.py --sample 10         # ffprobe 10 sample MP4s for inspection
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

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
TRANSCRIPTS_DB = os.path.join(BASE_DIR, "transcripts.db")
MEDIA_EXTS = {'.mp4', '.avi', '.m4a', '.wav', '.mov', '.mp3', '.m4v', '.wmv',
              '.vob', '.opus', '.ts', '.amr', '.3gp'}
AUDIO_ONLY_EXTS = {'.m4a', '.wav', '.opus', '.amr', '.mp3', '.3gp'}

# Known surveillance durations (in seconds, with tolerance)
SURVEILLANCE_DURATIONS = {3624, 3625}  # ~1:00:24, exact match from logs
SURVEILLANCE_TOLERANCE = 5  # seconds

# Known surveillance resolutions
SURVEILLANCE_RESOLUTIONS = {
    (320, 240), (352, 240), (352, 288), (640, 480), (704, 480), (720, 480),
    (176, 120), (176, 144),  # very low res CCTV
}


def ffprobe_file(filepath):
    """Run ffprobe on a file and return parsed info."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None


def classify_file(args):
    """Classify a single media file. Worker function."""
    efta, filepath, filesize, ext = args

    result = {
        'efta': efta,
        'path': filepath,
        'ext': ext,
        'size_mb': filesize / 1e6,
        'classification': 'UNKNOWN',
        'reason': '',
        'duration': 0,
        'has_audio': False,
        'has_video': False,
        'audio_codec': '',
        'video_resolution': '',
    }

    # Audio-only files are almost always speech
    if ext in AUDIO_ONLY_EXTS:
        result['classification'] = 'SPEECH_LIKELY'
        result['reason'] = 'audio-only format'
        result['has_audio'] = True
        # Still probe for duration
        probe = ffprobe_file(filepath)
        if probe and 'format' in probe:
            result['duration'] = float(probe['format'].get('duration', 0))
        return result

    # For video files, probe to check streams
    probe = ffprobe_file(filepath)
    if probe is None:
        result['classification'] = 'PROBE_FAILED'
        result['reason'] = 'ffprobe failed'
        return result

    # Parse streams
    audio_streams = []
    video_streams = []
    for stream in probe.get('streams', []):
        if stream.get('codec_type') == 'audio':
            audio_streams.append(stream)
        elif stream.get('codec_type') == 'video':
            video_streams.append(stream)

    result['has_audio'] = len(audio_streams) > 0
    result['has_video'] = len(video_streams) > 0

    # Get duration
    duration = float(probe.get('format', {}).get('duration', 0))
    result['duration'] = duration

    # Get audio codec
    if audio_streams:
        result['audio_codec'] = audio_streams[0].get('codec_name', '')

    # Get video resolution
    if video_streams:
        w = video_streams[0].get('width', 0)
        h = video_streams[0].get('height', 0)
        result['video_resolution'] = f"{w}x{h}"

    # No audio stream = no speech possible
    if not audio_streams:
        result['classification'] = 'NO_AUDIO'
        result['reason'] = 'no audio stream'
        return result

    # Check for surveillance patterns
    is_surveillance_duration = any(
        abs(duration - sd) <= SURVEILLANCE_TOLERANCE
        for sd in SURVEILLANCE_DURATIONS
    )

    is_surveillance_resolution = False
    if video_streams:
        w = video_streams[0].get('width', 0)
        h = video_streams[0].get('height', 0)
        is_surveillance_resolution = (w, h) in SURVEILLANCE_RESOLUTIONS

    # Tiny AVI files from the same EFTA range as known surveillance
    is_tiny_avi = ext == '.avi' and filesize < 500_000  # < 500KB

    # Classification logic
    if is_surveillance_duration and ext == '.mp4':
        result['classification'] = 'SURVEILLANCE'
        result['reason'] = f'1-hour MP4 ({duration:.0f}s)'
        return result

    if is_tiny_avi and is_surveillance_resolution:
        result['classification'] = 'SURVEILLANCE'
        result['reason'] = f'tiny AVI + CCTV resolution ({result["video_resolution"]})'
        return result

    if is_tiny_avi:
        # Tiny AVIs that aren't definitively surveillance - quick to process anyway
        result['classification'] = 'QUICK_CHECK'
        result['reason'] = f'tiny AVI ({filesize/1e3:.0f}KB), fast to transcribe'
        return result

    # Short duration with audio = likely speech
    if duration < 600 and result['has_audio']:  # < 10 min
        result['classification'] = 'SPEECH_LIKELY'
        result['reason'] = f'short ({duration:.0f}s) with audio'
        return result

    # Medium duration with audio = maybe speech
    if duration < 3600 and result['has_audio']:
        result['classification'] = 'SPEECH_POSSIBLE'
        result['reason'] = f'medium ({duration:.0f}s) with audio'
        return result

    # Long but not exact surveillance duration
    if duration >= 3600 and result['has_audio']:
        if is_surveillance_resolution:
            result['classification'] = 'SURVEILLANCE'
            result['reason'] = f'long ({duration:.0f}s) + CCTV resolution'
        else:
            result['classification'] = 'SPEECH_POSSIBLE'
            result['reason'] = f'long ({duration:.0f}s) but non-CCTV resolution'
        return result

    return result


def find_remaining_files():
    """Find all unprocessed media files."""
    conn = sqlite3.connect(TRANSCRIPTS_DB)
    cur = conn.cursor()
    cur.execute('SELECT efta_number FROM transcripts')
    done = {r[0] for r in cur.fetchall()}
    conn.close()

    remaining = []
    seen_eftas = set()
    for dsn in range(1, 13):
        for variant in [f'dataset{dsn}', f'dataset{dsn}_full']:
            ds_dir = os.path.join(BASE_DIR, 'datasets', variant)
            if not os.path.isdir(ds_dir) or os.path.islink(ds_dir):
                continue
            for root, _, files in os.walk(ds_dir):
                for fn in files:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in MEDIA_EXTS:
                        continue
                    base = os.path.splitext(fn)[0]
                    if not base.startswith('EFTA'):
                        continue
                    if base in done or base in seen_eftas:
                        continue
                    fp = os.path.join(root, fn)
                    sz = os.path.getsize(fp)
                    if sz == 0:
                        continue  # skip empty/corrupt files
                    seen_eftas.add(base)
                    remaining.append((base, fp, sz, ext))

    return remaining


def main():
    parser = argparse.ArgumentParser(description="Pre-screen media files for transcription")
    parser.add_argument('--workers', type=int, default=32, help="Parallel ffprobe workers")
    parser.add_argument('--stats', action='store_true', help="Just show classification stats")
    parser.add_argument('--sample', type=int, default=0, help="Sample N files for inspection")
    parser.add_argument('--output', type=str, default='', help="Save classifications to JSON")
    parser.add_argument('--transcribe-list', type=str, default='',
                        help="Output file list for transcription (ordered by priority)")
    args = parser.parse_args()

    print("Finding remaining unprocessed media files...")
    remaining = find_remaining_files()
    print(f"Found {len(remaining)} unprocessed files")

    if args.sample:
        # Just sample some MP4s
        mp4s = [f for f in remaining if f[3] == '.mp4']
        import random
        sample = random.sample(mp4s, min(args.sample, len(mp4s)))
        print(f"\nSampling {len(sample)} MP4 files:")
        for efta, fp, sz, ext in sample:
            info = ffprobe_file(fp)
            if info:
                dur = float(info.get('format', {}).get('duration', 0))
                streams = info.get('streams', [])
                audio = [s for s in streams if s.get('codec_type') == 'audio']
                video = [s for s in streams if s.get('codec_type') == 'video']
                res = ''
                if video:
                    res = f"{video[0].get('width', '?')}x{video[0].get('height', '?')}"
                acodec = audio[0].get('codec_name', '') if audio else 'NONE'
                print(f"  {efta} | {sz/1e6:.1f}MB | {dur:.0f}s | audio:{acodec} | video:{res}")
            else:
                print(f"  {efta} | {sz/1e6:.1f}MB | PROBE FAILED")
        return

    # Classify all files
    print(f"\nClassifying {len(remaining)} files with {args.workers} workers...")
    t0 = time.time()

    results = []
    done_count = 0

    # Split: audio-only files don't need ffprobe, just classify directly
    audio_only = [f for f in remaining if f[3] in AUDIO_ONLY_EXTS]
    need_probe = [f for f in remaining if f[3] not in AUDIO_ONLY_EXTS]

    # Fast-classify audio-only
    for efta, fp, sz, ext in audio_only:
        r = classify_file((efta, fp, sz, ext))
        results.append(r)
    print(f"  {len(audio_only)} audio-only files classified instantly")

    # Probe video files in parallel
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(classify_file, f): f for f in need_probe}
        for future in as_completed(futures):
            r = future.result()
            results.append(r)
            done_count += 1
            if done_count % 500 == 0:
                elapsed = time.time() - t0
                print(f"  [{done_count}/{len(need_probe)}] {elapsed:.1f}s")

    elapsed = time.time() - t0
    print(f"\nClassification complete: {elapsed:.1f}s")

    # Stats
    by_class = defaultdict(list)
    for r in results:
        by_class[r['classification']].append(r)

    print(f"\n{'='*80}")
    print(f"{'CLASSIFICATION':<20} {'COUNT':>6} {'TOTAL SIZE':>12} {'AVG DUR':>10}")
    print(f"{'='*80}")

    priority_order = ['SPEECH_LIKELY', 'SPEECH_POSSIBLE', 'QUICK_CHECK',
                       'UNKNOWN', 'SURVEILLANCE', 'NO_AUDIO', 'PROBE_FAILED']

    for cls in priority_order:
        items = by_class.get(cls, [])
        if not items:
            continue
        total_mb = sum(r['size_mb'] for r in items)
        durations = [r['duration'] for r in items if r['duration'] > 0]
        avg_dur = sum(durations) / len(durations) if durations else 0
        print(f"  {cls:<18} {len(items):>6} {total_mb:>10.1f} MB {avg_dur:>8.0f}s")

    # Show what we'd skip
    skip_count = len(by_class.get('SURVEILLANCE', [])) + len(by_class.get('NO_AUDIO', []))
    process_count = len(results) - skip_count
    skip_hours = sum(r['duration'] for r in by_class.get('SURVEILLANCE', [])) / 3600
    print(f"\n{'='*60}")
    print(f"  WILL PROCESS: {process_count} files")
    print(f"  WILL SKIP:    {skip_count} files ({skip_hours:.0f} hours of surveillance)")
    print(f"{'='*60}")

    # Show top SPEECH_LIKELY files
    speech = by_class.get('SPEECH_LIKELY', []) + by_class.get('SPEECH_POSSIBLE', [])
    if speech:
        print(f"\nTop speech candidates:")
        for r in sorted(speech, key=lambda x: -x['duration'])[:20]:
            print(f"  {r['efta']} | {r['ext']:>5} | {r['duration']:>7.0f}s | "
                  f"{r['size_mb']:>7.1f}MB | {r['reason']}")

    # Show sample surveillance files (for verification)
    surv = by_class.get('SURVEILLANCE', [])
    if surv:
        print(f"\nSample surveillance files (first 10):")
        for r in sorted(surv, key=lambda x: x['efta'])[:10]:
            print(f"  {r['efta']} | {r['ext']:>5} | {r['duration']:>7.0f}s | "
                  f"{r['video_resolution']:>10} | {r['reason']}")

    # Save results
    output_path = args.output or os.path.join(BASE_DIR, 'media_classifications.json')
    serializable = []
    for r in results:
        serializable.append({
            'efta': r['efta'],
            'path': r['path'],
            'ext': r['ext'],
            'size_mb': round(r['size_mb'], 2),
            'classification': r['classification'],
            'reason': r['reason'],
            'duration': round(r['duration'], 1),
            'has_audio': r['has_audio'],
            'has_video': r['has_video'],
            'audio_codec': r['audio_codec'],
            'video_resolution': r['video_resolution'],
        })

    with open(output_path, 'w') as f:
        json.dump(serializable, f, indent=2)
    print(f"\nClassifications saved to {output_path}")

    # Generate transcription priority list
    list_path = args.transcribe_list or os.path.join(BASE_DIR, 'transcription_priority.txt')
    priority = []

    # Order: SPEECH_LIKELY first, then SPEECH_POSSIBLE, then QUICK_CHECK, then UNKNOWN
    for cls in ['SPEECH_LIKELY', 'SPEECH_POSSIBLE', 'QUICK_CHECK', 'UNKNOWN']:
        items = by_class.get(cls, [])
        # Within each class, sort by duration (shorter first = faster results)
        items.sort(key=lambda x: x['duration'])
        for r in items:
            priority.append(r['path'])

    with open(list_path, 'w') as f:
        for p in priority:
            f.write(p + '\n')
    print(f"Transcription priority list ({len(priority)} files) saved to {list_path}")


if __name__ == '__main__':
    main()
