"""
kis_eris_up.py
Синхронизация данных kis_eris из ClickHouse (via VPN) в целевую ClickHouse (dwh_test_db).
Таблицы источника: kis.eris, kis.instrumental, kis.facility
Таблица назначения: dwh_test_db.kis_eris (создаётся автоматически)

Запуск: python kis_eris_up.py
  Количество дней задаётся константой DAYS_TO_SYNC в начале файла.

# INPUT:  kis.eris, kis.instrumental, kis.facility (source CH, через VPN)
# OUTPUT: kis_eris (target CH, dwh_test_db, Yandex Cloud)
"""

import subprocess
import pyautogui
import time
import sys
import os
import sqlite3
import clickhouse_connect
from datetime import datetime, timedelta
import datetime as datetime_module

from ntfy_notifier import send_ntfy_alert
import personal_config as cfg

# === Настройки синхронизации (плавающие даты) ===
DAYS_TO_SYNC = 20

# === Настройки исходной ClickHouse ===
CH_HOST_SOURCE     = cfg.CH_HOST
CH_PORT_SOURCE     = cfg.CH_PORT
CH_USER_SOURCE     = cfg.CH_USER
CH_PASSWORD_SOURCE = cfg.CH_PASSWORD
CH_DATABASE_SOURCE = cfg.CH_DATABASE_KIS  # 'kis'

# === Настройки целевой ClickHouse (Yandex Cloud) ===
CH_HOST_TARGET     = cfg.CH_HOST_TARGET
CH_PORT_TARGET     = cfg.CH_PORT_TARGET
CH_USER_TARGET     = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_TARGET  # 'dwh_test_db'

_TABLE_NAME  = 'kis_eris'

# === VPN ===
VPN_APP_PATH           = cfg.VPN_APP_PATH
VPN_PASSWORD           = cfg.VPN_PASSWORD
PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUFFER_PATH = os.path.join(_SCRIPTS_DIR, 'temp_buffer_kis_eris.db')

_COLUMNS = [
    'date_dt', 'date_time', 'facility_id', 'facility_short_name',
    'device_type', 'ae_title', 'procedure_code', 'procedure_name',
    'patient_id', 'naz_accession_number', 'study_uid', 'pay_type_name', 'sign_emp_id',
]


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
    print("Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN для kis_eris...", title="VPN Connect", priority="default", tags="lock")
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
    """Создаёт таблицу kis_eris в dwh_test_db, если её ещё нет."""
    client.command(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_NAME}
        (
            date_dt               Date,
            date_time             DateTime64(3, 'UTC'),
            facility_id           String,
            facility_short_name   String,
            device_type           String,
            ae_title              String,
            procedure_code        String,
            procedure_name        String,
            patient_id            String,
            naz_accession_number  String,
            study_uid             String,
            pay_type_name         String,
            sign_emp_id           String
        )
        ENGINE = MergeTree()
        ORDER BY (date_dt, facility_id, study_uid)
        SETTINGS index_granularity = 8192;
    """)
    print(f"Таблица {_TABLE_NAME} проверена/создана.")


def _build_query(n_days_ago: str) -> str:
    return f"""
SELECT
    toDate(i.sign_dt)                        AS date_dt,
    toDateTime64(i.sign_dt, 3, 'UTC')        AS date_time,
    e2.facility_id                           AS facility_id,
    f.short_name                             AS facility_short_name,
    e2.device_type                           AS device_type,
    e2.ae_title                              AS ae_title,
    e2.naz_code                              AS procedure_code,
    e2.naz_name                              AS procedure_name,
    i.patient_id                             AS patient_id,
    e2.accession_number                      AS naz_accession_number,
    e2.studyuid                              AS study_uid,
    i.pay_type_name                          AS pay_type_name,
    i.sign_emp_id                            AS sign_emp_id
FROM kis.eris e2
LEFT JOIN kis.instrumental i
    ON e2.naz_id = i.naz_id
LEFT JOIN kis.facility f
    ON e2.facility_id = f.id
