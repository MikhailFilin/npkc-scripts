"""
validation_summary_export.py
Экспорт данных валидации из ClickHouse (datalake.emias.ru) в ClickHouse (dwh_test_db)
через SQLite-буфер. Три таблицы: validation_summary, validation_summary_detailed,
validation_summary_instrumental. Данные тянутся и перезаливаются за последние DAYS_TO_SYNC дней.
"""

import subprocess
import pyautogui
import time
import sys
import os
from datetime import datetime, timedelta

import clickhouse_connect
import sqlite3
import pandas as pd

from ntfy_notifier import send_ntfy_alert
import personal_config as cfg

# === Настройки исходного ClickHouse (за VPN) ===
CH_HOST = cfg.CH_HOST
CH_PORT = cfg.CH_PORT
CH_USER = cfg.CH_USER
CH_PASSWORD = cfg.CH_PASSWORD
CH_DATABASE = cfg.CH_DATABASE

# === Локальный SQLite буфер ===
SQLITE_DB = r'C:\Users\m.filin\trauma_undescribed_buffer.db'

# === Настройки целевой ClickHouse базы ===
CH_HOST_TARGET = cfg.CH_HOST_TARGET
CH_PORT_TARGET = cfg.CH_PORT_TARGET
CH_USER_TARGET = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_TARGET

# === Настройки синхронизации ===
DAYS_TO_SYNC = 15

CH_TABLE_VALIDATION             = 'validation_summary'
CH_TABLE_VALIDATION_DETAILED    = 'validation_summary_detailed'
CH_TABLE_VALIDATION_INSTRUMENTAL = 'validation_summary_instrumental'

# === Настройки VPN ===
VPN_APP_PATH = cfg.VPN_APP_PATH
VPN_PASSWORD = cfg.VPN_PASSWORD

PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y

# Строковые колонки по таблице (для fix NaN)
_STR_COLS = {
    CH_TABLE_VALIDATION: [
        'assignment_result_emp_fio', 'payment_source', 'device_type',
    ],
    CH_TABLE_VALIDATION_DETAILED: [
        'assignment_result_emp_fio', 'payment_source', 'device_type', 'diagnostic_name',
    ],
    CH_TABLE_VALIDATION_INSTRUMENTAL: [
        'conduct_mo_name', 'conduct_mu_name', 'research_subtype_name',
        'device_type', 'ae_title', 'research_id', 'research_name', 'payment_source',
    ],
}


# ===========================================================================
# VPN
# ===========================================================================

def connect_vpn():
    print("Запускаю TrGUI...")
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
    except Exception as e:
        raise RuntimeError(f"Ошибка запуска VPN: {e}") from e

    time.sleep(15)

    windows = pyautogui.getWindowsWithTitle('')
    activated = False
    for window in windows:
        if any(kw in window.title.lower() for kw in ['check point', 'trgui', 'endpoint']):
            try:
                window.activate()
                time.sleep(2)
                print(f"Окно '{window.title}' активировано.")
                activated = True
                break
            except Exception:
                pass

    if not activated:
        print("Не удалось активировать окно VPN.")

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
    print("Подключение к VPN инициировано.")


def disconnect_vpn():
    print("Отключение от VPN...")
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print("VPN отключён.")


# ===========================================================================
# SQLite буфер
# ===========================================================================

