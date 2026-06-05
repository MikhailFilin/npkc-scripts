### ПЕРЕВЕДЕН НА МОСКОВСКОЕ ВРЕМЯ
"""
cometa_conclusions_summary_floating.py
Синхронизация данных cometa_conclusions_summary из исходной ClickHouse в целевую
через SQLite-буфер с плавающими датами.
Все уведомления отправляются через ntfy.
"""

import subprocess
import pyautogui
import time
import sys
import os
import sqlite3
import requests
import clickhouse_connect
from datetime import datetime, timedelta
import datetime as datetime_module

# === Импорт модуля уведомлений ===
from ntfy_notifier import send_ntfy_alert
import personal_config as cfg

# === Настройки целевой ClickHouse базы ===
CH_HOST_TARGET = cfg.CH_HOST_TARGET
CH_PORT_TARGET = cfg.CH_PORT_TARGET
CH_USER_TARGET = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_TARGET

# === Настройки исходной ClickHouse базы ===
CH_HOST_SOURCE = cfg.CH_HOST
CH_PORT_SOURCE = cfg.CH_PORT
CH_USER_SOURCE = cfg.CH_USER
CH_PASSWORD_SOURCE = cfg.CH_PASSWORD
CH_DATABASE_SOURCE = cfg.CH_DATABASE

# === Настройки синхронизации (плавающие даты) ===
DAYS_TO_SYNC = 15

_BUFFER_KOMET = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp_buffer_cometa.db')
_KOMET_COLUMNS = ['accession_number', 'study_uid', 'doc_date',
                   'assignment_result_doc_created_date_plus_3h', 'assignment_result_emp_fio',
                   'payment_source', 'device_type', 'diagnostic_name', 'assessment_result_type_code',
                   'conduct_mo_name', 'conduct_mu_name', 'conduct_district_name',
                   'patient_gender', 'assignment_describe_mu_id', 'load_datetime',
                   'assignment_result_emp_id', 'conduct_datetime', 'conduct_date_only']

# === Настройки VPN ===
VPN_APP_PATH = cfg.VPN_APP_PATH
VPN_PASSWORD = cfg.VPN_PASSWORD

# Координаты для pyautogui
PASSWORD_FIELD_X, PASSWORD_FIELD_Y = cfg.PASSWORD_FIELD_X, cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X, CONNECT_BUTTON_Y = cfg.CONNECT_BUTTON_X, cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y = cfg.RIGHT_CLICK_MENU_X, cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y = cfg.CONFIRMATION_CLICK_X, cfg.CONFIRMATION_CLICK_Y


# === Функция: Исправление SQLite DeprecationWarning ===
def setup_sqlite_adapters():
    """Настраивает адаптеры и конвертеры для sqlite3."""
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


# === Функция: Подключение к VPN ===
def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN-клиент для Cometa Sync...", title="VPN Connect", priority="default", tags="lock")
    
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
    except Exception as e:
        error_msg = f"❌ Ошибка запуска VPN: {e}"
        print(error_msg)
        send_ntfy_alert(error_msg, title="VPN Error", priority="urgent", tags="warning")
        raise
    
    time.sleep(15)
    
    # Активация окна
    windows = pyautogui.getWindowsWithTitle('')
    activated = False
    for window in windows:
        if any(kw in window.title.lower() for kw in ['check point', 'trgui', 'endpoint']):
            try:
                window.activate()
                time.sleep(2)
                print(f"✅ Окно '{window.title}' активировано.")
                activated = True
                break
            except:
                pass
    
    if not activated:
        print("⚠️ Не удалось активировать окно.")
    
    # Ввод пароля
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
    send_ntfy_alert("VPN подключён", title="VPN Connected", priority="high", tags="key")


# === Функция: Отключение от VPN ===
def disconnect_vpn():
    print("🛑 Начинаю отключение...")
    send_ntfy_alert("Отключаюсь от VPN...", title="VPN Disconnect", priority="default", tags="unlock")
    
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    
    print("🛑 Отключение завершено.")
    send_ntfy_alert("VPN отключён", title="VPN Disconnected", priority="default", tags="check")


