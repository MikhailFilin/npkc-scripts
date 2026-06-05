"""
instrumental_3w_up.py
Синхронизация инструментальных исследований за последние 7 дней
из исходной ClickHouse в целевую через SQLite-буфер.
При каждом запуске: TRUNCATE + полная перезагрузка.
Исключение: пары (ae_title, research_id) замеченные в v_route_eris_trauma за 14 дней;
процедуры-исключения (33, 427, 34) проходят с любого аппарата.

# INPUT:  data_views.v_instrumental_examinations  (source CH, через VPN)
#         data_views.v_route_eris_trauma           (source CH, через VPN)
#         data_views.v_eris_assignment_results     (source CH, через VPN)
#         data_views.v_task_pin                    (source CH, через VPN)
# OUTPUT: instrumental_examinations_3w             (target CH, Yandex Cloud)
"""

import subprocess
import pyautogui
import time
import sys
import os
import sqlite3
import clickhouse_connect
from datetime import datetime, timedelta, date
import datetime as datetime_module

from ntfy_notifier import send_ntfy_alert
import personal_config as cfg

# === Настройки целевой ClickHouse ===
CH_HOST_TARGET     = cfg.CH_HOST_TARGET
CH_PORT_TARGET     = cfg.CH_PORT_TARGET
CH_USER_TARGET     = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_TARGET

# === Настройки исходной ClickHouse ===
CH_HOST_SOURCE     = cfg.CH_HOST
CH_PORT_SOURCE     = cfg.CH_PORT
CH_USER_SOURCE     = cfg.CH_USER
CH_PASSWORD_SOURCE = cfg.CH_PASSWORD
CH_DATABASE_SOURCE = cfg.CH_DATABASE

# === Настройки VPN ===
VPN_APP_PATH   = cfg.VPN_APP_PATH
VPN_PASSWORD   = cfg.VPN_PASSWORD
PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUFFER_PATH = os.path.join(_SCRIPTS_DIR, 'temp_buffer_instrumental_3w.db')
_MON_BUFFER  = os.path.join(_SCRIPTS_DIR, 'temp_buffer_mon_3w.db')
_TABLE_NAME  = 'instrumental_examinations_3w'
_SQLITE_TMP  = 'temp_instrumental_3w'

# Мониторинг актуальности данных
MONITORING_TABLE       = 'dwh_test_db.dashboard_data_health'
MONITORING_DASHBOARD   = 'Инструментальные исследования 7д'
MIN_SAFE_DATETIME      = datetime(1971, 1, 1, 0, 0, 0)
MAX_SAFE_DATETIME      = datetime(2099, 12, 31, 23, 59, 59)
MIN_SAFE_DATE          = date(1971, 1, 1)

# Процедуры-исключения: всегда попадают в выборку даже с травма-аппаратов
_TRAUMA_EXCEPT_RESEARCH_IDS = (33, 427, 34)

# Строго упорядоченный список колонок — порядок совпадает с SELECT в _build_query()
_COLUMNS = [
    'assignment_conduct_id',
    'conduct_date',
    'conduct_doc_id',
    'conduct_doc_cct',
    'conduct_doc_author_job_execution_id',
    'conduct_doc_author_id',
    'conduct_doc_author_fio',
    'conduct_mu_id',
    'conduct_mo_id',
    'conduct_mu_name',
    'conduct_mo_name',
    'district_short_name',
    'assignment_id',
    'assignment_status',
    'assignment_mu_id',
    'assignment_mo_id',
    'assignment_mu_name',
    'assignment_mo_name',
    'research_id',
    'research_name',
    'research_subtype_id',
    'research_subtype_name',
    'research_type_id',
    'research_type',
    'research_kind_id',
    'research_kind',
    'device_type',
    'body_part',
    'diagnosis_code',
    'patient_id',
    'patient_birth_date',
    'patient_age',
    'patient_gender',
    'technician_job_execution_id',
    'technician_id',
    'technician_fio',
    'payment_source',
    'accession_number',
    'ae_title',
    'equipment_result_date',
    'study_uid',
    'url',
    'is_contrast',
    'dose_msv',
    'dose_micro_sv',
    'model_name',
    'eris_result_fio',   # ФИО врача из v_eris_assignment_results (NULL если не описано)
    'pin_status',
    'task_pin_end_date',
    'task_pin_status',
    'is_trauma_exam',        # 'Травма' если accession_number есть в v_route_eris_trauma
    'undescr_status',        # status из dwh_views.v_undescribed_researches
    'undescr_reading_type',  # reading_type_code
    'undescr_action',        # action (DEL_ASSIGN и др.)
    'undescr_cito',          # признак CITO
    'task_list_fio',         # ФИО врача, у кого последним исследование было "в работе"
    'meta_load_date',        # дата загрузки записи в ЛИ (DateTime64(9)) из источника
    'load_datetime',
]

_DATETIME_COLS   = {'conduct_date', 'equipment_result_date', 'task_pin_end_date', 'meta_load_date'}
_DATE_COLS       = set()   # patient_birth_date хранится как String (Date32 до 1970)
_INT_NOT_NULL    = {'assignment_conduct_id'}
_INT_NULLABLE    = {
    'conduct_doc_cct', 'conduct_doc_author_job_execution_id', 'conduct_doc_author_id',
    'conduct_mu_id', 'conduct_mo_id', 'assignment_id', 'assignment_mu_id',
    'assignment_mo_id', 'research_id', 'research_subtype_id', 'research_type_id',
    'research_kind_id', 'patient_id', 'patient_age', 'technician_job_execution_id',
    'technician_id',
}


# === SQLite адаптеры ===
def setup_sqlite_adapters():
    def adapt_date(val):     return val.isoformat()
    def adapt_datetime(val): return val.isoformat()
    def convert_date(val):   return datetime_module.date.fromisoformat(val.decode())
    def convert_ts(val):     return datetime_module.datetime.fromisoformat(val.decode())
    sqlite3.register_adapter(datetime_module.date, adapt_date)
    sqlite3.register_adapter(datetime_module.datetime, adapt_datetime)
    sqlite3.register_converter("date", convert_date)
    sqlite3.register_converter("timestamp", convert_ts)


