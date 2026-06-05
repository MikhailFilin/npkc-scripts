"""
cometa_conclusions_summary_new.py
Синхронизация данных cometa_conclusions_summary_new из исходной ClickHouse в целевую
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
CH_DATABASE_TARGET = cfg.CH_DATABASE_DIT

# === Настройки исходной ClickHouse базы ===
CH_HOST_SOURCE = cfg.CH_HOST
CH_PORT_SOURCE = cfg.CH_PORT
CH_USER_SOURCE = cfg.CH_USER
CH_PASSWORD_SOURCE = cfg.CH_PASSWORD
CH_DATABASE_SOURCE = cfg.CH_DATABASE

# === Настройки синхронизации (плавающие даты) ===
DAYS_TO_SYNC = 20

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


def _kill_vpn_process():
    result = subprocess.run(['taskkill', '/F', '/IM', 'TrGUI.exe'], capture_output=True, text=True)
    if result.returncode == 0:
        print("   🔪 TrGUI процесс завершён.")
    time.sleep(2)


def _ensure_caps_off():
    import ctypes
    if ctypes.WinDLL("User32.dll").GetKeyState(0x14) & 1:
        print("   ⚠️ Caps Lock включён — выключаю.")
        pyautogui.press('capslock')
        time.sleep(0.1)


def _check_vpn_connectivity() -> bool:
    try:
        client = clickhouse_connect.get_client(
            host=cfg.CH_HOST, port=cfg.CH_PORT,
            username=cfg.CH_USER, password=cfg.CH_PASSWORD,
            database=cfg.CH_DATABASE, secure=True, verify=False,
            connect_timeout=10, send_receive_timeout=10,
        )
        client.query("SELECT 1")
        client.close()
        print("   ✅ VPN проверка: ClickHouse отвечает.")
        return True
    except Exception as e:
        print(f"   ❌ VPN проверка: ClickHouse не отвечает — {e}")
        return False


# === Функция: Подключение к VPN ===
def connect_vpn():
    MAX_ATTEMPTS = 3
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"🔄 Запускаю TrGUI (попытка {attempt}/{MAX_ATTEMPTS})...")
        send_ntfy_alert(
            f"Подключаю VPN (попытка {attempt}/{MAX_ATTEMPTS})...",
            title="VPN Connect", priority="default", tags="lock",
        )

        _kill_vpn_process()

        try:
            process = subprocess.Popen(VPN_APP_PATH)
            print(f"   PID: {process.pid}")
        except Exception as e:
            err = f"❌ Ошибка запуска TrGUI: {e}"
            print(err)
            if attempt == MAX_ATTEMPTS:
                send_ntfy_alert(err, title="VPN Fatal", priority="urgent", tags="fire")
                raise RuntimeError(err)
            time.sleep(30)
            continue

        time.sleep(15)

        for window in pyautogui.getWindowsWithTitle(''):
            if any(kw in window.title.lower() for kw in ['check point', 'trgui', 'endpoint']):
                try:
                    window.activate()
                    time.sleep(2)
                    print(f"   ✅ Окно '{window.title}' активировано.")
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
        _ensure_caps_off()
        for char in VPN_PASSWORD:
            pyautogui.write(char)
            time.sleep(0.1)
        time.sleep(1)
        pyautogui.click(CONNECT_BUTTON_X, CONNECT_BUTTON_Y)

        print("   ⏳ Ждём подключения VPN (20 сек)...")
        time.sleep(20)

        if _check_vpn_connectivity():
            print(f"✅ VPN подключён и проверен (попытка {attempt}).")
            send_ntfy_alert(f"VPN подключён (попытка {attempt})", title="VPN Connected", priority="high", tags="key")
            return

        warn = f"⚠️ VPN попытка {attempt}/{MAX_ATTEMPTS}: CH не отвечает"
        print(warn)
        send_ntfy_alert(warn, title="VPN Retry", priority="default", tags="warning")

        if attempt < MAX_ATTEMPTS:
            try:
                disconnect_vpn()
            except Exception:
                _kill_vpn_process()
            print(f"   ⏳ Жду 30 сек перед попыткой {attempt + 1}...")
            time.sleep(30)

    fatal = f"❌ VPN не подключился после {MAX_ATTEMPTS} попыток — прерываю"
    print(fatal)
    send_ntfy_alert(fatal, title="VPN Fatal", priority="urgent", tags="fire")
    raise RuntimeError(fatal)


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
    Загрузка данных за последние N дней из v_eris_assignment_results и v_route_instrumental
    в новую ClickHouse базу через SQLite буфер.
    Returns:
        bool: True если успешно, False если ошибка
    """
    print(f"📊 Загрузка cometa_conclusions_summary_new за последние {DAYS_TO_SYNC} дней...")
    send_ntfy_alert(
        f"Начинаю синхронизацию Cometa New за {DAYS_TO_SYNC} дней...",
        title="Cometa New Sync Start",
        priority="default",
        tags="inbox"
    )
    
    # Исправление DeprecationWarning для SQLite
    setup_sqlite_adapters()
    
    # Вычисляем даты
    today = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)
    print(f"   Синхронизация за период: {n_days_ago} - {today}")
    
    # --- Шаг 1: Подключение к целевой базе и подготовка таблицы ---
    client_target = None
    temp_db_name = "temp_buffer_cometa_new.db"
    
    try:
        print("🔌 Подключаюсь к целевой базе...")
        client_target = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )
        print("✅ Целевая ClickHouse подключена.")
        
        table_name = 'cometa_conclusions_summary_new'
        
        # Проверяем существование таблицы
        check_table_query = f"EXISTS TABLE {table_name}"
        table_exists_result = client_target.query(check_table_query)
        table_exists = table_exists_result.result_rows[0][0] if table_exists_result.result_rows else 0
        
        if not table_exists:
            print(f"🔍 Таблица {table_name} не найдена. Создаю таблицу...")
            create_table_query = f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                assessment_result_type_code String,
                assignment_result_doc_created_date DateTime,
                patient_id String,
                accession_number String,
                study_uid String,
                diagnostic_code String,
                diagnostic_name String,
                device_type String,
                conduct_mu_id String,
                assignment_result_emp_id String,
                assignment_result_emp_fio String,
                technician_id String,
                technician_fio String,
                descr_result_description String,
                descr_result_conclusion String,
                patient_birth_date Nullable(String),
                load_datetime DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            ORDER BY (accession_number, assignment_result_doc_created_date);
            """
            client_target.command(create_table_query)
            print(f"✅ Таблица {table_name} создана.")
        else:
            print(f"✅ Таблица {table_name} уже существует.")
        
        # Удаляем только последние DAYS_TO_SYNC дней — старые данные остаются
        start_date_del = (today - timedelta(days=DAYS_TO_SYNC)).strftime('%Y-%m-%d')
        print(f"🧹 Удаляю данные за последние {DAYS_TO_SYNC} дней (с {start_date_del}) из {table_name}...")
        delete_query = f"ALTER TABLE {table_name} DELETE WHERE toDate(assignment_result_doc_created_date) >= '{start_date_del}'"
        client_target.command(delete_query)
        print(f"✅ Данные за последние {DAYS_TO_SYNC} дней удалены из {table_name}.")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при создании или очистке таблицы: {e}"
        print(error_msg)
        send_ntfy_alert(f"Ошибка подготовки БД: {str(e)[:80]}", title="DB Setup Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        return False
    
    # --- Шаг 2: Подключение к исходной базе через VPN ---
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
        
        # --- Шаг 3: Формируем запрос ---
        start_date_str = n_days_ago.strftime('%Y-%m-%d')
        end_date_str = today.strftime('%Y-%m-%d')
        
        base_query = f"""
    SELECT DISTINCT
    a.assessment_result_type_code,
    a.assignment_result_doc_created_date,
    a.patient_id,
    a.accession_number,
    a.study_uid,
    a.diagnostic_code,
    a.diagnostic_name,
    a.device_type,
    a.conduct_mu_id,
    a.assignment_result_emp_id,
    a.assignment_result_emp_fio,
    a.technician_id,
    a.technician_fio,
    r.descr_result_description,
    r.descr_result_conclusion,
    if(isNull(a.patient_birth_date), NULL, left(toString(a.patient_birth_date), 10)) AS patient_birth_date,
    now() AS load_datetime
