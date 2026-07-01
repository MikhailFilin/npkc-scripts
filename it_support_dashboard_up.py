"""
it_support_dashboard_up.py — Загрузка задач очереди поддержки ИТ из Яндекс.Трекера в PostgreSQL
Постранично выгружает задачи очереди ITSPPRT за TARGET_YEAR, сохраняет их в CSV
и делает upsert в PostgreSQL. Работает по расписанию — обновление каждый час в :00.

# INPUT:  Yandex Tracker API (очередь ITSPPRT)
# OUTPUT: it_support.it_database (PostgreSQL, Yandex Cloud) + CSV-буфер рядом со скриптом
"""

import ast
import logging
import time
from collections import defaultdict
from datetime import datetime

import pandas as pd
import psycopg2
import pytz
import requests
import schedule
from psycopg2.extras import execute_values

import personal_config as cfg

# Инициализация логгера
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === Настройки Яндекс.Трекера ===
TRACKER_TOKEN  = cfg.TRACKER_TOKEN
TRACKER_ORG_ID = cfg.TRACKER_ORG_ID
QUEUE_KEY      = 'ITSPPRT'
TARGET_YEAR    = 2026

HEADERS = {
    'Authorization': f'OAuth {TRACKER_TOKEN}',
    'X-Cloud-Org-Id': TRACKER_ORG_ID,
}

ESSENTIAL_FIELDS = [
    'key',
    'summary',
    'createdAt',
    'updatedAt',
    'resolvedAt',
    'statusStartTime',
    'deadline',
    'status.display',
    'previousStatus.display',
    'resolution.display',
    'project.display',
    'tags',
    'assignee.display',
    'createdBy.display',
    'resolvedBy.display',
    'commentWithExternalMessageCount',
    'commentWithoutExternalMessageCount',
    'priority.display',
    'statusType.display',
    'Line',
    'issue_url',
]

# === Настройки PostgreSQL ===
PG_CONN_PARAMS = {
    'host': cfg.PG_HOST,
    'port': cfg.PG_PORT,
    'dbname': cfg.PG_DATABASE,
    'user': cfg.PG_USER_ITSUPPORT,
    'password': cfg.PG_PASSWORD_ITSUPPORT,
    'connect_timeout': 10,
    'options': '-c statement_timeout=60000',
}
TABLE_NAME = '"it_support"."it_database"'
CSV_FILE   = f'{QUEUE_KEY}_issues_{TARGET_YEAR}_filtered.csv'

LOCAL_TZ = pytz.timezone('Europe/Moscow')


def _extract_fields(issue: dict) -> dict:
    """Достаёт значения ESSENTIAL_FIELDS из задачи, разворачивая вложенные поля через точку."""
    filtered = {}
    for field in ESSENTIAL_FIELDS:
        if '.' in field:
            value = issue
            for part in field.split('.'):
                value = value.get(part) if isinstance(value, dict) else None
                if value is None:
                    break
            filtered[field] = value
        else:
            filtered[field] = issue.get(field)

    issue_key = issue.get('key')
    filtered['issue_url'] = f'https://tracker.yandex.ru/{issue_key}' if issue_key else None
    return filtered


def fetch_issues() -> list[dict]:
    """Постранично выгружает задачи очереди QUEUE_KEY и оставляет только созданные в TARGET_YEAR."""
    page_size = 50
    max_pages = 200
    filtered_issues = []
    yearly_counts = defaultdict(int)

    logger.info(f"Начинаю выгрузку задач очереди {QUEUE_KEY} за {TARGET_YEAR} год...")

    for page in range(1, max_pages + 1):
        try:
            url = f'https://api.tracker.yandex.net/v2/issues?filter=queue:{QUEUE_KEY}&perPage={page_size}&page={page}'
            response = requests.get(url, headers=HEADERS, timeout=30)

            if response.status_code != 200:
                logger.error(f"Ошибка {response.status_code}: {response.text}")
                break

            issues = response.json()
            if not issues:
                logger.info("Достигнут конец списка задач.")
                break

            page_count = 0
            for issue in issues:
                created_at = issue.get('createdAt')
                if not created_at:
                    continue

                try:
                    created_date = datetime.strptime(created_at, '%Y-%m-%dT%H:%M:%S.%f%z')
                except Exception as e:
                    logger.error(f"Ошибка разбора даты в задаче {issue.get('key')}: {e}")
                    continue

                yearly_counts[created_date.year] += 1
                if created_date.year != TARGET_YEAR:
                    continue

                filtered_issues.append(_extract_fields(issue))
                page_count += 1

            logger.info(f"Страница {page}: обработано {len(issues)} задач, найдено {page_count} за {TARGET_YEAR} год")

        except Exception as e:
            logger.error(f"Критическая ошибка при обработке страницы {page}: {e}")
            break

    for year in sorted(yearly_counts):
        logger.info(f"  {year}: {yearly_counts[year]} задач")

    return filtered_issues


