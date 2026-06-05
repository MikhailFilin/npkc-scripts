"""
datalens_sync.py
Оркестратор: запускает 3 парсера последовательно и загружает данные в ClickHouse.

Таблицы в dwh_test_db:
  datalens_cloud_users     ← ycloud_users_parser
  datalens_workbooks_users ← datalens_workbooks_parser
  datalens_dashboards      ← datalens_dashboards_parser

# INPUT:  Ручная авторизация (если сессия не сохранена)
# OUTPUT: 3 таблицы в ClickHouse dwh_test_db

Использование:
    python datalens_sync.py
    python datalens_sync.py --skip-cloud     # пропустить парсер пользователей Cloud
    python datalens_sync.py --skip-workbooks # пропустить парсер доступов
    python datalens_sync.py --skip-dashboards
    python datalens_sync.py --debug
"""

import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime

# Подключаем папку скрипта для импортов
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import clickhouse_connect

# Импортируем collect() из каждого парсера
from ycloud_users_parser       import collect as collect_cloud_users
from datalens_workbooks_parser import collect as collect_workbooks_users
from datalens_dashboards_parser import collect as collect_dashboards

SCRIPT_DIR = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "datalens_sync.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ClickHouse (Yandex Cloud, целевая БД)
try:
    from personal_config import (
        CH_HOST_TARGET    as CH_HOST,
        CH_PORT_TARGET    as CH_PORT,
        CH_USER_TARGET    as CH_USER,
        CH_PASSWORD_TARGET as CH_PASSWORD,
        CH_DATABASE_TARGET as CH_DATABASE,
    )
except ImportError:
    logger.error("personal_config.py не найден — добавьте project/scripts/ в PATH")
    sys.exit(1)

# ============================================================
# DDL таблиц
# ============================================================
DDL = {
    "datalens_cloud_users": """
        CREATE TABLE IF NOT EXISTS {db}.datalens_cloud_users
        (
            display_name  String,
            username      String,
            provider      String,
            last_auth     String,
            user_id       String,
            updated_at    DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (user_id)
    """,
    "datalens_workbooks_users": """
        CREATE TABLE IF NOT EXISTS {db}.datalens_workbooks_users
        (
            workbook      String,
            user_name     String,
            email         String,
            access_level  String,
            updated_at    DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (workbook, email)
    """,
    "datalens_dashboards": """
        CREATE TABLE IF NOT EXISTS {db}.datalens_dashboards
        (
            collection    String,
            workbook      String,
            dashboard     String,
            updated_at    DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (collection, workbook, dashboard)
    """,
}


def get_ch_client():
    """Создаёт подключение к ClickHouse (Yandex Cloud, HTTPS)."""
    return clickhouse_connect.get_client(
        host=CH_HOST,
        port=CH_PORT,
        username=CH_USER,
        password=CH_PASSWORD,
        database=CH_DATABASE,
        secure=True,
        verify=False,
    )


def ensure_table(client, table: str) -> None:
    """Создаёт таблицу если её нет."""
    ddl = DDL[table].format(db=CH_DATABASE)
    client.command(ddl)
    logger.info(f"  Таблица {CH_DATABASE}.{table} — OK")


def upload(client, table: str, rows: list[dict], columns: list[str]) -> None:
    """Полная перезапись таблицы: TRUNCATE + INSERT."""
    if not rows:
        logger.warning(f"  Нет данных для {table} — пропускаем")
        return

    # Очищаем таблицу
    client.command(f"TRUNCATE TABLE {CH_DATABASE}.{table}")
    logger.info(f"  TRUNCATE {table} — OK")

    # updated_at должен быть datetime-объектом, не строкой
    ts = datetime.now()
    data = [[row.get(c, "") for c in columns] + [ts] for row in rows]

    client.insert(
        table=f"{CH_DATABASE}.{table}",
        data=data,
        column_names=columns + ["updated_at"],
    )
    logger.info(f"  INSERT {table}: {len(rows)} строк — OK")


