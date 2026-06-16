# bot_validators.py
from __future__ import annotations

import re
from typing import Any, Optional

from telethon import TelegramClient

STANDARD_QUIK_CLASSES = ["TQBR", "TQCB", "TQTF"]


def normalize_telegram_source(raw: str) -> str:
    s = raw.strip()
    if s.startswith("https://t.me/"):
        s = s.replace("https://t.me/", "", 1)
    if s.startswith("http://t.me/"):
        s = s.replace("http://t.me/", "", 1)
    if s.startswith("@"):
        s = s[1:]
    return s.strip().strip("/")


def looks_like_telegram_username(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_]{4,64}", value) is not None


async def validate_telegram_source(
    client: TelegramClient,
    source_raw: str,
) -> dict[str, Any]:
    normalized = normalize_telegram_source(source_raw)

    if not normalized or not looks_like_telegram_username(normalized):
        return {
            "ok": False,
            "reason": "invalid_format",
            "input": source_raw,
        }

    try:
        entity = await client.get_entity(normalized)
    except Exception as e:
        return {
            "ok": False,
            "reason": "not_found",
            "input": source_raw,
            "error": f"{type(e).__name__}: {e}",
        }

    username = getattr(entity, "username", None)
    title = getattr(entity, "title", None) or getattr(entity, "first_name", None)
    entity_id = getattr(entity, "id", None)
    source_id = f"tg:{username.lower()}" if username else f"tg:{entity_id}"

    return {
        "ok": True,
        "input": source_raw,
        "normalized": normalized,
        "source_id": source_id,
        "username": username,
        "title": title,
    }

async def validate_quik_ticker(quik, ticker_raw: str) -> dict[str, Any]:
    sec_code = ticker_raw.strip().upper()

    if not sec_code:
        return {
            "ok": False,
            "reason": "empty",
            "input": ticker_raw,
        }

    matches: list[dict[str, Any]] = []

    for class_code in STANDARD_QUIK_CLASSES:
        try:
            resp = await quik._clazz.get_security_info(class_code, sec_code)
            #resp = quik.GetSecurityInfo(class_code, sec_code)
        except Exception as e:
            continue

        if not resp:
            continue

        display_name = resp.name

        matches.append(
            {
                "sec_code": sec_code,
                "class_code": class_code,
                "display_name": display_name,
                "raw": resp,
            }
        )

    if len(matches) == 1:
        return {
            "ok": True,
            "sec_code": matches[0]["sec_code"],
            "class_code": matches[0]["class_code"],
            "display_name": matches[0]["display_name"],
            "raw": matches[0]["raw"],
        }

    if len(matches) == 0:
        return {
            "ok": False,
            "reason": "not_found",
            "input": ticker_raw,
        }

    return {
        "ok": False,
        "reason": "ambiguous",
        "input": ticker_raw,
        "matches": [
            {
                "sec_code": x["sec_code"],
                "class_code": x["class_code"],
                "display_name": x["display_name"],
            }
            for x in matches
        ],
    }