from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from bs4 import BeautifulSoup
import html
import re

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


BASE_URL = "https://e-disclosure.ru"
EVENT_URL_TEMPLATE = BASE_URL + "/portal/event.aspx?EventId={event_id}"

BASE_URL = "https://e-disclosure.ru"
E_DISCLOSURE_EVENT_PRINT_URL = "https://e-disclosure.ru/Event/Print"
E_DISCLOSURE_SEARCH_URL = "https://e-disclosure.ru/api/search/companies"
E_DISCLOSURE_EVENTS_URL = "https://e-disclosure.ru/api/events/page"
E_DISCLOSURE_EVENT_PAGE_URL = "https://e-disclosure.ru/portal/event.aspx?EventId={pseudo_guid}"


def search_e_disclosure_companies(
    query: str,
    page_size: int = 10,
    page_number: int = 1,
    session: Optional[requests.Session] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """
    Ищет компании на e-disclosure и возвращает список найденных записей.

    Каждая запись примерно такая:
    {
        "id": 38025,
        "name": "ООО «Лизинг-Трейд»",
        "district": "Приволжский",
        "region": "Татарстан",
        "branch": "Иное",
        "lastActivity": "2026-06-09T11:58:59.677",
        "docCount": 108
    }
    """
    if not query or not query.strip():
        raise ValueError("query must be non-empty")

    if session is None:
        session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://e-disclosure.ru",
        "Referer": "https://e-disclosure.ru/poisk-po-kompaniyam",
    }

    data = {
        "textfield": query,
        "radReg": "FederalDistricts",
        "districtsCheckboxGroup": "-1",
        "regionsCheckboxGroup": "-1",
        "branchesCheckboxGroup": "-1",
        "lastPageSize": str(page_size),
        "lastPageNumber": str(page_number),
        "query": query,
    }

    resp = session.post(
        E_DISCLOSURE_SEARCH_URL,
        data=data,
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()

    payload = resp.json()
    return payload.get("foundCompaniesList", [])


def get_e_disclosure_company_id(
    query: str,
    exact_name: Optional[str] = None,
    session: Optional[requests.Session] = None,
    timeout: int = 30,
) -> int:
    """
    Возвращает внутренний ID компании с e-disclosure.

    Если exact_name задан, пытается найти точное совпадение по name.
    Иначе возвращает id первой найденной компании.
    """
    companies = search_e_disclosure_companies(
        query=query,
        session=session,
        timeout=timeout,
    )

    if not companies:
        raise LookupError(f"Компании по запросу {query!r} не найдены")

    if exact_name:
        exact_name_norm = exact_name.strip().casefold()
        for company in companies:
            name = (company.get("name") or "").strip().casefold()
            if name == exact_name_norm:
                company_id = company.get("id")
                if company_id is None:
                    break
                return int(company_id)

        raise LookupError(
            f"Точное совпадение {exact_name!r} не найдено по запросу {query!r}"
        )

    company_id = companies[0].get("id")
    if company_id is None:
        raise LookupError(f"У первой найденной компании нет поля id: query={query!r}")

    return int(company_id)


def get_e_disclosure_events(
    company_id: int,
    year: int,
    session: Optional[requests.Session] = None,
    timeout: int = 30,
) -> List[Dict[str, str]]:
    """
    Получает список существенных фактов / событий компании за указанный год
    и возвращает список словарей только с нужными полями:

    [
        {
            "event_name": "...",
            "pseudo_guid": "...",
            "pub_date": "...",
            "event_url": "https://e-disclosure.ru/portal/event.aspx?EventId=..."
        },
        ...
    ]
    """
    if company_id <= 0:
        raise ValueError("company_id must be > 0")

    if year <= 0:
        raise ValueError("year must be > 0")

    if session is None:
        session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://e-disclosure.ru/portal/company.aspx?id={company_id}",
        "X-Requested-With": "XMLHttpRequest",
    }

    params = {
        "companyId": company_id,
        "year": year,
    }

    resp = session.get(
        E_DISCLOSURE_EVENTS_URL,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()

    payload = resp.json()
    if not isinstance(payload, list):
        raise ValueError(f"Ожидался list в ответе API, получено: {type(payload).__name__}")

    result: List[Dict[str, str]] = []

    for item in payload:
        pseudo_guid = item.get("pseudoGUID")
        if not pseudo_guid:
            continue

        result.append(
            {
                "event_name": item.get("eventName") or "",
                "pseudo_guid": pseudo_guid,
                "pub_date": item.get("pubDate") or "",
                "event_url": E_DISCLOSURE_EVENT_PAGE_URL.format(
                    pseudo_guid=pseudo_guid
                ),
            }
        )

    return result


def build_chrome_driver(
    headless: bool = False,
    driver_path: Optional[str] = None,
) -> webdriver.Chrome:
    options = Options()

    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--window-size=1440,1100")
    options.add_argument("--lang=ru-RU")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    )

    if driver_path:
        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.execute_script(
        """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        """
    )

    return driver


