#!/usr/bin/env python3
"""
ZSE Bydgoszcz — plan lekcji → .ics (tylko bieżący tydzień ze strony)
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
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_plan(html: str) -> list[dict]:
    """
    Zwraca listę słowników z danymi każdej pary:
    { lesson_no, day_index (0=pon..4=pt), subject, teacher, room }
    """
    soup = BeautifulSoup(html, "html.parser")

    # Szukamy tabeli zawierającej linki do nauczycieli (/plany/nXX)
    plan_table = None
    for table in soup.find_all("table"):
        if table.find("a", href=re.compile(r"/plany/n\d+")):
            plan_table = table
            break

    if not plan_table:
        print("❌ Nie znaleziono tabeli z planem!")
        sys.exit(1)

    rows = plan_table.find_all("tr")
    lessons = []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue

        # Kolumna 0: numer lekcji
        try:
            lesson_no = int(cells[0].get_text(strip=True))
        except ValueError:
            continue

        # Kolumny 2–6: pon, wt, śr, czw, pt
        day_cells = cells[2:7]

        for day_idx, cell in enumerate(day_cells):
            if not cell.get_text(strip=True):
                continue

            # Wyodrębnij wszystkich nauczycieli i sale w tej komórce
            teacher_links = cell.find_all("a", href=re.compile(r"/plany/n\d+"))
            room_links    = cell.find_all("a", href=re.compile(r"/plany/s\d+"))

            if not teacher_links:
                continue

            # Pełny tekst komórki do wyciągania nazwy przedmiotu
            raw = cell.get_text(" ", strip=True)

            if len(teacher_links) == 1:
                # Prosta komórka — jeden przedmiot
                subj = raw
                for lnk in cell.find_all("a"):
                    subj = subj.replace(lnk.get_text(strip=True), "")
                subj = re.sub(r"\s+", " ", subj).strip(" -/")
                if not subj:
                    subj = raw.split()[0]

                lessons.append({
                    "lesson_no": lesson_no,
                    "day_index": day_idx,
                    "subject":  subj,
                    "teacher":  teacher_links[0].get_text(strip=True),
                    "room":     room_links[0].get_text(strip=True) if room_links else "",
                })
            else:
                # Kilka grup — każdy nauczyciel to osobna grupa
                for i, t_link in enumerate(teacher_links):
                    r_link = room_links[i] if i < len(room_links) else None

                    # Tekst tuż przed linkiem nauczyciela = nazwa przedmiotu
                    prev = t_link.previous_sibling
                    parts = []
                    while prev:
                        if hasattr(prev, "name") and prev.name == "a" and \
                                re.search(r"/plany/[ns]\d+", prev.get("href", "")):
                            break
                        if isinstance(prev, str):
                            parts.insert(0, prev.strip())
                        prev = prev.previous_sibling
                    subj = " ".join(parts).strip(" -/")
                    if not subj:
                        # fallback
                        subj = raw.split()[0]

                    lessons.append({
                        "lesson_no": lesson_no,
                        "day_index": day_idx,
                        "subject":  subj,
                        "teacher":  t_link.get_text(strip=True),
                        "room":     r_link.get_text(strip=True) if r_link else "",
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
        if no not in LESSON_TIMES:
            continue

        sh, sm, eh, em = LESSON_TIMES[no]
        lesson_date = monday + timedelta(days=lesson["day_index"])

        dtstart = datetime(lesson_date.year, lesson_date.month, lesson_date.day,
                           sh, sm, tzinfo=TIMEZONE)
        dtend   = datetime(lesson_date.year, lesson_date.month, lesson_date.day,
                           eh, em, tzinfo=TIMEZONE)

        subj    = lesson["subject"]
        teacher = lesson["teacher"]
        room    = lesson["room"]

        summary = f"{subj} [{room}]" if room else subj
        desc    = (
            f"Nauczyciel: {teacher}\n"
            f"Sala: {room}\n"
            f"Lekcja {no}: {sh:02d}:{sm:02d}–{eh:02d}:{em:02d}"
        )

        # Unikalny UID per dzień+lekcja+przedmiot (bez powtórzeń dla grup)
        uid_base = f"zse4I-d{lesson['day_index']}-l{no}-{re.sub(r'[^a-z0-9]', '', subj.lower())}"
        uid = uid_base
        counter = 1
        while uid in seen_uids:
            uid = f"{uid_base}-g{counter}"
            counter += 1
        seen_uids.add(uid)

        event = Event()
        event.add("SUMMARY",     summary)
        event.add("DTSTART",     dtstart)
        event.add("DTEND",       dtend)
        event.add("DESCRIPTION", desc)
        if room:
            event.add("LOCATION", f"Sala {room}, ZSE Bydgoszcz")
        event.add("UID", f"{uid}@zse.bydgoszcz.pl")

        cal.add_component(event)

    return cal.to_ical()


def main():
    print(f"📥 Pobieram: {URL}")
    html = fetch_html(URL)

    soup = BeautifulSoup(html, "html.parser")
    for td in soup.find_all("td"):
        if "Obowi" in td.get_text():
            print(f"ℹ️  {td.get_text(strip=True)}")
            break

    monday = get_current_monday()
    print(f"📅 Tydzień: {monday} – {monday + timedelta(days=4)}")

    lessons = parse_plan(html)
    print(f"✅ Znaleziono {len(lessons)} par lekcyjnych")

    ics_bytes = build_ics(lessons, monday)

    with open(OUTPUT_FILE, "wb") as f:
        f.write(ics_bytes)

    print(f"💾 Zapisano: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()