def create_local_buffer():
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS validation_summary_buffer (
            doc_date DATE,
            assignment_result_emp_fio VARCHAR,
            payment_source VARCHAR,
            device_type VARCHAR,
            is_norm INTEGER,
            cnt INTEGER,
            load_datetime TIMESTAMP
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS validation_summary_detailed_buffer (
            doc_date DATE,
            assignment_result_emp_fio VARCHAR,
            payment_source VARCHAR,
            device_type VARCHAR,
            diagnostic_name VARCHAR,
            is_norm INTEGER,
            cnt INTEGER,
            load_datetime TIMESTAMP
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS validation_summary_instrumental_buffer (
            conduct_date DATE,
            conduct_mo_name VARCHAR,
            conduct_mu_name VARCHAR,
            research_subtype_name VARCHAR,
            device_type VARCHAR,
            ae_title VARCHAR,
            research_id VARCHAR,
            research_name VARCHAR,
            payment_source VARCHAR,
            is_norm INTEGER,
            count_accession INTEGER,
            load_datetime TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('DELETE FROM validation_summary_buffer')
        cursor.execute('DELETE FROM validation_summary_detailed_buffer')
        cursor.execute('DELETE FROM validation_summary_instrumental_buffer')

        conn.commit()
        conn.close()
        print("Локальный буфер создан и очищен.")
        return True
    except Exception as e:
        print(f"Ошибка создания локального буфера: {e}")
        return False


# ===========================================================================
# Целевой ClickHouse: DDL
# ===========================================================================

def create_clickhouse_tables():
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST_TARGET,
            port=CH_PORT_TARGET,
            username=CH_USER_TARGET,
            password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET,
            secure=True,
            verify=True,
        )

        client.command(f"""
        CREATE TABLE IF NOT EXISTS {CH_DATABASE_TARGET}.{CH_TABLE_VALIDATION}
        (
            doc_date                    Date,
            assignment_result_emp_fio   String,
            payment_source              String,
            device_type                 String,
            is_norm                     Int32,
            cnt                         Int64,
            load_datetime               DateTime
        )
        ENGINE = MergeTree()
        ORDER BY (doc_date, assignment_result_emp_fio, payment_source, device_type)
        """)

        client.command(f"""
        CREATE TABLE IF NOT EXISTS {CH_DATABASE_TARGET}.{CH_TABLE_VALIDATION_DETAILED}
        (
            doc_date                    Date,
            assignment_result_emp_fio   String,
            payment_source              String,
            device_type                 String,
            diagnostic_name             String,
            is_norm                     Int32,
            cnt                         Int64,
            load_datetime               DateTime
        )
        ENGINE = MergeTree()
        ORDER BY (doc_date, assignment_result_emp_fio, payment_source, device_type, diagnostic_name)
        """)

        client.command(f"""
        CREATE TABLE IF NOT EXISTS {CH_DATABASE_TARGET}.{CH_TABLE_VALIDATION_INSTRUMENTAL}
        (
            conduct_date                Date,
            conduct_mo_name             String,
            conduct_mu_name             String,
            research_subtype_name       String,
            device_type                 String,
            ae_title                    String,
            research_id                 String,
            research_name               String,
            payment_source              String,
            is_norm                     Int32,
            count_accession             Int64,
            load_datetime               DateTime
        )
        ENGINE = MergeTree()
        ORDER BY (conduct_date, conduct_mo_name, conduct_mu_name, ae_title)
        """)

        client.close()
        print("Таблицы в целевом ClickHouse проверены/созданы.")
        return True
    except Exception as e:
        print(f"Ошибка создания таблиц в ClickHouse: {e}")
        return False


# ===========================================================================
# Извлечение из источника → SQLite буфер
# ===========================================================================

def extract_and_buffer_data(date_from: str) -> bool:
    print("Извлечение данных из ClickHouse источника...")

    if not create_local_buffer():
        return False

    try:
        connect_vpn()
    except Exception as e:
        print(f"Ошибка VPN: {e}")
        return False

    client = None
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST,
            port=CH_PORT,
            username=CH_USER,
            password=CH_PASSWORD,
            database=CH_DATABASE,
            secure=True,
            verify=False,
            send_receive_timeout=94200,
            connect_timeout=999999,
        )
        print("ClickHouse источник подключён.")

        # --- validation_summary_instrumental ---
        print("Запрос validation_summary_instrumental...")
        query3 = f"""
        SELECT
            toDate(vie.conduct_date)             AS conduct_date,
            vie.conduct_mo_name,
            vie.conduct_mu_name,
            vie.research_subtype_name,
            vie.device_type,
            vie.ae_title,
            vie.research_id,
            vie.research_name,
            vie.payment_source,
            toInt32OrZero(ai_res.norma_value)    AS is_norm,
            count(DISTINCT vie.accession_number) AS count_accession,
            now()                                AS load_datetime
        FROM data_views.v_instrumental_examinations vie
        LEFT JOIN (
            SELECT
                JSONExtractString(raw_data, 'studyIUID') AS studyIUID,
                min(JSONExtractString(JSONExtractString(raw_data, 'aiResult'), 'norma')) AS norma_value
            FROM dwh_views.v_eris_report
            WHERE app_source = 'CDS'
              AND parseDateTimeBestEffort(JSONExtractString(computed_data, 'pumStudyReadyForAiTime')) IS NOT NULL
              AND JSONExtractString(raw_data, 'studyIUID') != ''
            GROUP BY studyIUID
        ) ai_res ON vie.study_uid = ai_res.studyIUID
        WHERE vie.ae_title IS NOT NULL
          AND vie.patient_age > 17
          AND toDate(vie.conduct_date) >= toDate('{date_from}')
          AND vie.research_name IN (
              'Rg органов грудной клетки (2 проекции)',
              'Rg органов грудной клетки (обзорная, 1 проекция)',
              'Рентгенография органов грудной клетки',
              'Рентгенография органов грудной клетки обзорная',
              'Флюорография легких профилактическая',
              'Флюорография легких'
          )
          AND vie.conduct_mo_name NOT LIKE '%Кончал%'
        GROUP BY
            toDate(vie.conduct_date),
            vie.conduct_mo_name,
            vie.conduct_mu_name,
            vie.research_subtype_name,
            vie.device_type,
            vie.ae_title,
            vie.research_id,
            vie.research_name,
            vie.payment_source,
            ai_res.norma_value
        ORDER BY
            toDate(vie.conduct_date),
            vie.conduct_mo_name,
            vie.conduct_mu_name
        """
        res3 = client.query(query3)
        cols3 = [
            'conduct_date', 'conduct_mo_name', 'conduct_mu_name',
            'research_subtype_name', 'device_type', 'ae_title',
            'research_id', 'research_name', 'payment_source',
            'is_norm', 'count_accession', 'load_datetime',
        ]
        df3 = pd.DataFrame(res3.result_rows, columns=cols3)
        print(f"validation_summary_instrumental: {len(df3)} строк.")
        if not df3.empty:
            sc = sqlite3.connect(SQLITE_DB)
            df3.to_sql('validation_summary_instrumental_buffer', sc, if_exists='append', index=False)
            sc.close()

        # --- validation_summary ---
        print("Запрос validation_summary...")
        query1 = f"""
        SELECT
            toDate(toTimeZone(toDateTime(vie.assignment_result_doc_created_date), 'Europe/Moscow')) AS doc_date,
            vie.assignment_result_emp_fio,
            vie.payment_source,
            vie.device_type,
            toInt32OrZero(ai_res.norma_value) AS is_norm,
            COUNT(DISTINCT vie.accession_number) AS cnt,
            now() AS load_datetime
        FROM data_views.v_eris_assignment_results vie
        LEFT JOIN (
            SELECT
                JSONExtractString(raw_data, 'studyIUID') AS studyIUID,
                min(JSONExtractString(JSONExtractString(raw_data, 'aiResult'), 'norma')) AS norma_value
            FROM dwh_views.v_eris_report
            WHERE app_source = 'CDS'
              AND parseDateTimeBestEffort(JSONExtractString(computed_data, 'pumStudyReadyForAiTime')) IS NOT NULL
              AND JSONExtractString(raw_data, 'studyIUID') != ''
            GROUP BY studyIUID
        ) ai_res ON vie.study_uid = ai_res.studyIUID
        WHERE vie.ae_title IS NOT NULL
          AND vie.diagnostic_code IN (33, 427, 34)
          AND vie.conduct_mo_name NOT LIKE '%Кончал%'
          AND toDate(toTimeZone(toDateTime(vie.assignment_result_doc_created_date), 'Europe/Moscow')) >= toDate('{date_from}')
        GROUP BY
            toDate(toTimeZone(toDateTime(vie.assignment_result_doc_created_date), 'Europe/Moscow')),
            vie.assignment_result_emp_fio,
            vie.payment_source,
            vie.device_type,
            ai_res.norma_value
        ORDER BY
            toDate(toTimeZone(toDateTime(vie.assignment_result_doc_created_date), 'Europe/Moscow')),
            vie.assignment_result_emp_fio,
            vie.payment_source,
            vie.device_type,
            ai_res.norma_value
        """
        res1 = client.query(query1)
        cols1 = [
            'doc_date', 'assignment_result_emp_fio', 'payment_source',
            'device_type', 'is_norm', 'cnt', 'load_datetime',
        ]
        df1 = pd.DataFrame(res1.result_rows, columns=cols1)
        print(f"validation_summary: {len(df1)} строк.")
        if not df1.empty:
            sc = sqlite3.connect(SQLITE_DB)
            df1.to_sql('validation_summary_buffer', sc, if_exists='append', index=False)
            sc.close()

        # --- validation_summary_detailed ---
        print("Запрос validation_summary_detailed...")
        query2 = f"""
        SELECT
            toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')) AS doc_date,
            t2.assignment_result_emp_fio,
            t2.payment_source,
            t2.device_type,
            t2.diagnostic_name,
            toInt32OrZero(ai_res.norma_value) AS is_norm,
            countDistinct(t2.accession_number) AS cnt,
            now() AS load_datetime
        FROM data_views.v_eris_assignment_results t2
        LEFT JOIN (
            SELECT
                JSONExtractString(raw_data, 'studyIUID') AS studyIUID,
                min(JSONExtractString(JSONExtractString(raw_data, 'aiResult'), 'norma')) AS norma_value
            FROM dwh_views.v_eris_report
            WHERE app_source = 'CDS'
              AND parseDateTimeBestEffort(JSONExtractString(computed_data, 'pumStudyReadyForAiTime')) IS NOT NULL
              AND JSONExtractString(raw_data, 'studyIUID') != ''
            GROUP BY studyIUID
        ) ai_res ON t2.study_uid = ai_res.studyIUID
        WHERE toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')) >= toDate('{date_from}')
        GROUP BY
            toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')),
            t2.assignment_result_emp_fio,
            t2.payment_source,
            t2.device_type,
            t2.diagnostic_name,
            ai_res.norma_value
        ORDER BY
            toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')),
            t2.assignment_result_emp_fio,
            t2.payment_source,
            t2.device_type,
            t2.diagnostic_name,
            ai_res.norma_value
        """
        res2 = client.query(query2)
        cols2 = [
            'doc_date', 'assignment_result_emp_fio', 'payment_source',
            'device_type', 'diagnostic_name', 'is_norm', 'cnt', 'load_datetime',
        ]
        df2 = pd.DataFrame(res2.result_rows, columns=cols2)
        print(f"validation_summary_detailed: {len(df2)} строк.")
        if not df2.empty:
            sc = sqlite3.connect(SQLITE_DB)
            df2.to_sql('validation_summary_detailed_buffer', sc, if_exists='append', index=False)
            sc.close()

    except Exception as e:
        print(f"Ошибка при работе с ClickHouse источником: {e}")
        try:
            disconnect_vpn()
        except Exception:
            pass
        return False
    finally:
        if client:
            client.close()

    try:
        disconnect_vpn()
    except Exception as e:
        print(f"Ошибка отключения VPN: {e}")

    return True


# ===========================================================================
# Вспомогательная: очистить NaN перед вставкой в ClickHouse
# ===========================================================================

def _fix_df(df: pd.DataFrame, table: str) -> pd.DataFrame:
    df = df.copy()
    # строковые колонки: NaN -> пустая строка
    for col in _STR_COLS.get(table, []):
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str)
    # числовые колонки: NaN -> 0
    for col in ['is_norm', 'cnt', 'count_accession']:
        if col in df.columns:
            df[col] = df[col].fillna(0).astype('int64')
    # date-колонки: SQLite хранит как строку, CH ожидает datetime.date
    for col in ['doc_date', 'conduct_date']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.date
    # datetime-колонки: аналогично
    if 'load_datetime' in df.columns:
        df['load_datetime'] = pd.to_datetime(df['load_datetime'], errors='coerce')
    return df


