from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from scripts import telegram_codex as bridge


def document_message(caption=None):
    message = {
        "message_id": 42,
        "date": 1791417600,
        "from": {"id": 7, "first_name": "User"},
        "chat": {"id": 9},
        "document": {
            "file_id": "file-id",
            "file_name": "../../Research Paper.pdf",
            "mime_type": "application/pdf",
            "file_size": 4,
        },
    }
    if caption is not None:
        message["caption"] = caption
    return message


def mock_download(monkeypatch, tmp_path, content=b"test"):
    monkeypatch.setattr(bridge, "RAW_ROOT", tmp_path / "raw")
    api = Mock(return_value={"result": {"file_path": "documents/file.pdf", "file_size": 4}})
    download = Mock(side_effect=lambda *args, **kwargs: BytesIO(content))
    monkeypatch.setattr(bridge, "api_request", api)
    monkeypatch.setattr(bridge, "urlopen", download)
    return api, download


@pytest.mark.parametrize("caption", [None, "Summarize the attached paper"])
def test_document_reaches_codex_with_local_file_and_caption(monkeypatch, tmp_path, caption):
    mock_download(monkeypatch, tmp_path)
    send = Mock()
    run = Mock(return_value=(0, "Done"))
    monkeypatch.setattr(bridge, "send_message", send)
    monkeypatch.setattr(bridge, "run_codex", run)

    bridge.handle_update("token", {"message": document_message(caption)}, "7", None, tmp_path, "codex")

    prompt = run.call_args.args[0]
    saved = list((tmp_path / "raw").iterdir())
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"test"
    assert str(saved[0]) in prompt
    assert "application/pdf" in prompt
    assert caption in prompt if caption else "ingest the document" in prompt
    assert "Docling" in prompt
    assert send.call_args.args == ("token", 9, "Done")


def test_plain_text_still_runs_codex(monkeypatch, tmp_path):
    message = document_message()
    del message["document"]
    message["text"] = "Explain attention"
    run = Mock(return_value=(0, "Answer"))
    download = Mock()
    monkeypatch.setattr(bridge, "run_codex", run)
    monkeypatch.setattr(bridge, "download_document", download)
    monkeypatch.setattr(bridge, "send_message", Mock())
    bridge.handle_update("token", {"message": message}, "7", None, tmp_path, "codex")
    assert "Explain attention" in run.call_args.args[0]
    download.assert_not_called()


def test_unauthorized_document_is_ignored_before_download(monkeypatch, tmp_path):
    download, run, send = Mock(), Mock(), Mock()
    monkeypatch.setattr(bridge, "download_document", download)
    monkeypatch.setattr(bridge, "run_codex", run)
    monkeypatch.setattr(bridge, "send_message", send)
    bridge.handle_update("token", {"message": document_message()}, "99", None, tmp_path, "codex")
    download.assert_not_called()
    run.assert_not_called()
    send.assert_not_called()


def test_download_failure_does_not_run_or_expose_token(monkeypatch, tmp_path):
    error = HTTPError("https://api.telegram.org/file/botSECRET/file.pdf", 500, "error", {}, None)
    monkeypatch.setattr(bridge, "download_document", Mock(side_effect=error))
    run, send = Mock(), Mock()
    monkeypatch.setattr(bridge, "run_codex", run)
    monkeypatch.setattr(bridge, "send_message", send)
    bridge.handle_update("SECRET", {"message": document_message()}, "7", None, tmp_path, "codex")
    run.assert_not_called()
    assert "Could not download document" in send.call_args.args[2]
    assert "SECRET" not in send.call_args.args[2]


def test_safe_filenames_immutable_sources_and_distinct_messages(monkeypatch, tmp_path):
    _, download = mock_download(monkeypatch, tmp_path)
    message = document_message()
    first = bridge.download_document("token", message)
    assert first.parent == tmp_path / "raw"
    assert first.name.endswith("-research-paper.pdf")
    assert bridge.download_document("token", message) == first
    assert download.call_count == 1
    message["message_id"] = 43
    second = bridge.download_document("token", message)
    assert second != first
    assert first.read_bytes() == second.read_bytes() == b"test"
    assert not list((tmp_path / "raw").glob(".telegram-*"))


def test_oversized_document_is_rejected_before_network(monkeypatch, tmp_path):
    api, download = mock_download(monkeypatch, tmp_path)
    message = document_message()
    message["document"]["file_size"] = bridge.MAX_DOCUMENT_BYTES + 1
    with pytest.raises(bridge.DocumentTooLarge):
        bridge.download_document("token", message)
    api.assert_not_called()
    download.assert_not_called()
    assert not (tmp_path / "raw").exists()


@pytest.mark.parametrize("content, limit, error", [(b"abc", 10, RuntimeError), (b"12345", 4, bridge.DocumentTooLarge)])
def test_partial_or_oversized_download_leaves_no_raw_file(monkeypatch, tmp_path, content, limit, error):
    mock_download(monkeypatch, tmp_path, content)
    monkeypatch.setattr(bridge, "MAX_DOCUMENT_BYTES", limit)
    with pytest.raises(error):
        bridge.download_document("token", document_message())
    assert not list((tmp_path / "raw").iterdir())
