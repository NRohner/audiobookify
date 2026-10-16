import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional

import soundfile as sf

from .db import Database
from .engines import DEFAULT_ENGINE, ENGINES
from .ingest import ingest_by_path
from .lexicon.substitution import apply_lexicon
from .models import ChapterStatus
from . import worker
from .synth.pipeline import generate_book
from .voice.storage import store_reference_audio
from .voice.validation import params_from_profile, validate_reference_audio


def _check_reference_audio_present(profile, db: Optional[Database] = None) -> None:
    if profile is not None and not Path(profile.reference_audio_path).is_file():
        print(
            f"Error: reference audio for voice profile '{profile.name}' is missing: "
            f"{profile.reference_audio_path}",
            file=sys.stderr,
        )
        if db is not None:
            db.close()
        sys.exit(1)


def _make_engine(name: str):
    return ENGINES[name].cls()


def _sanitize_filename_component(value: str) -> str:
    return re.sub(r"[^\w.-]+", "_", value)


def _handle_generate(args) -> None:
    source_path = str(Path(args.input).resolve())
    output_dir = str(Path(args.out).resolve())

    db = Database(args.db)
    book = db.find_book_by_source(source_path)
    if book is None:
        try:
            parsed, source_format = ingest_by_path(source_path, args.heading_level)
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

    requested_profile = None
    if args.voice_profile:
        requested_profile = db.find_voice_profile_by_name(args.voice_profile)
        if requested_profile is None:
            print(f"Error: no voice profile named '{args.voice_profile}'", file=sys.stderr)
            db.close()
            sys.exit(1)
    requested_profile_id = requested_profile.id if requested_profile else None

    any_chapter_done = any(c.status == ChapterStatus.DONE for c in db.list_chapters(book.id))

    if not any_chapter_done:
        # First time this book has been associated with a voice (or explicitly
        # with none) - lock it in so later runs stay consistent.
        db.update_book_voice_profile(book.id, requested_profile_id)
        profile = requested_profile
    elif book.voice_profile_id != requested_profile_id:
        # A different voice than the one already used for this book's earlier
        # chapters was requested - keep the original for consistency and warn
        # loudly rather than silently mixing voices across chapters.
        original = db.get_voice_profile_by_id(book.voice_profile_id) if book.voice_profile_id else None
        print(
            f"Warning: this book was previously generated with voice profile "
            f"'{original.name if original else 'the default voice'}'; continuing to use that "
            "voice for consistency. Pass a fresh --db to use a different voice.",
            file=sys.stderr,
        )
        profile = original
    else:
        profile = requested_profile

    _check_reference_audio_present(profile, db)

    engine_name = args.engine or book.engine
    spec = ENGINES[engine_name]
    if spec.cls.needs_transcript and profile is None:
        print(f"Error: {spec.label} needs a voice: pass --voice-profile", file=sys.stderr)
        db.close()
        sys.exit(1)
    db.update_book_engine(book.id, engine_name)
    engine = _make_engine(engine_name)
    profile = worker.load_voice(db, profile.id, engine) if profile else None

    voice_name = args.voice or (profile.name if profile else "default")
    voice_params = {"voice_name": voice_name}
    if profile:
        voice_params.update(params_from_profile(profile))

    results = generate_book(book, engine, db, voice_params)
    for chapter in results:
        print(f"{chapter.status.value}: {chapter.title} -> {chapter.output_mp3_path}")

    failed = [c for c in results if c.status == ChapterStatus.FAILED]
    for chapter in failed:
        print(f"Error: chapter '{chapter.title}' failed: {chapter.error}", file=sys.stderr)

    db.close()
    if failed:
        sys.exit(1)


def _handle_voice_add(args) -> None:
    if not args.name.strip():
        print("Error: voice profile name must not be empty", file=sys.stderr)
        sys.exit(1)
    args.name = args.name.strip()

    reference_path = str(Path(args.reference).resolve())
    try:
        info = validate_reference_audio(reference_path)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    db = Database(args.db)
    try:
        profile = db.create_voice_profile(
            name=args.name, engine=DEFAULT_ENGINE, reference_audio_path=reference_path,
            reference_transcript=args.transcript, exaggeration=args.exaggeration,
            cfg_weight=args.cfg, temperature=args.temperature,
            # A typed transcript is used as-is; otherwise Whisper runs in the worker,
            # or the first time an engine needs it.
            transcript_status="done" if args.transcript else "pending",
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    try:
        permanent_path = store_reference_audio(reference_path, profile.id, profile.name)
        db.update_voice_profile_reference_audio_path(profile.id, str(permanent_path))
    except OSError as exc:
        print(f"Error: could not store reference audio permanently: {exc}", file=sys.stderr)
        db.delete_voice_profile(profile.id)
        db.close()
        sys.exit(1)

    for warning in info.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

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
    if profile is None:
        db.close()
        print(f"Error: no voice profile named '{args.name}'", file=sys.stderr)
        sys.exit(1)

    _check_reference_audio_present(profile, db)

    engine = _make_engine(args.engine)
    params = params_from_profile(worker.load_voice(db, profile.id, engine))
    db.close()
    try:
        engine.load()
        handle = engine.clone_profile(params["reference_audio"], params["reference_text"], params)
        audio = engine.synthesize(args.text, handle, params)
        out_path = args.out or f"{profile.name}-preview.wav"
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_path, audio, engine.sample_rate())
    finally:
        engine.teardown()

    print(f"Wrote preview to {out_path}")