def _insert_df(client, df: pd.DataFrame, table: str) -> int:
    df = _fix_df(df, table)
    total = 0
    for start in range(0, len(df), 500):
        chunk = df.iloc[start:start + 500]
        client.insert_df(f"{CH_DATABASE_TARGET}.{table}", chunk)
        total += len(chunk)
    return total


# ===========================================================================
# Загрузка буфера → целевой ClickHouse
# ===========================================================================

def load_buffer_to_clickhouse(date_from: str) -> bool:
    print("Загрузка данных из буфера в целевой ClickHouse...")

    if not create_clickhouse_tables():
        return False

    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST_TARGET,
            port=CH_PORT_TARGET,
            username=CH_USER_TARGET,
            password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET,
            secure=True,
            verify=True,
        )

        sc = sqlite3.connect(SQLITE_DB)

        # instrumental
        df3 = pd.read_sql_query("SELECT * FROM validation_summary_instrumental_buffer", sc)
        if not df3.empty:
            client.command(
                f"ALTER TABLE {CH_DATABASE_TARGET}.{CH_TABLE_VALIDATION_INSTRUMENTAL} "
                f"DELETE WHERE conduct_date >= toDate('{date_from}') "
                f"SETTINGS mutations_sync = 1"
            )
            n3 = _insert_df(client, df3, CH_TABLE_VALIDATION_INSTRUMENTAL)
            print(f"Вставлено {n3} строк в {CH_TABLE_VALIDATION_INSTRUMENTAL}.")
        else:
            n3 = 0

        # summary
        df1 = pd.read_sql_query("SELECT * FROM validation_summary_buffer", sc)
        if not df1.empty:
            client.command(
                f"ALTER TABLE {CH_DATABASE_TARGET}.{CH_TABLE_VALIDATION} "
                f"DELETE WHERE doc_date >= toDate('{date_from}') "
                f"SETTINGS mutations_sync = 1"
            )
            n1 = _insert_df(client, df1, CH_TABLE_VALIDATION)
            print(f"Вставлено {n1} строк в {CH_TABLE_VALIDATION}.")
        else:
            n1 = 0

        # detailed
        df2 = pd.read_sql_query("SELECT * FROM validation_summary_detailed_buffer", sc)
        if not df2.empty:
            client.command(
                f"ALTER TABLE {CH_DATABASE_TARGET}.{CH_TABLE_VALIDATION_DETAILED} "
                f"DELETE WHERE doc_date >= toDate('{date_from}') "
                f"SETTINGS mutations_sync = 1"
            )
            n2 = _insert_df(client, df2, CH_TABLE_VALIDATION_DETAILED)
            print(f"Вставлено {n2} строк в {CH_TABLE_VALIDATION_DETAILED}.")
        else:
            n2 = 0

        sc.close()
        client.close()

        print(f"Итого вставлено: {n1 + n2 + n3} строк.")
        return True

    except Exception as e:
        print(f"Ошибка загрузки данных в ClickHouse: {e}")
        return False


# ===========================================================================
# Точка входа
# ===========================================================================

def _cleanup():
    import gc
    gc.collect()  # закрываем все висящие sqlite3-соединения перед удалением файла
    try:
        if os.path.exists(SQLITE_DB):
            os.remove(SQLITE_DB)
            print("Временный файл буфера удалён.")
    except Exception as e:
        print(f"Ошибка удаления временного файла: {e}")


def main():
    date_from = (datetime.now() - timedelta(days=DAYS_TO_SYNC)).strftime('%Y-%m-%d')
    print(f"Запуск. Окно синхронизации: с {date_from} ({DAYS_TO_SYNC} дней)")

    success = extract_and_buffer_data(date_from) and load_buffer_to_clickhouse(date_from)

    _cleanup()

    if success:
        send_ntfy_alert(
            "Дашборд Валидация нормы завершён успешно! Дашборд обновлён.",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895",
        )
        print("Экспорт завершён успешно.")
    else:
        send_ntfy_alert(
            "Дашборд Валидация норм завершился с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895",
        )
        print("Экспорт завершён с ошибкой.")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
