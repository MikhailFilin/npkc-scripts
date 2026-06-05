"""
eris_trauma_aggregated_sync.py
Синхронизация данных eris_trauma_aggregated из исходной ClickHouse в целевую
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


# === Функция: Подключение к VPN ===
def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    send_ntfy_alert("Запускаю VPN-клиент для синхронизации травмы...", title="VPN Connect", priority="default", tags="lock")
    
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


# === Основная функция синхронизации ===
def sync_eris_trauma_aggregated():
    """
    Синхронизация данных eris_trauma_aggregated за последние N дней.
    Returns:
        bool: True если успешно, False если ошибка
    """
    print(f"📊 Синхронизация eris_trauma_aggregated за последние {DAYS_TO_SYNC} дней...")
    send_ntfy_alert(
        f"Начинаю синхронизацию травмы за {DAYS_TO_SYNC} дней...",
        title="Trauma Sync Start",
        priority="default",
        tags="inbox"
    )

    setup_sqlite_adapters()

    today = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)
    print(f"   Период синхронизации: {n_days_ago} – {today}")

    # --- Шаг 1: Очистка целевой таблицы за период ---
    client_target_delete = None
    try:
        print("🧹 Очистка целевой таблицы за последние N дней...")
        client_target_delete = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )
        delete_query = f"ALTER TABLE eris_trauma_aggregated DELETE WHERE conduct_date >= '{n_days_ago}'"
        client_target_delete.command(delete_query)
        print("✅ Старые данные удалены из целевой таблицы.")
        client_target_delete.close()
    except Exception as e:
        error_msg = f"❌ Ошибка очистки целевой таблицы: {e}"
        print(error_msg)
        send_ntfy_alert(f"Ошибка очистки БД: {str(e)[:80]}", title="DB Clean Error", priority="urgent", tags="database")
        if client_target_delete:
            client_target_delete.close()
        return False

    # --- Шаг 2: Получение данных из источника через VPN ---
    client_source = None
    temp_db_name = "temp_buffer_eris_trauma.db"
    try:
        connect_vpn()
        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999
        )
        print("✅ Подключились к источнику.")

        query = f"""
        SELECT 
          toDate(vie.conduct_date) AS conduct_date,
          vie.conduct_mo_name,
          vie.conduct_mu_name,
          vret.conduct_result_mu_id,
          vear.payment_source,
          vie.device_type,
          vie.research_name,
          if(vret.request_second_opinion_id IS NULL, 0, 1) AS second_opinion,
          vear.assignment_result_emp_job_execution_id,
          vear.assignment_result_emp_fio,
          COUNT(vie.study_uid) AS study_count
        FROM data_views.v_route_eris_trauma AS vret
        LEFT JOIN data_views.v_instrumental_examinations AS vie
          ON vret.accession_number = vie.accession_number
        LEFT JOIN data_views.v_eris_assignment_results AS vear
          ON vret.accession_number = vear.accession_number
        WHERE 
          toDate(vie.conduct_date) >= '{n_days_ago}'
          AND toDate(vie.conduct_date) <= '{today}'
        GROUP BY
          toDate(vie.conduct_date),
          vie.conduct_mo_name,
          vie.conduct_mu_name,
          vret.conduct_result_mu_id,
          vear.payment_source,
          vie.device_type,
          vie.research_name,
          if(vret.request_second_opinion_id IS NULL, 0, 1),
          vear.assignment_result_emp_job_execution_id,
          vear.assignment_result_emp_fio
        ORDER BY 
          toDate(vie.conduct_date),
          vie.conduct_mo_name,
          vie.conduct_mu_name,
          vear.payment_source,
          vie.device_type,
          vie.research_name,
          if(vret.request_second_opinion_id IS NULL, 0, 1),
          vear.assignment_result_emp_fio
        """

        result = client_source.query(query)
        raw_rows = result.result_rows
        column_names = result.column_names
        client_source.close()
        print(f"📥 Получено {len(raw_rows)} строк.")

        if not raw_rows:
            print("📭 Нет данных для синхронизации.")
            send_ntfy_alert(f"Нет данных для синхронизации травмы за {DAYS_TO_SYNC} дней", title="Trauma Empty", priority="default", tags="inbox")
            disconnect_vpn()
            return True

        # --- Шаг 3: Сохранение в SQLite буфер ---
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("DROP TABLE IF EXISTS temp_data;")
        sqlite_cursor.execute("""
        CREATE TABLE temp_data (
            conduct_date TEXT,
            conduct_mo_name TEXT,
            conduct_mu_name TEXT,
            conduct_result_mu_id TEXT,
            payment_source TEXT,
            device_type TEXT,
            research_name TEXT,
            second_opinion INTEGER,
            assignment_result_emp_job_execution_id TEXT,
            assignment_result_emp_fio TEXT,
            study_count INTEGER
        );
        """)
        insert_sql = "INSERT INTO temp_data VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);"
        sqlite_cursor.executemany(insert_sql, raw_rows)
        sqlite_conn.commit()
        sqlite_conn.close()
        print("✅ SQLite буфер заполнен.")

    except Exception as e:
        error_msg = f"❌ Ошибка при получении данных или работе с SQLite: {e}"
        print(error_msg)
        send_ntfy_alert(f"Сбой синхронизации: {str(e)[:80]}", title="Trauma Sync Error", priority="urgent", tags="fire")
        if client_source:
            client_source.close()
        try:
            os.remove(temp_db_name)
        except:
            pass
        try:
            disconnect_vpn()
        except:
            pass
        return False

    # --- Шаг 4: Отключение от VPN ---
    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f"⚠️ Ошибка отключения VPN: {e}", title="VPN Warning", priority="default", tags="warning")

    # --- Шаг 5: Вставка в целевую ClickHouse ---
    client_target = None
    for attempt in range(3):
        try:
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False
            )
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            else:
                error_msg = f"❌ Не удалось подключиться к целевой ClickHouse: {e}"
                print(error_msg)
                send_ntfy_alert(f"Ошибка подключения к БД: {str(e)[:60]}", title="DB Connect Error", priority="urgent", tags="database")
                try:
                    os.remove(temp_db_name)
                except:
                    pass
                return False

    try:
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM temp_data;")
        rows = sqlite_cursor.fetchall()
        sqlite_conn.close()

        # Преобразование типов
        processed_rows = []
        for row in rows:
            conduct_date_str = row[0]
            conduct_date = datetime.strptime(conduct_date_str, '%Y-%m-%d').date() if conduct_date_str else None
            new_row = [
                conduct_date,
                row[1] or '',
                row[2] or '',
                row[3] or '',
                row[4] or '',
                row[5] or '',
                row[6] or '',
                str(int(row[7]) if row[7] is not None else 0),  # second_opinion → String
                row[8] or '',
                row[9] or '',
                int(row[10]) if row[10] is not None else 0  # study_count → Int32
            ]
            processed_rows.append(new_row)

        target_columns = [
            'conduct_date', 'conduct_mo_name', 'conduct_mu_name', 'conduct_result_mu_id',
            'payment_source', 'device_type', 'research_name', 'second_opinion',
            'assignment_result_emp_job_execution_id', 'assignment_result_emp_fio', 'accession_number_count'
        ]

        print(f"📤 Вставка {len(processed_rows)} строк в eris_trauma_aggregated...")
        client_target.insert('eris_trauma_aggregated', processed_rows, column_names=target_columns)
        print("✅ Данные успешно вставлены.")
        send_ntfy_alert(f"✅ Успешно синхронизировано {len(processed_rows)} строк", title="Trauma Sync Success", priority="high", tags="white_check_mark")

        client_target.close()
        os.remove(temp_db_name)
        return True

    except Exception as e:
        error_msg = f"❌ Ошибка вставки в целевую ClickHouse: {e}"
        print(error_msg)
        send_ntfy_alert(f"Ошибка выгрузки: {str(e)[:80]}", title="Trauma Insert Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        try:
            os.remove(temp_db_name)
        except:
            pass
        return False


# === Main ===
def main():
    """Основная функция"""
    script_name = "eris_trauma_aggregated_sync.py"
    print(f"🚀 Запуск синхронизации {script_name}")
    send_ntfy_alert(f"Запускаю синхронизацию травмы...", title="Trauma Sync Start", priority="default", tags="robot")

    success = sync_eris_trauma_aggregated()

    if success:
        send_ntfy_alert(
            "✅ Дашборд Исследования в трампунктах завершен успешно! Дашборд обновлён.",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )
        print("🏁 Синхронизация завершена успешно")
    else:
        send_ntfy_alert(
            "❌ Дашборд Исследования в трампунктах завершился с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895"  # ← указываем топик напрямую
        )
        print("❌ Синхронизация завершена с ошибкой")

    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)