# === VPN ===
def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN для instrumental_3w...", title="VPN Connect", priority="default", tags="lock")
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
    except Exception as e:
        msg = f"❌ Ошибка запуска VPN: {e}"
        print(msg)
        send_ntfy_alert(msg, title="VPN Error", priority="urgent", tags="warning")
        raise
    time.sleep(15)
    for window in pyautogui.getWindowsWithTitle(''):
        if any(kw in window.title.lower() for kw in ['check point', 'trgui', 'endpoint']):
            try:
                window.activate()
                time.sleep(2)
                print(f"✅ Окно '{window.title}' активировано.")
                break
            except Exception:
                pass
    pyautogui.click(PASSWORD_FIELD_X, PASSWORD_FIELD_Y); time.sleep(0.5)
    pyautogui.click(PASSWORD_FIELD_X, PASSWORD_FIELD_Y); time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'a'); time.sleep(0.3)
    pyautogui.press('delete'); time.sleep(0.5)
    for char in VPN_PASSWORD:
        pyautogui.write(char); time.sleep(0.1)
    time.sleep(1)
    pyautogui.click(CONNECT_BUTTON_X, CONNECT_BUTTON_Y)
    time.sleep(15)
    print("✅ VPN подключён.")
    send_ntfy_alert("VPN подключён", title="VPN Connected", priority="high", tags="key")


def disconnect_vpn():
    print("🛑 Отключаю VPN...")
    send_ntfy_alert("Отключаюсь от VPN...", title="VPN Disconnect", priority="default", tags="unlock")
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y); time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y); time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y); time.sleep(3)
    print("🛑 VPN отключён.")
    send_ntfy_alert("VPN отключён", title="VPN Disconnected", priority="default", tags="check")


def _ensure_table_exists(client) -> None:
    client.command(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_NAME}
        (
            assignment_conduct_id               Int64,
            conduct_date                        Nullable(DateTime),
            conduct_doc_id                      Nullable(String),
            conduct_doc_cct                     Nullable(Int64),
            conduct_doc_author_job_execution_id Nullable(Int64),
            conduct_doc_author_id               Nullable(Int64),
            conduct_doc_author_fio              Nullable(String),
            conduct_mu_id                       Nullable(Int64),
            conduct_mo_id                       Nullable(Int64),
            conduct_mu_name                     Nullable(String),
            conduct_mo_name                     Nullable(String),
            district_short_name                 Nullable(String),
            assignment_id                       Nullable(Int64),
            assignment_status                   Nullable(String),
            assignment_mu_id                    Nullable(Int64),
            assignment_mo_id                    Nullable(Int64),
            assignment_mu_name                  Nullable(String),
            assignment_mo_name                  Nullable(String),
            research_id                         Nullable(Int64),
            research_name                       Nullable(String),
            research_subtype_id                 Nullable(Int64),
            research_subtype_name               Nullable(String),
            research_type_id                    Nullable(Int64),
            research_type                       Nullable(String),
            research_kind_id                    Nullable(Int64),
            research_kind                       Nullable(String),
            device_type                         Nullable(String),
            body_part                           Nullable(String),
            diagnosis_code                      Nullable(String),
            patient_id                          Nullable(Int64),
            patient_birth_date                  Nullable(String),
            patient_age                         Nullable(Int64),
            patient_gender                      Nullable(String),
            technician_job_execution_id         Nullable(Int64),
            technician_id                       Nullable(Int64),
            technician_fio                      Nullable(String),
            payment_source                      Nullable(String),
            accession_number                    Nullable(String),
            ae_title                            Nullable(String),
            equipment_result_date               Nullable(DateTime),
            study_uid                           Nullable(String),
            url                                 Nullable(String),
            is_contrast                         Nullable(String),
            dose_msv                            Nullable(String),
            dose_micro_sv                       Nullable(String),
            model_name                          Nullable(String),
            eris_result_fio                     Nullable(String),
            pin_status                          Nullable(String),
            task_pin_end_date                   Nullable(DateTime),
            task_pin_status                     Nullable(String),
            is_trauma_exam                      String,
            undescr_status                      Nullable(String),
            undescr_reading_type                Nullable(String),
            undescr_action                      Nullable(String),
            undescr_cito                        Nullable(String),
            task_list_fio                       Nullable(String),
            meta_load_date                      Nullable(DateTime64(9)),
            load_datetime                       DateTime
        )
        ENGINE = MergeTree()
        ORDER BY (assignment_conduct_id)
        SETTINGS index_granularity = 8192;
    """)

    # Автомиграция: добавляем колонки которых ещё нет в таблице
    # Нужно если таблица создавалась старой версией скрипта
    new_cols = {
        'eris_result_fio':   'Nullable(String)',
        'pin_status':        'Nullable(String)',
        'task_pin_end_date': 'Nullable(DateTime)',
        'task_pin_status':   'Nullable(String)',
        'is_trauma_exam':       'String',
        'undescr_status':       'Nullable(String)',
        'undescr_reading_type': 'Nullable(String)',
        'undescr_action':       'Nullable(String)',
        'undescr_cito':         'Nullable(String)',
        'task_list_fio':        'Nullable(String)',
        'meta_load_date':       'Nullable(DateTime64(9))',
        'load_datetime':        'DateTime DEFAULT now()',
    }
    try:
        existing = {row[0] for row in client.query(f"DESCRIBE TABLE {_TABLE_NAME}").result_rows}
        for col, col_type in new_cols.items():
            if col not in existing:
                client.command(f"ALTER TABLE {_TABLE_NAME} ADD COLUMN IF NOT EXISTS {col} {col_type}")
                print(f"   ➕ Добавлена колонка {col} {col_type}")
    except Exception as e:
        print(f"⚠️ Автомиграция: {e}")

    print(f"✅ Таблица {_TABLE_NAME} проверена/создана.")


def safe_parse_datetime(val, is_date_only=False):
    if val is None or val in ('', 'NULL'):
        return MIN_SAFE_DATE if is_date_only else MIN_SAFE_DATETIME
    try:
        if isinstance(val, datetime):
            dt = val.replace(tzinfo=None) if val.tzinfo else val
        elif isinstance(val, date):
            dt = datetime.combine(val, datetime.min.time())
        elif isinstance(val, str):
            import dateutil.parser
            dt = dateutil.parser.parse(val.strip()).replace(tzinfo=None)
        else:
            return MIN_SAFE_DATE if is_date_only else MIN_SAFE_DATETIME
        dt = max(MIN_SAFE_DATETIME, min(MAX_SAFE_DATETIME, dt))
        return dt.date() if is_date_only else dt
    except Exception:
        return MIN_SAFE_DATE if is_date_only else MIN_SAFE_DATETIME


def _build_monitoring_query() -> str:
    return f"""