# === Основная функция: Экспорт через SQLite буфер ===
def export_cometa_conclusions_summary():
    """
    Загрузка данных за последние N дней из v_eris_assignment_results
    в новую ClickHouse базу через SQLite буфер.
    Returns:
        bool: True если успешно, False если ошибка
    """
    print(f"📊 Загрузка cometa_conclusions_summary за последние {DAYS_TO_SYNC} дней...")
    send_ntfy_alert(
        f"Начинаю синхронизацию Cometa за {DAYS_TO_SYNC} дней...",
        title="Cometa Sync Start",
        priority="default",
        tags="inbox"
    )
    
    # Исправление DeprecationWarning для SQLite
    setup_sqlite_adapters()
    
    # Вычисляем даты
    today = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)
    print(f"   Синхронизация за период: {n_days_ago} - {today}")
    
    # --- Шаг 1: Очистка старых данных в целевой базе ---
    client_target_delete = None
    try:
        print("🧹 Подключаюсь к целевой базе для очистки...")
        client_target_delete = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )
        
        delete_query = f"ALTER TABLE cometa_conclusions_summary DELETE WHERE doc_date >= '{n_days_ago.isoformat()}'"
        print(f"   Выполняю DELETE для doc_date >= {n_days_ago.isoformat()}")
        client_target_delete.command(delete_query)
        print("✅ Старые данные удалены из целевой таблицы.")
        client_target_delete.close()
        client_target_delete = None
        
    except Exception as e:
        error_msg = f"❌ Ошибка очистки целевой базы: {e}"
        print(error_msg)
        send_ntfy_alert(f"Ошибка очистки БД: {str(e)[:80]}", title="DB Clean Error", priority="urgent", tags="database")
        if client_target_delete:
            client_target_delete.close()
        return False
    
    # --- Шаг 2: Подключение к исходной базе через VPN ---
    client_source = None
    temp_db_name = "temp_buffer_cometa.db"
    
    try:
        connect_vpn()
        
        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999
        )
        print("✅ Исходная ClickHouse подключена.")
        
        # --- Шаг 3: Формируем запрос ---
        base_query = """
    SELECT DISTINCT
    t2.accession_number,
    t2.study_uid,
    toDate(assignment_result_doc_created_date_moscow) AS doc_date,
    assignment_result_doc_created_date_moscow AS assignment_result_doc_created_date_plus_3h,
    t2.assignment_result_emp_fio,
    t2.payment_source,
    t2.device_type,
    t2.diagnostic_name,
    t2.assessment_result_type_code,
    t2.conduct_mo_name,
    t2.conduct_mu_name,
    t2.conduct_district_name,
    t2.patient_gender,
    t2.assignment_describe_mu_id,
    now() AS load_datetime,
    toString(t2.assignment_result_emp_id) AS assignment_result_emp_id,
    conduct_datetime_moscow AS conduct_datetime,
    toDate(conduct_datetime_moscow) AS conduct_date_only
FROM
(
    SELECT *,
        toTimeZone(toDateTime(assignment_result_doc_created_date), 'Europe/Moscow') AS assignment_result_doc_created_date_moscow,
        toTimeZone(toDateTime(conduct_date), 'Europe/Moscow') AS conduct_datetime_moscow
    FROM data_views.v_eris_assignment_results
) t2
LEFT JOIN data_views.v_instrumental_examinations ie
    ON t2.accession_number = ie.accession_number
LEFT JOIN data_views.v_route_eris_trauma trauma_res
    ON t2.accession_number = trauma_res.accession_number AND trauma_res.accession_number != ''
LEFT JOIN data_views.v_task_pin pin_res
    ON t2.accession_number = pin_res.accession_number AND pin_res.accession_number != ''
WHERE
    t2.accession_number != ''
    and assessment_result_type_code = 1
    AND t2.assignment_describe_mu_id = 100001912827
        """
        
        # Добавляем фильтр по дате
        date_filter_clause = f"  AND toDate((t2.assignment_result_doc_created_date + INTERVAL 3 HOUR)) >= '{n_days_ago.isoformat()}' AND toDate(t2.assignment_result_doc_created_date + INTERVAL 3 HOUR) <= '{today.isoformat()}'"
        final_query = base_query + date_filter_clause
        
        print("📥 Выполняю запрос к исходной ClickHouse...")
        result = client_source.query(final_query)
        raw_rows = result.result_rows
        column_names = result.column_names
        
        client_source.close()
        client_source = None
        print(f"📥 Получено {len(raw_rows)} строк данных.")
        
        # Если данных нет
        if not raw_rows:
            print(f"📭 Нет данных за последние {DAYS_TO_SYNC} дней.")
            send_ntfy_alert(f"Нет данных для синхронизации Cometa за {DAYS_TO_SYNC} дней", title="Cometa Empty", priority="default", tags="inbox")
            disconnect_vpn()
            return True
        
        # --- Шаг 4: Создание и заполнение SQLite буфера ---
        print(f"💾 Создаю SQLite буфер ({temp_db_name})...")
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("DROP TABLE IF EXISTS temp_cometa_conclusions_summary_data;")
        
        create_table_sql = """
        CREATE TABLE temp_cometa_conclusions_summary_data (
            accession_number TEXT,
            study_uid TEXT,
            doc_date TEXT,
            assignment_result_doc_created_date_plus_3h TEXT,
            assignment_result_emp_fio TEXT,
            payment_source TEXT,
            device_type TEXT,
            diagnostic_name TEXT,
            assessment_result_type_code TEXT,
            conduct_mo_name TEXT,
            conduct_mu_name TEXT,
            conduct_district_name TEXT,
            patient_gender TEXT,
            assignment_describe_mu_id TEXT,
            load_datetime TEXT,
            assignment_result_emp_id TEXT,
            conduct_datetime TEXT,
            conduct_date_only TEXT
        );
        """
        sqlite_cursor.execute(create_table_sql)
        insert_sql = f"INSERT INTO temp_cometa_conclusions_summary_data VALUES ({', '.join(['?' for _ in column_names])});"
        sqlite_cursor.executemany(insert_sql, raw_rows)
        sqlite_conn.commit()
        sqlite_conn.close()
        print(f"✅ SQLite буфер заполнен.")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при работе с исходной ClickHouse или SQLite: {e}"
        print(error_msg)
        send_ntfy_alert(f"Сбой синхронизации: {str(e)[:80]}", title="Cometa Sync Error", priority="urgent", tags="fire")
        if client_source:
            client_source.close()
        try:
            if os.path.exists(temp_db_name):
                os.remove(temp_db_name)
        except:
            pass
        try:
            disconnect_vpn()
        except:
            pass
        return False
    
    # --- Шаг 5: Отключение VPN ---
    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f"⚠️ Ошибка отключения VPN: {e}", title="VPN Warning", priority="default", tags="warning")
    
    # --- Шаг 6: Вставка в целевую базу ---
    client_target = None
    max_retries_target = 3
    retry_count_target = 0
    success_target = False
    
    while retry_count_target < max_retries_target and not success_target:
        try:
            print(f"🔌 Подключаюсь к целевой ClickHouse, попытка {retry_count_target + 1}...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False
            )
            print("✅ Целевая ClickHouse подключена.")
            success_target = True
        except Exception as e:
            retry_count_target += 1
            error_msg_conn = f"❌ Ошибка подключения к целевой ClickHouse (попытка {retry_count_target}): {e}"
            print(error_msg_conn)
            send_ntfy_alert(f"Ошибка подключения к БД: {str(e)[:60]}", title="DB Connect Error", priority="urgent", tags="database")
            if retry_count_target < max_retries_target:
                print("⏳ Ждем 5 секунд перед повторной попыткой...")
                time.sleep(5)
            else:
                print("❌ Все попытки подключения исчерпаны.")
                break
    
    if not success_target:
        error_msg_no_conn = "❌ Не удалось подключиться к целевой ClickHouse."
        print(error_msg_no_conn)
        send_ntfy_alert(error_msg_no_conn, title="DB Connect Failed", priority="urgent", tags="warning")
        try:
            if os.path.exists(temp_db_name):
                os.remove(temp_db_name)
        except:
            pass
        return False
    
    try:
        print(f"📤 Загружаю данные из SQLite в целевую ClickHouse...")
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM temp_cometa_conclusions_summary_data;")
        sqlite_rows = sqlite_cursor.fetchall()
        total_rows = len(sqlite_rows)
        print(f"   Прочитано {total_rows} строк из SQLite.")
        sqlite_conn.close()
        
        # Обработка типов данных
        processed_column_names = [
            'accession_number', 'study_uid',
            'doc_date', 'assignment_result_doc_created_date_plus_3h', 'assignment_result_emp_fio', 'payment_source',
            'device_type', 'diagnostic_name', 'assessment_result_type_code',
            'conduct_mo_name', 'conduct_mu_name', 'conduct_district_name',
            'patient_gender', 'assignment_describe_mu_id',
            'load_datetime', 'assignment_result_emp_id',
            'conduct_datetime', 'conduct_date_only'
        ]
        
        processed_rows = []
        for row in sqlite_rows:
            processed_row = []
            for i, value in enumerate(row):
                col_name = processed_column_names[i]
                
                if col_name in ['accession_number', 'study_uid']:
                    processed_value = value if value is not None else ''
                elif col_name in ['doc_date', 'conduct_date_only']:
                    if value is not None:
                        try:
                            processed_value = datetime.strptime(value, '%Y-%m-%d').date()
                        except ValueError:
                            processed_value = datetime(1900, 1, 1).date()
                    else:
                        processed_value = None
                elif col_name in [
                    'assignment_result_emp_fio', 'payment_source', 'device_type',
                    'diagnostic_name', 'assessment_result_type_code', 'conduct_mo_name',
                    'conduct_mu_name', 'conduct_district_name', 'patient_gender',
                    'assignment_describe_mu_id', 'assignment_result_emp_id'
                ]:
                    processed_value = value if value is not None else ''
                elif col_name in ['load_datetime', 'conduct_datetime', 'assignment_result_doc_created_date_plus_3h']:
                    if value is not None:
                        if isinstance(value, str):
                            try:
                                processed_value = datetime.fromisoformat(value.replace('Z', '+00:00'))
                            except ValueError:
                                try:
                                    from dateutil import parser
                                    processed_value = parser.isoparse(value)
                                except ImportError:
                                    print(f"⚠️ Неизвестный формат DateTime для '{col_name}': '{value}'")
                                    processed_value = None
                                except ValueError:
                                    print(f"⚠️ Неизвестный формат DateTime для '{col_name}': '{value}'")
                                    processed_value = None
                        else:
                            processed_value = value
                    else:
                        processed_value = None
                else:
                    processed_value = value
                processed_row.append(processed_value)
            processed_rows.append(processed_row)
        
        table_name = 'cometa_conclusions_summary'
        print(f"📤 Загружаю {len(processed_rows)} строк в {CH_DATABASE_TARGET}.{table_name}...")
        client_target.insert(table_name, processed_rows, column_names=processed_column_names)
        
        success_msg = f"✅ Синхронизировано {len(processed_rows)} строк за {DAYS_TO_SYNC} дней."
        print(success_msg)
        send_ntfy_alert(success_msg, title="Cometa Sync Success", priority="high", tags="white_check_mark")
        
        client_target.close()
        client_target = None
        
        try:
            if os.path.exists(temp_db_name):
                os.remove(temp_db_name)
            print(f"🧹 Временный файл {temp_db_name} удалён.")
        except Exception as cleanup_e:
            print(f"⚠️ Ошибка при удалении временного файла: {cleanup_e}")
        
        return True
        
    except Exception as e:
        error_msg = f"❌ Ошибка выгрузки в целевую ClickHouse: {e}"
        print(error_msg)
        send_ntfy_alert(f"Ошибка выгрузки: {str(e)[:80]}", title="Cometa Insert Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        try:
            if os.path.exists(temp_db_name):
                os.remove(temp_db_name)
        except:
            pass
        return False


# === Основная точка входа ===
def main():
    """Основная функция"""
    print(f"🚀 Запуск синхронизации cometa_conclusions_summary (последние {DAYS_TO_SYNC} дней)")
    send_ntfy_alert(
        f"Запускаю синхронизацию Cometa за {DAYS_TO_SYNC} дней...",
        title="Dashbord Start",
        priority="default",
        tags="robot",
        
    )
    
    success = export_cometa_conclusions_summary()
    
    # Финальные уведомления
    if success:
        send_ntfy_alert(
            f"✅ Данные для потока исследований (Таблица Волошина) выгружены успешно!",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )
        print("🏁 Синхронизация завершена успешно.")
    else:
        send_ntfy_alert(
            "❌ Данные для потока исследований (Таблица Волошина) выгружены с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )
        print("❌ Синхронизация завершена с ошибками.")
    
    return success


def extract_phase():
    """Фаза 1 (VPN включён): source ClickHouse → SQLite буфер."""
    print(f"📥 [komet] extract_phase: запрос к source ClickHouse за последние {DAYS_TO_SYNC} дней...")
    setup_sqlite_adapters()
    today = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)

    client_source = None
    try:
        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999
        )
        base_query = """
    SELECT DISTINCT
    t2.accession_number, t2.study_uid,
    toDate(assignment_result_doc_created_date_moscow) AS doc_date,
    assignment_result_doc_created_date_moscow AS assignment_result_doc_created_date_plus_3h,
    t2.assignment_result_emp_fio, t2.payment_source, t2.device_type, t2.diagnostic_name,
    t2.assessment_result_type_code, t2.conduct_mo_name, t2.conduct_mu_name, t2.conduct_district_name,
    t2.patient_gender, t2.assignment_describe_mu_id,
    now() AS load_datetime,
    toString(t2.assignment_result_emp_id) AS assignment_result_emp_id,
    conduct_datetime_moscow AS conduct_datetime,
    toDate(conduct_datetime_moscow) AS conduct_date_only
FROM (
    SELECT *,
        toTimeZone(toDateTime(assignment_result_doc_created_date), 'Europe/Moscow') AS assignment_result_doc_created_date_moscow,
        toTimeZone(toDateTime(conduct_date), 'Europe/Moscow') AS conduct_datetime_moscow
    FROM data_views.v_eris_assignment_results
) t2
LEFT JOIN data_views.v_instrumental_examinations ie ON t2.accession_number = ie.accession_number
LEFT JOIN data_views.v_route_eris_trauma trauma_res ON t2.accession_number = trauma_res.accession_number AND trauma_res.accession_number != ''
LEFT JOIN data_views.v_task_pin pin_res ON t2.accession_number = pin_res.accession_number AND pin_res.accession_number != ''
WHERE t2.accession_number != '' AND assessment_result_type_code = 1 AND t2.assignment_describe_mu_id = 100001912827
        """
        date_clause = f"  AND toDate((t2.assignment_result_doc_created_date + INTERVAL 3 HOUR)) >= '{n_days_ago.isoformat()}' AND toDate(t2.assignment_result_doc_created_date + INTERVAL 3 HOUR) <= '{today.isoformat()}'"
        final_query = base_query + date_clause
        result = client_source.query(final_query)
        raw_rows = result.result_rows
        client_source.close()
        client_source = None

        if not raw_rows:
            print(f"  ⚠️ [komet] extract_phase: нет данных за {DAYS_TO_SYNC} дней.")
            open(_BUFFER_KOMET, 'w').close()
            return True

        sqlite_conn = sqlite3.connect(_BUFFER_KOMET)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("DROP TABLE IF EXISTS temp_cometa_conclusions_summary_data;")
        sqlite_cursor.execute("""
            CREATE TABLE temp_cometa_conclusions_summary_data (
                accession_number TEXT, study_uid TEXT, doc_date TEXT,
                assignment_result_doc_created_date_plus_3h TEXT, assignment_result_emp_fio TEXT,
                payment_source TEXT, device_type TEXT, diagnostic_name TEXT,
                assessment_result_type_code TEXT, conduct_mo_name TEXT, conduct_mu_name TEXT,
                conduct_district_name TEXT, patient_gender TEXT, assignment_describe_mu_id TEXT,
                load_datetime TEXT, assignment_result_emp_id TEXT, conduct_datetime TEXT, conduct_date_only TEXT
            );
        """)
        sqlite_cursor.executemany(f"INSERT INTO temp_cometa_conclusions_summary_data VALUES ({','.join(['?']*18)});", raw_rows)
        sqlite_conn.commit()
        sqlite_conn.close()
        print(f"  ✅ [komet] extract_phase: сохранено {len(raw_rows)} строк в буфер.")
        return True
    except Exception as e:
        print(f"  ❌ [komet] extract_phase: {e}")
        if client_source:
            client_source.close()
        return False


