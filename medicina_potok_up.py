"""
medicina_potok_up.py
Парсинг дашборда DataLens "Общий поток исследований" и запись зеркальных
таблиц в онлайн-таблицу "для тест.xlsx" на Яндекс.Диске через эмуляцию
браузера (Playwright UI-автоматизация, без OAuth-токена).

Режимы запуска:
    py -3 medicina_potok_up.py --dry-run   # парсинг + план upsert, БЕЗ записи (безопасно, для проверки)
    py -3 medicina_potok_up.py --setup     # создать недостающие листы-зеркала и заголовки (один раз)
    py -3 medicina_potok_up.py --write     # парсинг + реальная запись/обновление данных (ежедневный запуск)

ВАЖНО: лист "Данные CH" в таблице никогда не создаётся, не открывается и не изменяется.
"""

import argparse
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# INPUT:  ../screenshots/datalens_session.json — сохранённая сессия DataLens (Playwright storage_state)
# OUTPUT: новые листы-зеркала в "для тест.xlsx" на Яндекс.Диске
#         + medicina_potok_state.json рядом со скриптом — локальный учёт upsert (дата -> номер строки)

_LOG_FILE = Path(__file__).resolve().parent / "medicina_potok_up.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),  # консоль Windows (cp866) портит кириллицу — лог пиши/читай из файла
    ],
)
log = logging.getLogger(__name__)

DASHBOARD_URL = "https://datalens.ru/zic343hcd77kk-medicina-obshie-potok-issledovaniy"
SHEET_URL = "https://disk.yandex.ru/edit/d/zPfzwWYpD0-t8R-_eAvUZSPegnqahzm72s0qoIz-cKg6eFp3T0pxY29tUQ"

_SCRIPT_DIR = Path(__file__).resolve().parent
COOKIES_FILE = _SCRIPT_DIR.parent / "screenshots" / "datalens_session.json"
STATE_FILE = _SCRIPT_DIR / "medicina_potok_state.json"

BROWSER_WIDTH, BROWSER_HEIGHT = 1920, 1300
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

REFRESH_DAYS = 7  # сколько последних дней перезаписываем при каждом запуске (по просьбе пользователя)
DO_NOT_TOUCH_SHEET = "Данные CH"  # HARD CONSTRAINT: этот лист скрипт не трогает вообще

# Координаты сетки таблицы (подобраны эмпирически для viewport 1920x1300, масштаб 100%)
def col_x(idx: int) -> int:
    return 76 + idx * 74  # idx=0 -> колонка A


def row_y(idx: int) -> int:
    return 183 + idx * 21  # idx=0 -> строка 1 (заголовок)


# ---------------------------------------------------------------------------
# Конфигурация зеркальных таблиц
# ---------------------------------------------------------------------------

@dataclass
class TableSpec:
    sheet_name: str       # имя листа-зеркала в онлайн-таблице (создаётся скриптом)
    dom_index: int        # индекс <table> на дашборде (порядок DOM, 0-based)
    header_rows: int      # сколько первых строк исходной таблицы — заголовки
    n_data_cols: int      # количество колонок данных для чтения из DataLens (без даты)
    headers: list[str]    # плоские заголовки для записи в новый лист
    komета_special: bool = False          # включает подстановку "вчера" из таблицы "Корректное..."
    write_col_indices: list[int] | None = None  # индексы из vals, которые пишем в таблицу
                                                 # None = писать все; иначе — только указанные


