from fastapi.testclient import TestClient

from audiobookify.api.app import create_app


def _client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"), upload_dir=str(tmp_path / "uploads"))
    return TestClient(app)


def test_create_book_ingests_uploaded_markdown_and_returns_chapters(tmp_path):
    client = _client(tmp_path)
    md_bytes = b"# Chapter One\nFirst chapter body.\n\n# Chapter Two\nSecond chapter body.\n"

    response = client.post(
        "/api/books",
        files={"file": ("book.md", md_bytes, "text/markdown")},
        data={"output_dir": str(tmp_path / "out")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "book"
    assert len(body["chapters"]) == 2
    assert body["chapters"][0]["title"] == "Chapter One"
    assert body["chapters"][0]["status"] == "pending"
    assert body["chapters"][0]["progress"] is None


def test_create_book_rejects_unsupported_format(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/api/books",
        files={"file": ("book.txt", b"hello", "text/plain")},
        data={"output_dir": str(tmp_path / "out")},
    )

    assert response.status_code == 400
    assert "unsupported input format" in response.json()["detail"]


def test_create_book_creates_a_new_book_row_for_each_upload_even_with_the_same_filename(tmp_path):
    client = _client(tmp_path)
    md_bytes = b"# Ch\nBody.\n"

    first = client.post("/api/books", files={"file": ("book.md", md_bytes, "text/markdown")},
                         data={"output_dir": str(tmp_path / "out")})
    second = client.post("/api/books", files={"file": ("book.md", md_bytes, "text/markdown")},
                          data={"output_dir": str(tmp_path / "out")})

    assert first.json()["id"] != second.json()["id"]


def test_list_books_returns_all_imported_books(tmp_path):
    client = _client(tmp_path)
    client.post("/api/books", files={"file": ("a.md", b"# Ch\nBody.\n", "text/markdown")},
                 data={"output_dir": str(tmp_path / "out")})
    client.post("/api/books", files={"file": ("b.md", b"# Ch\nBody.\n", "text/markdown")},
                 data={"output_dir": str(tmp_path / "out")})

    response = client.get("/api/books")

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_get_book_returns_404_for_unknown_id(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/books/999")

    assert response.status_code == 404