def load_phase():
    """Фаза 2 (VPN выключен): DELETE target CH + load from SQLite буфер."""
    print(f"📤 [komet] load_phase: загрузка из буфера в target ClickHouse...")
    setup_sqlite_adapters()
    today = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)

    if not os.path.exists(_BUFFER_KOMET):
        print("  ❌ [komet] load_phase: буфер не найден.")
        return False

    client_target = None
    try:
        client_target = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )
        client_target.command(f"ALTER TABLE cometa_conclusions_summary DELETE WHERE doc_date >= '{n_days_ago.isoformat()}'")
        print(f"  ✅ [komet] load_phase: DELETE выполнен для дат >= {n_days_ago.isoformat()}.")

        sqlite_conn = sqlite3.connect(_BUFFER_KOMET)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM temp_cometa_conclusions_summary_data;")
        sqlite_rows = sqlite_cursor.fetchall()
        sqlite_conn.close()

        if not sqlite_rows:
            print("  ⚠️ [komet] load_phase: буфер пуст, вставка не требуется.")
            client_target.close()
            return True

        processed_rows = []
        for row in sqlite_rows:
            processed_row = []
            for i, value in enumerate(row):
                col = _KOMET_COLUMNS[i]
                if col in ['accession_number', 'study_uid']:
                    processed_row.append(value if value is not None else '')
                elif col in ['doc_date', 'conduct_date_only']:
                    if value is not None:
                        try:
                            processed_row.append(datetime.strptime(value, '%Y-%m-%d').date())
                        except ValueError:
                            processed_row.append(datetime(1900, 1, 1).date())
                    else:
                        processed_row.append(None)
                elif col in ['assignment_result_emp_fio', 'payment_source', 'device_type', 'diagnostic_name',
                              'assessment_result_type_code', 'conduct_mo_name', 'conduct_mu_name',
                              'conduct_district_name', 'patient_gender', 'assignment_describe_mu_id',
                              'assignment_result_emp_id']:
                    processed_row.append(value if value is not None else '')
                elif col in ['load_datetime', 'conduct_datetime', 'assignment_result_doc_created_date_plus_3h']:
                    if value is not None and isinstance(value, str):
                        try:
                            processed_row.append(datetime.fromisoformat(value.replace('Z', '+00:00')))
                        except ValueError:
                            processed_row.append(None)
                    else:
                        processed_row.append(value)
                else:
                    processed_row.append(value)
            processed_rows.append(processed_row)

        client_target.insert('cometa_conclusions_summary', processed_rows, column_names=_KOMET_COLUMNS)
        client_target.close()
        print(f"  ✅ [komet] load_phase: вставлено {len(processed_rows)} строк.")
        return True
    except Exception as e:
        print(f"  ❌ [komet] load_phase: {e}")
        if client_target:
            client_target.close()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)