FROM
    data_views.v_eris_assignment_results AS a
INNER JOIN
    data_views.v_route_instrumental AS r
    ON a.accession_number = r.eris_id and r.descr_result_doc_id = a.assignment_result_doc_id
WHERE
    a.assessment_result_type_code = 1
    AND a.accession_number != ''
    AND a.accession_number IS NOT NULL
    AND a.assignment_result_doc_created_date >= toDate('{start_date_str}')
    AND a.assignment_result_doc_created_date <= toDate('{end_date_str}')
    AND r.descr_result_doctor_job_name != 'ERISPUM'
        """
        
        print("📥 Выполняю запрос к исходной ClickHouse...")
        result = client_source.query(base_query)
        raw_rows = result.result_rows
        column_names = result.column_names
        
        client_source.close()
        client_source = None
        print(f"📥 Получено {len(raw_rows)} строк данных.")
        
        # Если данных нет
        if not raw_rows:
            print(f"📭 Нет данных для синхронизации.")
            send_ntfy_alert(f"Нет данных для синхронизации Cometa New за {DAYS_TO_SYNC} дней", title="Cometa New Empty", priority="default", tags="inbox")
            disconnect_vpn()
            if client_target:
                client_target.close()
            return True
        
        # --- Шаг 4: Создание и заполнение SQLite буфера ---
        print(f"💾 Создаю SQLite буфер ({temp_db_name})...")
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("DROP TABLE IF EXISTS temp_cometa_conclusions_summary_new_data;")
        
        create_table_sql = """
        CREATE TABLE temp_cometa_conclusions_summary_new_data (
            assessment_result_type_code TEXT,
            assignment_result_doc_created_date TEXT,
            patient_id TEXT,
            accession_number TEXT,
            study_uid TEXT,
            diagnostic_code TEXT,
            diagnostic_name TEXT,
            device_type TEXT,
            conduct_mu_id TEXT,
            assignment_result_emp_id TEXT,
            assignment_result_emp_fio TEXT,
            technician_id TEXT,
            technician_fio TEXT,
            descr_result_description TEXT,
            descr_result_conclusion TEXT,
            patient_birth_date TEXT,
            load_datetime TEXT
        );
        """
        sqlite_cursor.execute(create_table_sql)
        insert_sql = f"INSERT INTO temp_cometa_conclusions_summary_new_data VALUES ({', '.join(['?' for _ in column_names])});"
        sqlite_cursor.executemany(insert_sql, raw_rows)
        sqlite_conn.commit()
        sqlite_conn.close()
        print(f"✅ SQLite буфер заполнен.")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при работе с исходной ClickHouse или SQLite: {e}"
        print(error_msg)
        send_ntfy_alert(f"Сбой синхронизации: {str(e)[:80]}", title="Cometa New Sync Error", priority="urgent", tags="fire")
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
        if client_target:
            client_target.close()
        return False
    
    # --- Шаг 5: Отключение VPN ---
    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f"⚠️ Ошибка отключения VPN: {e}", title="VPN Warning", priority="default", tags="warning")
    
    # --- Шаг 6: Вставка в целевую базу ---
    try:
        print(f"📤 Загружаю данные из SQLite в целевую ClickHouse...")
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM temp_cometa_conclusions_summary_new_data;")
        sqlite_rows = sqlite_cursor.fetchall()
        total_rows = len(sqlite_rows)
        print(f"   Прочитано {total_rows} строк из SQLite.")
        sqlite_conn.close()
        
        # Обработка типов данных
        processed_column_names = [
            'assessment_result_type_code', 'assignment_result_doc_created_date',
            'patient_id', 'accession_number', 'study_uid', 'diagnostic_code',
            'diagnostic_name', 'device_type', 'conduct_mu_id',
            'assignment_result_emp_id', 'assignment_result_emp_fio',
            'technician_id', 'technician_fio', 'descr_result_description',
            'descr_result_conclusion', 'patient_birth_date', 'load_datetime'
        ]
        
        processed_rows = []
        for row in sqlite_rows:
            processed_row = []
            for i, value in enumerate(row):
                col_name = processed_column_names[i]
                
                if col_name in [
                    'assessment_result_type_code', 'patient_id', 'accession_number',
                    'study_uid', 'diagnostic_code', 'diagnostic_name', 'device_type',
                    'conduct_mu_id', 'assignment_result_emp_id', 'assignment_result_emp_fio',
                    'technician_id', 'technician_fio', 'descr_result_description',
                    'descr_result_conclusion'
                ]:
                    processed_value = value if value is not None else ''
                elif col_name == 'patient_birth_date':
                    # Nullable(String): хранить как 'YYYY-MM-DD' или None
                    # Нельзя использовать Date — даты рождения до 1970 ломают struct.error
                    processed_value = value if value else None
                elif col_name in ['assignment_result_doc_created_date', 'load_datetime']:
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
        
        print(f"📤 Загружаю {len(processed_rows)} строк в {CH_DATABASE_TARGET}.{table_name}...")
        client_target.insert(table_name, processed_rows, column_names=processed_column_names)
        
        success_msg = f"✅ Синхронизировано {len(processed_rows)} строк."
        print(success_msg)
        send_ntfy_alert(success_msg, title="Cometa New Sync Success", priority="high", tags="white_check_mark")
        
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
        send_ntfy_alert(f"Ошибка выгрузки: {str(e)[:80]}", title="Cometa New Insert Error", priority="urgent", tags="database")
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
    print(f"🚀 Запуск синхронизации cometa_conclusions_summary_new (последние {DAYS_TO_SYNC} дней)")
    send_ntfy_alert(
        f"Запускаю синхронизацию Cometa New за {DAYS_TO_SYNC} дней...",
        title="Dashbord Start",
        priority="default",
        tags="robot"
    )
    
    success = export_cometa_conclusions_summary()
    
    # Финальные уведомления
    if success:
        send_ntfy_alert(
            f"✅ Данные для АРИ выгружены успешно!",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )
        print("🏁 Синхронизация завершена успешно.")
    else:
        send_ntfy_alert(
            "❌ Данные для АРИ выгружены  с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )
        print("❌ Синхронизация завершена с ошибками.")
    
    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)