/**
 * ZSE Bydgoszcz — Plan lekcji → .ics on-demand
 * Cloudflare Worker
 *
 * GET /plan.ics?group=2&wf=1&religia=0&holidays=1
 */

const PLAN_URL = "https://plan.zse.bydgoszcz.pl/plany/o24.html";

const LESSON_TIMES = {
  0:  [7,  5,  7, 50],
  1:  [8,  0,  8, 45],
  2:  [8, 55,  9, 40],
  3:  [9, 50, 10, 35],
  4:  [10, 45, 11, 30],
  5:  [11, 40, 12, 25],
  6:  [12, 45, 13, 30],
  7:  [13, 40, 14, 25],
  8:  [14, 45, 15, 30],
  9:  [15, 40, 16, 25],
  10: [16, 35, 17, 20],
};

// Polskie święta państwowe — stałe daty
const FIXED_HOLIDAYS = [
  "01-01", // Nowy Rok
  "01-06", // Trzech Króli
  "05-01", // Święto Pracy
  "05-03", // Konstytucja 3 Maja
  "08-15", // Wniebowzięcie NMP
  "11-01", // Wszystkich Świętych
  "11-11", // Niepodległości
  "12-25", // Boże Narodzenie
  "12-26", // Boże Narodzenie 2
];

// Oblicza datę Wielkanocy algorytmem Gaussa
function easter(year) {
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31);
  const day   = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(Date.UTC(year, month - 1, day));
}

function getPolishHolidays(year) {
  const holidays = new Set();

  // Stałe
  for (const mmdd of FIXED_HOLIDAYS) {
    holidays.add(`${year}-${mmdd}`);
  }

  // Ruchome — liczone od Wielkanocy
  const e = easter(year);
  const addDays = (base, n) => {
    const d = new Date(base);
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  };

  holidays.add(addDays(e, 0));   // Niedziela Wielkanocna
  holidays.add(addDays(e, 1));   // Poniedziałek Wielkanocny
  holidays.add(addDays(e, 49));  // Zielone Świątki (7 tyg.)
  holidays.add(addDays(e, 60));  // Boże Ciało (8 tyg. + 4 dni)

  return holidays;
}

// ── PARSER ──────────────────────────────────────────

function shouldInclude(subj, config) {
  const s = subj.toLowerCase();

  if (s.includes("religia")) return config.religia === "1";
  if (/wf-?j/.test(s))      return config.wf === "1" ? /wf-?j1/.test(s) : /wf-?j2/.test(s);
  if (s.startsWith("#"))     return config.wf === "2";
  if (/-1\/2/.test(s))       return config.group === "1";
  if (/-2\/2/.test(s))       return config.group === "2";

  return true;
}

function stripTags(html) {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function splitCells(rowHTML) {
  return [...rowHTML.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)].map(m => m[1]);
}

function parseHTML(html, config) {
  const tableMatch = html.match(/<table[^>]*class="tabela"[^>]*>([\s\S]*?)<\/table>/i);
  if (!tableMatch) throw new Error("Nie znaleziono tabeli planu");

  const rows = tableMatch[1].split(/<tr[\s>]/i).slice(1);
  const lessons = [];

  for (const row of rows) {
    const cells = splitCells(row);
    if (cells.length !== 7) continue;

    const lessonNo = parseInt(stripTags(cells[0]), 10);
    if (isNaN(lessonNo) || !(lessonNo in LESSON_TIMES)) continue;

    for (let dayIdx = 0; dayIdx < 5; dayIdx++) {
      const cell = cells[dayIdx + 2];
      if (!stripTags(cell).replace(/\u00a0/g, "").trim()) continue;

      const teacherLinks = [...cell.matchAll(/<a[^>]*class="n"[^>]*>(.*?)<\/a>/gi)];
      const roomLinks    = [...cell.matchAll(/<a[^>]*class="s"[^>]*>(.*?)<\/a>/gi)];
      const subjSpans    = [...cell.matchAll(/<span[^>]*class="p"[^>]*>(.*?)<\/span>/gi)];

      if (!teacherLinks.length) continue;

      for (let i = 0; i < teacherLinks.length; i++) {
        const rawSubj = subjSpans[i] ? stripTags(subjSpans[i][1]) : stripTags(cell).split(" ")[0];
        if (!shouldInclude(rawSubj, config)) continue;

        const displaySubj = rawSubj
          .replace(/-[12]\/2$/, "")
          .replace(/-j[12]$/, "")
          .replace(/^[-\s]+|[-\s]+$/g, "");

        lessons.push({
          lessonNo,
          dayIdx,
          subject: displaySubj,
          teacher: stripTags(teacherLinks[i][1]),
          room:    roomLinks[i] ? stripTags(roomLinks[i][1]) : "",
        });
      }
    }
  }

  return lessons;
}

