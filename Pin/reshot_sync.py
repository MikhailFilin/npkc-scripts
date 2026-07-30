"""
reshot_sync.py
Обновление mrc_2.reshot_pin из последнего Excel-файла в downloads/.

Алгоритм:
  1. Читаем последний скачанный Excel (решот) из downloads/
  2. Нормализуем accession numbers (убираем пробелы, добавляем 'A' если нет)
  3. Выгружаем текущее содержимое mrc_2.reshot_pin
  4. Объединяем, дедублицируем
  5. TRUNCATE mrc_2.reshot_pin + INSERT
  6. REFRESH MATERIALIZED VIEW x4

Запуск: py -3 reshot_sync.py [--file path/to/file.xlsx]
  --file   принудительно указать файл (по умолчанию — последний из downloads/)

# INPUT:  downloads/*.xlsx, PostgreSQL mrc_2.reshot_pin
# OUTPUT: PostgreSQL mrc_2.reshot_pin, обновлённые матвью datalake.*
"""

import argparse
import os
import re
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import openpyxl
import pandas as pd
from sqlalchemy import create_engine, text

_project = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(_project, "scripts"))
sys.path.insert(0, _project)
import personal_config as cfg
from ntfy_notifier import send_ntfy_alert

# === Настройки ===
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")

# Таблица accession numbers в PostgreSQL
PG_TABLE_RESHOT = "mrc_2.reshot_pin"
# Имя столбца с accession number в mrc_2.reshot_pin
RESHOT_COL = "accession_number"