WITH
query_time AS (
    SELECT toDateTime64(now(), 6, 'Europe/Moscow') AS check_timestamp
),
table_checks AS (
    SELECT 'v_instrumental_examinations' AS table_name, 'critical' AS table_type,
        COALESCE((SELECT COUNT(*) FROM data_views.v_instrumental_examinations WHERE toDate(conduct_date) >= today() - 1), 0) AS row_count,
        COALESCE((SELECT toDateTime64(max(conduct_date), 6, 'Europe/Moscow') FROM data_views.v_instrumental_examinations WHERE toDate(conduct_date) >= today() - 1), toDateTime64('1971-01-01 00:00:00', 6, 'Europe/Moscow')) AS last_data_datetime,
        COALESCE((SELECT toDateTime64(conduct_date, 6, 'Europe/Moscow') FROM data_views.v_instrumental_examinations WHERE toDate(conduct_date) >= today() - 1 ORDER BY conduct_date DESC LIMIT 1 OFFSET 1), toDateTime64('1971-01-01 00:00:00', 6, 'Europe/Moscow')) AS previous_data_datetime,
        'Данные о проведённых исследованиях' AS description
    UNION ALL
    SELECT 'v_eris_assignment_results', 'critical',
        COALESCE((SELECT COUNT(*) FROM data_views.v_eris_assignment_results ear LEFT JOIN data_views.v_instrumental_examinations ie ON ear.accession_number = ie.accession_number WHERE toDate(ie.conduct_date) >= today() - 1), 0),
        COALESCE((SELECT toDateTime64(max(ie.conduct_date), 6, 'Europe/Moscow') FROM data_views.v_eris_assignment_results ear LEFT JOIN data_views.v_instrumental_examinations ie ON ear.accession_number = ie.accession_number WHERE toDate(ie.conduct_date) >= today() - 1), toDateTime64('1971-01-01 00:00:00', 6, 'Europe/Moscow')),
        COALESCE((SELECT toDateTime64(ie.conduct_date, 6, 'Europe/Moscow') FROM data_views.v_eris_assignment_results ear LEFT JOIN data_views.v_instrumental_examinations ie ON ear.accession_number = ie.accession_number WHERE toDate(ie.conduct_date) >= today() - 1 ORDER BY ie.conduct_date DESC LIMIT 1 OFFSET 1), toDateTime64('1971-01-01 00:00:00', 6, 'Europe/Moscow')),
        'Описания рентгенологов'
    UNION ALL
    SELECT 'v_route_eris_trauma', 'auxiliary',
        COALESCE((SELECT COUNT(*) FROM data_views.v_route_eris_trauma WHERE toDate(assignment_signed_date) >= today() - 1), 0),
        COALESCE((SELECT toDateTime64(max(assignment_signed_date), 6, 'Europe/Moscow') FROM data_views.v_route_eris_trauma WHERE toDate(assignment_signed_date) >= today() - 1), toDateTime64('1971-01-01 00:00:00', 6, 'Europe/Moscow')),
        COALESCE((SELECT toDateTime64(assignment_signed_date, 6, 'Europe/Moscow') FROM data_views.v_route_eris_trauma WHERE toDate(assignment_signed_date) >= today() - 1 ORDER BY assignment_signed_date DESC LIMIT 1 OFFSET 1), toDateTime64('1971-01-01 00:00:00', 6, 'Europe/Moscow')),
        'Травматологические направления'
    UNION ALL
    SELECT 'v_undescribed_researches', 'auxiliary',
        COALESCE((SELECT COUNT(*) FROM dwh_views.v_undescribed_researches WHERE toDate(create_date) >= today() - 1), 0),
        COALESCE((SELECT toDateTime64(max(create_date), 6, 'Europe/Moscow') FROM dwh_views.v_undescribed_researches WHERE toDate(create_date) >= today() - 1), toDateTime64('1971-01-01 00:00:00', 6, 'Europe/Moscow')),
        COALESCE((SELECT toDateTime64(create_date, 6, 'Europe/Moscow') FROM dwh_views.v_undescribed_researches WHERE toDate(create_date) >= today() - 1 ORDER BY create_date DESC LIMIT 1 OFFSET 1), toDateTime64('1971-01-01 00:00:00', 6, 'Europe/Moscow')),
        'Неописанные исследования'
    UNION ALL
    SELECT 'v_task_pin', 'auxiliary',
        COALESCE((SELECT COUNT(*) FROM data_views.v_task_pin WHERE (task_pin_end_date IS NULL OR toDate(task_pin_end_date) >= today() - 1) AND toDate(task_pin_send_date) <= today()), 0),
        COALESCE((SELECT toDateTime64(max(task_pin_send_date), 6, 'Europe/Moscow') FROM data_views.v_task_pin WHERE (task_pin_end_date IS NULL OR toDate(task_pin_end_date) >= today() - 1) AND toDate(task_pin_send_date) <= today()), toDateTime64('1971-01-01 00:00:00', 6, 'Europe/Moscow')),
        COALESCE((SELECT toDateTime64(task_pin_send_date, 6, 'Europe/Moscow') FROM data_views.v_task_pin WHERE (task_pin_end_date IS NULL OR toDate(task_pin_end_date) >= today() - 1) AND toDate(task_pin_send_date) <= today() ORDER BY task_pin_send_date DESC LIMIT 1 OFFSET 1), toDateTime64('1971-01-01 00:00:00', 6, 'Europe/Moscow')),
        'Задачи в ПИНе'
),
table_status AS (
    SELECT table_name, table_type, row_count, last_data_datetime, previous_data_datetime,
        toDate(last_data_datetime) AS last_data_date,
        CASE
            WHEN last_data_datetime >= toDateTime64(today(), 6, 'Europe/Moscow') THEN 'Данные за текущий день (' || formatDateTime(last_data_datetime, '%d.%m.%Y %H:%M:%S') || ')'
            WHEN last_data_datetime >= toDateTime64(today() - 1, 6, 'Europe/Moscow') THEN 'Данные за предыдущий день (' || formatDateTime(last_data_datetime, '%d.%m.%Y %H:%M:%S') || ')'
            WHEN row_count > 0 THEN 'Данные старше 2 дней (последние: ' || formatDateTime(last_data_datetime, '%d.%m.%Y %H:%M:%S') || ')'
            ELSE 'Нет данных за последние 2 дня'
        END AS table_status,
        CASE WHEN table_type = 'critical' AND (toDate(last_data_datetime) < today() OR row_count = 0) THEN 1 ELSE 0 END AS critical_issue,
        description
    FROM table_checks
),
dashboard_status AS (
    SELECT MAX(CASE WHEN critical_issue = 1 THEN 1 ELSE 0 END) AS has_critical_issues,
        COUNT() AS total_tables,
        SUM(CASE WHEN toDate(last_data_datetime) = today() THEN 1 ELSE 0 END) AS tables_with_today_data,
        SUM(CASE WHEN table_type = 'critical' THEN 1 ELSE 0 END) AS critical_tables_count
    FROM table_status
)
SELECT qt.check_timestamp, ts.table_name, ts.table_type, ts.description, ts.row_count,
    ts.last_data_datetime, ts.previous_data_datetime, ts.last_data_date, ts.table_status,
    CASE WHEN ds.has_critical_issues = 1 THEN 'НЕ ПРИГОДЕН' ELSE 'ПРИГОДЕН' END AS overall_status,
    'Критических таблиц: ' || toString(ds.tables_with_today_data) || ' из ' || toString(ds.critical_tables_count) AS critical_tables_details,
    toUInt32(dateDiff('second', ts.last_data_datetime, qt.check_timestamp)) AS data_latency_seconds,
    '{MONITORING_DASHBOARD}' AS dashboard_name
