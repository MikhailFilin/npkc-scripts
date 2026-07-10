"""
svo_eris_llo_up.py — построчная выгрузка исследований НПКЦ (assignment_describe_mu_id=100001912827)
пациентов с активной ЛЛО-льготой (940/941, PATIENT_STATUS_ID=11, STATUS_LLO_ID=4) на дату
проведения исследования. Без агрегации по accession_number — одна строка = одно исследование.
Источник статуса ЛЛО и дата описания добавлены к обычной витрине v_eris_assignment_results.

# INPUT:  data_views.v_eris_assignment_results, v_instrumental_examinations,
#         v_llo_benefit_patient, dm_ap2.dct_lpu, dwh_views.v_eris_report (source CH, требуется VPN)
# OUTPUT: svo_eris_llo_examinations (target CH, Yandex Cloud, dwh_test_db)
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

# === Параметры выборки ===
MU_ID        = '100001912827'   # НПКЦ ДИТ ДЗМ
DAYS_TO_SYNC = 20

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUFFER_PKL  = os.path.join(_SCRIPTS_DIR, 'temp_buffer_svo_eris_llo.pkl')
_TABLE_NAME  = 'svo_eris_llo_examinations'

_COLUMNS = [
    'conduct_date', 'describe_date', 'accession_number', 'patient_id', 'payment_source', 'device_type',
    'diagnostic_name', 'assessment_result_type_code', 'conduct_mo_name', 'conduct_mu_name',
    'assignment_describe_mu_id', 'lpu_short_name_by_lpu_id', 'assignment_mu_id',
    'lpu_short_name_by_assignment_mu_id', 'diag_code', 'norma_value', 'age_group',
    'pin_status', 'task_pin_end_date', 'task_pin_status',
    'llo_benefit_status', 'load_datetime', 'assignment_doctor_job_name',
]


# === VPN ===
def connect_vpn():
    print("Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN для svo_eris_llo...", title="VPN Connect", priority="default", tags="lock")
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
            conduct_date                       DateTime,
            describe_date                      Nullable(DateTime),
            accession_number                   String,
            patient_id                         Nullable(String),
            payment_source                     String,
            device_type                        String,
            diagnostic_name                    String,
            assessment_result_type_code        String,
            conduct_mo_name                    String,
            conduct_mu_name                    String,
            assignment_describe_mu_id          String,
            lpu_short_name_by_lpu_id           Nullable(String),
            assignment_mu_id                   Nullable(String),
            lpu_short_name_by_assignment_mu_id Nullable(String),
            diag_code                          Nullable(String),
            norma_value                        Nullable(String),
            age_group                          String,
            pin_status                         String,
            task_pin_end_date                  Nullable(DateTime),
            task_pin_status                    Nullable(String),
            llo_benefit_status                 String,
            load_datetime                      DateTime,
            assignment_doctor_job_name         Nullable(String)
        )
        ENGINE = ReplacingMergeTree(load_datetime)
        ORDER BY (conduct_date, accession_number)
        SETTINGS index_granularity = 8192;
    """)
    print(f"Таблица {_TABLE_NAME} проверена/создана.")