# ============================================================
# Основные шаги
# ============================================================
def step_cloud_users(client, debug: bool) -> None:
    logger.info("\n" + "=" * 50)
    logger.info("ШАГ 1/3: Пользователи Yandex Cloud (center.yandex.cloud)")
    logger.info("=" * 50)
    ensure_table(client, "datalens_cloud_users")

    rows = collect_cloud_users(debug=debug)
    logger.info(f"  Собрано пользователей: {len(rows)}")

    upload(client, "datalens_cloud_users", rows,
           ["display_name", "username", "provider", "last_auth", "user_id"])


def step_workbooks_users(client, debug: bool) -> None:
    logger.info("\n" + "=" * 50)
    logger.info("ШАГ 2/3: Права доступа к воркбукам DataLens")
    logger.info("=" * 50)
    ensure_table(client, "datalens_workbooks_users")

    rows = collect_workbooks_users(debug=debug)
    logger.info(f"  Собрано записей доступа: {len(rows)}")

    upload(client, "datalens_workbooks_users", rows,
           ["workbook", "user_name", "email", "access_level"])


def step_dashboards(client, debug: bool) -> None:
    logger.info("\n" + "=" * 50)
    logger.info("ШАГ 3/3: Дашборды по воркбукам DataLens")
    logger.info("=" * 50)
    ensure_table(client, "datalens_dashboards")

    rows = collect_dashboards(debug=debug)
    logger.info(f"  Собрано дашбордов: {len(rows)}")

    upload(client, "datalens_dashboards", rows,
           ["collection", "workbook", "dashboard"])


# ============================================================
# Точка входа
# ============================================================
def run(skip_cloud: bool = False, skip_workbooks: bool = False,
        skip_dashboards: bool = False, debug: bool = False) -> None:

    logger.info(f"\n{'='*50}")
    logger.info(f"DataLens Sync — старт {datetime.now():%Y-%m-%d %H:%M:%S}")
    logger.info(f"База: {CH_DATABASE} @ {CH_HOST}")
    logger.info(f"{'='*50}")

    client = get_ch_client()
    logger.info("  Подключение к ClickHouse — OK")

    errors = []

    if not skip_cloud:
        try:
            step_cloud_users(client, debug)
        except Exception as exc:
            logger.error(f"  ШАГ 1 ОШИБКА: {exc}")
            errors.append(("cloud_users", str(exc)))

    if not skip_workbooks:
        try:
            step_workbooks_users(client, debug)
        except Exception as exc:
            logger.error(f"  ШАГ 2 ОШИБКА: {exc}")
            errors.append(("workbooks_users", str(exc)))

    if not skip_dashboards:
        try:
            step_dashboards(client, debug)
        except Exception as exc:
            logger.error(f"  ШАГ 3 ОШИБКА: {exc}")
            errors.append(("dashboards", str(exc)))

    logger.info(f"\n{'='*50}")
    if errors:
        logger.warning(f"Завершено с ошибками: {[e[0] for e in errors]}")
    else:
        logger.info("Все шаги выполнены успешно.")
    logger.info(f"DataLens Sync — конец {datetime.now():%Y-%m-%d %H:%M:%S}")
    logger.info("=" * 50)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="DataLens Sync: парсит данные и загружает в ClickHouse dwh_test_db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python datalens_sync.py                      # запустить все 3 шага
  python datalens_sync.py --skip-cloud         # только доступы + дашборды
  python datalens_sync.py --skip-dashboards    # только пользователи + доступы
  python datalens_sync.py --debug              # сохранить HTML-дампы
        """,
    )
    parser.add_argument("--skip-cloud",      action="store_true", help="Пропустить сбор пользователей Cloud")
    parser.add_argument("--skip-workbooks",  action="store_true", help="Пропустить сбор прав доступа")
    parser.add_argument("--skip-dashboards", action="store_true", help="Пропустить сбор дашбордов")
    parser.add_argument("--debug",           action="store_true", help="Сохранять HTML-дампы при парсинге")
    args = parser.parse_args()

    run(
        skip_cloud=args.skip_cloud,
        skip_workbooks=args.skip_workbooks,
        skip_dashboards=args.skip_dashboards,
        debug=args.debug,
    )
