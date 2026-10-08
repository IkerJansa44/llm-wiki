#!/usr/bin/env python3
"""Forward allowed Telegram messages to non-interactive Codex runs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_STATE_DIR = Path("var/telegram-codex")
DEFAULT_TIMEOUT = 60
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
MAX_TELEGRAM_MESSAGE = 3900
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[1]
VAULT_ROOT = (REPO_ROOT / "vault").resolve()
RAW_ROOT = VAULT_ROOT / "raw"
OFFSET_FILE = "offset.txt"


class DocumentTooLarge(ValueError):
    pass


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def api_request(token: str, method: str, payload: dict[str, object]) -> dict[str, object]:
    data = urlencode(payload).encode()
    request = Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        body = json.loads(response.read().decode())
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body}")
    return body


def read_offset(state_dir: Path) -> int:
    path = state_dir / OFFSET_FILE
    if not path.exists():
        return 0
    return int(path.read_text(encoding="utf-8").strip() or "0")


def write_offset(state_dir: Path, offset: int) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / OFFSET_FILE).write_text(f"{offset}\n", encoding="utf-8")


def user_allowed(user: dict[str, object], allowed_id: str | None, username: str | None) -> bool:
    if allowed_id and str(user.get("id")) == allowed_id:
        return True
    if (
        username
        and str(user.get("username", "")).lower() == username.lower().lstrip("@")
    ):
        return True
    return False


def download_document(token: str, message: dict[str, object]) -> Path:
    """Preserve a complete attachment without overwriting an existing raw source."""
    document = message["document"]
    if document.get("file_size", 0) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLarge("Document exceeds the Telegram bot download limit of 20 MB.")

    body = api_request(token, "getFile", {"file_id": document["file_id"]})
    file_info = body.get("result")
    if not isinstance(file_info, dict) or not file_info.get("file_path"):
        raise RuntimeError("Telegram did not provide a download path.")
    remote_path = str(file_info["file_path"])
    original_name = str(document.get("file_name") or Path(remote_path).name)
    basename = Path(original_name.replace("\\", "/")).name
    suffix = re.sub(r"[^a-zA-Z0-9.]", "", Path(basename).suffix).lower()[:16]
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", Path(basename).stem).strip("-").lower()
    day = datetime.fromtimestamp(int(message["date"]), tz=UTC).strftime("%Y-%m-%d")
    chat_id = int(message["chat"]["id"])
    message_id = int(message["message_id"])
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    destination = RAW_ROOT / f"{day}-telegram-{chat_id}-{message_id}-{stem[:100] or 'document'}{suffix}"
    if destination.exists():
        return destination

    url = f"https://api.telegram.org/file/bot{token}/{quote(remote_path, safe='/')}"
    with urlopen(url, timeout=DEFAULT_TIMEOUT) as response:
        content = response.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLarge("Document exceeds the Telegram bot download limit of 20 MB.")
    expected_size = file_info.get("file_size", document.get("file_size"))
    if expected_size is not None and len(content) != expected_size:
        raise RuntimeError("Telegram document download was incomplete.")

    # Publish only complete downloads; hard-linking never replaces a raw source.
    with tempfile.NamedTemporaryFile(dir=RAW_ROOT, prefix=".telegram-", delete=False) as temp:
        temporary_path = Path(temp.name)
        try:
            temp.write(content)
            temp.flush()
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                pass
        finally:
            temporary_path.unlink(missing_ok=True)
    return destination


def prompt_for(message: dict[str, object], document_path: Path | None = None) -> str:
    user = message.get("from", {})
    if not isinstance(user, dict):
        user = {}
    text = str(message.get("text") or message.get("caption") or "").strip()
    name = " ".join(
        str(user.get(key, "")).strip() for key in ("first_name", "last_name")
    ).strip()
    username = user.get("username")
    sent_at = datetime.fromtimestamp(int(message["date"]), tz=UTC).isoformat()

    prompt = textwrap.dedent(
        f"""
        Telegram message received from allowed user.

        Sender: {name or "unknown"}{f" (@{username})" if username else ""}
        Telegram user id: {user.get("id")}
        Message date: {sent_at}

        Message:
        {text}
        """
    ).strip()
    if document_path is not None:
        document = message["document"]
        prompt += "\n\nAttached document (already downloaded and preserved):\n"
        prompt += f"Local path: {document_path}\n"
        prompt += f"Original filename: {document.get('file_name', document_path.name)}\n"
        prompt += f"MIME type: {document.get('mime_type', 'unknown')}\n"
        prompt += (
            "Read the local attachment and follow the caption's instructions. "
            "If there is no caption, ingest the document into the wiki according to AGENTS.md. "
            "Preserve the downloaded raw file unchanged. For PDFs, use the repository "
            "Docling helper to create a working extraction and verify important claims "
            "against the original PDF."
        )
    return prompt


def run_codex(prompt: str, state_dir: Path, codex_bin: str) -> tuple[int, str]:
    state_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = state_dir / f"{stamp}-last-message.txt"
    command = [
        codex_bin,
        "--ask-for-approval",
        "never",
        "exec",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(REPO_ROOT),
        "--add-dir",
        str(VAULT_ROOT),
        "--output-last-message",
        str(output_path),
        "-",
    ]
    process_env = os.environ.copy()
    process_env["PATH"] = f"{process_env.get('PATH', '')}:{DEFAULT_PATH}"
    result = subprocess.run(
        command,
        input=prompt,
        cwd=REPO_ROOT,
        env=process_env,
        text=True,
        capture_output=True,
        check=False,
    )
    if output_path.exists():
        last_message = output_path.read_text(encoding="utf-8").strip()
        if last_message:
            return result.returncode, last_message

    output = "\n".join(
        part.strip() for part in (result.stdout, result.stderr) if part.strip()
    )
    return result.returncode, output.strip() or "Codex finished without output."


def send_message(token: str, chat_id: object, text: str) -> None:
    api_request(
        token,
        "sendMessage",
        {"chat_id": chat_id, "text": text[-MAX_TELEGRAM_MESSAGE:]},
    )


def handle_update(
    token: str,
    update: dict[str, object],
    allowed_id: str | None,
    username: str | None,
    state_dir: Path,
    codex_bin: str,
) -> None:
    message = update.get("message")
    if not isinstance(message, dict):
        return
    document = message.get("document")
    has_document = isinstance(document, dict) and bool(document.get("file_id"))
    if not message.get("text") and not has_document:
        return

    user = message.get("from")
    if not isinstance(user, dict) or not user_allowed(user, allowed_id, username):
        return

    chat = message.get("chat")
    if not isinstance(chat, dict) or "id" not in chat:
        return

    chat_id = chat["id"]
    document_path = None
    if has_document:
        send_message(token, chat_id, "Downloading document...")
        try:
            document_path = download_document(token, message)
        except DocumentTooLarge as exc:
            send_message(token, chat_id, f"Could not download document: {exc}")
            return
        except Exception:
            # HTTP exceptions can contain the bot token in the download URL.
            send_message(token, chat_id, "Could not download document. Please resend the attachment.")
            return
    send_message(token, chat_id, "Running Codex...")
    code, output = run_codex(prompt_for(message, document_path), state_dir, codex_bin)
    if code == 0:
        send_message(token, chat_id, output)
        return

    send_message(token, chat_id, f"Codex failed with exit code {code}.\n\n{output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll Telegram and run Codex for messages from one allowed user."
    )
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv(args.dotenv)
    token = env("TELEGRAM_BOT_TOKEN")
    allowed_id = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "").strip() or None
    username = os.environ.get("TELEGRAM_ALLOWED_USERNAME", "").strip() or None
    if not allowed_id and not username:
        raise SystemExit(
            "Set TELEGRAM_ALLOWED_USER_ID or TELEGRAM_ALLOWED_USERNAME in the environment."
        )

    args.state_dir.mkdir(parents=True, exist_ok=True)
    offset = read_offset(args.state_dir)
    while True:
        try:
            body = api_request(
                token,
                "getUpdates",
                {
                    "timeout": 50,
                    "offset": offset,
                    "allowed_updates": json.dumps(["message"]),
                },
            )
            for update in body.get("result", []):
                offset = int(update["update_id"]) + 1
                write_offset(args.state_dir, offset)
                handle_update(
                    token,
                    update,
                    allowed_id,
                    username,
                    args.state_dir,
                    args.codex_bin,
                )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"telegram_codex error: {exc}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
