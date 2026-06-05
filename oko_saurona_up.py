"""
oko_saurona_up.py — Мониторинг отложенных/просроченных исследований (Oko Saurona)
Синхронизация данных overdue_studies_monitoring из исходной ClickHouse в целевую.
Фильтр: assignment_describe_mu_id = '100001912827' (НПКЦ), последние DAYS_TO_SYNC дней.
Все уведомления отправляются через ntfy.

# INPUT:  data_views.v_eris_assignment_results, v_task_pin, v_clinical_events, v_events_status_change
# OUTPUT: overdue_studies_monitoring (target CH, Yandex Cloud)
"""

import subprocess
import pyautogui
import time
import sys
import os
import pickle as _pickle
import clickhouse_connect
from datetime import datetime, timedelta
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
VPN_APP_PATH            = cfg.VPN_APP_PATH
VPN_PASSWORD            = cfg.VPN_PASSWORD
PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y

# === Синхронизация ===
DAYS_TO_SYNC = 40

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUFFER_PKL  = os.path.join(_SCRIPTS_DIR, 'temp_buffer_oko_saurona.pkl')
_TABLE_NAME  = 'overdue_studies_monitoring'

_COLUMNS = [
    'accession_number', 'patient_id', 'assignment_result_emp_id', 'assignment_result_emp_fio',
    'payment_source', 'device_type', 'diagnostic_code', 'diagnostic_name',
    'assessment_result_type_code', 'conduct_mo_name', 'conduct_mu_name',
    'assignment_describe_mu_id', 'conduct_date', 'effective_start_date', 'has_pin',
    'assignment_result_doc_created_date', 'hours_overdue', 'time_interval',
    'event_id', 'first_running', 'last_running', 'deferred_time_hhmmss',
    'status_chain', 'cnt_deferred', 'load_datetime',
]


# === VPN ===
def connect_vpn():
    print("Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN для oko_saurona...", title="VPN Connect", priority="default", tags="lock")
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
    except Exception as e:
        msg = f"Ошибка запуска VPN: {e}"
        print(msg)
        send_ntfy_alert(msg, title="VPN Error", priority="urgent", tags="warning")
        raise
    time.sleep(15)

    for window in pyautogui.getWindowsWithTitle(''):
        if any(kw in window.title.lower() for kw in ['check point', 'trgui', 'endpoint']):
            try:
                window.activate()
                time.sleep(2)
                print(f"Окно '{window.title}' активировано.")
                break
            except Exception:
                pass

    pyautogui.click(PASSWORD_FIELD_X, PASSWORD_FIELD_Y)
    time.sleep(0.5)
    pyautogui.click(PASSWORD_FIELD_X, PASSWORD_FIELD_Y)
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.3)
    pyautogui.press('delete')
    time.sleep(0.5)
    for char in VPN_PASSWORD:
        pyautogui.write(char)
        time.sleep(0.1)
    time.sleep(1)
    pyautogui.click(CONNECT_BUTTON_X, CONNECT_BUTTON_Y)
    time.sleep(15)
    print("VPN подключён.")
    send_ntfy_alert("VPN подключён", title="VPN Connected", priority="high", tags="key")


def disconnect_vpn():
    print("Отключаю VPN...")
    send_ntfy_alert("Отключаюсь от VPN...", title="VPN Disconnect", priority="default", tags="unlock")
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print("VPN отключён.")
    send_ntfy_alert("VPN отключён", title="VPN Disconnected", priority="default", tags="check")


