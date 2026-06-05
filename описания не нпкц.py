###ПЕРЕВЕДЕН НА МОСКОВСКОЕ ВРЕМЯ
import sys
print("📍 Python запущен из:", sys.executable)
import subprocess
import pyautogui
import time
import os
import sqlite3
import requests
import clickhouse_connect
from datetime import datetime, timedelta
import datetime as datetime_module

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

# === Синхронизация ===
DAYS_TO_SYNC = 3

# === VPN ===
VPN_APP_PATH = cfg.VPN_APP_PATH
VPN_PASSWORD = cfg.VPN_PASSWORD
PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y

# === Telegram ===
LOG_BOT_TOKEN       = cfg.LOG_BOT_TOKEN
LOG_CHAT_ID         = cfg.LOG_CHAT_ID
DASHBOARD_BOT_TOKEN = cfg.DASHBOARD_BOT_TOKEN
DASHBOARD_CHAT_ID   = cfg.DASHBOARD_CHAT_ID

# === SQLite буфер ===
_SQLITE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp_buf_opisania_ne_npkc.db')

_PROCESSED_COLUMN_NAMES = [
    'doc_date', 'assignment_result_emp_fio', 'accession_number', 'study_uid', 'patient_id',
    'payment_source', 'device_type', 'diagnostic_name', 'assessment_result_type_code',
    'conduct_mo_name', 'conduct_mu_name', 'conduct_district_name',
    'patient_gender', 'assignment_describe_mu_id', 'age_group',
    'has_task_pin_record', 'trauma_after_orthopedist_flag',
    'load_datetime', 'assignment_result_emp_id'
]


# ===========================================================================
# Вспомогательные функции
# ===========================================================================

def setup_sqlite_adapters():
    def adapt_date_iso(val):
        return val.isoformat()
    def adapt_datetime_iso(val):
        return val.isoformat()
    def convert_date(val):
        return datetime_module.date.fromisoformat(val.decode())
    def convert_timestamp(val):
        return datetime_module.datetime.fromisoformat(val.decode())
    sqlite3.register_adapter(datetime_module.date, adapt_date_iso)
    sqlite3.register_adapter(datetime_module.datetime, adapt_datetime_iso)
    sqlite3.register_converter("date", convert_date)
    sqlite3.register_converter("timestamp", convert_timestamp)


def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
    except Exception as e:
        print(f"❌ Ошибка запуска VPN: {e}")
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
    print("✅ Подключение к VPN инициировано.")


def disconnect_vpn():
    print("🛑 Начинаю отключение...")
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print("🛑 Отключение завершено.")


# ===========================================================================
# Фаза 1: Извлечение из source CH в SQLite буфер (VPN включён)
# ===========================================================================

def extract_phase() -> bool:
    """
    # INPUT:  data_views.v_eris_assignment_results (source CH через VPN)
    # OUTPUT: temp_buf_opisania_ne_npkc.db (SQLite буфер)
    # NOTE:   DELETE из target CH выполняется в load_phase (target недоступен через VPN)
    """
    setup_sqlite_adapters()

    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)
    print(f"   Синхронизация за период: {n_days_ago} — {today}")

    # --- Извлекаем из source CH в SQLite ---
    client_source = None
    try:
        connect_vpn()
        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999
        )
        print("✅ Исходная ClickHouse подключена.")

        base_query = """
        SELECT
            toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')) AS doc_date,
            t2.assignment_result_emp_fio,
            t2.accession_number,
            t2.study_uid,
            t2.patient_id,
            t2.payment_source,
            t2.device_type,
            t2.diagnostic_name,
            t2.assessment_result_type_code,
            t2.conduct_mo_name,
            t2.conduct_mu_name,
            t2.conduct_district_name,
            t2.patient_gender,
            t2.assignment_describe_mu_id,
            multiIf(
                ie.patient_age >= 18, 'Взрослые',
                ie.patient_age < 18 AND ie.patient_age IS NOT NULL, 'Дети',
                'Не указано'
            ) AS age_group,
            multiIf(pin_res.max_task_pin_end_date IS NOT NULL, 1, 0) AS has_task_pin_record,
            multiIf(trauma_res.accession_number IS NOT NULL, 1, 0) AS trauma_after_orthopedist_flag,
            toTimeZone(now(), 'Europe/Moscow') AS load_datetime,
            toString(t2.assignment_result_emp_id) AS assignment_result_emp_id
        FROM data_views.v_eris_assignment_results t2
        LEFT JOIN (
            SELECT accession_number, any(patient_age) AS patient_age
            FROM data_views.v_instrumental_examinations
            WHERE accession_number != ''
            GROUP BY accession_number
        ) ie ON t2.accession_number = ie.accession_number
        LEFT JOIN (
            SELECT DISTINCT accession_number
            FROM data_views.v_route_eris_trauma
            WHERE accession_number != ''
        ) trauma_res ON t2.accession_number = trauma_res.accession_number
        LEFT JOIN (
            SELECT accession_number,
                   toTimeZone(toDateTime(max(task_pin_end_date)), 'Europe/Moscow') AS max_task_pin_end_date
            FROM data_views.v_task_pin
            WHERE accession_number != ''
            GROUP BY accession_number
        ) pin_res ON t2.accession_number = pin_res.accession_number
        WHERE t2.accession_number != ''
          AND t2.assignment_describe_mu_id != '100001912827'
        """
        date_filter = (
            f"  AND toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow'))"
            f" >= '{n_days_ago.isoformat()}'"
            f"  AND toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow'))"
            f" <= '{today.isoformat()}'"
        )
        result    = client_source.query(base_query + date_filter)
        raw_rows  = result.result_rows
        col_names = result.column_names
        client_source.close()
        client_source = None
        print(f"📥 Получено {len(raw_rows)} строк за последние {DAYS_TO_SYNC} дней.")

        if not raw_rows:
            print("ℹ️ Нет данных для синхронизации.")
            disconnect_vpn()
            return True

        # --- Пишем в SQLite буфер ---
        conn = sqlite3.connect(_SQLITE_DB)
        cur  = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS temp_assignment_results_other_mo_data;")
        cur.execute("""
            CREATE TABLE temp_assignment_results_other_mo_data (
                doc_date TEXT, assignment_result_emp_fio TEXT, accession_number TEXT,
                study_uid TEXT, patient_id TEXT, payment_source TEXT, device_type TEXT,
                diagnostic_name TEXT, assessment_result_type_code TEXT,
                conduct_mo_name TEXT, conduct_mu_name TEXT, conduct_district_name TEXT,
                patient_gender TEXT, assignment_describe_mu_id TEXT, age_group TEXT,
                has_task_pin_record INTEGER, trauma_after_orthopedist_flag INTEGER,
                load_datetime TEXT, assignment_result_emp_id TEXT
            );
        """)
        placeholders = ', '.join(['?' for _ in col_names])
        cur.executemany(f"INSERT INTO temp_assignment_results_other_mo_data VALUES ({placeholders});", raw_rows)
        conn.commit()
        conn.close()
        print(f"✅ SQLite буфер заполнен ({_SQLITE_DB}).")

    except Exception as e:
        print(f"❌ Ошибка при извлечении данных: {e}")
        if client_source:
            client_source.close()
        if os.path.exists(_SQLITE_DB):
            os.remove(_SQLITE_DB)
        try:
            disconnect_vpn()
        except Exception:
            pass
        return False

    try:
        disconnect_vpn()
    except Exception:
        pass

    return True


