import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional

import soundfile as sf

from .db import Database
from .engines.chatterbox import ChatterboxEngine
from .ingest import epub, markdown
from .lexicon.substitution import apply_lexicon
from .models import ChapterStatus, SourceFormat
from .phoneme.respelling import phonemes_to_respelling
from .phoneme.wav2vec2 import Wav2Vec2PhonemeExtractor
from .synth.pipeline import generate_book
from .voice.validation import validate_reference_audio


def _ingest(path: str, heading_level: int):
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return epub.parse(path), SourceFormat.EPUB
    if suffix == ".md":
        return markdown.parse(path, heading_level=heading_level), SourceFormat.MARKDOWN
    raise ValueError(f"unsupported input format: {suffix}")


def _voice_params(profile) -> dict:
    return {
        "reference_audio": profile.reference_audio_path,
        "reference_text": profile.reference_transcript,
        "exaggeration": profile.exaggeration,
        "cfg": profile.cfg_weight,
        "temperature": profile.temperature,
    }


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


def _sanitize_filename_component(value: str) -> str:
    return re.sub(r"[^\w.-]+", "_", value)


def _validate_pronunciation_clip(path: str) -> None:
    try:
        data, _ = sf.read(path, always_2d=True, dtype="float32")
    except (RuntimeError, OSError) as exc:
        raise ValueError(f"could not read pronunciation clip '{path}': {exc}") from exc
    if len(data) == 0:
        raise ValueError(f"pronunciation clip '{path}' contains no audio")


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

    voice_name = args.voice or (profile.name if profile else "default")
    voice_params = {"voice_name": voice_name}
    if profile:
        voice_params.update(_voice_params(profile))

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
            name=args.name, engine=ChatterboxEngine.name, reference_audio_path=reference_path,
            reference_transcript=args.transcript, exaggeration=args.exaggeration,
            cfg_weight=args.cfg, temperature=args.temperature,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
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
    db.close()
    if profile is None:
        print(f"Error: no voice profile named '{args.name}'", file=sys.stderr)
        sys.exit(1)

    _check_reference_audio_present(profile)

    engine = ChatterboxEngine()
    try:
        engine.load(args.device)
        handle = engine.clone_profile(profile.reference_audio_path, profile.reference_transcript, {})
        audio = engine.synthesize(args.text, handle, _voice_params(profile))
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


def _handle_lexicon_add(args) -> None:
    if not args.term.strip():
        print("Error: lexicon term must not be empty", file=sys.stderr)
        sys.exit(1)
    args.term = args.term.strip()

    if not args.respell.strip():
        print("Error: lexicon respelling must not be empty", file=sys.stderr)
        sys.exit(1)
    args.respell = args.respell.strip()

    if not re.match(r"^\w", args.term) or not re.search(r"\w$", args.term):
        print(
            f"Warning: '{args.term}' doesn't start and end with a letter or digit; "
            "whole-word substitution requires a word boundary there and may never match",
            file=sys.stderr,
        )

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


def _handle_lexicon_add_from_audio(args) -> None:
    if not args.term.strip():
        print("Error: lexicon term must not be empty", file=sys.stderr)
        sys.exit(1)
    args.term = args.term.strip()

    if not re.match(r"^\w", args.term) or not re.search(r"\w$", args.term):
        print(
            f"Warning: '{args.term}' doesn't start and end with a letter or digit; "
            "whole-word substitution requires a word boundary there and may never match",
            file=sys.stderr,
        )

    audio_path = str(Path(args.audio).resolve())
    try:
        _validate_pronunciation_clip(audio_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    db = Database(args.db)
    book_id = 0
    scope = "global"
    if args.book:
        book = _resolve_book_for_lexicon(db, args.book)
        book_id = book.id
        scope = "book"

    extractor = Wav2Vec2PhonemeExtractor()
    try:
        extractor.load(args.device)
        phonemes = extractor.extract(audio_path)
    finally:
        extractor.teardown()

    respelling = phonemes_to_respelling(phonemes)

    try:
        entry = db.create_lexicon_entry(
            term=args.term, override_value=respelling, scope=scope, book_id=book_id,
            notes=args.notes, source="from_audio", phonemes=phonemes,
            sample_audio_path=audio_path,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        db.close()
        sys.exit(1)

    print(f"Extracted phonemes: {phonemes}")
    print(f"Derived respelling: {respelling}")
    print(
        f"Added pronunciation for '{entry.term}' -> '{entry.override_value}' "
        f"({entry.scope} scope, source: from_audio)"
    )
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
    db.close()

    _check_reference_audio_present(profile)

    sentence = args.sentence or f"The pronunciation of {entry.term} is being previewed."
    text = apply_lexicon(sentence, [entry])

    engine = ChatterboxEngine()
    try:
        engine.load(args.device)
        handle = engine.clone_profile(
            profile.reference_audio_path if profile else None,
            profile.reference_transcript if profile else None,
            {},
        )
        audio = engine.synthesize(text, handle, _voice_params(profile) if profile else {})
        out_path = args.out or f"{_sanitize_filename_component(entry.term)}-pronunciation-preview.wav"
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
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

    lexicon_add_from_audio_parser = lexicon_subparsers.add_parser(
        "add-from-audio",
        help="Derive a pronunciation override from an audio clip of the term spoken correctly",
    )
    lexicon_add_from_audio_parser.add_argument("--term", required=True)
    lexicon_add_from_audio_parser.add_argument("--audio", required=True)
    lexicon_add_from_audio_parser.add_argument("--book", default=None)
    lexicon_add_from_audio_parser.add_argument("--notes", default=None)
    lexicon_add_from_audio_parser.add_argument("--device", default="auto")
    lexicon_add_from_audio_parser.add_argument("--db", default="audiobookify.db")

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
    lexicon_preview_parser.add_argument("--device", default="auto")
    lexicon_preview_parser.add_argument("--db", default="audiobookify.db")

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
        elif args.lexicon_command == "add-from-audio":
            _handle_lexicon_add_from_audio(args)
        elif args.lexicon_command == "list":
            _handle_lexicon_list(args)
        elif args.lexicon_command == "remove":
            _handle_lexicon_remove(args)
        elif args.lexicon_command == "edit":
            _handle_lexicon_edit(args)
        elif args.lexicon_command == "preview":
            _handle_lexicon_preview(args)


if __name__ == "__main__":
    main()