FROM table_status ts CROSS JOIN query_time qt CROSS JOIN dashboard_status ds
ORDER BY ts.table_type DESC, ts.table_name
"""


def _build_query() -> str:
    # ВАЖНО: порядок колонок в SELECT совпадает с _COLUMNS
    return f"""
WITH
eris_described AS (
    -- Берём последнее описание по каждому исследованию (один accession → один врач)
    SELECT accession_number, assignment_result_emp_fio
    FROM (
        SELECT accession_number, assignment_result_emp_fio,
               ROW_NUMBER() OVER (PARTITION BY accession_number
                                  ORDER BY assignment_result_doc_created_date DESC) AS rn
        FROM data_views.v_eris_assignment_results
        WHERE accession_number IS NOT NULL
    )
    WHERE rn = 1
),
active_pin AS (
    SELECT accession_number
    FROM data_views.v_task_pin
    WHERE task_pin_end_date IS NULL
      AND accession_number IS NOT NULL
    GROUP BY accession_number
),
last_pin AS (
    SELECT accession_number, task_pin_status, task_pin_end_date
    FROM (
        SELECT accession_number, task_pin_status, task_pin_end_date,
               ROW_NUMBER() OVER (PARTITION BY accession_number ORDER BY task_pin_end_date DESC) AS rn
        FROM data_views.v_task_pin
        WHERE task_pin_end_date IS NOT NULL
          AND accession_number  IS NOT NULL
    )
    WHERE rn = 1
),
trauma_exam AS (
    -- Исследования которые ЛИЧНО попали в витрину травмы (не по аппарату, а по accession_number)
    SELECT DISTINCT accession_number
    FROM data_views.v_route_eris_trauma
    WHERE accession_number IS NOT NULL
),
undescribed_status AS (
    -- Последний статус из v_undescribed_researches: приоритет DEL_ASSIGN, потом свежее
    SELECT accession_number, status, reading_type_code, action, cito
    FROM (
        SELECT accession_number, status, reading_type_code, action, cito,
               ROW_NUMBER() OVER (
                   PARTITION BY accession_number
                   ORDER BY
                       CASE WHEN action = 'DEL_ASSIGN' THEN 0 ELSE 1 END,
                       create_date DESC
               ) AS rn
        FROM dwh_views.v_undescribed_researches
        WHERE accession_number IS NOT NULL
    )
    WHERE rn = 1
),
mei_1 AS (
    SELECT medical_employee_id, medical_employee_fio_obf, snils_formatted_obf,
           sdate, edate, cur
    FROM dm_ap2.dct_medical_employees
    WHERE edate >= '2023-01-01'
    GROUP BY ALL
),
mei AS (
    SELECT medical_employee_id, medical_employee_fio_obf, snils_formatted_obf,
           MIN(sdate) AS sdate, MAX(edate) AS edate, cur
    FROM mei_1
    GROUP BY ALL
),
dd AS (
    SELECT doctor_id, medical_employee_id, sdate,
           toDate(REPLACE(toString(edate), '2299-12-31', '2099-12-31')) AS edate, cur
    FROM dm_ap2.dct_doctors
    WHERE edate >= '2023-01-01'
),
res_med_rab AS (
    SELECT dd.doctor_id, mei.medical_employee_fio_obf AS fio,
           dd.sdate AS doc_sdate, dd.edate AS doc_edate, dd.cur AS cur_doc_id,
           mei.sdate AS mei_sdate, mei.edate AS mei_edate, mei.cur AS cur_mei
    FROM dd
    INNER JOIN mei ON dd.medical_employee_id = mei.medical_employee_id
    WHERE dd.edate >= mei.sdate AND dd.edate <= mei.edate
),
grouped_res_med_rab AS (
    SELECT DISTINCT doctor_id, fio,
           MIN(doc_sdate) AS min_doc_sdate, MAX(doc_edate) AS max_doc_edate,
           cur_doc_id, mei_sdate, mei_edate, cur_mei
    FROM res_med_rab
    GROUP BY ALL
),
dop_info AS (
    SELECT DISTINCT assignment_id, accession_number
    FROM data_views.v_instrumental_examinations
    WHERE accession_number IS NOT NULL
    GROUP BY ALL
),
latest_task_per_accession AS (
    SELECT tl.assignment_id, tl.job_execution_id, tl.task_status_code,
           tl.personal_task_list_updated, vie.accession_number
    FROM data_views.v_instrumental_task_lists tl
    LEFT JOIN dop_info vie ON tl.assignment_id = vie.assignment_id
    WHERE vie.accession_number IS NOT NULL
    ORDER BY vie.accession_number, tl.personal_task_list_updated DESC
    LIMIT 1 BY vie.accession_number
),
latest_task_with_fio AS (
    SELECT lta.accession_number, grm.fio AS task_list_fio
    FROM latest_task_per_accession lta
    LEFT JOIN grouped_res_med_rab grm ON lta.job_execution_id = grm.doctor_id
)
SELECT
    e.assignment_conduct_id,
    e.conduct_date,
    e.conduct_doc_id,
    e.conduct_doc_cct,
    e.conduct_doc_author_job_execution_id,
    e.conduct_doc_author_id,
    e.conduct_doc_author_fio,
    e.conduct_mu_id,
    e.conduct_mo_id,
    e.conduct_mu_name,
    e.conduct_mo_name,
    e.district_short_name,
    e.assignment_id,
    e.assignment_status,
    e.assignment_mu_id,
    e.assignment_mo_id,
    e.assignment_mu_name,
    e.assignment_mo_name,
    e.research_id,
    e.research_name,
    e.research_subtype_id,
    e.research_subtype_name,
    e.research_type_id,
    e.research_type,
    e.research_kind_id,
    e.research_kind,
    e.device_type,
    e.body_part,
    e.diagnosis_code,
    e.patient_id,
    e.patient_birth_date,
    e.patient_age,
    e.patient_gender,
    e.technician_job_execution_id,
    e.technician_id,
    e.technician_fio,
    e.payment_source,
    e.accession_number,
    e.ae_title,
    e.equipment_result_date,
    e.study_uid,
    e.url,
    e.is_contrast,
    e.dose_msv,
    e.dose_micro_sv,
    e.model_name,
    ear.assignment_result_emp_fio AS eris_result_fio,
    CASE WHEN ap.accession_number IS NOT NULL THEN 'находится в ПИНе'
         ELSE 'не в ПИНе'
    END AS pin_status,
    lp.task_pin_end_date,
    lp.task_pin_status,
    CASE WHEN tex.accession_number IS NOT NULL THEN 'Травма' ELSE 'Не травма' END AS is_trauma_exam,
    us.status            AS undescr_status,
    us.reading_type_code AS undescr_reading_type,
    us.action            AS undescr_action,
    us.cito              AS undescr_cito,
    ltwf.task_list_fio   AS task_list_fio
