"""
cometa_conclusions_summary_new.py
Синхронизация данных cometa_conclusions_summary_new из исходной ClickHouse в целевую
через SQLite-буфер с плавающими датами.
Все уведомления отправляются через ntfy.
"""

import argparse
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

# === Битрикс24 ===
BITRIX_WEBHOOK     = cfg.BITRIX_WEBHOOK
_BX_LOG_DIALOG     = "chat145691"
_BX_SUMMARY_DIALOG = "chat145721"
_BX_DASH_NAME      = "АРИ — Описания ЕРИС (cometa_conclusions_summary_new)"
_BX_DASH_URL       = ""   # отдельного дашборда нет

_run_log: list = []


def _rlog(msg: str) -> None:
    print(msg)
    _run_log.append(msg)


def _bx_send(dialog_id: str, text: str) -> None:
    url = f"{BITRIX_WEBHOOK.rstrip('/')}/im.message.add.json"
    chunks = [text[i:i + 3900] for i in range(0, max(len(text), 1), 3900)]
    for chunk in chunks:
        try:
            resp = requests.post(url, json={"DIALOG_ID": dialog_id, "MESSAGE": chunk}, timeout=30)
            if not resp.ok:
                print(f"⚠️ Битрикс [{dialog_id}] {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"⚠️ Битрикс [{dialog_id}]: {e}")


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
CHUNK_SIZE = 2000

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
    _rlog(f"📊 Загрузка cometa_conclusions_summary_new за последние {DAYS_TO_SYNC} дней...")
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
        _rlog("🔌 Подключаюсь к целевой базе...")
        client_target = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )
        _rlog("✅ Целевая ClickHouse подключена.")
        
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
                ae_title String,
                conduct_date Nullable(DateTime),
                patient_gender String,
                patient_birth_date Nullable(String),
                load_datetime DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            ORDER BY (accession_number, assignment_result_doc_created_date);
            """
            client_target.command(create_table_query)
            print(f"✅ Таблица {table_name} создана.")
        else:
            print(f"✅ Таблица {table_name} уже существует.")
            client_target.command(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS ae_title String")
            client_target.command(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS conduct_date Nullable(DateTime)")
            client_target.command(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS patient_gender String")
        
        # Удаляем только последние DAYS_TO_SYNC дней — старые данные остаются
        start_date_del = (today - timedelta(days=DAYS_TO_SYNC)).strftime('%Y-%m-%d')
        _rlog(f"🧹 Удаляю данные за последние {DAYS_TO_SYNC} дней (с {start_date_del}) из {table_name}...")
        delete_query = f"ALTER TABLE {table_name} DELETE WHERE toDate(assignment_result_doc_created_date) >= '{start_date_del}'"
        client_target.command(delete_query)
        _rlog(f"✅ Данные за последние {DAYS_TO_SYNC} дней удалены из {table_name}.")
        client_target.close()
        client_target = None

    except Exception as e:
        error_msg = f"❌ Ошибка при создании или очистке таблицы: {e}"
        _rlog(error_msg)
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
        _rlog("✅ Исходная ClickHouse подключена.")

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
    a.ae_title,
    a.conduct_date,
    a.patient_gender,
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
    AND r.descr_result_description IS NOT NULL AND r.descr_result_description != ''
    AND r.descr_result_conclusion  IS NOT NULL AND r.descr_result_conclusion  != ''
        """
        
        _rlog("📥 Выполняю запрос к исходной ClickHouse...")
        result = client_source.query(base_query)
        raw_rows = result.result_rows
        column_names = result.column_names

        client_source.close()
        client_source = None
        _rlog(f"📥 Получено {len(raw_rows)} строк данных.")

        # Если данных нет
        if not raw_rows:
            _rlog("📭 Нет данных для синхронизации.")
            send_ntfy_alert(f"Нет данных для синхронизации Cometa New за {DAYS_TO_SYNC} дней", title="Cometa New Empty", priority="default", tags="inbox")
            disconnect_vpn()
            if client_target:
                client_target.close()
            return True
        
        # --- Шаг 4: Создание и заполнение SQLite буфера ---
        _rlog(f"💾 Создаю SQLite буфер ({temp_db_name})...")
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
            ae_title TEXT,
            conduct_date TEXT,
            patient_gender TEXT,
            patient_birth_date TEXT,
            load_datetime TEXT
        );
        """
        sqlite_cursor.execute(create_table_sql)
        insert_sql = f"INSERT INTO temp_cometa_conclusions_summary_new_data VALUES ({', '.join(['?' for _ in column_names])});"
        sqlite_cursor.executemany(insert_sql, raw_rows)
        sqlite_conn.commit()
        sqlite_conn.close()
        _rlog("✅ SQLite буфер заполнен.")

    except Exception as e:
        error_msg = f"❌ Ошибка при работе с исходной ClickHouse или SQLite: {e}"
        _rlog(error_msg)
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
    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"🔌 Подключаюсь к целевой базе для вставки (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
                send_receive_timeout=600, connect_timeout=30,
            )
            break
        except Exception as e:
            print(f"   Попытка {attempt}: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                send_ntfy_alert(f"Ошибка подключения к target CH: {str(e)[:60]}",
                                title="Cometa New Connect Error", priority="urgent", tags="database")
                if os.path.exists(temp_db_name):
                    try: os.remove(temp_db_name)
                    except Exception: pass
                return False

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
            'descr_result_conclusion', 'ae_title', 'conduct_date', 'patient_gender', 'patient_birth_date', 'load_datetime'
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
                    'descr_result_conclusion', 'ae_title', 'patient_gender'
                ]:
                    processed_value = value if value is not None else ''
                elif col_name == 'patient_birth_date':
                    # Nullable(String): хранить как 'YYYY-MM-DD' или None
                    # Нельзя использовать Date — даты рождения до 1970 ломают struct.error
                    processed_value = value if value else None
                elif col_name in ['assignment_result_doc_created_date', 'conduct_date', 'load_datetime']:
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
        
        total = len(processed_rows)
        _rlog(f"📤 Загружаю {total} строк в {CH_DATABASE_TARGET}.{table_name} (батчи по {CHUNK_SIZE})...")
        for i in range(0, total, CHUNK_SIZE):
            chunk = processed_rows[i:i + CHUNK_SIZE]
            client_target.insert(table_name, chunk, column_names=processed_column_names)
            print(f"   Загружено {min(i + CHUNK_SIZE, total)}/{total} строк.")

        success_msg = f"✅ Синхронизировано {total} строк."
        _rlog(success_msg)
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
        _rlog(error_msg)
        send_ntfy_alert(f"Ошибка выгрузки: {str(e)[:80]}", title="Cometa New Insert Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        try:
            if os.path.exists(temp_db_name):
                os.remove(temp_db_name)
        except:
            pass
        return False


# === Основная функция: Экспорт результатов ИИ-сервисов через SQLite буфер ===
def export_ai_results(start_date_override: str = None):
    """
    Загрузка результатов ИИ-сервисов (dwh_views.v_eris_report, app_source='CDS')
    в ari_ai_results через SQLite буфер. Требует уже подключённый VPN (см. main()).

    start_date_override: если задано (например '2026-01-01') — грузит с этой даты
    (разовый бэкфилл), иначе — плавающее окно последних DAYS_TO_SYNC дней.
    """
    table_name = 'ari_ai_results'
    today = datetime.now().date()

    if start_date_override:
        start_date_str = start_date_override
        _rlog(f"📊 Загрузка {table_name}: бэкфилл с {start_date_str}...")
    else:
        start_date_str = (today - timedelta(days=DAYS_TO_SYNC)).strftime('%Y-%m-%d')
        _rlog(f"📊 Загрузка {table_name} за последние {DAYS_TO_SYNC} дней (с {start_date_str})...")

    send_ntfy_alert(
        f"Начинаю синхронизацию {table_name} с {start_date_str}...",
        title="AI Results Sync Start",
        priority="default",
        tags="inbox"
    )

    # --- Шаг 1: Подключение к целевой базе и подготовка таблицы ---
    client_target = None
    temp_db_name = "temp_buffer_ari_ai_results.db"

    try:
        _rlog("🔌 Подключаюсь к целевой базе (ИИ)...")
        client_target = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )
        _rlog("✅ Целевая ClickHouse подключена (ИИ).")

        check_table_query = f"EXISTS TABLE {table_name}"
        table_exists_result = client_target.query(check_table_query)
        table_exists = table_exists_result.result_rows[0][0] if table_exists_result.result_rows else 0

        if not table_exists:
            print(f"🔍 Таблица {table_name} не найдена. Создаю таблицу...")
            create_table_query = f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                study_uid String,
                model_id Int32,
                pathology_probability Int32,
                norma String,
                pathology_flag UInt8,
                pum_study_ready_for_ai_time Nullable(DateTime),
                load_datetime DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            ORDER BY (study_uid, model_id);
            """
            client_target.command(create_table_query)
            print(f"✅ Таблица {table_name} создана.")
        else:
            print(f"✅ Таблица {table_name} уже существует.")

        # Плавающее окно: удаляем только загружаемый период — старые данные остаются
        _rlog(f"🧹 Удаляю данные {table_name} с {start_date_str}...")
        delete_query = (
            f"ALTER TABLE {table_name} DELETE "
            f"WHERE toDate(pum_study_ready_for_ai_time) >= '{start_date_str}'"
        )
        client_target.command(delete_query)
        _rlog(f"✅ Данные {table_name} за период удалены.")
        client_target.close()
        client_target = None

    except Exception as e:
        error_msg = f"❌ Ошибка при создании или очистке {table_name}: {e}"
        _rlog(error_msg)
        send_ntfy_alert(f"Ошибка подготовки {table_name}: {str(e)[:80]}", title="AI Results DB Error", priority="urgent", tags="database")
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
        _rlog("✅ Исходная ClickHouse подключена (ИИ).")

        # --- Шаг 3: Формируем запрос ---
        # GROUP BY studyIUID, modelId — на одно исследование+модель бывает несколько
        # kafka-событий. dwh_views.v_eris_report построена с FINAL, поэтому GROUP BY
        # требует полного скана периода — этим и объясняется большой send_receive_timeout.
        base_query = f"""
        SELECT
            studyIUID,
            modelId,
            min(confidenceLevel_raw) AS pathology_probability,
            arrayFirst(x -> x != '', groupUniqArray(norma_raw)) AS norma,
            arrayFirst(x -> x != false, groupUniqArray(pathologyFlag_raw)) AS pathologyFlag,
            min(pum_ready_dt) AS pum_study_ready_for_ai_time
        FROM (
            SELECT
                if(raw_data = 'null',
                    JSONExtractString(computed_data, 'studyUid'),
                    JSONExtractString(raw_data, 'studyIUID')
                ) AS studyIUID,
                if(raw_data = 'null',
                    JSONExtractInt(computed_data, 'modelId'),
                    JSONExtractInt(raw_data, 'aiResult', 'modelId')
                ) AS modelId,
                JSONExtractInt(raw_data, 'aiResult', 'confidenceLevel') AS confidenceLevel_raw,
                JSONExtractString(raw_data, 'aiResult', 'norma') AS norma_raw,
                JSONExtractBool(raw_data, 'aiResult', 'pathologyFlag') AS pathologyFlag_raw,
                parseDateTimeBestEffortOrNull(
                    replaceRegexpAll(JSONExtractString(computed_data, 'pumStudyReadyForAiTime'), ':(60)(\\.\\d+)?', ':59.000')
                ) AS pum_ready_dt
            FROM dwh_views.v_eris_report
            WHERE app_source = 'CDS'
              AND create_date >= '{start_date_str}'
        ) t
        GROUP BY studyIUID, modelId
        """

        _rlog("📥 Выполняю запрос к исходной ClickHouse (ИИ)...")
        result = client_source.query(base_query)
        raw_rows = result.result_rows
        column_names = result.column_names

        client_source.close()
        client_source = None
        _rlog(f"📥 Получено {len(raw_rows)} строк данных (ИИ).")

        if not raw_rows:
            _rlog("📭 Нет данных для синхронизации (ИИ).")
            send_ntfy_alert(f"Нет данных для синхронизации {table_name}", title="AI Results Empty", priority="default", tags="inbox")
            disconnect_vpn()
            return True

        # --- Шаг 4: Создание и заполнение SQLite буфера ---
        _rlog(f"💾 Создаю SQLite буфер ({temp_db_name})...")
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("DROP TABLE IF EXISTS temp_ari_ai_results_data;")

        create_table_sql = """
        CREATE TABLE temp_ari_ai_results_data (
            study_uid TEXT,
            model_id INTEGER,
            pathology_probability INTEGER,
            norma TEXT,
            pathology_flag INTEGER,
            pum_study_ready_for_ai_time TEXT
        );
        """
        sqlite_cursor.execute(create_table_sql)
        insert_sql = f"INSERT INTO temp_ari_ai_results_data VALUES ({', '.join(['?' for _ in column_names])});"
        sqlite_cursor.executemany(insert_sql, raw_rows)
        sqlite_conn.commit()
        sqlite_conn.close()
        _rlog("✅ SQLite буфер заполнен (ИИ).")

    except Exception as e:
        error_msg = f"❌ Ошибка при работе с исходной ClickHouse или SQLite (ИИ): {e}"
        _rlog(error_msg)
        send_ntfy_alert(f"Сбой синхронизации {table_name}: {str(e)[:80]}", title="AI Results Sync Error", priority="urgent", tags="fire")
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
        send_ntfy_alert(f"⚠️ Ошибка отключения VPN (ИИ): {e}", title="VPN Warning", priority="default", tags="warning")

    # --- Шаг 6: Вставка в целевую базу ---
    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"🔌 Подключаюсь к целевой базе для вставки ИИ (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
                send_receive_timeout=600, connect_timeout=30,
            )
            break
        except Exception as e:
            print(f"   Попытка {attempt}: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                send_ntfy_alert(f"Ошибка подключения к target CH (ИИ): {str(e)[:60]}",
                                title="AI Results Connect Error", priority="urgent", tags="database")
                if os.path.exists(temp_db_name):
                    try: os.remove(temp_db_name)
                    except Exception: pass
                return False

    try:
        print(f"📤 Загружаю данные ИИ из SQLite в целевую ClickHouse...")
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM temp_ari_ai_results_data;")
        sqlite_rows = sqlite_cursor.fetchall()
        total_rows = len(sqlite_rows)
        print(f"   Прочитано {total_rows} строк из SQLite.")
        sqlite_conn.close()

        processed_column_names = [
            'study_uid', 'model_id', 'pathology_probability', 'norma', 'pathology_flag', 'pum_study_ready_for_ai_time'
        ]

        processed_rows = []
        for row in sqlite_rows:
            processed_row = []
            for i, value in enumerate(row):
                col_name = processed_column_names[i]

                if col_name in ('study_uid', 'norma'):
                    processed_value = value if value is not None else ''
                elif col_name == 'pum_study_ready_for_ai_time':
                    if value is not None:
                        if isinstance(value, str):
                            try:
                                processed_value = datetime.fromisoformat(value.replace('Z', '+00:00'))
                            except ValueError:
                                try:
                                    from dateutil import parser
                                    processed_value = parser.isoparse(value)
                                except (ImportError, ValueError):
                                    print(f"⚠️ Неизвестный формат DateTime для '{col_name}': '{value}'")
                                    processed_value = None
                        else:
                            processed_value = value
                    else:
                        processed_value = None
                else:
                    processed_value = value if value is not None else 0
                processed_row.append(processed_value)
            processed_rows.append(processed_row)

        total = len(processed_rows)
        _rlog(f"📤 Загружаю {total} строк в {CH_DATABASE_TARGET}.{table_name} (батчи по {CHUNK_SIZE})...")
        for i in range(0, total, CHUNK_SIZE):
            chunk = processed_rows[i:i + CHUNK_SIZE]
            client_target.insert(table_name, chunk, column_names=processed_column_names)
            print(f"   Загружено {min(i + CHUNK_SIZE, total)}/{total} строк.")

        success_msg = f"✅ Синхронизировано {total} строк ({table_name})."
        _rlog(success_msg)
        send_ntfy_alert(success_msg, title="AI Results Sync Success", priority="high", tags="white_check_mark")

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
        error_msg = f"❌ Ошибка выгрузки в целевую ClickHouse (ИИ): {e}"
        _rlog(error_msg)
        send_ntfy_alert(f"Ошибка выгрузки {table_name}: {str(e)[:80]}", title="AI Results Insert Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        try:
            if os.path.exists(temp_db_name):
                os.remove(temp_db_name)
        except:
            pass
        return False


# === Основная точка входа ===
def main(ai_start_date: str = None):
    """
    ai_start_date: если задано (например '2026-01-01') — ari_ai_results грузится
    бэкфиллом с этой даты вместо плавающего окна DAYS_TO_SYNC (для разовой
    первоначальной загрузки; см. флаг --ai-start-date).
    """
    global _run_log
    _run_log = []
    _start = datetime.now()

    _rlog(f"🚀 Запуск синхронизации АРИ (cometa + ИИ, последние {DAYS_TO_SYNC} дней)")
    send_ntfy_alert(
        f"Запускаю синхронизацию АРИ за {DAYS_TO_SYNC} дней...",
        title="Dashbord Start", priority="default", tags="robot",
    )

    # Каждая выгрузка сама поднимает и гасит VPN вокруг запроса к source ClickHouse —
    # целевая база (Yandex Cloud) недоступна, пока поднят CheckPoint VPN, поэтому
    # общая VPN-сессия на обе выгрузки ломает подключение к target (см. feedback_clickhouse_view_final_groupby/vpn_checkpoint).
    success_cometa = export_cometa_conclusions_summary()
    success_ai = export_ai_results(start_date_override=ai_start_date)

    success = success_cometa and success_ai

    now_str = _start.strftime("%d.%m.%Y")
    dur_str = str(datetime.now() - _start).split('.')[0]

    if success:
        send_ntfy_alert("✅ Данные для АРИ выгружены успешно!",
                        title="Dashbord Done", priority="high", tags="tada",
                        topic_override="push_mrc_dashboards_7895")
        _rlog("🏁 Синхронизация завершена успешно.")
    else:
        send_ntfy_alert("❌ Данные для АРИ выгружены с ошибкой! Проверьте консоль.",
                        title="Dashbord Failed", priority="urgent", tags="warning",
                        topic_override="push_mrc_dashboards_7895")
        _rlog("❌ Синхронизация завершена с ошибками.")

    bx_status = (
        f"  {'✅' if success_cometa else '❌'}  cometa_conclusions_summary_new\n"
        f"  {'✅' if success_ai else '❌'}  ari_ai_results"
    )

    _bx_send(_BX_LOG_DIALOG, f"[ари_up] {now_str} | {dur_str}\n\n" + "\n".join(_run_log))
    _bx_send(_BX_SUMMARY_DIALOG,
             f"[B]ари_up[/B]  {now_str}\nВремя: {dur_str}\n\n{bx_status}")

    return success


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument(
        "--ai-start-date", default=None,
        help="Бэкфилл ari_ai_results с указанной даты (YYYY-MM-DD), напр. 2026-01-01. "
             "Без флага используется плавающее окно последних DAYS_TO_SYNC дней."
    )
    args = arg_parser.parse_args()

    success = main(ai_start_date=args.ai_start_date)
    sys.exit(0 if success else 1)