import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from telethon import TelegramClient
from telethon.tl.types import Message, MessageEntityTextUrl  # type: ignore

from ingest_store import IngestStore, compute_since_minutes

import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

load_dotenv(Path(".") / ".env")

API_ID = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "monitor_account")

SOURCE_TYPE_TELEGRAM = "telegram"


def _extract_links(msg: Message) -> List[str]:
    urls: List[str] = []

    if msg.entities:
        for ent in msg.entities:
            if isinstance(ent, MessageEntityTextUrl):
                urls.append(ent.url)
            else:
                offset = getattr(ent, "offset", None)
                length = getattr(ent, "length", None)
                if offset is None or length is None:
                    continue
                if msg.raw_text:
                    candidate = msg.raw_text[offset : offset + length]
                    if candidate.startswith("http://") or candidate.startswith("https://"):
                        urls.append(candidate)

    if msg.raw_text:
        for token in msg.raw_text.split():
            if token.startswith("http://") or token.startswith("https://"):
                urls.append(token.strip(".,);]}>\"'"))

    return list(dict.fromkeys(urls))


def _build_source_id(entity: Any) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"tg:{username.lower()}"
    entity_id = getattr(entity, "id", None)
    return f"tg:{entity_id}"


def _message_to_dict(msg: Message) -> Dict[str, Any]:
    links = _extract_links(msg)

    file_meta: Optional[Dict[str, Any]] = None
    if msg.document:
        file_meta = {
            "id": msg.document.id,
            "name": getattr(msg.file, "name", None),
            "size": getattr(msg.document, "size", None),
            "mime_type": getattr(msg.document, "mime_type", None),
        }

    reply_to_msg_id = getattr(msg, "reply_to_msg_id", None)
    peer_id = getattr(msg, "peer_id", None)

    chat_id = getattr(msg, "chat_id", None)
    chat_title = (
        getattr(msg.chat, "title", None)
        or getattr(msg.chat, "username", None)
        or str(chat_id)
    )
    chat_username = getattr(msg.chat, "username", None)

    return {
        "chat_id": chat_id,
        "chat_title": chat_title,
        "chat_username": chat_username,
        "message_id": msg.id,
        "date": msg.date.astimezone(timezone.utc).isoformat() if msg.date else None,
        "author_id": getattr(msg, "sender_id", None),
        "is_reply": reply_to_msg_id is not None,
        "reply_to_msg_id": reply_to_msg_id,
        "text": msg.raw_text or "",
        "links": links,
        "has_media": bool(msg.media),
        "file": file_meta,
        "peer_id": str(peer_id) if peer_id is not None else None,
    }


async def fetch_new_messages_for_source(
    client: TelegramClient,
    source: str | int,
    since_utc: datetime,
    cursor_ts: Optional[datetime] = None,
    cursor_id: Optional[int] = None,
    limit: int = 1000,
) -> tuple[str, List[Dict[str, Any]]]:
    entity = await client.get_entity(source)
    source_id = _build_source_id(entity)

    out: List[Dict[str, Any]] = []

    async for msg in client.iter_messages(entity, limit=limit):
        if not isinstance(msg, Message):
            continue
        if not msg.date:
            continue

        msg_dt = msg.date.astimezone(timezone.utc)

        if msg_dt < since_utc:
            break

        if cursor_ts is not None:
            if msg_dt < cursor_ts:
                break
            if msg_dt == cursor_ts and cursor_id is not None and msg.id <= cursor_id:
                break

        out.append(_message_to_dict(msg))

    out.reverse()
    return source_id, out


async def update_single_source(
    source: str | int,
    store: IngestStore,
    default_minutes: int = 24 * 60,
    overlap_minutes: int = 10,
    limit: int = 1000,
) -> Dict[str, Any]:
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        entity = await client.get_entity(source)
        source_id = _build_source_id(entity)

        cursor_ts_raw, cursor_id_raw = store.get_cursor(SOURCE_TYPE_TELEGRAM, source_id)
        since_utc = compute_since_minutes(
            cursor_ts=cursor_ts_raw,
            default_minutes=default_minutes,
            overlap_minutes=overlap_minutes,
        )

        cursor_ts = datetime.fromisoformat(cursor_ts_raw) if cursor_ts_raw else None
        cursor_id = int(cursor_id_raw) if cursor_id_raw else None

        _, messages = await fetch_new_messages_for_source(
            client=client,
            source=source,
            since_utc=since_utc,
            cursor_ts=cursor_ts,
            cursor_id=cursor_id,
            limit=limit,
        )

        inserted = store.upsert_telegram_messages(messages, source_id=source_id)

        if messages:
            last_msg = max(
                messages,
                key=lambda x: (x["date"] or "", x["message_id"]),
            )
            store.save_cursor(
                SOURCE_TYPE_TELEGRAM,
                source_id,
                last_msg["date"],
                str(last_msg["message_id"]),
            )

        return {
            "source_id": source_id,
            "fetched": len(messages),
            "upserted": inserted,
            "cursor_ts": messages[-1]["date"] if messages else cursor_ts_raw,
            "cursor_id": str(messages[-1]["message_id"]) if messages else cursor_id_raw,
        }
    

def sync_single_source(
    source: str | int,
    db_path: str = "ingest_state.db",
    default_minutes: int = 24 * 60,
    overlap_minutes: int = 10,
    limit: int = 1000,
) -> Dict[str, Any]:
    store = IngestStore(db_path=db_path)
    return asyncio.run(
        update_single_source(
            source=source,
            store=store,
            default_minutes=default_minutes,
            overlap_minutes=overlap_minutes,
            limit=limit,
        )
    )


async def refresh_and_get_telegram_messages(
    source: str | int,
    db_path: str = "ingest_state.db",
    default_minutes: int = 24 * 60,
    overlap_minutes: int = 10,
    fetch_limit: int = 1000,
    return_minutes: Optional[int] = None,
    only_with_media: bool = False,
    only_replies: bool = False,
    result_limit: Optional[int] = None,
) -> list[dict]:
    store = IngestStore(db_path=db_path)

    refresh_result = await update_single_source(
        source=source,
        store=store,
        default_minutes=default_minutes,
        overlap_minutes=overlap_minutes,
        limit=fetch_limit,
    )

    source_id = refresh_result["source_id"]

    return store.get_telegram_messages_from_db(
        source_id=source_id,
        minutes=return_minutes if return_minutes is not None else default_minutes,
        only_with_media=only_with_media,
        only_replies=only_replies,
        limit=result_limit,
    )


if __name__ == "__main__":
    result = sync_single_source(
        source="https://t.me/some_public_channel",
        db_path="ingest_state.db",
        default_minutes=180,
        overlap_minutes=10,
        limit=500,
    )
    print(result)