FROM data_views.v_instrumental_examinations AS e
LEFT JOIN eris_described        AS ear  ON e.accession_number = ear.accession_number
LEFT JOIN active_pin            AS ap   ON e.accession_number = ap.accession_number
LEFT JOIN last_pin              AS lp   ON e.accession_number = lp.accession_number
LEFT JOIN trauma_exam           AS tex  ON e.accession_number = tex.accession_number
LEFT JOIN undescribed_status    AS us   ON e.accession_number = us.accession_number
LEFT JOIN latest_task_with_fio  AS ltwf ON e.accession_number = ltwf.accession_number
WHERE
    toDate(e.conduct_date) >= today() - 7
    AND e.ae_title            IS NOT NULL
    AND e.accession_number    IS NOT NULL
    AND e.assignment_status  != 'Выполнено'
    -- Исключаем пары (ae_title, research_id) замеченные в травме за 14 дней;
    -- процедуры {_TRAUMA_EXCEPT_RESEARCH_IDS} из фильтра исключены (всегда проходят)
    AND (e.ae_title, e.research_id) NOT IN (
        SELECT DISTINCT t.ae_title, vie.research_id
        FROM data_views.v_route_eris_trauma t
        INNER JOIN data_views.v_instrumental_examinations vie
            ON t.accession_number = vie.accession_number
        WHERE t.ae_title     IS NOT NULL
          AND vie.research_id IS NOT NULL
          AND vie.research_id NOT IN {_TRAUMA_EXCEPT_RESEARCH_IDS}
          AND toDate(t.assignment_signed_date) >= today() - 14
    )
