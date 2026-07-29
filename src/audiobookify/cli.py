import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .db import Database
from .engines.chatterbox import ChatterboxEngine
from .ingest import epub, markdown
from .models import ChapterStatus, SourceFormat
from .synth.pipeline import generate_book
from .voice.validation import validate_reference_audio

import soundfile as sf


def _ingest(path: str, heading_level: int):
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return epub.parse(path), SourceFormat.EPUB
    if suffix == ".md":
        return markdown.parse(path, heading_level=heading_level), SourceFormat.MARKDOWN
    raise ValueError(f"unsupported input format: {suffix}")


def _handle_generate(args) -> None:
    source_path = str(Path(args.input).resolve())
    output_dir = str(Path(args.out).resolve())

    db = Database(args.db)
    book = db.find_book_by_source(source_path)
    if book is None:
        try:
            parsed, source_format = _ingest(source_path, args.heading_level)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        book = db.create_book(
            title=parsed.title, author=parsed.author, source_path=source_path,
            source_format=source_format, output_dir=output_dir,
        )
        db.add_chapters(book.id, parsed.chapters)
    elif book.output_dir != output_dir:
        # Switching output_dir mid-book would orphan the paths already recorded
        # for finished chapters, so keep the original and make the mismatch loud.
        print(
            f"Warning: this book was previously generated to {book.output_dir}; "
            "continuing to use that directory. Pass a fresh --db to start over.",
            file=sys.stderr,
        )

    profile = None
    if args.voice_profile:
        profile = db.find_voice_profile_by_name(args.voice_profile)
        if profile is None:
            print(f"Error: no voice profile named '{args.voice_profile}'", file=sys.stderr)
            db.close()
            sys.exit(1)

    voice_name = args.voice or (profile.name if profile else "default")
    voice_params = {"voice_name": voice_name}
    if profile:
        voice_params.update({
            "reference_audio": profile.reference_audio_path,
            "reference_text": profile.reference_transcript,
            "exaggeration": profile.exaggeration,
            "cfg": profile.cfg_weight,
            "temperature": profile.temperature,
        })

    engine = ChatterboxEngine()
    results = generate_book(book, engine, db, voice_params, device=args.device)
    for chapter in results:
        print(f"{chapter.status.value}: {chapter.title} -> {chapter.output_mp3_path}")

    failed = [c for c in results if c.status == ChapterStatus.FAILED]
    for chapter in failed:
        print(f"Error: chapter '{chapter.title}' failed: {chapter.error}", file=sys.stderr)

    db.close()
    if failed:
        sys.exit(1)


def _handle_voice_add(args) -> None:
    reference_path = str(Path(args.reference).resolve())
    try:
        info = validate_reference_audio(reference_path)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    for warning in info.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    db = Database(args.db)
    try:
        profile = db.create_voice_profile(
            name=args.name, engine=ChatterboxEngine.name, reference_audio_path=reference_path,
            reference_transcript=args.transcript, exaggeration=args.exaggeration,
            cfg_weight=args.cfg, temperature=args.temperature,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(
        f"Created voice profile '{profile.name}' ({info.duration_seconds:.1f}s, "
        f"{info.sample_rate} Hz, {info.channels}ch reference)"
    )
    db.close()


def _handle_voice_list(args) -> None:
    db = Database(args.db)
    profiles = db.list_voice_profiles()
    if not profiles:
        print("No voice profiles yet. Add one with 'audiobookify voice add'.")
    for profile in profiles:
        print(
            f"{profile.name}\t{profile.reference_audio_path}\t"
            f"exaggeration={profile.exaggeration} cfg={profile.cfg_weight} "
            f"temperature={profile.temperature}"
        )
    db.close()


def _handle_voice_preview(args) -> None:
    db = Database(args.db)
    profile = db.find_voice_profile_by_name(args.name)
    db.close()
    if profile is None:
        print(f"Error: no voice profile named '{args.name}'", file=sys.stderr)
        sys.exit(1)

    engine = ChatterboxEngine()
    try:
        engine.load(args.device)
        handle = engine.clone_profile(profile.reference_audio_path, profile.reference_transcript, {})
        audio = engine.synthesize(
            args.text, handle,
            {
                "exaggeration": profile.exaggeration,
                "cfg": profile.cfg_weight,
                "temperature": profile.temperature,
            },
        )
        out_path = args.out or f"{profile.name}-preview.wav"
        sf.write(out_path, audio, engine.sample_rate())
    finally:
        engine.teardown()

    print(f"Wrote preview to {out_path}")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="audiobookify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate an audiobook from a book file")
    generate_parser.add_argument("--input", required=True)
    generate_parser.add_argument("--out", required=True)
    generate_parser.add_argument("--voice", default=None)
    generate_parser.add_argument("--voice-profile", default=None)
    generate_parser.add_argument("--device", default="auto")
    generate_parser.add_argument("--db", default="audiobookify.db")
    generate_parser.add_argument("--heading-level", type=int, default=1)

    voice_parser = subparsers.add_parser("voice", help="Manage voice profiles")
    voice_subparsers = voice_parser.add_subparsers(dest="voice_command", required=True)

    voice_add_parser = voice_subparsers.add_parser(
        "add", help="Import a reference clip as a named voice profile"
    )
    voice_add_parser.add_argument("--name", required=True)
    voice_add_parser.add_argument("--reference", required=True)
    voice_add_parser.add_argument("--transcript", default=None)
    voice_add_parser.add_argument("--exaggeration", type=float, default=0.5)
    voice_add_parser.add_argument("--cfg", type=float, default=0.5)
    voice_add_parser.add_argument("--temperature", type=float, default=0.8)
    voice_add_parser.add_argument("--db", default="audiobookify.db")

    voice_list_parser = voice_subparsers.add_parser("list", help="List saved voice profiles")
    voice_list_parser.add_argument("--db", default="audiobookify.db")

    voice_preview_parser = voice_subparsers.add_parser(
        "preview", help="Synthesize a sample sentence with a saved voice profile"
    )
    voice_preview_parser.add_argument("--name", required=True)
    voice_preview_parser.add_argument("--text", required=True)
    voice_preview_parser.add_argument("--out", default=None)
    voice_preview_parser.add_argument("--device", default="auto")
    voice_preview_parser.add_argument("--db", default="audiobookify.db")

    args = parser.parse_args(argv)

    if args.command == "generate":
        _handle_generate(args)
    elif args.command == "voice":
        if args.voice_command == "add":
            _handle_voice_add(args)
        elif args.voice_command == "list":
            _handle_voice_list(args)
        elif args.voice_command == "preview":
            _handle_voice_preview(args)


if __name__ == "__main__":
    main()