def _ensure_table_exists(client) -> None:
    client.command(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_NAME}
        (
            accession_number                  String,
            patient_id                        String,
            assignment_result_emp_id          String,
            assignment_result_emp_fio         String,
            payment_source                    String,
            device_type                       String,
            diagnostic_code                   String,
            diagnostic_name                   String,
            assessment_result_type_code       String,
            conduct_mo_name                   String,
            conduct_mu_name                   String,
            assignment_describe_mu_id         String,
            conduct_date                      DateTime,
            effective_start_date              DateTime,
            has_pin                           UInt8,
            assignment_result_doc_created_date DateTime,
            hours_overdue                     Int32,
            time_interval                     String,
            event_id                          Nullable(String),
            first_running                     Nullable(DateTime),
            last_running                      Nullable(DateTime),
            deferred_time_hhmmss              Nullable(String),
            status_chain                      Nullable(String),
            cnt_deferred                      Int32,
            load_datetime                     DateTime
        )
        ENGINE = ReplacingMergeTree(load_datetime)
        ORDER BY (assignment_result_doc_created_date, accession_number)
        SETTINGS index_granularity = 8192;
    """)
    print(f"Таблица {_TABLE_NAME} проверена/создана.")


def _build_query(n_days_ago: str, today: str) -> str:
    today_dt      = datetime.strptime(today, '%Y-%m-%d').date()
    n_days_ago_dt = datetime.strptime(n_days_ago, '%Y-%m-%d').date()
    # Диапазон дат для v_events_status_change: 14 дней буфера назад от начала периода.
    # v_clinical_events фильтруется через event_id (первичный ключ таблицы) —
    # это позволяет ClickHouse использовать индекс вместо полного скана 1.32B строк по patient_id.
    esc_date_from = (n_days_ago_dt - timedelta(days=14)).isoformat()
    esc_date_to   = today

    return f"""
WITH

pin_cte AS (
    SELECT
        accession_number,
        toTimeZone(toDateTime(max(task_pin_end_date)), 'Europe/Moscow') AS max_pin_date
    FROM data_views.v_task_pin
    WHERE accession_number != ''
    GROUP BY accession_number
),