// ── ICS BUILDER ─────────────────────────────────────

function pad(n) { return String(n).padStart(2, "0"); }

function fmtDateTime(date, h, m) {
  return `${date.getUTCFullYear()}${pad(date.getUTCMonth()+1)}${pad(date.getUTCDate())}T${pad(h)}${pad(m)}00`;
}

function buildICS(lessons, monday, skipDays) {
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//ZSE Bydgoszcz//Plan Lekcji//PL",
    "CALSCALE:GREGORIAN",
    "X-WR-CALNAME:Plan lekcji ZSE",
    "X-WR-TIMEZONE:Europe/Warsaw",
  ];

  const seen = new Set();

  for (const lesson of lessons) {
    const day = new Date(monday);
    day.setUTCDate(monday.getUTCDate() + lesson.dayIdx);
    const dayStr = day.toISOString().slice(0, 10);

    if (skipDays.has(dayStr)) continue;

    const [sh, sm, eh, em] = LESSON_TIMES[lesson.lessonNo];
    const dtstart = fmtDateTime(day, sh, sm);
    const dtend   = fmtDateTime(day, eh, em);

    const weekStr = monday.toISOString().slice(0,10).replace(/-/g, "");
    let uid = `zse-${weekStr}-d${lesson.dayIdx}-l${lesson.lessonNo}-${lesson.subject.toLowerCase().replace(/[^a-z0-9]/g,"")}`;
    let n = 1;
    while (seen.has(uid)) uid = `${uid}-${n++}`;
    seen.add(uid);

    const desc = `Nauczyciel: ${lesson.teacher}\\nSala: ${lesson.room}\\nLekcja ${lesson.lessonNo}: ${pad(sh)}:${pad(sm)}-${pad(eh)}:${pad(em)}`;

    lines.push(
      "BEGIN:VEVENT",
      `SUMMARY:${lesson.subject}`,
      `DTSTART;TZID=Europe/Warsaw:${dtstart}`,
      `DTEND;TZID=Europe/Warsaw:${dtend}`,
      `DESCRIPTION:${desc}`,
      `LOCATION:${lesson.room ? `Sala ${lesson.room}, ZSE Bydgoszcz` : "ZSE Bydgoszcz"}`,
      `UID:${uid}@zse.bydgoszcz.pl`,
      "END:VEVENT",
    );
  }

  lines.push("END:VCALENDAR");
  return lines.join("\r\n");
}

// ── HANDLER ─────────────────────────────────────────

function getCurrentMonday() {
  const today = new Date();
  const dow = today.getUTCDay();
  const diff = dow === 0 ? -6 : 1 - dow;
  const monday = new Date(today);
  monday.setUTCDate(today.getUTCDate() + diff);
  monday.setUTCHours(0, 0, 0, 0);
  return monday;
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET",
        },
      });
    }

    if (url.pathname !== "/plan.ics") {
      return new Response("Użyj /plan.ics?group=2&wf=1&religia=0&holidays=1", { status: 404 });
    }

    const config = {
      group:    url.searchParams.get("group")    ?? "2",
      wf:       url.searchParams.get("wf")       ?? "1",
      religia:  url.searchParams.get("religia")  ?? "0",
      holidays: url.searchParams.get("holidays") ?? "1",
    };

    try {
      const resp = await fetch(PLAN_URL, {
        headers: {
          "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
          "Accept-Language": "pl-PL,pl;q=0.9",
        },
        cf: { cacheTtl: 3600, cacheEverything: true }, // cache po stronie CF
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status} z serwera szkoły`);

      const html = await resp.text();
      const monday = getCurrentMonday();
      const skipDays = config.holidays === "1"
        ? getPolishHolidays(monday.getUTCFullYear())
        : new Set();

      const lessons = parseHTML(html, config);
      const ics = buildICS(lessons, monday, skipDays);

      return new Response(ics, {
        headers: {
          "Content-Type": "text/calendar; charset=utf-8",
          "Content-Disposition": 'attachment; filename="plan.ics"',
          "Cache-Control": "public, max-age=3600",
          "Access-Control-Allow-Origin": "*",
        },
      });
    } catch (err) {
      return new Response(`Błąd: ${err.message}`, { status: 500 });
    }
  },
};