# telegram_sender.py
import requests
from typing import Optional

BOT_TOKEN = "8673614301:AAES4U3MaxOnSiRRWlaFOg2GISlilKwmolc"
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(
    chat_id: int | str,
    text: str,
    parse_mode: Optional[str] = "Markdown",
    disable_web_page_preview: bool = True,
) -> dict:
    """
    Отправляет текстовое сообщение пользователю/в чат через Bot API.
    """
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    resp = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    # Тест: отправить себе "ping"
    test_chat_id = 123456789  # узнай через /start и getUpdates
    send_message(test_chat_id, "Тестовое сообщение от агента")