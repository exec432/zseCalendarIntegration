#!/usr/bin/env python3
"""
ZSE Bydgoszcz — plan lekcji → .ics (tylko bieżący tydzień)
Uruchamiany co tydzień przez GitHub Actions.
"""

import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

# ─────────────── KONFIGURACJA ───────────────
URL = "https://plan.zse.bydgoszcz.pl/plany/o24.html"
OUTPUT_FILE = "plan.ics"
TIMEZONE = ZoneInfo("Europe/Warsaw")

LESSON_TIMES = {
    0:  (7,  5,  7, 50),
    1:  (8,  0,  8, 45),
    2:  (8, 55,  9, 40),
    3:  (9, 50, 10, 35),
    4:  (10, 45, 11, 30),
    5:  (11, 40, 12, 25),
    6:  (12, 45, 13, 30),
    7:  (13, 40, 14, 25),
    8:  (14, 45, 15, 30),
    9:  (15, 40, 16, 25),
    10: (16, 35, 17, 20),
}
# ─────────────────────────────────────────────


def get_current_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def fetch_html(url: str) -> str:
    """Pobiera stronę z lepszymi nagłówkami, aby uniknąć blokady 403."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/134.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    session = requests.Session()
    resp = session.get(url, headers=headers, timeout=20)

    if resp.status_code == 403:
        print("❌ Otrzymano 403 Forbidden – strona blokuje request.")
        print("   Spróbuj później lub dodaj więcej nagłówków / proxy.")
        resp.raise_for_status()

    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_plan(html: str) -> list[dict]:
    """
    Zwraca listę lekcji lub pustą listę, jeśli nie ma normalnego planu (praktyki itp.).
    """
    soup = BeautifulSoup(html, "html.parser")

    # Najpierw sprawdzamy komunikat "Obowiązuje od"
    info_text = ""
    for td in soup.find_all(["td", "p", "div"]):
        if "Obowiązuje od" in td.get_text():
            info_text = td.get_text(strip=True)
            print(f"ℹ️  {info_text}")
            break

    # Szukamy tabeli z planem – dwa sposoby (bardziej odporne)
    plan_table = None

    # Sposób 1: tabela zawierająca linki do nauczycieli (/plany/n)
    for table in soup.find_all("table"):
        if table.find("a", href=re.compile(r"/plany/n\d+")):
            plan_table = table
            break

    # Sposób 2: tabela z kolumnami dni tygodnia (Poniedziałek, Wtorek itp.)
    if not plan_table:
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all(["th", "td"])]
            if any(d in " ".join(headers) for d in ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek"]):
                plan_table = table
                break

    if not plan_table:
        print("⚠️  Nie znaleziono tabeli z planem lekcji.")
        print("   Prawdopodobnie trwają praktyki zawodowe lub przerwa świąteczna.")
        return []  # Zwracamy pustą listę zamiast kończyć z błędem

    rows = plan_table.find_all("tr")
    lessons = []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:        # minimum: nr lekcji + 5 dni
            continue

        # Numer lekcji
        try:
            lesson_no_str = cells[0].get_text(strip=True)
            lesson_no = int(re.search(r'\d+', lesson_no_str).group()) if re.search(r'\d+', lesson_no_str) else None
        except (ValueError, AttributeError, IndexError):
            continue

        if lesson_no is None or lesson_no not in LESSON_TIMES:
            continue

        # Kolumny z dniami (zazwyczaj od indeksu 2 do 6)
        day_cells = cells[2:7] if len(cells) >= 7 else cells[1:6]

        for day_idx, cell in enumerate(day_cells):
            if not cell.get_text(strip=True):
                continue

            teacher_links = cell.find_all("a", href=re.compile(r"/plany/n\d+"))
            room_links    = cell.find_all("a", href=re.compile(r"/plany/s\d+"))

            if not teacher_links:
                continue

            raw = cell.get_text(" ", strip=True)

            if len(teacher_links) == 1:
                # Pojedynczy przedmiot
                subj = raw
                for lnk in cell.find_all("a"):
                    subj = subj.replace(lnk.get_text(strip=True), "")
                subj = re.sub(r"\s+", " ", subj).strip(" -/")

                if not subj:
                    subj = raw.split()[0] if raw.split() else "Brak nazwy"

                lessons.append({
                    "lesson_no": lesson_no,
                    "day_index": day_idx,
                    "subject": subj,
                    "teacher": teacher_links[0].get_text(strip=True),
                    "room": room_links[0].get_text(strip=True) if room_links else "",
                })
            else:
                # Wiele grup w jednej komórce
                for i, t_link in enumerate(teacher_links):
                    r_link = room_links[i] if i < len(room_links) else None

                    # Próba wyciągnięcia nazwy przedmiotu z tekstu przed linkiem
                    prev = t_link.previous_sibling
                    parts = []
                    while prev:
                        if hasattr(prev, "name") and prev.name == "a":
                            break
                        if isinstance(prev, str):
                            parts.insert(0, prev.strip())
                        prev = prev.previous_sibling

                    subj = " ".join(parts).strip(" -/")
                    if not subj:
                        subj = raw.split()[0] if raw.split() else "Brak nazwy"

                    lessons.append({
                        "lesson_no": lesson_no,
                        "day_index": day_idx,
                        "subject": subj,
                        "teacher": t_link.get_text(strip=True),
                        "room": r_link.get_text(strip=True) if r_link else "",
                    })

    return lessons


def build_ics(lessons: list[dict], monday: date) -> bytes:
    cal = Calendar()
    cal.add("PRODID", "-//ZSE Bydgoszcz Plan 4I//PL")
    cal.add("VERSION", "2.0")
    cal.add("CALSCALE", "GREGORIAN")
    cal.add("X-WR-CALNAME", "Plan lekcji 4I")
    cal.add("X-WR-TIMEZONE", "Europe/Warsaw")

    seen_uids = set()

    for lesson in lessons:
        no = lesson["lesson_no"]
        sh, sm, eh, em = LESSON_TIMES[no]
        lesson_date = monday + timedelta(days=lesson["day_index"])

        dtstart = datetime(lesson_date.year, lesson_date.month, lesson_date.day, sh, sm, tzinfo=TIMEZONE)
        dtend   = datetime(lesson_date.year, lesson_date.month, lesson_date.day, eh, em, tzinfo=TIMEZONE)

        summary = f"{lesson['subject']} [{lesson['room']}]" if lesson['room'] else lesson['subject']
        desc = (
            f"Nauczyciel: {lesson['teacher']}\n"
            f"Sala: {lesson['room']}\n"
            f"Lekcja {no}: {sh:02d}:{sm:02d}–{eh:02d}:{em:02d}"
        )

        # UID z tygodniem, żeby nie dublować przy zmianach planu
        week_str = monday.strftime("%Y%m%d")
        uid_base = f"zse4I-{week_str}-d{lesson['day_index']}-l{no}-{re.sub(r'[^a-z0-9]', '', lesson['subject'].lower())}"
        uid = uid_base
        counter = 1
        while uid in seen_uids:
            uid = f"{uid_base}-g{counter}"
            counter += 1
        seen_uids.add(uid)

        event = Event()
        event.add("SUMMARY", summary)
        event.add("DTSTART", dtstart)
        event.add("DTEND", dtend)
        event.add("DESCRIPTION", desc)
        if lesson['room']:
            event.add("LOCATION", f"Sala {lesson['room']}, ZSE Bydgoszcz")
        event.add("UID", f"{uid}@zse.bydgoszcz.pl")

        cal.add_component(event)

    return cal.to_ical()


def main():
    print(f"📥 Pobieram: {URL}")

    try:
        html = fetch_html(URL)
    except requests.exceptions.RequestException as e:
        print(f"❌ Błąd pobierania strony: {e}")
        sys.exit(1)

    monday = get_current_monday()
    print(f"📅 Tydzień: {monday} – {monday + timedelta(days=4)}")

    lessons = parse_plan(html)
    print(f"✅ Znaleziono {len(lessons)} par lekcyjnych")

    if not lessons:
        print("ℹ️  Brak lekcji w tym tygodniu (prawdopodobnie praktyki).")
        print("   Nie nadpisuję pliku plan.ics – zostaje poprzednia wersja.")
        sys.exit(0)   # sukces – GitHub Actions nie zgłosi błędu

    ics_bytes = build_ics(lessons, monday)

    with open(OUTPUT_FILE, "wb") as f:
        f.write(ics_bytes)

    print(f"💾 Zapisano: {OUTPUT_FILE} ({len(lessons)} wydarzeń)")


if __name__ == "__main__":
    main()