# ===========================================================================
# Фаза 2: Загрузка из SQLite буфера в целевую ClickHouse (VPN выключен)
# ===========================================================================

def load_phase() -> bool:
    """
    # INPUT:  temp_buf_opisania_ne_npkc.db (SQLite буфер)
    # OUTPUT: dwh_test_db.assignment_results_other_mo (target CH)
    """
    if not os.path.exists(_SQLITE_DB):
        print(f"❌ SQLite буфер не найден: {_SQLITE_DB}")
        return False

    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)

    client_target = None
    max_retries   = 3
    for attempt in range(1, max_retries + 1):
        try:
            print(f"🔌 Подключаюсь к целевой ClickHouse (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False
            )
            print("✅ Целевая ClickHouse подключена.")
            break
        except Exception as e:
            print(f"❌ Ошибка подключения (попытка {attempt}): {e}")
            if attempt < max_retries:
                time.sleep(5)
            else:
                if os.path.exists(_SQLITE_DB):
                    os.remove(_SQLITE_DB)
                return False

    try:
        # --- Удаляем старые данные из target CH ---
        print("🧹 Очищаю старые данные из целевой базы...")
        client_target.command(
            f"ALTER TABLE assignment_results_other_mo DELETE "
            f"WHERE doc_date >= '{n_days_ago.isoformat()}'"
        )
        print("✅ Старые данные удалены.")

        conn = sqlite3.connect(_SQLITE_DB)
        rows = conn.cursor().execute("SELECT * FROM temp_assignment_results_other_mo_data;").fetchall()
        conn.close()
        print(f"   Прочитано {len(rows)} строк из SQLite.")

        processed_rows = []
        for row in rows:
            processed_row = []
            for i, value in enumerate(row):
                col = _PROCESSED_COLUMN_NAMES[i]
                if col == 'doc_date':
                    processed_row.append(
                        datetime.strptime(value, '%Y-%m-%d').date() if value else None
                    )
                elif col in ('has_task_pin_record', 'trauma_after_orthopedist_flag'):
                    processed_row.append(value if value is not None else 0)
                elif col == 'load_datetime':
                    if value is None:
                        processed_row.append(None)
                    elif isinstance(value, str):
                        try:
                            processed_row.append(datetime.strptime(value, '%Y-%m-%d %H:%M:%S'))
                        except ValueError:
                            processed_row.append(datetime.now())
                    else:
                        processed_row.append(value)
                else:
                    processed_row.append(value if value is not None else '')
            processed_rows.append(processed_row)

        print(f"📤 Загружаю {len(processed_rows)} строк в assignment_results_other_mo...")
        client_target.insert(
            'assignment_results_other_mo',
            processed_rows,
            column_names=_PROCESSED_COLUMN_NAMES
        )
        print(f"✅ Успешно загружено {len(processed_rows)} строк.")
        client_target.close()

        os.remove(_SQLITE_DB)
        return True

    except Exception as e:
        print(f"❌ Ошибка загрузки в целевую ClickHouse: {e}")
        if client_target:
            client_target.close()
        if os.path.exists(_SQLITE_DB):
            os.remove(_SQLITE_DB)
        return False


# ===========================================================================
# Точка входа (автономный запуск)
# ===========================================================================

def main():
    print(f"🚀 Запуск описания_не_нпкц (последние {DAYS_TO_SYNC} дней)")
    if extract_phase() and load_phase():
        print("🏁 Готово.")
        return True
    print("❌ Завершено с ошибкой.")
    return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