base AS (
    SELECT
        t2.accession_number,
        toString(t2.patient_id)                                                         AS patient_id,
        toString(t2.assignment_result_emp_id)                                           AS assignment_result_emp_id,
        t2.assignment_result_emp_fio,
        t2.assignment_result_emp_job_execution_id,
        t2.payment_source,
        t2.device_type,
        toString(t2.diagnostic_code)                                                    AS diagnostic_code,
        t2.diagnostic_name,
        t2.assessment_result_type_code,
        t2.conduct_mo_name,
        t2.conduct_mu_name,
        t2.assignment_describe_mu_id,
        toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')  AS assignment_result_doc_created_date,
        toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow')                        AS conduct_date,
        COALESCE(
            pin.max_pin_date,
            toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow')
        )                                                                               AS effective_start_date,
        if(pin.max_pin_date IS NOT NULL, 1, 0)                                          AS has_pin,
        -- intDiv(seconds,3600) — точный floor, dateDiff('hour') усекает до начала часа и даёт неверный результат
        intDiv(dateDiff('second',
            COALESCE(pin.max_pin_date, toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow')),
            toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')
        ), 3600) AS hours_overdue,
        multiIf(
            intDiv(dateDiff('second',
                COALESCE(pin.max_pin_date, toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow')),
                toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')
            ), 3600) < 24, 'Менее 24 часов',
            intDiv(dateDiff('second',
                COALESCE(pin.max_pin_date, toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow')),
                toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')
            ), 3600) < 48, 'Более 24 часов',
            'Более 48 часов'
        ) AS time_interval
    FROM data_views.v_eris_assignment_results t2
    LEFT JOIN pin_cte AS pin ON pin.accession_number = t2.accession_number
    WHERE
        t2.accession_number != ''
        AND toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow'))
            BETWEEN '{n_days_ago}' AND '{today}'
        AND t2.assignment_describe_mu_id = '100001912827'
        AND intDiv(dateDiff('second',
            COALESCE(pin.max_pin_date, toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow')),
            toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')
        ), 3600) >= 24
),

event_cte AS (
    SELECT
        b.accession_number,
        toString(ce.event_id)   AS event_id,
        if(ce.event_id IS NOT NULL,
            arrayStringConcat(
                arrayMap(x -> x.2, arraySort(x -> x.1, groupArray((esc.change_time, esc.status)))),
                ' -> '
            ), NULL
        ) AS status_chain,
        if(ce.event_id IS NOT NULL, countIf(esc.status = 'DEFERRED'), 0)              AS cnt_deferred,
        if(ce.event_id IS NOT NULL,
            addHours(minIf(esc.change_time, esc.status = 'RUNNING'), 3), NULL)         AS first_running,
        if(ce.event_id IS NOT NULL,
            addHours(maxIf(esc.change_time, esc.status = 'RUNNING'), 3), NULL)         AS last_running
    FROM base AS b
    LEFT JOIN (
        SELECT patient_id, medical_employee_job_info_id, event_id
        FROM data_views.v_clinical_events
        WHERE event_id IN (
            SELECT DISTINCT event_id
            FROM data_views.v_events_status_change
            WHERE change_time >= '{esc_date_from}'
              AND change_time <= '{esc_date_to} 23:59:59'
        )
    ) AS ce
        ON  toString(ce.patient_id)         = b.patient_id
        AND toString(ce.medical_employee_job_info_id) = toString(b.assignment_result_emp_job_execution_id)
    LEFT JOIN (
        SELECT event_id, change_time, status
        FROM data_views.v_events_status_change
        WHERE change_time >= '{esc_date_from}'
          AND change_time <= '{esc_date_to} 23:59:59'
    ) AS esc ON esc.event_id = ce.event_id
    GROUP BY b.accession_number, ce.event_id
)

SELECT
    b.accession_number,
    b.patient_id,
    b.assignment_result_emp_id,
    b.assignment_result_emp_fio,
    b.payment_source,
    b.device_type,
    b.diagnostic_code,
    b.diagnostic_name,
    b.assessment_result_type_code,
    b.conduct_mo_name,
    b.conduct_mu_name,
    b.assignment_describe_mu_id,
    b.conduct_date,
    b.effective_start_date,
    b.has_pin,
    b.assignment_result_doc_created_date,
    b.hours_overdue,
    b.time_interval,
    e.event_id,
    e.first_running,
    e.last_running,
    if(e.first_running IS NOT NULL AND e.last_running IS NOT NULL,
        concat(
            leftPad(toString(intDiv(dateDiff('second', e.first_running, e.last_running), 3600)), 2, '0'), ':',
            leftPad(toString(intDiv(dateDiff('second', e.first_running, e.last_running) % 3600, 60)), 2, '0'), ':',
            leftPad(toString(dateDiff('second', e.first_running, e.last_running) % 60), 2, '0')
        ), NULL
    ) AS deferred_time_hhmmss,
    e.status_chain,
    e.cnt_deferred,
    toTimeZone(now(), 'Europe/Moscow') AS load_datetime
FROM base AS b
LEFT JOIN event_cte AS e ON e.accession_number = b.accession_number
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY b.accession_number
    ORDER BY
        if(e.event_id IS NULL, 1, 0),
        if(e.first_running IS NOT NULL,
           abs(dateDiff('second', e.first_running, b.assignment_result_doc_created_date)), 0)
) = 1
ORDER BY b.hours_overdue DESC, b.assignment_result_emp_fio
"""


def _process_row(row: list) -> list:
    p = []
    for i, val in enumerate(row):
        col = _COLUMNS[i]
        if col in ('conduct_date', 'effective_start_date', 'assignment_result_doc_created_date'):
            if val is None:
                p.append(datetime(1900, 1, 1))
            elif isinstance(val, datetime_module.datetime):
                p.append(val.replace(tzinfo=None))
            else:
                try:    p.append(datetime.strptime(str(val), '%Y-%m-%d %H:%M:%S'))
                except: p.append(datetime(1900, 1, 1))
        elif col in ('first_running', 'last_running'):
            if val is None:
                p.append(None)
            elif isinstance(val, datetime_module.datetime):
                p.append(val.replace(tzinfo=None))
            else:
                try:    p.append(datetime.strptime(str(val)[:19], '%Y-%m-%d %H:%M:%S'))
                except: p.append(None)
        elif col == 'load_datetime':
            if val is None:
                p.append(datetime.now())
            elif isinstance(val, datetime_module.datetime):
                p.append(val.replace(tzinfo=None))
            else:
                try:    p.append(datetime.strptime(str(val)[:19], '%Y-%m-%d %H:%M:%S'))
                except: p.append(datetime.now())
        elif col == 'has_pin':
            p.append(int(val) if val is not None else 0)
        elif col in ('hours_overdue', 'cnt_deferred'):
            p.append(int(val) if val is not None else 0)
        elif col in ('event_id', 'deferred_time_hhmmss', 'status_chain'):
            p.append(str(val) if val is not None else None)
        else:
            p.append(str(val) if val is not None else '')
    return p


# === Фазовый интерфейс для all_dashboards_up.py ===

def extract_phase() -> bool:
    """VPN включён — source CH → pickle-буфер."""
    print(f"[oko_saurona] extract_phase: запрос к source CH за последние {DAYS_TO_SYNC} дней...")

    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC - 1)

    client_source = None
    try:
        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999,
        )
        query  = _build_query(n_days_ago.isoformat(), today.isoformat())
        result = client_source.query(query)
        rows   = result.result_rows
        client_source.close()
        client_source = None

        with open(_BUFFER_PKL, 'wb') as f:
            _pickle.dump(rows, f)
        print(f"[oko_saurona] extract_phase: сохранено {len(rows)} строк.")
        return True
    except Exception as e:
        print(f"[oko_saurona] extract_phase: {e}")
        if client_source:
            client_source.close()
        return False


def load_phase() -> bool:
    """VPN выключен — pickle-буфер → DELETE + INSERT в target CH."""
    if not os.path.exists(_BUFFER_PKL):
        print("[oko_saurona] load_phase: буфер не найден")
        return False

    try:
        with open(_BUFFER_PKL, 'rb') as f:
            raw_rows = _pickle.load(f)
        os.remove(_BUFFER_PKL)
    except Exception as e:
        print(f"[oko_saurona] load_phase: ошибка чтения буфера: {e}")
        return False

    if not raw_rows:
        print("[oko_saurona] load_phase: буфер пуст, пропускаю.")
        return True

    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC - 1)

    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"[oko_saurona] Подключаюсь к target CH (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
            )
            break
        except Exception as e:
            print(f"Попытка {attempt}: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                return False

    try:
        _ensure_table_exists(client_target)
        delete_q = (
            f"ALTER TABLE {_TABLE_NAME} DELETE "
            f"WHERE toDate(assignment_result_doc_created_date) >= '{n_days_ago.isoformat()}'"
        )
        print(f"DELETE doc_date >= {n_days_ago.isoformat()}")
        client_target.command(delete_q)

        processed_rows = [_process_row(list(row)) for row in raw_rows]

        print(f"[oko_saurona] load_phase: вставляю {len(processed_rows)} строк в {_TABLE_NAME}...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)
        print(f"[oko_saurona] load_phase: вставлено {len(processed_rows)} строк.")
        client_target.close()
        client_target = None
        return True
    except Exception as e:
        print(f"[oko_saurona] load_phase: {e}")
        if client_target:
            client_target.close()
        return False


def export_overdue_monitoring() -> bool:
    """Полный цикл: VPN → source CH → target CH."""
    print(f"[oko_saurona] Загрузка {_TABLE_NAME} за последние {DAYS_TO_SYNC} дней...")
    send_ntfy_alert(
        f"Oko Saurona: синхронизация за {DAYS_TO_SYNC} дней...",
        title="Oko Saurona Start",
        priority="default",
        tags="eye",
    )

    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC - 1)
    print(f"   Период: {n_days_ago} — {today}")

    # Шаг 1: Очистка целевой базы за период
    client_target_del = None
    try:
        print("Подключаюсь к target CH для очистки...")
        client_target_del = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False,
        )
        _ensure_table_exists(client_target_del)
        delete_q = (
            f"ALTER TABLE {_TABLE_NAME} DELETE "
            f"WHERE toDate(assignment_result_doc_created_date) >= '{n_days_ago.isoformat()}'"
        )
        client_target_del.command(delete_q)
        print("Старые данные удалены.")
        client_target_del.close()
        client_target_del = None
    except Exception as e:
        msg = f"Ошибка очистки target CH: {e}"
        print(msg)
        send_ntfy_alert(f"Ошибка очистки: {str(e)[:80]}", title="Oko Saurona Error",
                        priority="urgent", tags="database")
        if client_target_del:
            client_target_del.close()
        return False

    # Шаг 2: Извлечение из source CH через VPN
    client_source = None
    raw_rows = []
    try:
        connect_vpn()

        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999,
        )
        print("Source CH подключена.")

        query  = _build_query(n_days_ago.isoformat(), today.isoformat())
        print(f"   Период запроса: {n_days_ago} — {today} ({DAYS_TO_SYNC} дней)")
        print("Выполняю запрос к source CH...")
        result = client_source.query(query)
        raw_rows = result.result_rows
        client_source.close()
        client_source = None
        print(f"Получено {len(raw_rows)} строк.")

    except Exception as e:
        msg = f"Ошибка source CH: {e}"
        print(msg)
        send_ntfy_alert(f"Сбой oko_saurona: {str(e)[:80]}", title="Oko Saurona Error",
                        priority="urgent", tags="fire")
        if client_source:
            client_source.close()
        try: disconnect_vpn()
        except Exception: pass
        return False

    # Шаг 3: Отключение VPN
    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f"Ошибка отключения VPN: {e}", title="VPN Warning",
                        priority="default", tags="warning")

    if not raw_rows:
        print(f"Нет данных за {DAYS_TO_SYNC} дней.")
        send_ntfy_alert(f"Oko Saurona: нет данных за {DAYS_TO_SYNC} дней",
                        title="Oko Saurona Empty", priority="default", tags="inbox")
        return True

    # Шаг 4: Вставка в target CH
    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"Подключаюсь к target CH (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
            )
            break
        except Exception as e:
            print(f"Попытка {attempt}: {e}")
            send_ntfy_alert(f"Ошибка подключения target CH: {str(e)[:60]}",
                            title="Oko Saurona Connect Error", priority="urgent", tags="database")
            if attempt < 3:
                time.sleep(5)
            else:
                return False

    try:
        processed_rows = [_process_row(list(row)) for row in raw_rows]

        print(f"Загружаю {len(processed_rows)} строк в {CH_DATABASE_TARGET}.{_TABLE_NAME}...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)

        msg = f"Oko Saurona: синхронизировано {len(processed_rows)} строк за {DAYS_TO_SYNC} дней."
        print(msg)
        send_ntfy_alert(msg, title="Oko Saurona Done", priority="high", tags="white_check_mark")

        client_target.close()
        client_target = None
        return True

    except Exception as e:
        msg = f"Ошибка вставки в target CH: {e}"
        print(msg)
        send_ntfy_alert(f"Ошибка вставки oko_saurona: {str(e)[:80]}",
                        title="Oko Saurona Insert Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        return False


# === Основная точка входа ===
def main():
    print(f"Запуск oko_saurona (последние {DAYS_TO_SYNC} дней)")
    send_ntfy_alert(
        f"Oko Saurona запущен за {DAYS_TO_SYNC} дней...",
        title="Oko Saurona Start",
        priority="default",
        tags="robot",
    )

    success = export_overdue_monitoring()

    if success:
        send_ntfy_alert(
            f"Обновление {_TABLE_NAME} завершено!",
            title="Oko Saurona Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895",
        )
        print("Синхронизация завершена успешно.")
    else:
        send_ntfy_alert(
            f"Обновление {_TABLE_NAME} завершилось с ошибкой!",
            title="Oko Saurona Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895",
        )
        print("Синхронизация завершена с ошибками.")

    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