WHERE e2.studyuid IS NOT NULL
  AND i.sign_dt >= '{n_days_ago}'
"""


def _parse_datetime(val) -> datetime_module.datetime | None:
    """Парсит datetime из строки SQLite, включая timezone-aware форматы (Python < 3.11 safe)."""
    if val is None:
        return None
    if isinstance(val, datetime_module.datetime):
        return val.replace(tzinfo=None)
    s = str(val).strip()
    # Обрезаем timezone-offset: +00:00, -03:00, Z
    for i in range(len(s) - 1, 9, -1):
        if s[i] in ('+', '-'):
            s = s[:i]
            break
        if s[i] == 'Z':
            s = s[:i]
            break
    s = s.replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _process_rows(raw_rows: list) -> list:
    """Приводит строки из SQLite-буфера к типам ClickHouse."""
    result = []
    for row in raw_rows:
        date_dt, date_time, *rest = row
        # date_dt → datetime.date
        if isinstance(date_dt, datetime_module.date) and not isinstance(date_dt, datetime_module.datetime):
            d = date_dt
        elif date_dt is not None:
            try:
                d = datetime.strptime(str(date_dt)[:10], '%Y-%m-%d').date()
            except Exception:
                d = datetime(1900, 1, 1).date()
        else:
            d = datetime(1900, 1, 1).date()
        # date_time → datetime.datetime (timezone-safe)
        dt = _parse_datetime(date_time) or datetime(1900, 1, 1)
        # строковые поля — None → пустая строка (ClickHouse String не nullable)
        str_fields = [v if v is not None else '' for v in rest]
        result.append([d, dt] + str_fields)
    return result


def sync_kis_eris(days: int) -> bool:
    today      = datetime.now().date()
    n_days_ago = (today - timedelta(days=days)).isoformat()

    print(f"[kis_eris] Синхронизация за последние {days} дней (с {n_days_ago}).")
    send_ntfy_alert(
        f"Начинаю синхронизацию kis_eris за {days} дней...",
        title="kis_eris Start", priority="default", tags="inbox",
    )

    setup_sqlite_adapters()

    # Шаг 1: Очистка целевой ClickHouse за период
    client_target_del = None
    try:
        print("Подключаюсь к целевой ClickHouse для очистки...")
        client_target_del = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False,
        )
        _ensure_table_exists(client_target_del)
        delete_query = f"ALTER TABLE {_TABLE_NAME} DELETE WHERE date_dt >= '{n_days_ago}'"
        print(f"   DELETE для date_dt >= {n_days_ago}")
        client_target_del.command(delete_query)
        print("Старые данные удалены.")
        client_target_del.close()
        client_target_del = None
    except Exception as e:
        msg = f"Ошибка очистки целевой ClickHouse: {e}"
        print(msg)
        send_ntfy_alert(msg[:120], title="kis_eris Clean Error", priority="urgent", tags="database")
        if client_target_del:
            client_target_del.close()
        return False

    # Шаг 2: Извлечение из source CH через VPN → SQLite буфер
    client_source = None
    try:
        connect_vpn()

        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999,
        )
        print("Исходная ClickHouse подключена.")

        query    = _build_query(n_days_ago)
        print("Выполняю запрос к исходной ClickHouse...")
        result   = client_source.query(query)
        raw_rows = result.result_rows
        client_source.close()
        client_source = None
        print(f"Получено {len(raw_rows)} строк.")

        if not raw_rows:
            print(f"Нет данных за последние {days} дней.")
            send_ntfy_alert(f"Нет данных kis_eris за {days} дней",
                            title="kis_eris Empty", priority="default", tags="inbox")
            disconnect_vpn()
            return True

        # Шаг 3: SQLite буфер
        print(f"Создаю SQLite буфер ({_BUFFER_PATH})...")
        conn   = sqlite3.connect(_BUFFER_PATH)
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS temp_kis_eris;")
        cursor.execute("""
            CREATE TABLE temp_kis_eris (
                date_dt               TEXT,
                date_time             TEXT,
                facility_id           TEXT,
                facility_short_name   TEXT,
                device_type           TEXT,
                ae_title              TEXT,
                procedure_code        TEXT,
                procedure_name        TEXT,
                patient_id            TEXT,
                naz_accession_number  TEXT,
                study_uid             TEXT,
                pay_type_name         TEXT,
                sign_emp_id           TEXT
            );
        """)
        placeholders = ', '.join(['?' for _ in _COLUMNS])
        cursor.executemany(f"INSERT INTO temp_kis_eris VALUES ({placeholders});", raw_rows)
        conn.commit()
        conn.close()
        print("SQLite буфер заполнен.")

    except Exception as e:
        msg = f"Ошибка source CH / SQLite: {e}"
        print(msg)
        send_ntfy_alert(msg[:120], title="kis_eris Error", priority="urgent", tags="fire")
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
        send_ntfy_alert(f"Ошибка отключения VPN: {e}", title="VPN Warning",
                        priority="default", tags="warning")

    # Шаг 5: Вставка в целевую ClickHouse (с retry)
    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"Подключаюсь к целевой ClickHouse (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
            )
            break
        except Exception as e:
            print(f"Попытка {attempt}: {e}")
            send_ntfy_alert(f"Ошибка подключения к target CH: {str(e)[:60]}",
                            title="kis_eris Connect Error", priority="urgent", tags="database")
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
        cursor.execute("SELECT * FROM temp_kis_eris;")
        sqlite_rows = cursor.fetchall()
        conn.close()
        print(f"Прочитано {len(sqlite_rows)} строк из SQLite.")

        processed_rows = _process_rows(sqlite_rows)

        print(f"Загружаю {len(processed_rows)} строк в {CH_DATABASE_TARGET}.{_TABLE_NAME}...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)

        msg = f"[kis_eris] Синхронизировано {len(processed_rows)} строк за {days} дней."
        print(msg)
        send_ntfy_alert(msg, title="kis_eris Success", priority="high", tags="white_check_mark")

        client_target.close()
        client_target = None

        try:
            os.remove(_BUFFER_PATH)
            print("Временный файл удалён.")
        except Exception as e:
            print(f"Не удалось удалить буфер: {e}")

        return True

    except Exception as e:
        msg = f"Ошибка выгрузки в целевую ClickHouse: {e}"
        print(msg)
        send_ntfy_alert(msg[:120], title="kis_eris Insert Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        if os.path.exists(_BUFFER_PATH):
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
        return False


def extract_phase() -> bool:
    """Фаза 1: Извлечение из source CH в SQLite буфер (VPN включён)."""
    today      = datetime.now().date()
    n_days_ago = (today - timedelta(days=DAYS_TO_SYNC)).isoformat()

    print(f"[kis_eris] extract_phase: за последние {DAYS_TO_SYNC} дней (с {n_days_ago}).")
    setup_sqlite_adapters()

    client_source = None
    try:
        connect_vpn()
        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999,
        )
        print("[kis_eris] Исходная ClickHouse подключена.")

        query    = _build_query(n_days_ago)
        print("[kis_eris] Выполняю запрос к исходной ClickHouse...")
        result   = client_source.query(query)
        raw_rows = result.result_rows
        client_source.close()
        client_source = None
        print(f"[kis_eris] Получено {len(raw_rows)} строк.")

        print(f"[kis_eris] Создаю SQLite буфер ({_BUFFER_PATH})...")
        conn   = sqlite3.connect(_BUFFER_PATH)
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS temp_kis_eris;")
        cursor.execute("""
            CREATE TABLE temp_kis_eris (
                date_dt               TEXT,
                date_time             TEXT,
                facility_id           TEXT,
                facility_short_name   TEXT,
                device_type           TEXT,
                ae_title              TEXT,
                procedure_code        TEXT,
                procedure_name        TEXT,
                patient_id            TEXT,
                naz_accession_number  TEXT,
                study_uid             TEXT,
                pay_type_name         TEXT,
                sign_emp_id           TEXT
            );
        """)
        placeholders = ', '.join(['?' for _ in _COLUMNS])
        cursor.executemany(f"INSERT INTO temp_kis_eris VALUES ({placeholders});", raw_rows)
        conn.commit()
        conn.close()
        print("[kis_eris] SQLite буфер заполнен.")
        return True

    except Exception as e:
        msg = f"[kis_eris] Ошибка extract_phase: {e}"
        print(msg)
        send_ntfy_alert(msg[:120], title="kis_eris Extract Error", priority="urgent", tags="fire")
        if client_source:
            client_source.close()
        if os.path.exists(_BUFFER_PATH):
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
        return False


def load_phase() -> bool:
    """Фаза 2: Удаление старых данных из target CH + вставка из SQLite буфера (VPN выключен)."""
    today      = datetime.now().date()
    n_days_ago = (today - timedelta(days=DAYS_TO_SYNC)).isoformat()

    setup_sqlite_adapters()

    if not os.path.exists(_BUFFER_PATH):
        print(f"[kis_eris] Буфер не найден: {_BUFFER_PATH}")
        return False

    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"[kis_eris] load_phase: подключаюсь к target CH (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
            )
            break
        except Exception as e:
            print(f"[kis_eris] Попытка {attempt}: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                if os.path.exists(_BUFFER_PATH):
                    try: os.remove(_BUFFER_PATH)
                    except Exception: pass
                return False

    try:
        _ensure_table_exists(client_target)
        delete_query = f"ALTER TABLE {_TABLE_NAME} DELETE WHERE date_dt >= '{n_days_ago}'"
        print(f"[kis_eris] DELETE для date_dt >= {n_days_ago}")
        client_target.command(delete_query)
        print("[kis_eris] Старые данные удалены.")

        conn   = sqlite3.connect(_BUFFER_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM temp_kis_eris;")
        sqlite_rows = cursor.fetchall()
        conn.close()
        print(f"[kis_eris] Прочитано {len(sqlite_rows)} строк из SQLite.")

        processed_rows = _process_rows(sqlite_rows)

        if not processed_rows:
            print(f"[kis_eris] load_phase: 0 строк — нет данных за {DAYS_TO_SYNC} дней, только очистка.")
            client_target.close()
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
            return True

        print(f"[kis_eris] Загружаю {len(processed_rows)} строк в {CH_DATABASE_TARGET}.{_TABLE_NAME}...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)

        msg = f"[kis_eris] Синхронизировано {len(processed_rows)} строк за {DAYS_TO_SYNC} дней."
        print(msg)
        send_ntfy_alert(msg, title="kis_eris Success", priority="high", tags="white_check_mark")

        client_target.close()
        client_target = None

        try:
            os.remove(_BUFFER_PATH)
            print("[kis_eris] Временный файл удалён.")
        except Exception as e:
            print(f"[kis_eris] Не удалось удалить буфер: {e}")

        return True

    except Exception as e:
        msg = f"[kis_eris] Ошибка load_phase: {e}"
        print(msg)
        send_ntfy_alert(msg[:120], title="kis_eris Load Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        if os.path.exists(_BUFFER_PATH):
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
        return False


def main():
    days = DAYS_TO_SYNC
    print(f"Запуск kis_eris_up (последние {days} дней)")
    send_ntfy_alert(
        f"Запускаю kis_eris_up за {days} дней...",
        title="kis_eris Start", priority="default", tags="robot",
    )

    success = sync_kis_eris(days)

    if success:
        send_ntfy_alert(
            f"Обновление kis_eris завершено! ({days} дней)",
            title="kis_eris Done", priority="high", tags="tada",
        )
        print("Синхронизация завершена успешно.")
    else:
        send_ntfy_alert(
            "Обновление kis_eris завершилось с ошибкой!",
            title="kis_eris Failed", priority="urgent", tags="warning",
        )
        print("Синхронизация завершена с ошибками.")

    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