def _build_query(date_from: str, date_to: str) -> str:
    return f"""
WITH ie AS (
    SELECT accession_number,
           any(patient_age)      AS patient_age,
           any(assignment_mu_id) AS assignment_mu_id,
           any(patient_id)       AS patient_id
    FROM data_views.v_instrumental_examinations
    WHERE accession_number != ''
    GROUP BY accession_number
),
llo_status AS (
    SELECT `PATIENT_ID`, `START_DATE`, `END_DATE`, `BENEFIT_SHORT_NAME`
    FROM data_views.v_llo_benefit_patient
    WHERE BENEFIT_CODE IN ('940', '941')
      AND `PATIENT_STATUS_ID` = '11'
      AND STATUS_LLO_ID IN ('4')
    GROUP BY 1, 2, 3, 4
),
llo_periods AS (
    SELECT PATIENT_ID,
           groupArray(tuple(START_DATE, coalesce(END_DATE, toDate('2099-12-31')), BENEFIT_SHORT_NAME)) AS periods
    FROM llo_status
    GROUP BY PATIENT_ID
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
          AND accession_number IS NOT NULL
    )
    WHERE rn = 1
),
base AS (
    SELECT
        toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow') AS conduct_date,
        toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow') AS describe_date,
        t2.accession_number AS accession_number,
        t2.patient_id AS patient_id,
        t2.payment_source,
        t2.device_type,
        t2.diagnostic_name,
        t2.assessment_result_type_code,
        t2.conduct_mo_name,
        t2.conduct_mu_name,
        t2.assignment_describe_mu_id,
        lpu.lpu_short_name AS lpu_short_name_by_lpu_id,
        ie.assignment_mu_id,
        lpu_assign.lpu_short_name AS lpu_short_name_by_assignment_mu_id,
        t2.diag_code AS diag_code,
        ai_res.norma_value,
        multiIf(
            ie.patient_age >= 18, 'Взрослые',
            ie.patient_age IS NOT NULL AND ie.patient_age < 18, 'Дети',
            'Не указано'
        ) AS age_group,
        CASE WHEN ap.accession_number IS NOT NULL THEN 'находится в ПИНе'
             ELSE 'не в ПИНе'
        END AS pin_status,
        lp.task_pin_end_date,
        lp.task_pin_status,
        if(
            length(arrayFilter(x -> toDate(t2.conduct_date) >= x.1 AND toDate(t2.conduct_date) <= x.2, llop.periods)) = 0,
            NULL,
            arraySort(x -> (-toInt32(x.1), x.3),
                arrayFilter(x -> toDate(t2.conduct_date) >= x.1 AND toDate(t2.conduct_date) <= x.2, llop.periods)
            )[1].3
        ) AS llo_benefit_status,
        toTimeZone(now(), 'Europe/Moscow') AS load_datetime
    FROM data_views.v_eris_assignment_results t2
    LEFT JOIN dm_ap2.dct_lpu AS lpu
        ON lpu.lpu_id = t2.assignment_describe_mu_id
    LEFT JOIN ie ON t2.accession_number = ie.accession_number
    LEFT JOIN dm_ap2.dct_lpu AS lpu_assign
        ON lpu_assign.lpu_id = ie.assignment_mu_id
    LEFT JOIN (
        SELECT
            JSONExtractString(raw_data, 'studyIUID') AS studyIUID,
            min(JSONExtractString(JSONExtractString(raw_data, 'aiResult'), 'norma')) AS norma_value
        FROM dwh_views.v_eris_report
        WHERE app_source = 'CDS'
          AND parseDateTimeBestEffortOrNull(JSONExtractString(computed_data, 'pumStudyReadyForAiTime')) IS NOT NULL
          AND JSONExtractString(raw_data, 'studyIUID') != ''
        GROUP BY studyIUID
    ) ai_res ON t2.study_uid = ai_res.studyIUID
    LEFT JOIN llo_periods AS llop ON ie.patient_id = llop.PATIENT_ID
    LEFT JOIN active_pin AS ap ON t2.accession_number = ap.accession_number
    LEFT JOIN last_pin AS lp ON t2.accession_number = lp.accession_number
    WHERE t2.accession_number != ''
      AND t2.assignment_describe_mu_id = '{MU_ID}'
      AND toDate(t2.conduct_date) >= '{date_from}'
      AND toDate(t2.conduct_date) < '{date_to}'
),
filtered AS (
    SELECT * FROM base WHERE llo_benefit_status IS NOT NULL
),
route_doc AS (
    -- v_route_instrumental не индексирована по eris_id (полный скан + MD5 на PII),
    -- поэтому фильтруем только по уже отобранному (ЛЛО) набору accession_number, не по всей витрине.
    SELECT eris_id AS accession_number,
           any(assignment_doctor_job_name) AS assignment_doctor_job_name
    FROM data_views.v_route_instrumental
    WHERE eris_id IN (SELECT accession_number FROM filtered)
    GROUP BY eris_id
)
SELECT f.*, rd.assignment_doctor_job_name
FROM filtered f
LEFT JOIN route_doc rd ON f.accession_number = rd.accession_number
QUALIFY ROW_NUMBER() OVER (PARTITION BY accession_number ORDER BY conduct_date ASC) = 1
ORDER BY conduct_date DESC, payment_source ASC
"""


def _process_row(row: list) -> list:
    p = []
    for i, val in enumerate(row):
        col = _COLUMNS[i]
        if col in ('conduct_date', 'describe_date', 'task_pin_end_date', 'load_datetime'):
            nullable = col in ('describe_date', 'task_pin_end_date')
            if val is None:
                p.append(None if nullable else datetime(1900, 1, 1))
            elif isinstance(val, datetime_module.datetime):
                p.append(val.replace(tzinfo=None))
            else:
                try:    p.append(datetime.strptime(str(val)[:19], '%Y-%m-%d %H:%M:%S'))
                except: p.append(None if nullable else datetime(1900, 1, 1))
        elif col in ('lpu_short_name_by_lpu_id', 'assignment_mu_id', 'lpu_short_name_by_assignment_mu_id',
                     'diag_code', 'norma_value', 'assignment_doctor_job_name', 'task_pin_status', 'patient_id'):
            p.append(str(val) if val is not None and str(val) != '' else None)
        else:
            p.append(str(val) if val is not None else '')
    return p


# === Фазовый интерфейс для all_dashboards_up.py ===

def extract_phase() -> bool:
    """VPN включён — source CH → pickle-буфер."""
    print(f"[svo_eris_llo] extract_phase: запрос к source CH за последние {DAYS_TO_SYNC} дней...")

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
        query  = _build_query(n_days_ago.isoformat(), (today + timedelta(days=1)).isoformat())
        result = client_source.query(query)
        rows   = result.result_rows
        client_source.close()
        client_source = None

        with open(_BUFFER_PKL, 'wb') as f:
            _pickle.dump(rows, f)
        print(f"[svo_eris_llo] extract_phase: сохранено {len(rows)} строк.")
        return True
    except Exception as e:
        print(f"[svo_eris_llo] extract_phase: {e}")
        if client_source:
            client_source.close()
        return False


