from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from zoneinfo import ZoneInfo

import html
from bs4 import BeautifulSoup, Tag
from ingest_store import IngestStore, compute_since_minutes, utc_now_iso


KOMMERSANT_DOCLAZY_URL = "https://www.kommersant.ru/doc/doclazyload"
KOMMERSANT_LENTA_URL = "https://www.kommersant.ru/listpage/lazyloaddocs"

MSK_TZ = ZoneInfo("Europe/Moscow")


def _parse_kommersant_ms_date(value: Optional[str]) -> Optional[datetime]:
    """
    Парсит строки вида '/Date(1781468986560)/' в timezone-aware datetime (UTC).
    """
    if not value:
        return None

    m = re.search(r"/Date\((\d+)\)/", value)
    if not m:
        return None

    ts_ms = int(m.group(1))
    return datetime.fromtimestamp(ts_ms / 1000, tz=MSK_TZ)


def _build_item_url(item: Dict[str, Any]) -> Optional[str]:
    """
    У kommersant в ответе Link часто null, поэтому надежнее строить URL из DocsID.
    """
    docs_id = item.get("DocsID")
    if docs_id:
        return f"https://www.kommersant.ru/doc/{docs_id}"
    return None


def parse_kommersant_latest_news(
    minutes: int = 12 * 60,
    regionid: int = 77,
    listtypeid: int = 3,
    listid: int = 77,
    timeout: int = 20,
    sleep_sec: float = 0.2,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """
    Забирает новости из ленты kommersant.ru за последние `minutes` часов.

    Логика:
    1. Первый запрос делаем без idafter — получаем самую свежую страницу.
    2. На каждой следующей итерации передаем idafter = DocsID последнего элемента
       предыдущего батча.
    3. Собираем элементы, пока не встретим новости старше заданной границы.
    4. Защищаемся от дублей и зацикливания.

    Возвращает список словарей:
    [
        {
            "docs_id": 8737748,
            "title": "...",
            "subtitle": "...",
            "published_at": datetime(...),
            "published_at_iso": "...",
            "url": "...",
            "tags": [...],
            "raw": {...}
        },
        ...
    ]
    """
    if minutes <= 0:
        raise ValueError("minutes must be > 0")

    if session is None:
        session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.kommersant.ru/",
        }
    )

    now_utc = datetime.now(timezone.utc)
    threshold_utc = now_utc - timedelta(minutes=minutes)

    results: List[Dict[str, Any]] = []
    seen_ids = set()

    idafter: Optional[int] = None
    prev_last_docs_id: Optional[int] = None
    stop = False

    while not stop:
        params = {
            "regionid": regionid,
            "listtypeid": listtypeid,
            "listid": listid,
            "date": "",
            "intervaltype": "",
        }
        if idafter is not None:
            params["idafter"] = idafter

        resp = session.get(KOMMERSANT_LENTA_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()

        items = payload.get("Items") or []
        has_next = bool(payload.get("HasNextPage"))

        if not items:
            break

        current_batch_new = 0

        for item in items:
            docs_id = item.get("DocsID")
            if docs_id is None or docs_id in seen_ids:
                continue

            published_at = _parse_kommersant_ms_date(item.get("DateBegin"))
            if published_at is None:
                date_doc = item.get("DateDoc")
                if date_doc:
                    published_at = datetime.fromtimestamp(int(date_doc), tz=timezone.utc)

            if published_at is None:
                continue

            if published_at < threshold_utc:
                stop = True
                continue

            seen_ids.add(docs_id)
            current_batch_new += 1

            results.append(
                {
                    "docs_id": docs_id,
                    "title": item.get("Title", "") or "",
                    "subtitle": item.get("SubTitle", "") or "",
                    "published_at": published_at,
                    "published_at_iso": published_at.isoformat(),
                    "url": _build_item_url(item),
                    "tags": [tag.get("Name") for tag in item.get("Tags", []) if tag.get("Name")],
                    "raw": item,
                }
            )

        last_docs_id = items[-1].get("DocsID")
        if not has_next:
            break

        if last_docs_id is None:
            break

        if prev_last_docs_id == last_docs_id:
            break

        if current_batch_new == 0 and stop:
            break

        prev_last_docs_id = last_docs_id
        idafter = last_docs_id

        if sleep_sec > 0:
            time.sleep(sleep_sec)

    results.sort(key=lambda x: x["published_at"], reverse=True)
    return results


def fetch_kommersant_html_by_docid(
    doc_id: int,
    timeout: int = 20,
    session: Optional[requests.Session] = None,
) -> str:
    """
    Забирает HTML-фрагмент статьи (поле `html`) по DocID через doclazyload.
    """
    if session is None:
        session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://www.kommersant.ru/doc/{doc_id}",
        }
    )

    resp = session.get(
        KOMMERSANT_DOCLAZY_URL,
        params={"id": doc_id},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("html") or ""

def _clean_text(node: Tag) -> str:
    """
    Собирает текст из тега, убирая лишние пробелы и декодируя HTML-сущности.
    """
    txt = " ".join(node.stripped_strings)
    return html.unescape(txt).strip()


def _make_p(text: str) -> Tag:
    p = Tag(name="p")
    p.string = text
    return p


def _make_blockquote(text: str) -> Tag:
    bq = Tag(name="blockquote")
    p = Tag(name="p")
    p.string = text
    bq.append(p)
    return bq


def _wrap_in_section(section_name: Optional[str], children: list[Tag]) -> Tag:
    """
    Создаёт <section> с опциональным <h2> и наборам детей.
    """
    section = Tag(name="section")
    if section_name:
        h2 = Tag(name="h2")
        h2.string = section_name
        section.append(h2)
    for ch in children:
        section.append(ch)
    return section


def extract_kommersant_article_light_html(html_str: str) -> str:
    """
    Превращает HTML от doclazyload в "лёгкий" HTML:

    - Корень: <article>
    - Внутри:
        <h1>…</h1>
        <h2>…</h2> (подзаголовок, если есть)
        <section> ... </section> по главам:
            <h2>Название секции</h2> (если есть data-section-name)
            <p>абзацы</p>
            <blockquote><p>цитаты</p></blockquote>
            <aside class="incut">…</aside> для врезок
            <table>…</table> (если были)
    - Удаляет скрипты, стили, рекламные и интерфейсные блоки.
    - Все атрибуты, кроме явно нужных, вычищаются.
    """
    soup = BeautifulSoup(html_str, "html.parser")

    orig_article = soup.select_one("article.doc.js-article")
    if orig_article is None:
        orig_article = soup  # fallback

    # Новый "чистый" article
    clean_article = BeautifulSoup("", "html.parser").new_tag("article")

    # Заголовок
    h1 = orig_article.select_one("header.doc_header h1.doc_header__name")
    if h1:
        title = _clean_text(h1)
        if title:
            h1_clean = Tag(name="h1")
            h1_clean.string = title
            clean_article.append(h1_clean)

    # Подзаголовок
    h2 = orig_article.select_one("header.doc_header h2.doc_header__subheader")
    if h2:
        subtitle = _clean_text(h2)
        if subtitle:
            h2_clean = Tag(name="h2")
            h2_clean.string = subtitle
            clean_article.append(h2_clean)

    # Секции
    sections = orig_article.select("section.js-doc-section")
    if not sections:
        # fallback: без явных секций
        text_wrapper = orig_article.select_one("div.article_text_wrapper") or orig_article

        # удаляем заведомо не контентные блоки
        for selector in [
            "nav.doc_nav",
            "div.doc_media",
            "div.ui-modal",
            "script",
            "style",
        ]:
            for node in text_wrapper.select(selector):
                node.decompose()

        # обрабатываем инкуты/врезки отдельно: станет <aside class="incut">
        for incut_sel in ["div.incut", "div.incut_content"]:
            for incut in text_wrapper.select(incut_sel):
                incut.replace_with(_convert_incut_to_aside(incut))

        # собираем абзацы/цитаты напрямую в article
        for p in text_wrapper.select("p.doc__intro, p.doc__text, p.doc__thought"):
            txt = _clean_text(p)
            if not txt:
                continue
            classes = p.get("class", [])
            if "doc__thought" in classes:
                clean_article.append(_make_blockquote(txt))
            else:
                clean_article.append(_make_p(txt))

        # таблицы, если есть
        for table in text_wrapper.select("table"):
            clean_article.append(_clean_table(table))

        return clean_article.prettify()

    # Если секции есть — идём по ним и собираем секции в clean_article
    for section in sections:
        section_name = (section.get("data-section-name") or "").strip() or None

        # Копия для манипуляций
        sect_clone = section.__copy__()

        # Удаляем не контент
        for selector in [
            "nav.doc_nav",
            "div.doc_media",
            "div.ui-modal",
            "script",
            "style",
        ]:
            for node in sect_clone.select(selector):
                node.decompose()

        # Врезки → <aside class="incut">
        for incut_sel in ["div.incut", "div.incut_content"]:
            for incut in sect_clone.select(incut_sel):
                incut.replace_with(_convert_incut_to_aside(incut))

        children: list[Tag] = []

        # Абзацы и цитаты
        for p in sect_clone.select("p.doc__intro, p.doc__text, p.doc__thought"):
            txt = _clean_text(p)
            if not txt:
                continue
            classes = p.get("class", [])
            if "doc__thought" in classes:
                children.append(_make_blockquote(txt))
            else:
                children.append(_make_p(txt))

        # Врезки (уже заменены на <aside class="incut">)
        for aside in sect_clone.select("aside.incut"):
            children.append(aside)

        # Таблицы
        for table in sect_clone.select("table"):
            children.append(_clean_table(table))

        if not children and not section_name:
            # Пустую секцию можно пропустить
            continue

        clean_section = _wrap_in_section(section_name, children)
        clean_article.append(clean_section)

    return clean_article.prettify()


def _convert_incut_to_aside(incut_node: Tag) -> Tag:
    """
    Делает из incut/incut_content лёгкий <aside class="incut"> с содержимым.
    Выкидываем почти все атрибуты/классы, кроме семантической метки.
    """
    aside = Tag(name="aside")
    aside["class"] = ["incut"]

    # Пробуем вытащить заголовок врезки, если есть
    title = incut_node.select_one("h2, h3, h4")
    if title:
        h = Tag(name="h3")
        h.string = _clean_text(title)
        aside.append(h)

    # Абзацы внутри — обычные <p>
    for p in incut_node.select("p"):
        txt = _clean_text(p)
        if txt:
            aside.append(_make_p(txt))

    # Если ничего не нашли, хотя бы текст всего блока
    if not aside.contents:
        txt = _clean_text(incut_node)
        if txt:
            aside.append(_make_p(txt))

    return aside


def _clean_table(table_node: Tag) -> Tag:
    """
    Преобразует таблицу в простой <table> без стилей и лишних атрибутов.
    Сохраняем структуру thead/tbody/tr/th/td, текст чистим.
    """
    table = Tag(name="table")

    # Если есть thead
    thead_node = table_node.find("thead")
    if thead_node:
        thead = Tag(name="thead")
        for tr_node in thead_node.find_all("tr"):
            tr = Tag(name="tr")
            for cell in tr_node.find_all(["th", "td"]):
                tag_name = "th" if cell.name == "th" else "td"
                c = Tag(name=tag_name)
                c.string = _clean_text(cell)
                tr.append(c)
            thead.append(tr)
        table.append(thead)

    # tbody или просто tr
    body_parent = table_node.find("tbody") or table_node
    tbody = Tag(name="tbody")

    for tr_node in body_parent.find_all("tr"):
        tr = Tag(name="tr")
        for cell in tr_node.find_all(["th", "td"]):
            tag_name = "th" if cell.name == "th" else "td"
            c = Tag(name=tag_name)
            c.string = _clean_text(cell)
            tr.append(c)
        if tr.contents:
            tbody.append(tr)

    if tbody.contents:
        table.append(tbody)

    return table

def get_kommersant_article_light_html_by_docid(
    doc_id: int,
    timeout: int = 20,
    session: Optional[requests.Session] = None,
) -> str:
    """
    Высокоуровневая функция: по DocID возвращает легкий HTML
    (article + section/p/blockquote/aside/table) без стилей/скриптов.
    """
    raw_html = fetch_kommersant_html_by_docid(
        doc_id=doc_id,
        timeout=timeout,
        session=session,
    )
    return extract_kommersant_article_light_html(raw_html)

def sync_kommersant_news(
    db_path: str = "ingest_state.db",
    default_minutes: int = 12 * 60,
    overlap_minutes: int = 10,
    fetch_article_html: bool = True,
    timeout: int = 20,
    sleep_sec: float = 0.2,
) -> Dict[str, Any]:
    """
    Инкрементальная синхронизация ленты Коммерсанта.

    Шаги:
    1. Читаем курсор.
    2. Считаем окно выборки в минутах.
    3. Забираем список новостей.
    4. Делаем upsert metadata.
    5. Для новых/неполных статей догружаем light_html.
    6. Обновляем курсор.
    """
    store = IngestStore(db_path=db_path)

    cursor_ts, cursor_id = store.get_cursor("news", "kommersant")
    since_dt = compute_since_minutes(
        cursor_ts=cursor_ts,
        default_minutes=default_minutes,
        overlap_minutes=overlap_minutes,
    )

    now_utc = datetime.now(timezone.utc)
    delta_minutes = max(1, int((now_utc - since_dt).total_seconds() // 60) + 1)

    session = requests.Session()

    news_items = parse_kommersant_latest_news(
        minutes=delta_minutes,
        timeout=timeout,
        sleep_sec=sleep_sec,
        session=session,
    )

    upserted_meta = store.upsert_kommersant_news(news_items, source_id="kommersant")

    fetched_articles = 0

    if fetch_article_html:
        doc_ids_to_fetch = store.get_doc_ids_without_article_html(source_id="kommersant")

        for doc_id in doc_ids_to_fetch:
            try:
                raw_html = fetch_kommersant_html_by_docid(
                    doc_id=doc_id,
                    timeout=timeout,
                    session=session,
                )
                light_html = extract_kommersant_article_light_html(raw_html)

                store.save_kommersant_article_html(
                    doc_id=doc_id,
                    light_html=light_html,
                    article_fetched_at=utc_now_iso(),
                    source_id="kommersant",
                )
                fetched_articles += 1
            except Exception as e:
                print(f"[WARN] Failed to fetch article {doc_id}: {type(e).__name__}: {e}")

    max_item = None
    if news_items:
        max_item = max(
            news_items,
            key=lambda x: (x["published_at_iso"], x["docs_id"]),
        )
        store.save_cursor(
            source_type="news",
            source_id="kommersant",
            cursor_ts=max_item["published_at_iso"],
            cursor_id=str(max_item["docs_id"]),
        )

    return {
        "cursor_before": {"cursor_ts": cursor_ts, "cursor_id": cursor_id},
        "fetched_news_count": len(news_items),
        "upserted_meta_count": upserted_meta,
        "fetched_article_count": fetched_articles,
        "cursor_after": (
            {
                "cursor_ts": max_item["published_at_iso"],
                "cursor_id": str(max_item["docs_id"]),
            }
            if max_item
            else {"cursor_ts": cursor_ts, "cursor_id": cursor_id}
        ),
    }

def refresh_and_get_kommersant_news(
    db_path: str = "ingest_state.db",
    window_minutes: int = 12 * 60,
    default_sync_minutes: int = 12 * 60,
    overlap_minutes: int = 10,
    fetch_article_html: bool = True,
    only_with_html: bool = False,
) -> Dict[str, Any]:
    """
    1. Обновляет локальную базу по cursor.
    2. Возвращает новости из базы за нужное окно.
    """
    sync_result = sync_kommersant_news(
        db_path=db_path,
        default_minutes=default_sync_minutes,
        overlap_minutes=overlap_minutes,
        fetch_article_html=fetch_article_html,
    )

    store = IngestStore(db_path=db_path)
    news = store.get_kommersant_news_from_db(
        minutes=window_minutes,
        only_with_html=only_with_html,
    )

    return {
        "sync_result": sync_result,
        "news": news,
    }