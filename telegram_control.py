# telegram_control.py
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import requests
from telethon import TelegramClient
from dotenv import load_dotenv

from bot_config_store import BotConfigStore
from bot_validators import validate_telegram_source, validate_quik_ticker

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_SESSION_NAME = os.getenv("TG_SESSION_NAME", "monitor_account")

DB_PATH = os.getenv("BOT_CONFIG_DB_PATH", "bot_config.db")


class TelegramControlBot:
    def __init__(self, bot_token: str, db_path: str, quik_client: Any):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.store = BotConfigStore(db_path)
        self.quik = quik_client
        self.offset = 0
        self.tg_client: Optional[TelegramClient] = None

    async def start(self) -> None:
        self.tg_client = TelegramClient(TG_SESSION_NAME, TG_API_ID, TG_API_HASH)
        await self.tg_client.start()

        while True:
            try:
                updates = await self.get_updates_async(timeout=30)
                for update in updates:
                    self.offset = max(self.offset, update["update_id"] + 1)
                    await self.handle_update(update)
            except Exception as e:
                print(f"[bot loop error] {type(e).__name__}: {e}")
                await asyncio.sleep(2)

    def call_api(self, method: str, payload: dict) -> dict:
        resp = requests.post(f"{self.base_url}/{method}", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_updates(self, timeout: int = 30) -> list[dict]:
        payload = {
            "offset": self.offset,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        resp = requests.post(f"{self.base_url}/getUpdates", json=payload, timeout=timeout + 10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", [])

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.call_api("sendMessage", payload)

    def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> dict:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.call_api("editMessageText", payload)

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self.call_api("answerCallbackQuery", payload)

    def main_inline_menu(self, chat_id: int) -> tuple[str, dict]:
        settings = self.store.get_chat_settings(chat_id)
        status_text = "Активен" if settings.monitoring_enabled else "На паузе"

        text = (
            "Панель управления агентом\n\n"
            f"Статус мониторинга: {status_text}\n"
            f"Интервал опроса QUIK: {settings.poll_interval_seconds} сек.\n"
        )

        markup = {
            "inline_keyboard": [
                [{"text": "Telegram-каналы", "callback_data": "menu:tg_sources"}],
                [{"text": "Инструменты QUIK", "callback_data": "menu:quik_tools"}],
                [{"text": "Пауза / Возобновить", "callback_data": "menu:toggle_monitoring"}],
                [{"text": "Показать статус", "callback_data": "menu:status"}],
            ]
        }
        return text, markup

    def build_status_text(self, chat_id: int) -> str:
        settings = self.store.get_chat_settings(chat_id)
        tg_sources = self.store.list_telegram_sources(chat_id)
        quik_tools = self.store.list_quik_instruments(chat_id)

        active_tg = sum(1 for x in tg_sources if x["is_active"])
        active_quik = sum(1 for x in quik_tools if x["is_active"])

        return (
            f"Статус конфигурации для chat_id={chat_id}\n\n"
            f"Мониторинг: {'включен' if settings.monitoring_enabled else 'на паузе'}\n"
            f"Интервал QUIK: {settings.poll_interval_seconds} сек.\n"
            f"Telegram-источников: {len(tg_sources)} (активных {active_tg})\n"
            f"QUIK-инструментов: {len(quik_tools)} (активных {active_quik})"
        )

    def build_tg_sources_menu(self, chat_id: int) -> tuple[str, dict]:
        items = self.store.list_telegram_sources(chat_id)
        lines = ["Telegram-источники:\n"]
        keyboard = []

        if not items:
            lines.append("Пока пусто.")
        else:
            for item in items:
                status = "ON" if item["is_active"] else "OFF"
                title = item["title"] or item["username"] or item["source_input"]
                lines.append(f"{item['id']}. {title} [{status}]")
                keyboard.append(
                    [
                        {"text": f"Toggle {item['id']}", "callback_data": f"tg:toggle:{item['id']}"},
                        {"text": f"Delete {item['id']}", "callback_data": f"tg:delete:{item['id']}"},
                    ]
                )

        keyboard.append([{"text": "Добавить канал", "callback_data": "tg:add"}])
        keyboard.append([{"text": "Назад", "callback_data": "menu:home"}])

        return "\n".join(lines), {"inline_keyboard": keyboard}

    def build_quik_menu(self, chat_id: int) -> tuple[str, dict]:
        items = self.store.list_quik_instruments(chat_id)
        lines = ["Инструменты QUIK:\n"]
        keyboard = []

        if not items:
            lines.append("Пока пусто.")
        else:
            for item in items:
                status = "ON" if item["is_active"] else "OFF"
                name = item["display_name"] or item["sec_code"]
                lines.append(f"{item['id']}. {name} ({item['class_code']}.{item['sec_code']}) [{status}]")
                keyboard.append(
                    [
                        {"text": f"Toggle {item['id']}", "callback_data": f"quik:toggle:{item['id']}"},
                        {"text": f"Delete {item['id']}", "callback_data": f"quik:delete:{item['id']}"},
                    ]
                )

        keyboard.append([{"text": "Добавить тикер", "callback_data": "quik:add"}])
        keyboard.append([{"text": "Назад", "callback_data": "menu:home"}])

        return "\n".join(lines), {"inline_keyboard": keyboard}

    async def handle_update(self, update: dict) -> None:
        if "message" in update:
            await self.handle_message(update["message"])
        elif "callback_query" in update:
            await self.handle_callback(update["callback_query"])

    async def handle_message(self, message: dict) -> None:
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()

        self.store.ensure_chat_settings(chat_id)

        if text == "/start":
            if not self.store.is_reply_keyboard_removed(chat_id):
                await self.remove_reply_keyboard(chat_id, "")
                self.store.set_reply_keyboard_removed(chat_id, True)

            menu_text, markup = self.main_inline_menu(chat_id)
            await self.send_message_async(chat_id, menu_text, reply_markup=markup)
            return

        if text == "/menu":
            menu_text, markup = self.main_inline_menu(chat_id)
            await self.send_message_async(chat_id, menu_text, reply_markup=markup)
            return

        if text in {"/status", "Статус"}:
            await self.send_message_async(chat_id, self.build_status_text(chat_id))
            return

        state = self.store.get_dialog_state(chat_id)

        if state == "awaiting_tg_source":
            result = await validate_telegram_source(self.tg_client, text)
            if result["ok"]:
                self.store.add_telegram_source(
                    chat_id=chat_id,
                    source_input=result["input"],
                    source_id=result["source_id"],
                    username=result.get("username"),
                    title=result.get("title"),
                )
                self.store.set_dialog_state(chat_id, None)
                await self.send_message_async(
                    chat_id,
                    f"Канал сохранен: {result.get('title') or result.get('username') or result['source_id']}")
            else:
                await self.send_message_async(
                    chat_id,
                    "Не удалось проверить канал. Отправь username вида @channel")
            return

        if state == "awaiting_quik_ticker":
            result = await validate_quik_ticker(self.quik, text)
            if result["ok"]:
                self.store.add_quik_instrument(
                    chat_id=chat_id,
                    sec_code=result["sec_code"],
                    class_code=result["class_code"],
                    display_name=result.get("display_name"),
                )
                self.store.set_dialog_state(chat_id, None)
                await self.send_message_async(
                    chat_id,
                    f"Тикер сохранен: {result['class_code']}.{result['sec_code']} — {result.get('display_name') or result['sec_code']}")
            else:
                if result["reason"] == "ambiguous":
                    variants = ", ".join(x["class_code"] for x in result["matches"])
                    msg = f"Тикер найден в нескольких классах: {variants}. Нужна дополнительная логика уточнения."
                else:
                    msg = "Тикер не найден в TQBR/TQOB/TQTF."
                await self.send_message_async(chat_id, msg)
            return

        await self.send_message_async(
            chat_id,
            "Используй /menu")

    async def handle_callback(self, cq: dict) -> None:
        callback_id = cq["id"]
        data = cq["data"]
        message = cq["message"]
        chat_id = message["chat"]["id"]
        message_id = message["message_id"]

        await self.answer_callback_query_async(callback_id)

        if data == "menu:home":
            text, markup = self.main_inline_menu(chat_id)
            await self.edit_message_async(chat_id, message_id, text, reply_markup=markup)
            return

        if data == "menu:status":
            await self.edit_message_async(chat_id, message_id, self.build_status_text(chat_id), reply_markup={
                "inline_keyboard": [[{"text": "Назад", "callback_data": "menu:home"}]]
            })
            return

        if data == "menu:toggle_monitoring":
            settings = self.store.get_chat_settings(chat_id)
            self.store.set_monitoring_enabled(chat_id, not settings.monitoring_enabled)
            text, markup = self.main_inline_menu(chat_id)
            await self.edit_message_async(chat_id, message_id, text, reply_markup=markup)
            return

        if data == "menu:tg_sources":
            text, markup = self.build_tg_sources_menu(chat_id)
            await self.edit_message_async(chat_id, message_id, text, reply_markup=markup)
            return

        if data == "menu:quik_tools":
            text, markup = self.build_quik_menu(chat_id)
            await self.edit_message_async(chat_id, message_id, text, reply_markup=markup)
            return

        if data == "tg:add":
            self.store.set_dialog_state(chat_id, "awaiting_tg_source")
            await self.send_message_async(chat_id, "Отправь username канала (@name)")
            return

        if data.startswith("tg:toggle:"):
            row_id = int(data.split(":")[-1])
            self.store.toggle_telegram_source(chat_id, row_id)
            text, markup = self.build_tg_sources_menu(chat_id)
            await self.edit_message_async(chat_id, message_id, text, reply_markup=markup)
            return

        if data.startswith("tg:delete:"):
            row_id = int(data.split(":")[-1])
            self.store.delete_telegram_source(chat_id, row_id)
            text, markup = self.build_tg_sources_menu(chat_id)
            await self.edit_message_async(chat_id, message_id, text, reply_markup=markup)
            return

        if data == "quik:add":
            self.store.set_dialog_state(chat_id, "awaiting_quik_ticker")
            await self.send_message_async(chat_id, "Отправь тикер, например SBER или SBGB")
            return

        if data.startswith("quik:toggle:"):
            row_id = int(data.split(":")[-1])
            self.store.toggle_quik_instrument(chat_id, row_id)
            text, markup = self.build_quik_menu(chat_id)
            await self.edit_message_async(chat_id, message_id, text, reply_markup=markup)
            return

        if data.startswith("quik:delete:"):
            row_id = int(data.split(":")[-1])
            self.store.delete_quik_instrument(chat_id, row_id)
            text, markup = self.build_quik_menu(chat_id)
            await self.edit_message_async(chat_id, message_id, text, reply_markup=markup)
            return
        
    
    async def remove_reply_keyboard(self, chat_id: int, text: str = "Скрываю старую клавиатуру.") -> dict:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {"remove_keyboard": True},
            }
        return await self.call_api_async("sendMessage", payload)
    
    async def call_api_async(self, method: str, payload: dict) -> dict:
        return await asyncio.to_thread(self.call_api, method, payload)

    async def get_updates_async(self, timeout: int = 30) -> list[dict]:
        return await asyncio.to_thread(self.get_updates, timeout)

    async def send_message_async(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
        ) -> dict:
        return await asyncio.to_thread(self.send_message, chat_id, text, reply_markup)

    async def edit_message_async(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
        ) -> dict:
        return await asyncio.to_thread(
            self.edit_message,
            chat_id,
            message_id,
            text,
            reply_markup,
        )

    async def answer_callback_query_async(
        self,
        callback_query_id: str,
        text: str = "",
        ) -> None:
        await asyncio.to_thread(self.answer_callback_query, callback_query_id, text)


async def main(quik_client: Any) -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    bot = TelegramControlBot(
        bot_token=BOT_TOKEN,
        db_path=DB_PATH,
        quik_client=quik_client,
    )
    await bot.start()