-- Защита от дублей: если один accession_number встречается несколько раз — берём первое по дате
QUALIFY ROW_NUMBER() OVER (PARTITION BY e.accession_number ORDER BY e.conduct_date ASC) = 1
ORDER BY e.conduct_date DESC, e.conduct_mo_name ASC
"""


def _parse_dt(val) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    if isinstance(val, str):
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
            try:
                return datetime.strptime(val[:19], fmt[:len(val)])
            except (ValueError, TypeError):
                continue
    return None


def _process_row(row: tuple, run_dt: datetime) -> list:
    """Позиционное преобразование строки из SQLite → типы ClickHouse.
    Порядок позиций строго соответствует _COLUMNS."""
    processed = []
    for i, val in enumerate(row):
        col = _COLUMNS[i]
        if col == 'load_datetime':
            processed.append(run_dt)  # единая метка на весь запуск
        elif col in _DATETIME_COLS:
            processed.append(_parse_dt(val))
        elif col in _INT_NOT_NULL:
            try:    processed.append(int(val) if val is not None else 0)
            except: processed.append(0)
        elif col in _INT_NULLABLE:
            try:    processed.append(int(val) if val is not None else None)
            except: processed.append(None)
        else:
            # bool (ClickHouse Bool) → "1"/"0" как при прохождении через SQLite TEXT
            # Важно до str(): в Python bool — подкласс int, str(True)="True" а не "1"
            if isinstance(val, bool):
                processed.append('1' if val else '0')
            else:
                processed.append(str(val) if val is not None else None)
    return processed


def _rows_to_sqlite(raw_rows: list, col_names: list) -> list:
    """Возвращает строки в порядке _COLUMNS.
    meta_load_date вычисляется как max(conduct_date) по всем строкам батча и
    проставляется одинаковым значением во все строки (из CH не берётся).
    load_datetime добавляется как None и заполняется в _process_row."""
    expected = len(_COLUMNS)
    ch_cols   = len(col_names)  # CH возвращает всё кроме meta_load_date и load_datetime

    if ch_cols != expected - 2:
        print(f"⚠️ CH вернул {ch_cols} колонок, ожидалось {expected - 2}")
        print(f"   CH cols: {col_names}")
        print(f"   _COLUMNS: {_COLUMNS[:-2]}")

    # max(conduct_date) по всем строкам → одно значение для meta_load_date
    conduct_pos = _COLUMNS.index('conduct_date')  # позиция в _COLUMNS = позиция в CH-строке
    max_conduct = None
    for row in raw_rows:
        val = row[conduct_pos]
        if val is not None:
            if max_conduct is None or val > max_conduct:
                max_conduct = val

    result = []
    for row in raw_rows:
        new_row = list(row)       # позиции 0..N-1 из CH (без meta_load_date и load_datetime)
        new_row.append(max_conduct)  # meta_load_date — одно значение для всех строк
        new_row.append(None)         # load_datetime — заполнится в _process_row
        result.append(new_row)
    return result


def _insert_monitoring(client_target, run_dt: datetime) -> None:
    """Читает мониторинговые данные из SQLite и вставляет в MONITORING_TABLE."""
    # Создать таблицу если нет
    client_target.command(f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_TABLE} (
            check_timestamp        DateTime64(6, 'Europe/Moscow'),
            table_name             String,
            table_type             String,
            description            String,
            row_count              UInt64,
            last_data_datetime     DateTime64(6, 'Europe/Moscow'),
            previous_data_datetime DateTime64(6, 'Europe/Moscow'),
            last_data_date         Date,
            table_status           String,
            overall_status         String,
            critical_tables_details String,
            data_latency_seconds   UInt32,
            dashboard_name         String,
            load_datetime          DateTime
        )
        ENGINE = ReplacingMergeTree(check_timestamp)
        ORDER BY (check_timestamp, table_name)
    """)

    # Автомиграция: добавляем load_datetime если таблица создана старой версией
    try:
        existing_mon = {row[0] for row in client_target.query(f"DESCRIBE TABLE {MONITORING_TABLE}").result_rows}
        if 'load_datetime' not in existing_mon:
            client_target.command(
                f"ALTER TABLE {MONITORING_TABLE} ADD COLUMN IF NOT EXISTS load_datetime DateTime DEFAULT now()"
            )
            print("   ➕ Мониторинг: добавлена колонка load_datetime")
    except Exception as e:
        print(f"⚠️ Автомиграция мониторинга: {e}")

    conn = sqlite3.connect(_MON_BUFFER)
    cur  = conn.cursor()
    cur.execute("SELECT * FROM temp_mon_data;")
    rows = cur.fetchall()
    conn.close()

    _MON_COLS_SQLITE = [
        'check_timestamp', 'table_name', 'table_type', 'description', 'row_count',
        'last_data_datetime', 'previous_data_datetime', 'last_data_date',
        'table_status', 'overall_status', 'critical_tables_details',
        'data_latency_seconds', 'dashboard_name',
    ]
    _MON_COLS_CH = _MON_COLS_SQLITE + ['load_datetime']

    processed = []
    for row in rows:
        p = []
        for i, val in enumerate(row):
            col = _MON_COLS_SQLITE[i]
            if col == 'last_data_date':
                p.append(safe_parse_datetime(val, is_date_only=True))
            elif col == 'check_timestamp':
                p.append(run_dt)  # синхронизируем с load_datetime
            elif col in ('last_data_datetime', 'previous_data_datetime'):
                dt = safe_parse_datetime(val, is_date_only=False)
                p.append(dt)
            elif col in ('row_count', 'data_latency_seconds'):
                try:    p.append(int(val) if val is not None else 0)
                except: p.append(0)
            else:
                p.append(val if val is not None else '')
        p.append(run_dt)  # load_datetime — совпадает с основной таблицей
        processed.append(p)

    client_target.insert(MONITORING_TABLE, processed, column_names=_MON_COLS_CH)
    print(f"✅ Мониторинг записан: {len(processed)} строк → {MONITORING_TABLE}")
    send_ntfy_alert(
        f"Мониторинг '{MONITORING_DASHBOARD}': {processed[0][9] if processed else 'N/A'}",
        title="Monitoring Updated", priority="default", tags="chart_with_upwards_trend",
    )