TABLES: list[TableSpec] = [
    TableSpec(
        # Читаем 10 колонок из DataLens, пишем только 5 (без "Итого X")
        # DataLens-порядок: [КТ, ИтогоКТ, ММГ, ИтогоММГ, МРТ, ИтогоМРТ, РГ, РГдети, ИтогоРГ, Итого]
        # Индексы нужных: 0=КТ, 2=ММГ, 4=МРТ, 6=РГ взрослые, 7=РГ дети
        sheet_name="Волошина", dom_index=0, header_rows=2, n_data_cols=10,
        headers=["(doc_date)", "КТ", "ММГ", "МРТ", "РГ взрослые", "РГ дети"],
        write_col_indices=[0, 2, 4, 6, 7],
    ),
    TableSpec(
        # Читаем 6 колонок: [КТ, ММГ, МРТ, РГ, РГдети, Итого]; пишем первые 5 (без Итого)
        sheet_name="Комета", dom_index=1, header_rows=3, n_data_cols=6,
        headers=["(conduct_date)", "КТ", "ММГ", "МРТ", "РГ взрослые", "РГ дети"],
        komета_special=True,
        write_col_indices=[0, 1, 2, 3, 4],
    ),
    TableSpec(
        # Читаем 3 колонки: [ММГ, РГ, Итого]; пишем первые 2 (без Итого)
        sheet_name="Агфа исследования", dom_index=2, header_rows=1, n_data_cols=3,
        headers=["(conduct_date)", "ММГ", "РГ"],
        write_col_indices=[0, 1],
    ),
    TableSpec(
        # Читаем 3 колонки: [ММГ, РГ, Итого]; пишем первые 2 (без Итого)
        sheet_name="Агфа описания", dom_index=3, header_rows=1, n_data_cols=3,
        headers=["(date)", "ММГ", "РГ"],
        write_col_indices=[0, 1],
    ),
    TableSpec(
        sheet_name="Валидаторы", dom_index=5, header_rows=1, n_data_cols=1,
        headers=["(doc_date)", "Валидация"],
        # write_col_indices=None → пишем единственную колонку как есть
    ),
]

# "Корректное кол-во исследований за вчера" — источник подстановки для таблицы "Комета".
# Структура такая же, как у "Волошина" (10 колонок), обычно содержит ровно 1 строку.
KOMETA_SOURCE_DOM_INDEX = 4
KOMETA_SOURCE_HEADER_ROWS = 2

# Карта колонок источника (индексы в его 10-колоночном наборе) -> именованные колонки "Комета".
# Проверено арифметикой на реальных данных: 596+284+518+4510+837 = 6745 = "Итого" — сходится.
KOMETA_YESTERDAY_MAP = {"КТ": 0, "ММГ": 2, "МРТ": 4, "РГ": 6, "РГ дети": 7, "Итого": 9}
KOMETA_COLUMN_ORDER = ["КТ", "ММГ", "МРТ", "РГ", "РГ дети", "Итого"]


# ---------------------------------------------------------------------------
# Парсинг дашборда
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")

# Глубокий обход DOM — таблицы DataLens лежат в open shadow DOM,
# document.querySelectorAll('table') их не видит, поэтому собираем рекурсивно.
_DEEP_TABLES_JS = """
() => {
  function deepQueryAll(root, selector, acc) {
    acc.push(...root.querySelectorAll(selector));
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) deepQueryAll(el.shadowRoot, selector, acc);
    return acc;
  }
  return deepQueryAll(document, 'table', []).map(t =>
    Array.from(t.querySelectorAll('tr')).map(r =>
      Array.from(r.querySelectorAll('th,td')).map(c => c.innerText.trim())
    )
  );
}
"""


def _clean_int(text: str) -> int:
    """'4\\xa0817' -> 4817; '' -> 0."""
    t = text.replace("\xa0", "").replace(" ", "").strip()
    return int(t) if t else 0


def _parse_ru_date(text: str) -> date | None:
    m = _DATE_RE.match(text.strip())
    if not m:
        return None
    d, mo, y = m.groups()
    return date(int(y), int(mo), int(d))


def _extract_tables(page) -> list[list[list[str]]]:
    return page.evaluate(_DEEP_TABLES_JS)


def _parse_data_rows(raw_table: list[list[str]], header_rows: int, n_data_cols: int) -> list[tuple[date, list[int]]]:
    """[(дата, [значения]), ...] — пропускает строки заголовков и итоговую строку 'Итого'."""
    out = []
    for row in raw_table[header_rows:]:
        if not row:
            continue
        d = _parse_ru_date(row[0])
        if d is None:  # строка "Итого" или иной служебный текст
            continue
        out.append((d, [_clean_int(c) for c in row[1:1 + n_data_cols]]))
    return out


def _build_komета_override(source_rows: list[tuple[date, list[int]]]) -> dict[date, list[int]]:
    """{дата: [КТ,ММГ,МРТ,РГ,РГ дети,Итого]} из таблицы 'Корректное кол-во исследований за вчера'."""
    override = {}
    for d, vals in source_rows:
        if len(vals) < 10:
            log.warning("'Корректное...' за %s: неожиданное число колонок (%d) — пропускаю", d, len(vals))
            continue
        override[d] = [vals[KOMETA_YESTERDAY_MAP[name]] for name in KOMETA_COLUMN_ORDER]
    return override