def load_phase() -> bool:
    """VPN выключен — pickle-буфер → DELETE + INSERT в target CH."""
    if not os.path.exists(_BUFFER_PKL):
        print("[svo_eris_llo] load_phase: буфер не найден")
        return False

    try:
        with open(_BUFFER_PKL, 'rb') as f:
            raw_rows = _pickle.load(f)
        os.remove(_BUFFER_PKL)
    except Exception as e:
        print(f"[svo_eris_llo] load_phase: ошибка чтения буфера: {e}")
        return False

    if not raw_rows:
        print("[svo_eris_llo] load_phase: буфер пуст, пропускаю.")
        return True

    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC - 1)

    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"[svo_eris_llo] Подключаюсь к target CH (попытка {attempt})...")
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
            f"WHERE conduct_date >= '{n_days_ago.isoformat()}'"
        )
        print(f"DELETE conduct_date >= {n_days_ago.isoformat()}")
        client_target.command(delete_q)

        processed_rows = [_process_row(list(row)) for row in raw_rows]

        print(f"[svo_eris_llo] load_phase: вставляю {len(processed_rows)} строк в {_TABLE_NAME}...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)
        print(f"[svo_eris_llo] load_phase: вставлено {len(processed_rows)} строк.")
        client_target.close()
        client_target = None
        return True
    except Exception as e:
        print(f"[svo_eris_llo] load_phase: {e}")
        if client_target:
            client_target.close()
        return False


def export_svo_eris_llo() -> bool:
    """Полный цикл: VPN → source CH → target CH."""
    print(f"[svo_eris_llo] Загрузка {_TABLE_NAME} за последние {DAYS_TO_SYNC} дней...")
    send_ntfy_alert(
        f"SVO ERIS LLO: синхронизация за {DAYS_TO_SYNC} дней...",
        title="SVO ERIS LLO Start",
        priority="default",
        tags="hospital",
    )

    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC - 1)
    date_to    = today + timedelta(days=1)
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
        delete_q = f"ALTER TABLE {_TABLE_NAME} DELETE WHERE conduct_date >= '{n_days_ago.isoformat()}'"
        client_target_del.command(delete_q)
        print("Старые данные удалены.")
        client_target_del.close()
        client_target_del = None
    except Exception as e:
        msg = f"Ошибка очистки target CH: {e}"
        print(msg)
        send_ntfy_alert(f"Ошибка очистки: {str(e)[:80]}", title="SVO ERIS LLO Error",
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

        query = _build_query(n_days_ago.isoformat(), date_to.isoformat())
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
        send_ntfy_alert(f"Сбой svo_eris_llo: {str(e)[:80]}", title="SVO ERIS LLO Error",
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
        send_ntfy_alert(f"SVO ERIS LLO: нет данных за {DAYS_TO_SYNC} дней",
                        title="SVO ERIS LLO Empty", priority="default", tags="inbox")
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
                            title="SVO ERIS LLO Connect Error", priority="urgent", tags="database")
            if attempt < 3:
                time.sleep(5)
            else:
                return False

    try:
        processed_rows = [_process_row(list(row)) for row in raw_rows]

        print(f"Загружаю {len(processed_rows)} строк в {CH_DATABASE_TARGET}.{_TABLE_NAME}...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)

        msg = f"SVO ERIS LLO: синхронизировано {len(processed_rows)} строк за {DAYS_TO_SYNC} дней."
        print(msg)
        send_ntfy_alert(msg, title="SVO ERIS LLO Done", priority="high", tags="white_check_mark")

        client_target.close()
        client_target = None
        return True

    except Exception as e:
        msg = f"Ошибка вставки в target CH: {e}"
        print(msg)
        send_ntfy_alert(f"Ошибка вставки svo_eris_llo: {str(e)[:80]}",
                        title="SVO ERIS LLO Insert Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        return False


# === Основная точка входа ===
def main():
    print(f"Запуск svo_eris_llo (последние {DAYS_TO_SYNC} дней)")
    send_ntfy_alert(
        f"SVO ERIS LLO запущен за {DAYS_TO_SYNC} дней...",
        title="SVO ERIS LLO Start",
        priority="default",
        tags="robot",
    )

    success = export_svo_eris_llo()

    if success:
        send_ntfy_alert(
            f"Обновление {_TABLE_NAME} завершено!",
            title="SVO ERIS LLO Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895",
        )
        print("Синхронизация завершена успешно.")
    else:
        send_ntfy_alert(
            f"Обновление {_TABLE_NAME} завершилось с ошибкой!",
            title="SVO ERIS LLO Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895",
        )
        print("Синхронизация завершена с ошибками.")

    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
