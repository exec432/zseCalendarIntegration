#!/usr/bin/env python3
"""
ZSE Bydgoszcz — plan lekcji → .ics (bieżący tydzień)
Grupy: WF = j1 (1/2), pozostałe = 2/2. Religia usunięta.
"""

import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag
from icalendar import Calendar, Event

# ─────────────── KONFIGURACJA ───────────────
URL         = "https://plan.zse.bydgoszcz.pl/plany/o24.html"
OUTPUT_FILE = "plan.ics"
TIMEZONE    = ZoneInfo("Europe/Warsaw")

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


def should_include(subj: str) -> bool:
    """
    Zwraca True jeśli lekcja ma być w kalendarzu.

    Reguły:
      - religia           → zawsze wyklucz
      - wf-j2 / #4DI      → wyklucz (to grupa 2 z WF; zostaje wf-j1)
      - cokolwiek-1/2     → wyklucz (zostaje -2/2 dla pozostałych przedmiotów)
      - ABD-1/2           → wyklucz (j.angielski i ABD: zostaje -2/2)
      - reszta            → uwzględnij
    """
    s = subj.lower()

    # Religia — zawsze wyrzuć
    if "religia" in s:
        return False

    # WF: wyrzuć grupę j2 i wpisy #4DI (brak nauczyciela = druga grupa)
    if re.search(r"wf-?j2", s):
        return False
    if s.startswith("#"):
        return False

    # Przedmioty z podziałem na grupy: wyrzuć -1/2, zostaw -2/2
    if re.search(r"-1/2", s):
        return False

    return True


def get_current_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.4 Safari/605.1.15"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl-PL,pl;q=0.9",
        "Connection": "keep-alive",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code == 403:
        print("❌ Serwer zwrócił 403 – uruchom skrypt lokalnie (nie z serwera).")
        sys.exit(1)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_plan(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    plan_table = soup.find("table", class_="tabela")
    if not plan_table:
        print("❌ Nie znaleziono tabeli class='tabela'.")
        sys.exit(1)

    lessons = []

    for row in plan_table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != 7:
            continue

        try:
            lesson_no = int(cells[0].get_text(strip=True))
        except ValueError:
            continue

        if lesson_no not in LESSON_TIMES:
            continue

        for day_idx, cell in enumerate(cells[2:7]):
            if not cell.get_text(strip=True).replace('\xa0', '').strip():
                continue

            teacher_links = cell.find_all("a", class_="n")
            room_links    = cell.find_all("a", class_="s")

            if not teacher_links:
                continue

            for i, t_link in enumerate(teacher_links):
                # Znajdź span.p bezpośrednio przed tym linkiem nauczyciela
                subj = ""
                prev = t_link.previous_sibling
                while prev:
                    if isinstance(prev, Tag):
                        if "p" in prev.get("class", []):
                            subj = prev.get_text(strip=True)
                            break
                        sp = prev.find("span", class_="p")
                        if sp:
                            subj = sp.get_text(strip=True)
                            break
                    prev = prev.previous_sibling

                if not subj:
                    all_p = cell.find_all("span", class_="p")
                    if i < len(all_p):
                        subj = all_p[i].get_text(strip=True)

                if not subj:
                    subj = "?"

                # ── FILTROWANIE GRUP I RELIGII ──
                if not should_include(subj):
                    continue

                # Usuń sufiks grupy z nazwy wyświetlanej (-2/2 → czysta nazwa)
                display_subj = re.sub(r"-2/2$", "", subj).strip(" -")

                room = room_links[i].get_text(strip=True) if i < len(room_links) else ""

                lessons.append({
                    "lesson_no": lesson_no,
                    "day_index": day_idx,
                    "subject":   display_subj,
                    "teacher":   t_link.get_text(strip=True),
                    "room":      room,
                })

    return lessons


def build_ics(lessons: list[dict], monday: date) -> bytes:
    cal = Calendar()
    cal.add("PRODID", "-//ZSE Bydgoszcz Plan 4I//PL")
    cal.add("VERSION", "2.0")
    cal.add("CALSCALE", "GREGORIAN")
    cal.add("X-WR-CALNAME", "Plan lekcji 4I")
    cal.add("X-WR-TIMEZONE", "Europe/Warsaw")

    seen_uids: set[str] = set()

    for lesson in lessons:
        no      = lesson["lesson_no"]
        sh, sm, eh, em = LESSON_TIMES[no]
        day     = monday + timedelta(days=lesson["day_index"])

        dtstart = datetime(day.year, day.month, day.day, sh, sm, tzinfo=TIMEZONE)
        dtend   = datetime(day.year, day.month, day.day, eh, em, tzinfo=TIMEZONE)

        subj    = lesson["subject"]
        teacher = lesson["teacher"]
        room    = lesson["room"]

        summary = subj
        desc    = (
            f"Nauczyciel: {teacher}\n"
            f"Sala: {room}\n"
            f"Lekcja {no}: {sh:02d}:{sm:02d}–{eh:02d}:{em:02d}"
        )

        week_str = monday.strftime("%Y%m%d")
        uid_base = f"zse4I-{week_str}-d{lesson['day_index']}-l{no}-{re.sub(r'[^a-z0-9]','', subj.lower())}"
        uid = uid_base
        n = 1
        while uid in seen_uids:
            uid = f"{uid_base}-g{n}"
            n += 1
        seen_uids.add(uid)

        event = Event()
        event.add("SUMMARY",     summary)
        event.add("DTSTART",     dtstart)
        event.add("DTEND",       dtend)
        event.add("DESCRIPTION", desc)
        event.add("LOCATION",    f"Sala {room}, ZSE Bydgoszcz" if room else "ZSE Bydgoszcz")
        event.add("UID",         f"{uid}@zse.bydgoszcz.pl")
        cal.add_component(event)

    return cal.to_ical()


def main():
    print(f"📥 Pobieram: {URL}")
    html = fetch_html(URL)
    print(f"   HTML: {len(html)} znaków")

    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(string=re.compile("Obowi", re.I)):
        print(f"ℹ️  {el.strip()}")
        break

    monday = get_current_monday()
    print(f"📅 Tydzień: {monday} – {monday + timedelta(days=4)}")

    lessons = parse_plan(html)
    print(f"✅ Znaleziono {len(lessons)} par lekcyjnych")

    if not lessons:
        print("ℹ️  Brak lekcji – nie nadpisuję pliku.")
        sys.exit(0)

    ics = build_ics(lessons, monday)
    with open(OUTPUT_FILE, "wb") as f:
        f.write(ics)

    count = ics.count(b"BEGIN:VEVENT")
    print(f"💾 Zapisano: {OUTPUT_FILE} ({count} wydarzeń)")


if __name__ == "__main__":
    main()