def export_instrumental_3w() -> bool:
    """Полный цикл: TRUNCATE → VPN → source CH → SQLite → VPN off → target CH."""
    print("📊 [instrumental_3w] Загрузка за последние 7 дней (полная перезагрузка)...")
    send_ntfy_alert(
        "Начинаю синхронизацию instrumental_examinations_3w...",
        title="Instrumental 3W Start", priority="default", tags="inbox",
    )
    setup_sqlite_adapters()

    # Шаг 1: Создание таблицы + TRUNCATE
    client_target_del = None
    try:
        print("🧹 Подключаюсь к целевой базе (очистка старых загрузок)...")
        client_target_del = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False,
        )
        _ensure_table_exists(client_target_del)
        client_target_del.command(
            f"ALTER TABLE {_TABLE_NAME} DELETE WHERE load_datetime < now() - interval 5 day"
        )
        print(f"✅ Загрузки старше 5 дней удалены из {_TABLE_NAME}.")
        client_target_del.close()
        client_target_del = None
    except Exception as e:
        msg = f"❌ Ошибка TRUNCATE: {e}"
        print(msg)
        send_ntfy_alert(f"Ошибка очистки БД: {str(e)[:80]}", title="Instrumental 3W Error",
                        priority="urgent", tags="database")
        if client_target_del:
            client_target_del.close()
        return False

    # Шаг 2: Source CH через VPN → SQLite
    client_source = None
    try:
        connect_vpn()
        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999,
        )
        print("✅ Исходная ClickHouse подключена.")

        print("📥 Выполняю запрос...")
        result    = client_source.query(_build_query())
        raw_rows  = result.result_rows
        col_names = result.column_names
        print(f"📥 Получено {len(raw_rows)} строк. Колонок CH: {len(col_names)}, ожидается: {len(_COLUMNS)}")

        # Попутно выполняем мониторинговый запрос (VPN активен, соединение ещё открыто)
        print("🔍 Выполняю мониторинговый запрос...")
        try:
            mon_result    = client_source.query(_build_monitoring_query())
            mon_raw_rows  = mon_result.result_rows
            mon_col_names = mon_result.column_names
            print(f"✅ Мониторинг: получено {len(mon_raw_rows)} строк.")
        except Exception as e_mon:
            mon_raw_rows  = []
            mon_col_names = []
            print(f"⚠️ Мониторинг пропущен (ошибка запроса): {e_mon}")

        client_source.close()
        client_source = None

        if not raw_rows:
            print("📭 Нет данных за последние 7 дней.")
            send_ntfy_alert("Нет данных instrumental_3w", title="Instrumental 3W Empty",
                            priority="default", tags="inbox")
            disconnect_vpn()
            return True

        # Переупорядочиваем строки: CH-порядок → _COLUMNS-порядок
        ordered_rows = _rows_to_sqlite(raw_rows, col_names)

        # Шаг 3: SQLite буфер (CREATE по _COLUMNS, INSERT позиционно)
        print(f"💾 Создаю SQLite буфер ({_BUFFER_PATH})...")
        conn   = sqlite3.connect(_BUFFER_PATH)
        cursor = conn.cursor()
        cursor.execute(f"DROP TABLE IF EXISTS {_SQLITE_TMP};")
        cursor.execute(
            f"CREATE TABLE {_SQLITE_TMP} ({', '.join(f'{c} TEXT' for c in _COLUMNS)});"
        )
        cursor.executemany(
            f"INSERT INTO {_SQLITE_TMP} VALUES ({', '.join(['?' for _ in _COLUMNS])});",
            ordered_rows,
        )
        conn.commit()
        conn.close()
        print("✅ SQLite буфер заполнен.")

        # SQLite буфер для мониторинга
        if mon_raw_rows:
            conn_m   = sqlite3.connect(_MON_BUFFER)
            cur_m    = conn_m.cursor()
            cur_m.execute("DROP TABLE IF EXISTS temp_mon_data;")
            cur_m.execute("""CREATE TABLE temp_mon_data (
                check_timestamp TEXT, table_name TEXT, table_type TEXT, description TEXT,
                row_count INTEGER, last_data_datetime TEXT, previous_data_datetime TEXT,
                last_data_date TEXT, table_status TEXT, overall_status TEXT,
                critical_tables_details TEXT, data_latency_seconds INTEGER, dashboard_name TEXT
            );""")
            cur_m.executemany(
                f"INSERT INTO temp_mon_data VALUES ({', '.join(['?' for _ in mon_col_names])});",
                mon_raw_rows,
            )
            conn_m.commit()
            conn_m.close()
            print("✅ SQLite буфер мониторинга заполнен.")

    except Exception as e:
        msg = f"❌ Ошибка source CH / SQLite: {e}"
        print(msg)
        send_ntfy_alert(f"Сбой instrumental_3w: {str(e)[:80]}", title="Instrumental 3W Error",
                        priority="urgent", tags="fire")
        if client_source:
            client_source.close()
        if os.path.exists(_BUFFER_PATH):
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
        try: disconnect_vpn()
        except Exception: pass
        return False

    # Шаг 4: Отключение VPN
    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f"⚠️ Ошибка отключения VPN: {e}", title="VPN Warning",
                        priority="default", tags="warning")

    # Шаг 5: Вставка в target CH
    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"🔌 Подключаюсь к целевой ClickHouse (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
            )
            break
        except Exception as e:
            print(f"❌ Попытка {attempt}: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                if os.path.exists(_BUFFER_PATH):
                    try: os.remove(_BUFFER_PATH)
                    except Exception: pass
                return False

    try:
        conn   = sqlite3.connect(_BUFFER_PATH)
        cursor = conn.cursor()
        # SELECT * — позиционный, порядок = _COLUMNS (гарантировано при INSERT выше)
        cursor.execute(f"SELECT * FROM {_SQLITE_TMP};")
        sqlite_rows = cursor.fetchall()
        conn.close()
        print(f"   Прочитано {len(sqlite_rows)} строк из SQLite.")

        run_dt = datetime.now()
        processed_rows = [_process_row(row, run_dt) for row in sqlite_rows]

        print(f"📤 Загружаю {len(processed_rows)} строк в {CH_DATABASE_TARGET}.{_TABLE_NAME}...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)

        msg = f"✅ [instrumental_3w] Синхронизировано {len(processed_rows)} строк."
        print(msg)
        send_ntfy_alert(msg, title="Instrumental 3W Success", priority="high", tags="white_check_mark")

        # Вставка мониторинговых данных в target CH
        if os.path.exists(_MON_BUFFER):
            try:
                _insert_monitoring(client_target, run_dt)
            except Exception as e_mon:
                print(f"⚠️ Мониторинг не записан: {e_mon}")

        client_target.close()
        client_target = None

        for buf in (_BUFFER_PATH, _MON_BUFFER):
            try:
                if os.path.exists(buf):
                    os.remove(buf)
            except Exception:
                pass
        print("🧹 Временные файлы удалены.")

        return True

    except Exception as e:
        msg = f"❌ Ошибка выгрузки в целевую ClickHouse: {e}"
        print(msg)
        send_ntfy_alert(f"Ошибка выгрузки instrumental_3w: {str(e)[:80]}",
                        title="Instrumental 3W Insert Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        if os.path.exists(_BUFFER_PATH):
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
        return False


# === Фазовый интерфейс для all_dashboards_up.py ===
_buffer_rows_3w = None


def extract_phase() -> bool:
    """VPN включён — source CH → in-memory буфер."""
    global _buffer_rows_3w
    print("📥 [instrumental_3w] extract_phase...")
    setup_sqlite_adapters()
    client_source = None
    try:
        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999,
        )
        result = client_source.query(_build_query())
        # Сохраняем уже переупорядоченные строки
        _buffer_rows_3w = _rows_to_sqlite(result.result_rows, result.column_names)

        # Мониторинговый запрос (пока VPN активен и соединение открыто)
        print("🔍 [instrumental_3w] Выполняю мониторинговый запрос...")
        try:
            mon_result    = client_source.query(_build_monitoring_query())
            mon_raw_rows  = mon_result.result_rows
            mon_col_names = mon_result.column_names
            print(f"✅ [instrumental_3w] Мониторинг: получено {len(mon_raw_rows)} строк.")
            if mon_raw_rows:
                conn_m = sqlite3.connect(_MON_BUFFER)
                cur_m  = conn_m.cursor()
                cur_m.execute("DROP TABLE IF EXISTS temp_mon_data;")
                cur_m.execute("""CREATE TABLE temp_mon_data (
                    check_timestamp TEXT, table_name TEXT, table_type TEXT, description TEXT,
                    row_count INTEGER, last_data_datetime TEXT, previous_data_datetime TEXT,
                    last_data_date TEXT, table_status TEXT, overall_status TEXT,
                    critical_tables_details TEXT, data_latency_seconds INTEGER, dashboard_name TEXT
                );""")
                cur_m.executemany(
                    f"INSERT INTO temp_mon_data VALUES ({', '.join(['?' for _ in mon_col_names])});",
                    mon_raw_rows,
                )
                conn_m.commit()
                conn_m.close()
                print("✅ [instrumental_3w] SQLite буфер мониторинга заполнен.")
        except Exception as e_mon:
            print(f"⚠️ [instrumental_3w] Мониторинг пропущен (ошибка запроса): {e_mon}")

        client_source.close()
        client_source = None
        print(f"✅ [instrumental_3w] extract_phase: сохранено {len(_buffer_rows_3w)} строк.")
        return True
    except Exception as e:
        print(f"❌ [instrumental_3w] extract_phase: {e}")
        if client_source:
            client_source.close()
        return False


def load_phase() -> bool:
    """VPN выключен — буфер → TRUNCATE + INSERT в target CH."""
    global _buffer_rows_3w
    if _buffer_rows_3w is None:
        print("❌ [instrumental_3w] load_phase: буфер не инициализирован (extract_phase не выполнялась)")
        return False

    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"🔌 [instrumental_3w] Подключаюсь (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
            )
            break
        except Exception as e:
            print(f"❌ Попытка {attempt}: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                return False

    try:
        _ensure_table_exists(client_target)
        print(f"🧹 Удаляю загрузки старше 5 дней из {_TABLE_NAME}")
        client_target.command(
            f"ALTER TABLE {_TABLE_NAME} DELETE WHERE load_datetime < now() - interval 5 day"
        )

        if not _buffer_rows_3w:
            print("📭 [instrumental_3w] load_phase: 0 строк в буфере, нет данных за 7 дней.")
            client_target.close()
            _buffer_rows_3w = None
            return True

        run_dt = datetime.now()
        processed_rows = [_process_row(row, run_dt) for row in _buffer_rows_3w]
        print(f"📤 [instrumental_3w] load_phase: вставляю {len(processed_rows)} строк...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)

        msg = f"✅ [instrumental_3w] load_phase: вставлено {len(processed_rows)} строк."
        print(msg)
        send_ntfy_alert(msg, title="Instrumental 3W Success", priority="high", tags="white_check_mark")

        # Вставка мониторинговых данных в target CH
        if os.path.exists(_MON_BUFFER):
            try:
                _insert_monitoring(client_target, run_dt)
            except Exception as e_mon:
                print(f"⚠️ [instrumental_3w] Мониторинг не записан: {e_mon}")

        client_target.close()
        _buffer_rows_3w = None

        try:
            if os.path.exists(_MON_BUFFER):
                os.remove(_MON_BUFFER)
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"❌ [instrumental_3w] load_phase: {e}")
        if client_target:
            client_target.close()
        return False


# === Основная точка входа ===
def main():
    print("🚀 Запуск instrumental_3w (7 дней, полная перезагрузка)")
    send_ntfy_alert("Запускаю instrumental_3w...", title="Instrumental 3W Start",
                    priority="default", tags="robot")
    success = export_instrumental_3w()
    if success:
        send_ntfy_alert("✅ instrumental_examinations_3w обновлена!",
                        title="Dashbord Done", priority="high", tags="tada",
                        topic_override="push_mrc_dashboards_7895")
        print("🏁 Готово.")
    else:
        send_ntfy_alert("❌ Ошибка обновления instrumental_examinations_3w!",
                        title="Dashbord Failed", priority="urgent", tags="warning",
                        topic_override="push_mrc_dashboards_7895")
        print("❌ Завершено с ошибками.")
    return success


if __name__ == "__main__":
    sys.exit(0 if main() else 2)