def _resolve_book_for_lexicon(db: Database, book_path: str):
    resolved = str(Path(book_path).resolve())
    book = db.find_book_by_source(resolved)
    if book is None:
        print(
            f"Error: no book found for '{resolved}'; run 'generate' at least once to register it",
            file=sys.stderr,
        )
        db.close()
        sys.exit(1)
    return book


def _validate_lexicon_term(term: str) -> str:
    term = term.strip()
    if not term:
        print("Error: lexicon term must not be empty", file=sys.stderr)
        sys.exit(1)
    if not re.match(r"^\w", term) or not re.search(r"\w$", term):
        print(
            f"Warning: '{term}' doesn't start and end with a letter or digit; "
            "whole-word substitution requires a word boundary there and may never match",
            file=sys.stderr,
        )
    return term


def _handle_lexicon_add(args) -> None:
    args.term = _validate_lexicon_term(args.term)

    if not args.respell.strip():
        print("Error: lexicon respelling must not be empty", file=sys.stderr)
        sys.exit(1)
    args.respell = args.respell.strip()

    db = Database(args.db)
    book_id = 0
    scope = "global"
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id
        scope = "book"

    try:
        entry = db.create_lexicon_entry(
            term=args.term, override_value=args.respell, scope=scope,
            book_id=book_id, notes=args.notes,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(f"Added pronunciation for '{entry.term}' -> '{entry.override_value}' ({entry.scope} scope)")
    db.close()


def _handle_lexicon_edit(args) -> None:
    args.term = args.term.strip()

    if not args.respell.strip():
        print("Error: lexicon respelling must not be empty", file=sys.stderr)
        sys.exit(1)
    args.respell = args.respell.strip()

    db = Database(args.db)
    book_id = 0
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id

    try:
        entry = db.update_lexicon_entry_override(args.term, args.respell, book_id=book_id)
    except KeyError as exc:
        print(f"Error: {exc.args[0]}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(f"Updated pronunciation for '{entry.term}' -> '{entry.override_value}'")
    db.close()


def _handle_lexicon_list(args) -> None:
    db = Database(args.db)
    book_id = None
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id

    entries = db.list_lexicon_entries(book_id)
    if not entries:
        print("No lexicon entries yet. Add one with 'audiobookify lexicon add'.")
    for entry in entries:
        line = f"{entry.term}\t{entry.override_value}\t{entry.scope}\t{entry.source}"
        if entry.phonemes:
            line += f"\t{entry.phonemes}"
        print(line)
    db.close()


def _handle_lexicon_remove(args) -> None:
    args.term = args.term.strip()

    db = Database(args.db)
    book_id = 0
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id

    try:
        db.delete_lexicon_entry(args.term, book_id=book_id)
    except KeyError as exc:
        print(f"Error: {exc.args[0]}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(f"Removed pronunciation override for '{args.term}'")
    db.close()


def _handle_lexicon_preview(args) -> None:
    args.term = args.term.strip()

    db = Database(args.db)
    book_id = 0
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id

    entry = db.find_lexicon_entry(args.term, book_id=book_id)
    if entry is None and book_id:
        entry = db.find_lexicon_entry(args.term)  # fall back to the global entry
    if entry is None:
        print(f"Error: no lexicon entry for '{args.term}'", file=sys.stderr)
        db.close()
        sys.exit(1)

    profile = None
    if args.voice_profile:
        profile = db.find_voice_profile_by_name(args.voice_profile)
        if profile is None:
            print(f"Error: no voice profile named '{args.voice_profile}'", file=sys.stderr)
            db.close()
            sys.exit(1)

    _check_reference_audio_present(profile, db)

    sentence = args.sentence or f"The pronunciation of {entry.term} is being previewed."
    text = apply_lexicon(sentence, [entry])

    engine = _make_engine(args.engine)
    params = params_from_profile(worker.load_voice(db, profile.id, engine)) if profile else {}
    db.close()
    try:
        engine.load()
        handle = engine.clone_profile(params.get("reference_audio"), params.get("reference_text"), params)
        audio = engine.synthesize(text, handle, params)
        out_path = args.out or f"{_sanitize_filename_component(entry.term)}-pronunciation-preview.wav"
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_path, audio, engine.sample_rate())
    finally:
        engine.teardown()

    print(f"Wrote preview to {out_path}")


def _handle_worker(args) -> None:
    db = Database(args.db)
    engines = worker.EngineSlot(_make_engine)  # loads each job's engine on demand
    try:
        worker.run_forever(db, engines, poll_interval=args.poll_interval,
                            text_clip_output_dir=args.text_output_dir)
    finally:
        engines.teardown()
        db.close()


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="audiobookify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate an audiobook from a book file")
    generate_parser.add_argument("--input", required=True)
    generate_parser.add_argument("--out", required=True)
    generate_parser.add_argument("--voice", default=None)
    generate_parser.add_argument("--voice-profile", default=None)
    generate_parser.add_argument("--db", default="audiobookify.db")
    generate_parser.add_argument("--heading-level", type=int, default=1)
    generate_parser.add_argument("--engine", choices=sorted(ENGINES), default=None,
                                 help="TTS engine; stored on the book (default: the book's, else chatterbox-turbo)")

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
    voice_preview_parser.add_argument("--engine", choices=sorted(ENGINES), default=DEFAULT_ENGINE)
    voice_preview_parser.add_argument("--db", default="audiobookify.db")

    lexicon_parser = subparsers.add_parser("lexicon", help="Manage pronunciation overrides")
    lexicon_subparsers = lexicon_parser.add_subparsers(dest="lexicon_command", required=True)

    lexicon_add_parser = lexicon_subparsers.add_parser(
        "add", help="Add a pronunciation override for a term"
    )
    lexicon_add_parser.add_argument("--term", required=True)
    lexicon_add_parser.add_argument("--respell", required=True)
    lexicon_add_parser.add_argument("--book", default=None)
    lexicon_add_parser.add_argument("--notes", default=None)
    lexicon_add_parser.add_argument("--db", default="audiobookify.db")

    lexicon_list_parser = lexicon_subparsers.add_parser("list", help="List pronunciation overrides")
    lexicon_list_parser.add_argument("--book", default=None)
    lexicon_list_parser.add_argument("--db", default="audiobookify.db")

    lexicon_remove_parser = lexicon_subparsers.add_parser(
        "remove", help="Remove a pronunciation override"
    )
    lexicon_remove_parser.add_argument("--term", required=True)
    lexicon_remove_parser.add_argument("--book", default=None)
    lexicon_remove_parser.add_argument("--db", default="audiobookify.db")

    lexicon_edit_parser = lexicon_subparsers.add_parser(
        "edit", help="Correct the respelling for an existing pronunciation override"
    )
    lexicon_edit_parser.add_argument("--term", required=True)
    lexicon_edit_parser.add_argument("--respell", required=True)
    lexicon_edit_parser.add_argument("--book", default=None)
    lexicon_edit_parser.add_argument("--db", default="audiobookify.db")

    lexicon_preview_parser = lexicon_subparsers.add_parser(
        "preview", help="Synthesize a sample sentence with a term's pronunciation override applied"
    )
    lexicon_preview_parser.add_argument("--term", required=True)
    lexicon_preview_parser.add_argument("--sentence", default=None)
    lexicon_preview_parser.add_argument("--voice-profile", default=None)
    lexicon_preview_parser.add_argument("--book", default=None)
    lexicon_preview_parser.add_argument("--out", default=None)
    lexicon_preview_parser.add_argument("--engine", choices=sorted(ENGINES), default=DEFAULT_ENGINE)
    lexicon_preview_parser.add_argument("--db", default="audiobookify.db")

    worker_parser = subparsers.add_parser("worker", help="Run the background generation worker")
    worker_parser.add_argument("--poll-interval", type=float, default=2.0)
    worker_parser.add_argument("--db", default="audiobookify.db")
    worker_parser.add_argument("--text-output-dir", default=worker.DEFAULT_TEXT_CLIP_OUTPUT_DIR)

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
    elif args.command == "lexicon":
        if args.lexicon_command == "add":
            _handle_lexicon_add(args)
        elif args.lexicon_command == "list":
            _handle_lexicon_list(args)
        elif args.lexicon_command == "remove":
            _handle_lexicon_remove(args)
        elif args.lexicon_command == "edit":
            _handle_lexicon_edit(args)
        elif args.lexicon_command == "preview":
            _handle_lexicon_preview(args)
    elif args.command == "worker":
        _handle_worker(args)


if __name__ == "__main__":
    main()
