"""
concl_npkc_up.py
Синхронизация данных instrumental_summary_npkc из исходной ClickHouse в целевую
через SQLite-буфер с плавающими датами.
Фильтр: assignment_describe_mu_id = '100001912827' (НПКЦ).
Все уведомления отправляются через ntfy.

# INPUT:  data_views.v_eris_assignment_results (source CH, через VPN)
# OUTPUT: instrumental_summary_npkc (target CH, Yandex Cloud)
"""

import subprocess
import pyautogui
import time
import sys
import os
import sqlite3
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

# === Синхронизация (плавающие даты) ===
DAYS_TO_SYNC = 20

_SCRIPTS_DIR  = os.path.dirname(os.path.abspath(__file__))
_BUFFER_PATH  = os.path.join(_SCRIPTS_DIR, 'temp_buffer_summary_npkc.db')
_BUFFER_PKL   = os.path.join(_SCRIPTS_DIR, 'temp_buffer_npkc.pkl')
_TABLE_NAME   = 'instrumental_summary_npkc'

_COLUMNS = [
    'conduct_date', 'payment_source', 'device_type', 'assessment_result_type_code',
    'conduct_mo_name', 'conduct_mu_name', 'assignment_describe_mu_id',
    'norma_value', 'age_group', 'cnt', 'load_datetime',
]

# === Настройки VPN ===
VPN_APP_PATH   = cfg.VPN_APP_PATH
VPN_PASSWORD   = cfg.VPN_PASSWORD
PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y


# === SQLite адаптеры ===
def setup_sqlite_adapters():
    def adapt_date(val):      return val.isoformat()
    def adapt_datetime(val):  return val.isoformat()
    def convert_date(val):    return datetime_module.date.fromisoformat(val.decode())
    def convert_ts(val):      return datetime_module.datetime.fromisoformat(val.decode())

    sqlite3.register_adapter(datetime_module.date, adapt_date)
    sqlite3.register_adapter(datetime_module.datetime, adapt_datetime)
    sqlite3.register_converter("date", convert_date)
    sqlite3.register_converter("timestamp", convert_ts)


# === VPN ===
def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN для concl_npkc...", title="VPN Connect", priority="default", tags="lock")
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
    except Exception as e:
        msg = f"❌ Ошибка запуска VPN: {e}"
        print(msg)
        send_ntfy_alert(msg, title="VPN Error", priority="urgent", tags="warning")
        raise
    time.sleep(15)

    windows = pyautogui.getWindowsWithTitle('')
    for window in windows:
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
    print("✅ VPN подключён.")
    send_ntfy_alert("VPN подключён", title="VPN Connected", priority="high", tags="key")


def disconnect_vpn():
    print("🛑 Отключаю VPN...")
    send_ntfy_alert("Отключаюсь от VPN...", title="VPN Disconnect", priority="default", tags="unlock")
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print("🛑 VPN отключён.")
    send_ntfy_alert("VPN отключён", title="VPN Disconnected", priority="default", tags="check")


def _ensure_table_exists(client) -> None:
    """Создаёт таблицу instrumental_summary_npkc если её ещё нет."""
    client.command(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE_NAME}
        (
            conduct_date               Date,
            payment_source             String,
            device_type                String,
            assessment_result_type_code String,
            conduct_mo_name            String,
            conduct_mu_name            String,
            assignment_describe_mu_id  String,
            norma_value                String,
            age_group                  String,
            cnt                        UInt32,
            load_datetime              DateTime
        )
        ENGINE = MergeTree()
        ORDER BY (conduct_date, conduct_mo_name, device_type)
        SETTINGS index_granularity = 8192;
    """)
    print(f"✅ Таблица {_TABLE_NAME} проверена/создана.")


def _build_query(n_days_ago: str, today: str) -> str:
    return f"""
