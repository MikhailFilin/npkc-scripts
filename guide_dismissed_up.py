# Портфолио врача — справочник уволенных сотрудников
"""
guide_dismissed_up.py
Выгрузка уволенных из CRM Умного процесса Битрикс24 → ClickHouse (Yandex Cloud).
При каждом запуске таблица полностью перезаписывается (TRUNCATE + INSERT).

INPUT:  Bitrix24 REST API (SPA entityTypeId=1200), scope: crm, user
OUTPUT: dwh_test_db.guide_doctors_dismissal_v2

Запуск: py -3 -X utf8 guide_dismissed_up.py
"""

import os
import sys
import logging
from datetime import datetime, date

import requests
import clickhouse_connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import personal_config as cfg
from ntfy_notifier import send_ntfy_alert

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BITRIX_WEBHOOK    = cfg.BITRIX_WEBHOOK
SPA_ENTITY_TYPE_ID = 1200
PAGE_SIZE          = 50
TABLE_NAME         = "guide_doctors_dismissal_v2"

CH_HOST     = cfg.CH_HOST_TARGET
CH_PORT     = cfg.CH_PORT_TARGET
CH_USER     = cfg.CH_USER_TARGET
CH_PASSWORD = cfg.CH_PASSWORD_TARGET
CH_DATABASE = cfg.CH_DATABASE_TARGET

# API поле → (колонка CH, тип)
FIELD_MAP: list[tuple[str, str, str]] = [
    ("id",                   "bx_id",               "UInt32"),
    ("ufCrm79_1765522812",   "employee_name",        "String"),   # Сотрудник на увольнение
    ("createdBy",            "created_by",           "String"),   # Кем создан
    ("assignedById",         "assigned_to",          "String"),   # Ответственный
    ("stageId",              "stage",                "String"),   # Стадия
    ("ufCrm79_1765522100",   "kpe_document",         "String"),   # Документ о снижении КПЭ
    ("ufCrm79_1765522131",   "kpe_size",             "String"),   # Размер КПЭ
    ("ufCrm79_1765522166",   "voting_status_9",      "String"),   # Статус голосования 9
    ("ufCrm79_1765522182",   "commission_member_9",  "String"),   # Член комиссии 9
    ("ufCrm79_1765522196",   "department_parus",     "String"),   # Подразделение (из Паруса)
    ("ufCrm79_1765522215",   "additional_info",      "String"),   # Дополнительная информация
    ("ufCrm79_1765522238",   "reregistration",       "String"),   # Переоформление сотрудника
    ("ufCrm79_1765522259",   "descriptions_count",   "String"),   # Описаний за месяц
    ("ufCrm79_1765522270",   "labor_code_article",   "String"),   # Иное (Статья ТК РФ)
    ("ufCrm79_1765522279",   "dismissal_reason",     "String"),   # Иное (Причина увольнения)
    ("ufCrm79_1765522798",   "dismissal_date",       "Date"),     # Дата увольнения
]

# Поля с ID пользователя → разрешаем в ФИО
USER_REF_API_FIELDS = {"createdBy", "assignedById", "ufCrm79_1765522182", "ufCrm79_1765522812"}

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    bx_id               UInt32,
    employee_name       String,
    created_by          String,
    assigned_to         String,
    stage               String,
    kpe_document        String,
    kpe_size            String,
    voting_status_9     String,
    commission_member_9 String,
    department_parus    String,
    additional_info     String,
    reregistration      String,
    descriptions_count  String,
    labor_code_article  String,
    dismissal_reason    String,
    dismissal_date      Date,
    load_datetime       DateTime
) ENGINE = MergeTree()
ORDER BY bx_id
"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bitrix24
# ---------------------------------------------------------------------------