def extract_event_card_by_selenium(
    event_id: str,
    headless: bool = False,
    timeout_sec: int = 30,
    driver_path: Optional[str] = None,
) -> Dict[str, Any]:
    url = EVENT_URL_TEMPLATE.format(event_id=event_id)
    driver = build_chrome_driver(headless=headless, driver_path=driver_path)

    try:
        driver.get(url)

        wait = WebDriverWait(driver, timeout_sec)

        try:
            cookie_btn = wait.until(
                EC.element_to_be_clickable((By.ID, "AcceptCookieBtn"))
            )
            cookie_btn.click()
        except Exception:
            pass

        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.infoblock")))
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#cont_wrap")))
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.infoblock h2")))
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.infoblock h4")))
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#cont_wrap div[style*='white-space: pre-wrap']")
            )
        )

        company_name = driver.find_element(By.CSS_SELECTOR, "div.infoblock h2").text.strip()
        event_title = driver.find_element(By.CSS_SELECTOR, "div.infoblock h4").text.strip()
        published_at = driver.find_element(
            By.CSS_SELECTOR, "div.infoblock .time .date"
        ).text.strip()
        #event_code_raw = driver.find_element(
        #    By.CSS_SELECTOR, "div.event-identifier span"
        #).text.strip()

        message_text = driver.find_element(
            By.CSS_SELECTOR,
            "#cont_wrap div[style*='white-space: pre-wrap']"
        ).text.strip()

        #print_button = driver.find_element(By.CSS_SELECTOR, "button.button-print")
        #onclick_value = print_button.get_attribute("onclick") or ""

        #m_guid = re.search(r"guid=([0-9a-fA-F\\-]{36})", onclick_value)
        #print_guid = m_guid.group(1) if m_guid else None

        #m_code = re.search(r"(\d+)", event_code_raw)
        #event_code = m_code.group(1) if m_code else None

        result = {
            "event_id": event_id,
            "url": url,
            "company_name": company_name,
            "event_title": event_title,
            "published_at": published_at,
            "message_text": message_text,
            "html": driver.page_source,
        }
        return result

    finally:
        driver.quit()


def extract_event_card_by_selenium_js(
    event_id: str,
    headless: bool = False,
    timeout_sec: int = 45,
) -> Dict[str, Any]:
    url = EVENT_URL_TEMPLATE.format(event_id=event_id)
    driver = build_chrome_driver(headless=headless)

    try:
        driver.get(url)
        wait = WebDriverWait(driver, timeout_sec)

        try:
            wait.until(EC.element_to_be_clickable((By.ID, "AcceptCookieBtn"))).click()
        except Exception:
            pass

        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.infoblock")))
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#cont_wrap div[style*='white-space: pre-wrap']")
            )
        )

        data = driver.execute_script("""
            const q = (sel) => document.querySelector(sel);
            const txt = (sel) => q(sel)?.innerText?.trim() || null;
            const attr = (sel, name) => q(sel)?.getAttribute(name) || null;

            return {
                company_name: txt("div.infoblock h2"),
                event_title: txt("div.infoblock h4"),
                published_at: txt("div.infoblock .time .date"),
                event_code_raw: txt("div.event-identifier span"),
                message_text: txt("#cont_wrap div[style*='white-space: pre-wrap']"),
                print_onclick: attr("button.button-print", "onclick"),
                html: document.documentElement.outerHTML
            };
        """)

        event_code = None
        if data.get("event_code_raw"):
            m = re.search(r"(\\d+)", data["event_code_raw"])
            if m:
                event_code = m.group(1)

        print_guid = None
        if data.get("print_onclick"):
            m = re.search(r"guid=([0-9a-fA-F\\-]{36})", data["print_onclick"])
            if m:
                print_guid = m.group(1)

        return {
            "event_id": event_id,
            "url": url,
            "company_name": data.get("company_name"),
            "event_title": data.get("event_title"),
            "published_at": data.get("published_at"),
            "event_code": event_code,
            "print_guid": print_guid,
            "message_text": data.get("message_text"),
            "html": data.get("html"),
        }

    finally:
        driver.quit()