def _normalize_line_value(value):
    """Сворачивает списки из одного элемента (в т.ч. сериализованные строкой) в одиночное значение."""
    if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
        try:
            value = ast.literal_eval(value)
        except Exception:
            return value
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def save_to_csv(issues: list[dict]) -> bool:
    """Готовит DataFrame (часовой пояс дат, поле Line, имена колонок) и сохраняет его в CSV_FILE."""
    if not issues:
        logger.warning("Нет данных для сохранения.")
        return False

    df = pd.DataFrame(issues)

    date_columns = ['createdAt', 'updatedAt', 'resolvedAt', 'statusStartTime', 'deadline']
    for col in date_columns:
        if col not in df.columns:
            continue
        try:
            df[col] = pd.to_datetime(df[col], errors='coerce', utc=True)
            df[col] = df[col].dt.tz_convert(LOCAL_TZ)
            df[col] = df[col].dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.error(f"Ошибка обработки столбца {col}: {e}")
            df[col] = df[col].astype(str)

    if 'Line' in df.columns:
        df['Line'] = df['Line'].apply(_normalize_line_value)

    df = df.rename(columns={col: col.replace('.', '_') for col in df.columns if '.' in col})

    try:
        df.to_csv(CSV_FILE, index=False, encoding='utf-8-sig')
        logger.info(f"CSV: сохранено {len(df)} задач в файл {CSV_FILE}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения в CSV: {e}")
        return False


def load_csv_to_postgres() -> None:
    """Читает CSV_FILE и делает upsert (ON CONFLICT key) в TABLE_NAME PostgreSQL."""
    conn = None
    cursor = None
    try:
        logger.info(f"Читаю CSV {CSV_FILE}...")
        df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
        logger.info(f"CSV прочитан: {len(df)} строк. Подключаюсь к PostgreSQL ({cfg.PG_HOST}:{cfg.PG_PORT}/{cfg.PG_DATABASE})...")

        t0 = time.monotonic()
        conn = psycopg2.connect(**PG_CONN_PARAMS)
        logger.info(f"Подключение установлено за {time.monotonic() - t0:.1f} сек.")
        cursor = conn.cursor()

        records = df.where(pd.notnull(df), None).values.tolist()
        columns = ','.join(df.columns)
        update_set = ', '.join(f"{col} = EXCLUDED.{col}" for col in df.columns if col != 'key')

        insert_query = f"""
            INSERT INTO {TABLE_NAME} ({columns})
            VALUES %s
            ON CONFLICT (key)
            DO UPDATE SET {update_set}
        """

        logger.info(f"Запускаю upsert {len(records)} записей...")
        t0 = time.monotonic()
        execute_values(cursor, insert_query, records)
        conn.commit()
        logger.info(f"Загружено {len(records)} записей в PostgreSQL ({TABLE_NAME}) за {time.monotonic() - t0:.1f} сек.")
    except psycopg2.OperationalError as e:
        logger.error(f"Не удалось подключиться к PostgreSQL (сеть/VPN/таймаут): {e}")
        if conn:
            conn.rollback()
    except Exception as e:
        logger.error(f"Ошибка загрузки в PostgreSQL: {e}")
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def main_job() -> None:
    """Полный цикл обновления: выгрузка из Трекера → CSV → upsert в PostgreSQL."""
    logger.info('=' * 50)
    logger.info(f"Запуск обновления дашборда поддержки ИТ ({datetime.now():%Y-%m-%d %H:%M:%S})")
    logger.info('=' * 50)

    try:
        issues = fetch_issues()
        if save_to_csv(issues):
            load_csv_to_postgres()
        logger.info(f"Обновление завершено в {datetime.now():%Y-%m-%d %H:%M:%S}")
    except Exception as e:
        logger.error(f"Ошибка автоматического обновления: {e}")


def main() -> None:
    """Запускает планировщик: обновление каждый час в :00, первый запуск — сразу."""
    schedule.every().hour.at(":00").do(main_job)

    logger.info("Скрипт запущен. Выполняю первое обновление немедленно...")
    main_job()

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == '__main__':
    main()
