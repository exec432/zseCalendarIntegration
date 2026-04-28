#!/usr/bin/env python3
"""
ZSE Bydgoszcz — plan lekcji → .ics (bieżący tydzień)
Grupy: WF = j1 (1/2), pozostałe = 2/2. Religia usunięta.
Dni ze świętami państwowymi są automatycznie pomijane.
"""

import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays
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
    Reguły filtrowania grup i religii:
      - religia       → wyklucz
      - wf-j2 / #4DI  → wyklucz (zostaje wf-j1)
      - coś-1/2       → wyklucz (zostaje -2/2)
    """
    s = subj.lower()
    if "religia" in s:
        return False
    if re.search(r"wf-?j2", s):
        return False
    if s.startswith("#"):
        return False
    if re.search(r"-1/2", s):
        return False
    return True


def get_current_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def get_holidays_this_week(monday: date) -> dict:
    """
    Zwraca {data: nazwa} dla polskich świąt państwowych przypadających
    w dniach Pn–Pt danego tygodnia. Biblioteka `holidays` zawiera
    oficjalne święta z Dz.U. i automatycznie oblicza ruchome daty
    (Wielkanoc, Boże Ciało, Zielone Świątki).
    """
    years = {monday.year, (monday + timedelta(days=4)).year}
    pl = holidays.Poland(years=years)

    result = {}
    for offset in range(5):
        day = monday + timedelta(days=offset)
        if day in pl:
            result[day] = pl[day]
    return result


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

                if not should_include(subj):
                    continue

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


def build_ics(lessons: list[dict], monday: date, skip_days: set) -> bytes:
    """
    Buduje plik ICS. Lekcje przypadające na dni ze świętami (skip_days)
    są pomijane.
    """
    cal = Calendar()
    cal.add("PRODID", "-//ZSE Bydgoszcz Plan 4I//PL")
    cal.add("VERSION", "2.0")
    cal.add("CALSCALE", "GREGORIAN")
    cal.add("X-WR-CALNAME", "Plan lekcji 4I")
    cal.add("X-WR-TIMEZONE", "Europe/Warsaw")

    seen_uids: set[str] = set()
    skipped = 0

    for lesson in lessons:
        no  = lesson["lesson_no"]
        day = monday + timedelta(days=lesson["day_index"])

        # Pomiń lekcje w dni świąteczne
        if day in skip_days:
            skipped += 1
            continue

        sh, sm, eh, em = LESSON_TIMES[no]
        dtstart = datetime(day.year, day.month, day.day, sh, sm, tzinfo=TIMEZONE)
        dtend   = datetime(day.year, day.month, day.day, eh, em, tzinfo=TIMEZONE)

        subj    = lesson["subject"]
        teacher = lesson["teacher"]
        room    = lesson["room"]

        desc = (
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
        event.add("SUMMARY",     subj)
        event.add("DTSTART",     dtstart)
        event.add("DTEND",       dtend)
        event.add("DESCRIPTION", desc)
        event.add("LOCATION",    f"Sala {room}, ZSE Bydgoszcz" if room else "ZSE Bydgoszcz")
        event.add("UID",         f"{uid}@zse.bydgoszcz.pl")
        cal.add_component(event)

    if skipped:
        print(f"🎉 Pominięto {skipped} lekcji z powodu świąt")

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

    # Sprawdź święta
    week_holidays = get_holidays_this_week(monday)
    if week_holidays:
        for d, name in sorted(week_holidays.items()):
            print(f"🎉 Święto w tym tygodniu: {d.strftime('%A %d.%m')} – {name}")
    else:
        print("✅ Brak świąt w tym tygodniu")

    lessons = parse_plan(html)
    print(f"✅ Znaleziono {len(lessons)} par lekcyjnych")

    if not lessons:
        print("ℹ️  Brak lekcji – nie nadpisuję pliku.")
        sys.exit(0)

    skip_days = set(week_holidays.keys())
    ics = build_ics(lessons, monday, skip_days)

    with open(OUTPUT_FILE, "wb") as f:
        f.write(ics)

    count = ics.count(b"BEGIN:VEVENT")
    print(f"💾 Zapisano: {OUTPUT_FILE} ({count} wydarzeń)")


if __name__ == "__main__":
    main()