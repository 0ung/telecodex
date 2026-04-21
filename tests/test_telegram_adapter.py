from __future__ import annotations

from telecodex.gateway.telegram import TelegramAdapter


def test_telegram_adapter_parses_caption_and_photo(monkeypatch) -> None:  # noqa: ANN001
    def fake_get_updates(self, offset, timeout_sec):  # noqa: ANN001
        return [
            {
                "update_id": 7,
                "message": {
                    "chat": {"id": 10, "type": "private"},
                    "from": {"id": 1},
                    "caption": "analyze this image",
                    "photo": [
                        {"file_id": "small", "file_unique_id": "u1"},
                        {"file_id": "large", "file_unique_id": "u2"},
                    ],
                },
            }
        ]

    def fake_get_file(self, file_id: str):  # noqa: ANN001
        return {"file_id": file_id, "file_path": "photos/test.jpg"}

    def fake_download_file(self, file_path: str) -> bytes:  # noqa: ANN001
        return b"image-bytes"

    monkeypatch.setattr(TelegramAdapter, "_get_updates", fake_get_updates)
    monkeypatch.setattr(TelegramAdapter, "get_file", fake_get_file)
    monkeypatch.setattr(TelegramAdapter, "download_file", fake_download_file)

    adapter = TelegramAdapter(token="token")
    messages = adapter.poll_messages(timeout_sec=30)

    assert len(messages) == 1
    assert messages[0].conversation_id == "10"
    assert messages[0].text == "analyze this image"
    assert len(messages[0].attachments) == 1
    assert messages[0].attachments[0].mime_type == "image/jpeg"