def _apply_komета_override(parsed_rows, override, yesterday: date):
    """Заменяет/добавляет строку за 'вчера' данными из источника 'Корректное...' (по требованию пользователя)."""
    rows = [(d, v) for d, v in parsed_rows if d != yesterday]
    if yesterday in override:
        rows.append((yesterday, override[yesterday]))
    else:
        log.warning("Нет данных 'Корректное...' за вчера (%s) — оставляю обычные данные 'Комета', если есть", yesterday)
        orig = next((v for d, v in parsed_rows if d == yesterday), None)
        if orig is not None:
            rows.append((yesterday, orig))
    return rows


# ---------------------------------------------------------------------------
# ИАО — сводный лист, объединяющий все источники в одну строку на дату
# ---------------------------------------------------------------------------

IAO_SHEET = "ИАО"
IAO_HEADER_ROWS = 2  # строка названий групп + строка колонок; данные с grid-row IAO_HEADER_ROWS

# Порядок столбцов в листе ИАО: (имя_источника, индексы из vals этого источника)
# Волошина vals[10]: [КТ, ИтКТ, ММГ, ИтММГ, МРТ, ИтМРТ, РГ, РГдети, ИтРГ, Итого]
# Комета  vals[6]:  [КТ, ММГ, МРТ, РГ, РГдети, Итого]
# Агфа*   vals[3]:  [ММГ, РГ, Итого]
# Валид.  vals[1]:  [cnt]
IAO_COL_SOURCES: list[tuple[str, list[int]]] = [
    ("Волошина",          [0, 2, 4, 6, 7]),  # КТ ММГ МРТ РГ_взр РГ_дети
    ("Валидаторы",        [0]),              # Валидация
    ("Комета",            [0, 1, 2, 3, 4]),  # КТ ММГ МРТ РГ_взр РГ_дети
    ("Агфа исследования", [0, 1]),           # ММГ РГ
    ("Агфа описания",     [0, 1]),           # ММГ РГ
]


def _build_iao_rows(parsed: dict, override: dict, today: date) -> list[tuple[date, list]]:
    """Объединяет данные из всех источников в строки для листа ИАО (1 строка на дату)."""
    yesterday = today - timedelta(days=1)
    cutoff = today - timedelta(days=REFRESH_DAYS - 1)

    cometa_src = _apply_komета_override(parsed["Комета"], override, yesterday)
    cometa_by_date: dict[date, list[int]] = {d: v for d, v in cometa_src}

    sources: dict[str, dict[date, list[int]]] = {}
    for sheet_name, _ in IAO_COL_SOURCES:
        sources[sheet_name] = cometa_by_date if sheet_name == "Комета" else {d: v for d, v in parsed[sheet_name]}

    all_dates: set[date] = set()
    for by_date in sources.values():
        for d in by_date:
            if d >= cutoff:
                all_dates.add(d)

    result = []
    for d in sorted(all_dates):
        row: list = []
        for sheet_name, indices in IAO_COL_SOURCES:
            vals = sources[sheet_name].get(d, [])
            for i in indices:
                row.append(vals[i] if vals and i < len(vals) else "")
        result.append((d, row))
    return result


def _plan_iao(rows: list[tuple[date, list]], state: dict, today: date):
    """Возвращает (план, новый sheet_state) для листа ИАО."""
    sheet_state = state.get(IAO_SHEET, {"next_row": 0, "dates": {}})
    working = {"next_row": sheet_state["next_row"], "dates": dict(sheet_state["dates"])}
    cutoff = today - timedelta(days=REFRESH_DAYS - 1)

    plan = []
    for d, vals in sorted(rows, key=lambda x: x[0]):
        if d < cutoff:
            continue
        key = d.isoformat()
        if key in working["dates"]:
            plan.append(("replace", working["dates"][key], d, vals))
        else:
            idx = working["next_row"]
            plan.append(("append", idx, d, vals))
            working["dates"][key] = idx
            working["next_row"] = idx + 1
    return plan, working


def _write_iao_row(page, data_row_idx: int, values: list) -> None:
    """Записывает строку в ИАО с учётом двухстрочного заголовка (смещение IAO_HEADER_ROWS)."""
    _write_spreadsheet_row(page, data_row_idx + IAO_HEADER_ROWS, values)


# ---------------------------------------------------------------------------
# Локальный файл состояния (учёт upsert: дата -> индекс строки данных)
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Состояние сохранено: %s", STATE_FILE)