SELECT
    toDate(toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow')) AS conduct_date,
    t2.payment_source,
    t2.device_type,
    t2.assessment_result_type_code,
    t2.conduct_mo_name,
    t2.conduct_mu_name,
    t2.assignment_describe_mu_id,
    ai_res.norma_value,
    multiIf(
        ie.patient_age >= 18, 'Взрослые',
        ie.patient_age IS NOT NULL AND ie.patient_age < 18, 'Дети',
        'Не указано'
    ) AS age_group,
    countDistinct(t2.accession_number) AS cnt,
    toTimeZone(now(), 'Europe/Moscow') AS load_datetime
FROM data_views.v_eris_assignment_results t2
LEFT JOIN (
    SELECT accession_number, any(patient_age) AS patient_age
    FROM data_views.v_instrumental_examinations
    WHERE accession_number != ''
    GROUP BY accession_number
) ie ON t2.accession_number = ie.accession_number
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
WHERE t2.accession_number != ''
  AND t2.assignment_describe_mu_id = '100001912827'
  AND toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')) >= '{n_days_ago}'
  AND toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')) <= '{today}'
GROUP BY
    conduct_date,
    t2.payment_source,
    t2.device_type,
    t2.assessment_result_type_code,
    t2.conduct_mo_name,
    t2.conduct_mu_name,
    t2.assignment_describe_mu_id,
    ai_res.norma_value,
    age_group
ORDER BY conduct_date DESC, t2.payment_source ASC
"""


def export_instrumental_summary_npkc() -> bool:
    """
    Загрузка данных за последние DAYS_TO_SYNC дней в целевую ClickHouse через SQLite буфер.
    """
    print(f"📊 [concl_npkc] Загрузка instrumental_summary_npkc за последние {DAYS_TO_SYNC} дней...")
    send_ntfy_alert(
        f"Начинаю синхронизацию concl_npkc за {DAYS_TO_SYNC} дней...",
        title="concl_npkc Start",
        priority="default",
        tags="inbox",
    )

    setup_sqlite_adapters()
    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)
    print(f"   Период: {n_days_ago} — {today}")

    # Шаг 1: Очистка целевой базы за период
    client_target_del = None
    try:
        print("🧹 Подключаюсь к целевой базе для очистки...")
        client_target_del = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False,
        )
        _ensure_table_exists(client_target_del)
        delete_query = f"ALTER TABLE {_TABLE_NAME} DELETE WHERE conduct_date >= '{n_days_ago.isoformat()}'"
        print(f"   DELETE для conduct_date >= {n_days_ago.isoformat()}")
        client_target_del.command(delete_query)
        print("✅ Старые данные удалены.")
        client_target_del.close()
        client_target_del = None
    except Exception as e:
        msg = f"❌ Ошибка очистки целевой базы: {e}"
        print(msg)
        send_ntfy_alert(f"Ошибка очистки БД: {str(e)[:80]}", title="concl_npkc Clean Error",
                        priority="urgent", tags="database")
        if client_target_del:
            client_target_del.close()
        return False

    # Шаг 2: Извлечение из source CH через VPN → SQLite
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

        query = _build_query(n_days_ago.isoformat(), today.isoformat())
        print("📥 Выполняю запрос к исходной ClickHouse...")
        result = client_source.query(query)
        raw_rows     = result.result_rows
        column_names = result.column_names
        client_source.close()
        client_source = None
        print(f"📥 Получено {len(raw_rows)} строк.")

        if not raw_rows:
            print(f"📭 Нет данных за последние {DAYS_TO_SYNC} дней.")
            send_ntfy_alert(f"Нет данных concl_npkc за {DAYS_TO_SYNC} дней",
                            title="concl_npkc Empty", priority="default", tags="inbox")
            disconnect_vpn()
            return True

        # Шаг 3: SQLite буфер
        print(f"💾 Создаю SQLite буфер ({_BUFFER_PATH})...")
        conn   = sqlite3.connect(_BUFFER_PATH)
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS temp_summary_npkc;")
        cursor.execute("""
            CREATE TABLE temp_summary_npkc (
                conduct_date              TEXT,
                payment_source            TEXT,
                device_type               TEXT,
                assessment_result_type_code TEXT,
                conduct_mo_name           TEXT,
                conduct_mu_name           TEXT,
                assignment_describe_mu_id TEXT,
                norma_value               TEXT,
                age_group                 TEXT,
                cnt                       INTEGER,
                load_datetime             TEXT
            );
        """)
        cursor.executemany(
            f"INSERT INTO temp_summary_npkc VALUES ({', '.join(['?' for _ in column_names])});",
            raw_rows,
        )
        conn.commit()
        conn.close()
        print("✅ SQLite буфер заполнен.")

    except Exception as e:
        msg = f"❌ Ошибка source CH / SQLite: {e}"
        print(msg)
        send_ntfy_alert(f"Сбой concl_npkc: {str(e)[:80]}", title="concl_npkc Error",
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
            send_ntfy_alert(f"Ошибка подключения к target CH: {str(e)[:60]}",
                            title="concl_npkc Connect Error", priority="urgent", tags="database")
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
        cursor.execute("SELECT * FROM temp_summary_npkc;")
        sqlite_rows = cursor.fetchall()
        conn.close()
        print(f"   Прочитано {len(sqlite_rows)} строк из SQLite.")

        processed_rows = []
        for row in sqlite_rows:
            p = []
            for i, val in enumerate(row):
                col = _COLUMNS[i]
                if col == 'conduct_date':
                    if val is not None:
                        try:    p.append(datetime.strptime(val, '%Y-%m-%d').date())
                        except: p.append(datetime(1900, 1, 1).date())
                    else:
                        p.append(None)
                elif col in ('payment_source', 'device_type', 'assessment_result_type_code',
                             'conduct_mo_name', 'conduct_mu_name', 'assignment_describe_mu_id',
                             'norma_value', 'age_group'):
                    p.append(val if val is not None else '')
                elif col == 'cnt':
                    p.append(val if val is not None else 0)
                elif col == 'load_datetime':
                    if val is None:
                        p.append(datetime.now())
                    elif isinstance(val, str):
                        try:    p.append(datetime.strptime(val, '%Y-%m-%d %H:%M:%S'))
                        except: p.append(datetime.now())
                    else:
                        p.append(val)
                else:
                    p.append(val)
            processed_rows.append(p)

        print(f"📤 Загружаю {len(processed_rows)} строк в {CH_DATABASE_TARGET}.{_TABLE_NAME}...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)

        msg = f"✅ [concl_npkc] Синхронизировано {len(processed_rows)} строк за {DAYS_TO_SYNC} дней."
        print(msg)
        send_ntfy_alert(msg, title="concl_npkc Success", priority="high", tags="white_check_mark")

        client_target.close()
        client_target = None

        try:
            os.remove(_BUFFER_PATH)
            print(f"🧹 Временный файл удалён.")
        except Exception as e:
            print(f"⚠️ Не удалось удалить буфер: {e}")

        return True

    except Exception as e:
        msg = f"❌ Ошибка выгрузки в целевую ClickHouse: {e}"
        print(msg)
        send_ntfy_alert(f"Ошибка выгрузки concl_npkc: {str(e)[:80]}",
                        title="concl_npkc Insert Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        if os.path.exists(_BUFFER_PATH):
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
        return False


# === Фазовый интерфейс для all_dashboards_up.py ===

def extract_phase() -> bool:
    """VPN включён — source CH → pickle-буфер."""
    print(f"📥 [concl_npkc] extract_phase: запрос к source ClickHouse за последние {DAYS_TO_SYNC} дней...")

    setup_sqlite_adapters()
    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)

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
        print(f"✅ [concl_npkc] extract_phase: сохранено {len(rows)} строк в буфер.")
        return True
    except Exception as e:
        print(f"❌ [concl_npkc] extract_phase: {e}")
        if client_source:
            client_source.close()
        return False


def load_phase() -> bool:
    """VPN выключен — pickle-буфер → DELETE + INSERT в target CH."""
    if not os.path.exists(_BUFFER_PKL):
        print("❌ [concl_npkc] load_phase: буфер не найден")
        return False

    try:
        with open(_BUFFER_PKL, 'rb') as f:
            raw_rows = _pickle.load(f)
        os.remove(_BUFFER_PKL)
    except Exception as e:
        print(f"❌ [concl_npkc] load_phase: ошибка чтения буфера: {e}")
        return False

    if not raw_rows:
        print("⚠️ [concl_npkc] load_phase: буфер пуст, пропускаю.")
        return True

    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)

    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"🔌 [concl_npkc] Подключаюсь к целевой ClickHouse (попытка {attempt})...")
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
        delete_q = f"ALTER TABLE {_TABLE_NAME} DELETE WHERE conduct_date >= '{n_days_ago.isoformat()}'"
        print(f"🧹 DELETE conduct_date >= {n_days_ago.isoformat()}")
        client_target.command(delete_q)

        processed_rows = []
        for row in raw_rows:
            p = []
            for i, val in enumerate(row):
                col = _COLUMNS[i]
                if col == 'conduct_date':
                    if val is not None:
                        try:    p.append(datetime.strptime(str(val), '%Y-%m-%d').date())
                        except: p.append(datetime(1900, 1, 1).date())
                    else:
                        p.append(datetime(1900, 1, 1).date())
                elif col in ('payment_source', 'device_type', 'assessment_result_type_code',
                             'conduct_mo_name', 'conduct_mu_name', 'assignment_describe_mu_id',
                             'norma_value', 'age_group'):
                    p.append(str(val) if val is not None else '')
                elif col == 'cnt':
                    p.append(int(val) if val is not None else 0)
                elif col == 'load_datetime':
                    dt = val if val is not None else datetime.now()
                    # strip timezone for DateTime CH column
                    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
                        dt = dt.replace(tzinfo=None)
                    p.append(dt)
                else:
                    p.append(val)
            processed_rows.append(p)

        print(f"📤 [concl_npkc] load_phase: вставляю {len(processed_rows)} строк в {_TABLE_NAME}...")
        client_target.insert(_TABLE_NAME, processed_rows, column_names=_COLUMNS)
        print(f"✅ [concl_npkc] load_phase: вставлено {len(processed_rows)} строк.")
        client_target.close()
        client_target = None
        return True
    except Exception as e:
        print(f"❌ [concl_npkc] load_phase: {e}")
        if client_target:
            client_target.close()
        return False


# === Основная точка входа ===
def main():
    print(f"🚀 Запуск concl_npkc (последние {DAYS_TO_SYNC} дней)")
    send_ntfy_alert(
        f"Запускаю concl_npkc за {DAYS_TO_SYNC} дней...",
        title="concl_npkc Start",
        priority="default",
        tags="robot",
    )

    success = export_instrumental_summary_npkc()

    if success:
        send_ntfy_alert(
            "✅ Обновление instrumental_summary_npkc завершено!",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895",
        )
        print("🏁 Синхронизация завершена успешно.")
    else:
        send_ntfy_alert(
            "❌ Обновление instrumental_summary_npkc завершилось с ошибкой!",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895",
        )
        print("❌ Синхронизация завершена с ошибками.")

    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