MATERIALIZED_VIEWS = [
    "datalake.v_task_pin_dash",
    "datalake.defects",
    "datalake.defects_completed",
    "datalake.pin_mo_mrc",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_latest_excel() -> str | None:
    """Возвращает путь к самому свежему xlsx в downloads/."""
    files = [
        f for f in os.listdir(DOWNLOAD_DIR)
        if f.lower().endswith(('.xlsx', '.xls', '.xlsm'))
    ]
    if not files:
        return None
    files.sort(key=lambda f: os.path.getmtime(os.path.join(DOWNLOAD_DIR, f)), reverse=True)
    return os.path.join(DOWNLOAD_DIR, files[0])


def normalize_accession(raw: str) -> str | None:
    """
    Нормализует accession number:
      - Убирает пробелы
      - Заменяет кириллическую А на латинскую A
      - Добавляет 'A' если начинается с цифры
    """
    if not raw:
        return None
    s = str(raw).strip()
    s = re.sub(r'\s+', '', s)            # убираем все пробелы
    s = s.replace('А', 'A')             # кириллическая А → латинская A
    s = s.replace('а', 'A')             # на всякий случай строчная
    if s and s[0].isdigit():
        s = 'A' + s                      # нет буквы — добавляем A
    if not re.match(r'^[A-Za-z]\d{5,}$', s):
        return None                      # не похоже на accession — пропускаем
    return s.upper()


def read_excel_accessions(path: str) -> set[str]:
    """Читает accession numbers из Excel (первый столбец, все листы)."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    result = set()
    skipped = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=1, values_only=True):
            val = row[0] if row else None
            if val is None:
                continue
            norm = normalize_accession(str(val))
            if norm:
                result.add(norm)
            else:
                skipped += 1
                if skipped <= 5:
                    print(f"  [пропущено] {repr(val)}")
    wb.close()
    return result


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

def load_existing_reshot(engine) -> set[str]:
    """Читает текущее содержимое mrc_2.reshot_pin."""
    try:
        df = pd.read_sql(f'SELECT "{RESHOT_COL}" FROM {PG_TABLE_RESHOT}', engine)
        return set(df[RESHOT_COL].dropna().astype(str))
    except Exception as e:
        print(f"⚠️ Не удалось прочитать {PG_TABLE_RESHOT}: {e}")
        return set()


def save_reshot(to_add: set[str], engine) -> int:
    """INSERT только новых accession numbers (без TRUNCATE — существующие данные не трогаем)."""
    if not to_add:
        return 0
    schema, table = PG_TABLE_RESHOT.split('.')
    df = pd.DataFrame({RESHOT_COL: sorted(to_add)})
    df.to_sql(table, engine, schema=schema, if_exists='append', index=False, method='multi', chunksize=500)
    return len(df)


def refresh_views(engine) -> None:
    """Рефрешит все материализованные вью."""
    with engine.begin() as conn:
        for mv in MATERIALIZED_VIEWS:
            print(f"  🔄 REFRESH {mv}...")
            conn.execute(text(f"REFRESH MATERIALIZED VIEW {mv}"))
            print(f"  ✅ {mv}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Обновление reshot_pin из Excel")
    parser.add_argument("--file", help="Принудительно указать Excel-файл")
    parser.add_argument("--no-refresh", action="store_true", help="Не рефрешить матвью после заливки")
    parser.add_argument("--refresh-only", action="store_true", help="Только рефреш матвью, без заливки данных")
    args = parser.parse_args()

    pg_url = f"postgresql://{cfg.PG_USER}:{cfg.PG_PASSWORD}@{cfg.PG_HOST}:{cfg.PG_PORT}/{cfg.PG_DATABASE}"
    engine = create_engine(pg_url)

    if args.refresh_only:
        print("🔄 Рефреш матвью (финальный)...")
        send_ntfy_alert("Рефреш матвью Pin...", title="Pin Refresh", priority="default", tags="arrows_counterclockwise")
        try:
            refresh_views(engine)
            print("✅ Матвью обновлены.")
            send_ntfy_alert(
                "✅ Дашборд ПИН и Рейтинг МО обновлён.",
                title="Pin Dashboard Updated",
                priority="high",
                tags="bar_chart",
            )
        except Exception as e:
            msg = f"❌ Ошибка рефреша матвью: {e}"
            print(msg)
            send_ntfy_alert(msg[:200], title="Pin Refresh Error", priority="urgent", tags="sos")
        return

    excel_path = args.file or get_latest_excel()
    if not excel_path or not os.path.exists(excel_path):
        print("❌ Excel-файл не найден.")
        send_ntfy_alert("reshot_sync: Excel-файл не найден", title="Pin Reshot Error", priority="high", tags="warning")
        return

    filename = os.path.basename(excel_path)
    print(f"📂 Файл: {filename}")
    send_ntfy_alert(f"Обновление reshot_pin из {filename}...", title="Pin Reshot", priority="default", tags="inbox")

    # 1. Читаем новые accession numbers из Excel
    new_accessions = read_excel_accessions(excel_path)
    print(f"  Из Excel: {len(new_accessions)} номеров")

    # 2. Читаем существующие
    existing = load_existing_reshot(engine)
    print(f"  В БД сейчас: {len(existing)} номеров")

    # 3. Вычисляем только действительно новые (которых нет в БД)
    added = new_accessions - existing
    print(f"  Добавлено новых: {len(added)} (уже в БД: {len(existing)}, пропущено дублей: {len(new_accessions) - len(added)})")

    # 4. Вставляем только новые — существующие данные не трогаем
    count = save_reshot(added, engine)
    print(f"✅ mrc_2.reshot_pin: добавлено {count} строк")

    if args.no_refresh:
        return

    # 5. Рефрешим матвью (только если не передан --no-refresh)
    print("🔄 Обновляю материализованные вью...")
    send_ntfy_alert("Рефреш матвью Pin...", title="Pin Refresh", priority="default", tags="arrows_counterclockwise")
    try:
        refresh_views(engine)
        print(f"✅ Матвью обновлены. Добавлено: +{count} новых номеров")
        send_ntfy_alert(
            f"✅ Завершено обновление дашборда ПИН и Рейтинг МО (+{count} номеров)",
            title="Pin Dashboard Updated",
            priority="high",
            tags="bar_chart",
        )
    except Exception as e:
        msg = f"❌ Ошибка рефреша матвью: {e}"
        print(msg)
        send_ntfy_alert(msg[:200], title="Pin Refresh Error", priority="urgent", tags="sos")


if __name__ == "__main__":
    main()