def bx(method: str, params: dict | None = None) -> dict:
    url = f"{BITRIX_WEBHOOK}/{method}.json"
    try:
        r = requests.post(url, json=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("Bitrix API [%s]: %s", method, e)
        return {}


def fetch_stages() -> dict[str, str]:
    prefix = f"DYNAMIC_{SPA_ENTITY_TYPE_ID}_STAGE_"
    resp = bx("crm.status.list")
    return {
        s["STATUS_ID"]: s["NAME"]
        for s in resp.get("result", [])
        if str(s.get("ENTITY_ID", "")).startswith(prefix)
    }


def fetch_all_items() -> list[dict]:
    items: list[dict] = []
    start = 0
    while True:
        resp = bx("crm.item.list", {"entityTypeId": SPA_ENTITY_TYPE_ID, "start": start})
        batch = resp.get("result", {}).get("items", [])
        if not batch:
            break
        items.extend(batch)
        total = resp.get("total", 0)
        log.info("Загружено: %d / %d", len(items), total)
        if len(items) >= total:
            break
        start += PAGE_SIZE
    return items


def resolve_users(user_ids: set[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    names: dict[int, str] = {}
    ids = list(user_ids)
    for i in range(0, len(ids), 50):
        resp = bx("user.get", {"FILTER": {"ID": ids[i : i + 50]}})
        for u in resp.get("result", []):
            uid = int(u["ID"])
            full = " ".join(filter(None, [u.get("LAST_NAME"), u.get("NAME"), u.get("SECOND_NAME")]))
            names[uid] = full.strip() or str(uid)
    return names


# ---------------------------------------------------------------------------
# Преобразование значений
# ---------------------------------------------------------------------------

_EMPTY_DATE = date(1900, 1, 1)


def _to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x is not None)
    if isinstance(v, bool):
        return "Да" if v else "Нет"
    return str(v).strip()


def _to_date(v) -> date:
    if not v:
        return _EMPTY_DATE
    try:
        return datetime.fromisoformat(str(v)).date()
    except Exception:
        return _EMPTY_DATE


def build_rows(
    items: list[dict],
    stage_map: dict[str, str],
    user_names: dict[int, str],
    load_dt: datetime,
) -> list[list]:
    rows = []
    ch_cols = [col for _, col, _ in FIELD_MAP]

    for item in items:
        row = []
        for api_field, ch_col, ch_type in FIELD_MAP:
            raw = item.get(api_field)

            if api_field in USER_REF_API_FIELDS and raw is not None:
                try:
                    raw = user_names.get(int(raw), str(raw))
                except (ValueError, TypeError):
                    raw = str(raw) if raw else ""

            elif api_field == "stageId" and raw is not None:
                raw = stage_map.get(str(raw), str(raw))

            if ch_type == "Date":
                row.append(_to_date(raw))
            elif ch_type == "UInt32":
                try:
                    row.append(int(raw) if raw is not None else 0)
                except (ValueError, TypeError):
                    row.append(0)
            else:
                row.append(_to_str(raw))

        row.append(load_dt)  # load_datetime
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# ClickHouse
# ---------------------------------------------------------------------------

def get_ch_client():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT,
        username=CH_USER, password=CH_PASSWORD,
        database=CH_DATABASE, secure=True, verify=False,
    )


def ensure_table(client) -> None:
    client.command(DDL)
    log.info("Таблица %s готова.", TABLE_NAME)


def upload(client, rows: list[list]) -> None:
    col_names = [col for _, col, _ in FIELD_MAP] + ["load_datetime"]
    log.info("TRUNCATE %s …", TABLE_NAME)
    client.command(f"TRUNCATE TABLE {TABLE_NAME}")
    log.info("INSERT %d строк …", len(rows))
    client.insert(TABLE_NAME, rows, column_names=col_names)
    log.info("Готово: %d строк загружено в %s.%s", len(rows), CH_DATABASE, TABLE_NAME)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Запуск guide_dismissed_up: {TABLE_NAME}")
    send_ntfy_alert(
        f"Начинаю выгрузку уволенных → {TABLE_NAME}",
        title="guide_dismissed Start", priority="default", tags="inbox",
    )

    try:
        log.info("Получаем стадии …")
        stage_map = fetch_stages()
        log.info("Стадий: %d", len(stage_map))

        log.info("Загружаем элементы из Битрикс24 …")
        items = fetch_all_items()
        if not items:
            log.warning("Элементов не найдено — выход.")
            return
        log.info("Итого: %d записей", len(items))

        uid_set: set[int] = set()
        for item in items:
            for af in USER_REF_API_FIELDS:
                v = item.get(af)
                if v is not None:
                    try:
                        uid_set.add(int(v))
                    except (ValueError, TypeError):
                        pass
        log.info("Разрешаем %d пользователей …", len(uid_set))
        user_names = resolve_users(uid_set)

        load_dt = datetime.now()
        rows = build_rows(items, stage_map, user_names, load_dt)

        log.info("Подключаемся к ClickHouse …")
        client = get_ch_client()
        ensure_table(client)
        upload(client, rows)
        client.close()

        msg = f"✅ {len(rows)} строк → {CH_DATABASE}.{TABLE_NAME}"
        print(msg)
        send_ntfy_alert(msg, title="guide_dismissed Done", priority="high", tags="white_check_mark")

    except Exception as e:
        err = f"❌ Ошибка: {e}"
        log.exception(err)
        send_ntfy_alert(str(e)[:120], title="guide_dismissed Error", priority="urgent", tags="fire")
        sys.exit(1)


if __name__ == "__main__":
    main()