# ---------------------------------------------------------------------------
# Запись в онлайн-таблицу (UI-автоматизация канвас-сетки)
# ---------------------------------------------------------------------------
# Валидированный паттерн заполнения строки: клик по стартовой ячейке один раз,
# затем [type, Tab] для всех колонок кроме последней, [type, Enter] для последней,
# затем Home — возвращает курсор в колонку A (т.к. Enter переводит вниз В ТОЙ ЖЕ колонке).

def _write_spreadsheet_row(page, pixel_row_idx: int, values: list) -> None:
    """pixel_row_idx — 0-based номер строки СЕТКИ (0 = первая строка листа)."""
    page.mouse.click(col_x(0), row_y(pixel_row_idx))
    page.wait_for_timeout(150)
    for i, v in enumerate(values):
        page.keyboard.type(str(v), delay=20)
        page.keyboard.press("Tab" if i < len(values) - 1 else "Enter")
        page.wait_for_timeout(80)
    page.keyboard.press("Home")
    page.wait_for_timeout(100)


def _write_header(page, spec: TableSpec) -> None:
    _write_spreadsheet_row(page, 0, spec.headers)


def _write_data_row(page, data_row_idx: int, values: list) -> None:
    """data_row_idx=0 — первая строка данных (сразу под заголовком)."""
    _write_spreadsheet_row(page, data_row_idx + 1, values)


# ---------------------------------------------------------------------------
# Управление листами (поиск/переключение/создание по видимому тексту вкладки)
# ---------------------------------------------------------------------------

def _scroll_tab_bar_to_start(page, volga_frame) -> None:
    """Прокручивает горизонтальный контейнер вкладок-листов в самое начало.
    Необходимо при открытии файла: активен последний лист (Валидаторы), остальные вкладки
    могут отсутствовать в DOM из-за виртуализации."""
    volga_frame.evaluate("""() => {
        const span = document.querySelector('span.tab-name');
        if (!span) return;
        let el = span.parentElement;
        while (el && el !== document.body) {
            if (el.scrollWidth > el.clientWidth) { el.scrollLeft = 0; return; }
            el = el.parentElement;
        }
    }""")
    page.wait_for_timeout(800)


def _sheet_exists(volga_frame, name: str) -> bool:
    names: list[str] = volga_frame.evaluate("""() =>
        Array.from(document.querySelectorAll('span.tab-name'))
            .map(s => s.innerText.trim()).filter(t => t)
    """)
    return name in names


def _switch_to_sheet(page, volga_frame, name: str) -> bool:
    idx = volga_frame.evaluate(
        "(n) => { const s = Array.from(document.querySelectorAll('span.tab-name'));"
        " const i = s.findIndex(el => el.innerText.trim() === n); return i >= 0 ? i : null; }",
        name,
    )
    if idx is None:
        return False
    tab = volga_frame.locator("span.tab-name").nth(idx)
    try:
        tab.scroll_into_view_if_needed(timeout=3000)
        page.wait_for_timeout(300)
        tab.click(timeout=8000)
    except PWTimeout:
        return False
    page.wait_for_timeout(800)
    page.keyboard.press("Control+Home")
    page.wait_for_timeout(400)
    return True


def _tab_boxes(page, volga_frame):
    """Список {name, x, y, w, h} в КООРДИНАТАХ СТРАНИЦЫ для всех видимых вкладок листов."""
    raw = volga_frame.evaluate("""() => {
        return Array.from(document.querySelectorAll('span.tab-name')).map(s => {
            const r = s.getBoundingClientRect();
            return {text: s.innerText.trim(), x: r.x, y: r.y, w: r.width, h: r.height};
        }).filter(b => b.text && b.w > 0 && b.h > 0 && b.y > 1200 && b.y < 1310);
    }""")
    frame_el = volga_frame.frame_element()  # ElementHandle для <iframe> в родительском фрейме
    fbox = frame_el.bounding_box()
    return [
        {"name": b["text"], "x": fbox["x"] + b["x"], "y": fbox["y"] + b["y"], "w": b["w"], "h": b["h"]}
        for b in raw
    ]


