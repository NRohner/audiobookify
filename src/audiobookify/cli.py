import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .db import Database
from .engines.chatterbox import ChatterboxEngine
from .ingest import epub, markdown
from .models import ChapterStatus, SourceFormat
from .synth.pipeline import generate_book


def _ingest(path: str, heading_level: int):
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return epub.parse(path), SourceFormat.EPUB
    if suffix == ".md":
        return markdown.parse(path, heading_level=heading_level), SourceFormat.MARKDOWN
    raise ValueError(f"unsupported input format: {suffix}")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="audiobookify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate an audiobook from a book file")
    generate_parser.add_argument("--input", required=True)
    generate_parser.add_argument("--out", required=True)
    generate_parser.add_argument("--voice", default="default")
    generate_parser.add_argument("--device", default="auto")
    generate_parser.add_argument("--db", default="audiobookify.db")
    generate_parser.add_argument("--heading-level", type=int, default=1)

    args = parser.parse_args(argv)

    if args.command == "generate":
        # Resolve both paths so a book is identified by its real location, not by
        # the spelling of the CLI argument (cwd-relative, "./" prefixes, symlinks).
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

        engine = ChatterboxEngine()
        results = generate_book(book, engine, db, {"voice_name": args.voice}, device=args.device)
        for chapter in results:
            print(f"{chapter.status.value}: {chapter.title} -> {chapter.output_mp3_path}")

        failed = [c for c in results if c.status == ChapterStatus.FAILED]
        for chapter in failed:
            print(f"Error: chapter '{chapter.title}' failed: {chapter.error}", file=sys.stderr)

        db.close()
        if failed:
            sys.exit(1)


if __name__ == "__main__":
    main()
