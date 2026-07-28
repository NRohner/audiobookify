import argparse
from pathlib import Path
from typing import List, Optional

from .db import Database
from .engines.chatterbox import ChatterboxEngine
from .ingest import epub, markdown
from .models import SourceFormat
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
        db = Database(args.db)
        book = db.find_book_by_source(args.input)
        if book is None:
            parsed, source_format = _ingest(args.input, args.heading_level)
            book = db.create_book(
                title=parsed.title, author=parsed.author, source_path=args.input,
                source_format=source_format, output_dir=args.out,
            )
            db.add_chapters(book.id, parsed.chapters)

        engine = ChatterboxEngine()
        results = generate_book(book, engine, db, {"voice_name": args.voice}, device=args.device)
        for chapter in results:
            print(f"{chapter.status.value}: {chapter.title} -> {chapter.output_mp3_path}")
        db.close()


if __name__ == "__main__":
    main()