def _create_sheet(page, volga_frame, name: str) -> bool:
    """
    Создаёт новый лист (кнопка '+') и переименовывает его через контекстное меню вкладки
    (правый клик -> 'Переименовать'). Используется только при первом запуске (--setup).

    ВАЖНО: кнопка "+" сдвигается вправо после каждого создания листа, поэтому нельзя
    кликать по фиксированным координатам — позиция вычисляется динамически как
    "правый край самой правой вкладки", а новый лист определяется через сравнение
    множества имён вкладок до/после клика (set difference).
    """
    log.info("Создаю лист '%s'...", name)
    before = _tab_boxes(page, volga_frame)
    if not before:
        log.error("Не нашёл ни одной вкладки листа — не могу определить позицию кнопки '+'")
        return False
    before_names = {b["name"] for b in before}
    rightmost = max(before, key=lambda b: b["x"] + b["w"])
    base_x = rightmost["x"] + rightmost["w"]
    base_y = rightmost["y"] + rightmost["h"] / 2

    new_name = None
    after = before
    for offset in (30, 50, 70, 90, 110):
        page.mouse.click(base_x + offset, base_y)
        page.wait_for_timeout(900)
        after = _tab_boxes(page, volga_frame)
        diff = {b["name"] for b in after} - before_names
        if diff:
            new_name = diff.pop()
            break

    if new_name is None:
        log.error("Не удалось создать новый лист (кнопка '+' не найдена динамически) для '%s'", name)
        return False

    target = next(b for b in after if b["name"] == new_name)
    page.mouse.click(target["x"] + target["w"] / 2, target["y"] + target["h"] / 2, button="right")
    page.wait_for_timeout(500)
    try:
        volga_frame.get_by_text("Переименовать", exact=False).first.click(timeout=5000)
    except PWTimeout:
        log.error("Не нашёл пункт меню 'Переименовать' для нового листа '%s' — переименуй вручную и перезапусти --setup", new_name)
        return False

    page.wait_for_timeout(500)
    page.keyboard.press("Control+A")
    page.keyboard.type(name, delay=30)
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    return _sheet_exists(volga_frame, name)


# ---------------------------------------------------------------------------
# Планирование и применение upsert
# ---------------------------------------------------------------------------

def _plan_rows(spec: TableSpec, parsed_rows, state: dict, today: date, override):
    """Возвращает (план [(action, data_row_idx, дата, значения), ...], обновлённое sheet_state)."""
    yesterday = today - timedelta(days=1)
    rows = parsed_rows
    if spec.komета_special and override is not None:
        rows = _apply_komета_override(parsed_rows, override, yesterday)

    sheet_state = state.get(spec.sheet_name, {"next_row": 0, "dates": {}})
    working = {"next_row": sheet_state["next_row"], "dates": dict(sheet_state["dates"])}
    cutoff = today - timedelta(days=REFRESH_DAYS - 1)

    plan = []
    for d, vals in sorted(rows, key=lambda x: x[0]):
        if d < cutoff:
            continue  # даты старше окна обновления не трогаем
        key = d.isoformat()
        if key in working["dates"]:
            plan.append(("replace", working["dates"][key], d, vals))
        else:
            idx = working["next_row"]
            plan.append(("append", idx, d, vals))
            working["dates"][key] = idx
            working["next_row"] = idx + 1
    return plan, working


def _apply_plan(page, spec: TableSpec, plan) -> None:
    for action, row_idx, d, vals in plan:
        out = [vals[i] for i in spec.write_col_indices] if spec.write_col_indices else vals
        log.info("  [%s] %s -> строка данных #%d: %s = %s",
                 spec.sheet_name, "замена" if action == "replace" else "добавление", row_idx, d.isoformat(), out)
        _write_data_row(page, row_idx, [d.strftime("%d.%m.%Y")] + out)


# ---------------------------------------------------------------------------
# Основной сценарий
# ---------------------------------------------------------------------------

def _open_dashboard_and_parse(page, today: date):
    log.info("Открываю дашборд: %s", DASHBOARD_URL)
    page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=90_000)
    page.wait_for_timeout(20_000)

    raw_tables = _extract_tables(page)
    log.info("Найдено таблиц на дашборде: %d", len(raw_tables))

    parsed = {}
    for spec in TABLES:
        rows = _parse_data_rows(raw_tables[spec.dom_index], spec.header_rows, spec.n_data_cols)
        parsed[spec.sheet_name] = rows
        log.info("[%s] распарсено строк данных: %d (последняя: %s)",
                 spec.sheet_name, len(rows), rows[-1][0].isoformat() if rows else "—")

    source_rows = _parse_data_rows(raw_tables[KOMETA_SOURCE_DOM_INDEX], KOMETA_SOURCE_HEADER_ROWS, 10)
    override = _build_komета_override(source_rows)
    log.info("Подстановка 'вчера' для 'Комета' из 'Корректное...': %s",
             {d.isoformat(): v for d, v in override.items()})
    return parsed, override


def _run_dry_run(parsed, override, state, today):
    cutoff = today - timedelta(days=REFRESH_DAYS - 1)
    log.info("=== ПЛАН UPSERT (--dry-run, без записи). Окно: %s..%s ===",
             cutoff.isoformat(), today.isoformat())
    iao_rows = _build_iao_rows(parsed, override, today)
    plan, _ = _plan_iao(iao_rows, state, today)
    if not plan:
        log.info("[%s] нет изменений в окне обновления", IAO_SHEET)
        return
    for action, row_idx, d, vals in plan:
        log.info("  [%s] %s, строка данных #%d, %s: %s",
                 IAO_SHEET, "ЗАМЕНА" if action == "replace" else "ДОБАВЛЕНИЕ",
                 row_idx, d.isoformat(), vals)


def _run_setup(page, volga_frame):
    """Проверяет наличие листа ИАО (заголовки создаются вручную)."""
    _scroll_tab_bar_to_start(page, volga_frame)
    if _sheet_exists(volga_frame, IAO_SHEET):
        log.info("[%s] лист найден — заголовки установлены вручную, ничего не создаю", IAO_SHEET)
    else:
        log.error("[%s] лист НЕ найден — создай лист вручную с заголовками и перезапусти --setup", IAO_SHEET)


def _run_write(page, volga_frame, parsed, override, state, today):
    _scroll_tab_bar_to_start(page, volga_frame)

    if not _sheet_exists(volga_frame, IAO_SHEET):
        log.error("[%s] лист не найден", IAO_SHEET)
        return
    if not _switch_to_sheet(page, volga_frame, IAO_SHEET):
        log.error("[%s] не удалось переключиться на лист", IAO_SHEET)
        return

    iao_rows = _build_iao_rows(parsed, override, today)
    plan, new_sheet_state = _plan_iao(iao_rows, state, today)
    if not plan:
        log.info("[%s] нет изменений в окне обновления", IAO_SHEET)
        return

    for action, row_idx, d, vals in plan:
        log.info("  [%s] %s -> строка данных #%d: %s = %s",
                 IAO_SHEET, "замена" if action == "replace" else "добавление", row_idx, d.isoformat(), vals)
        _write_iao_row(page, row_idx, [d.strftime("%d.%m.%Y")] + vals)

    state[IAO_SHEET] = new_sheet_state
    _save_state(state)


def main() -> None:
    parser = argparse.ArgumentParser(description="Зеркалирование дашборда DataLens в онлайн-таблицу Яндекс.Диска")
    parser.add_argument("--dry-run", action="store_true", help="только парсинг и план upsert, без записи")
    parser.add_argument("--setup", action="store_true", help="создать недостающие листы-зеркала и заголовки (один раз)")
    parser.add_argument("--write", action="store_true", help="реальная запись/обновление данных (ежедневный запуск)")
    args = parser.parse_args()

    if not (args.dry_run or args.setup or args.write):
        parser.print_help()
        return

    if not COOKIES_FILE.exists():
        log.error("Файл сессии не найден: %s", COOKIES_FILE)
        log.error("Нужна сохранённая сессия DataLens (см. datalens_shooter.py --login)")
        return

    today = date.today()
    state = _load_state()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            storage_state=str(COOKIES_FILE),
            viewport={"width": BROWSER_WIDTH, "height": BROWSER_HEIGHT},
            user_agent=USER_AGENT,
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        parsed, override = _open_dashboard_and_parse(page, today)

        if args.dry_run:
            _run_dry_run(parsed, override, state, today)
            browser.close()
            return

        log.info("Открываю онлайн-таблицу: %s", SHEET_URL)
        page.goto(SHEET_URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(15_000)
        try:
            volga = next(fr for fr in page.frames if "volga.yandex.ru" in fr.url)
        except StopIteration:
            log.error("Не нашёл фрейм редактора таблицы (volga.yandex.ru) — проверь сессию/доступ")
            browser.close()
            return

        if args.setup:
            _run_setup(page, volga)
            # Ждём завершения автосохранения: Яндекс Диск отправляет данные на сервер
            # через сетевые запросы — networkidle гарантирует, что они отправлены до закрытия.
            log.info("Ожидаем автосохранения (networkidle, до 30 сек)...")
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PWTimeout:
                log.warning("networkidle не дождался за 30 сек — продолжаем")
            page.wait_for_timeout(3_000)
        elif args.write:
            _run_write(page, volga, parsed, override, state, today)

        browser.close()

    log.info("Готово.")


if __name__ == "__main__":
    main()
