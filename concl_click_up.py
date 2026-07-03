### ПЕРЕВЕДЕН НА МОСКОВСКОЕ ВРЕМЯ
"""
instrumental_summary_floating.py
Синхронизация данных instrumental_summary из исходной ClickHouse в целевую
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
DAYS_TO_SYNC = 100

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUFFER_CONCL = os.path.join(
    _SCRIPTS_DIR,
    f'temp_buffer_summary_{datetime.now().strftime("%Y%m%d_%H%M%S_%f")}_{os.getpid()}.db'
)
_CONCL_COLUMNS = [
    'doc_date', 'assignment_result_emp_fio', 'payment_source', 'device_type', 'diagnostic_name',
    'assessment_result_type_code', 'conduct_mo_name', 'conduct_mu_name', 'conduct_district_name',
    'patient_gender', 'assignment_describe_mu_id', 'service_type', 'anatomical_areas', 'norma_value',
    'total_coefficient', 'age_group', 'has_task_pin_record', 'trauma_after_orthopedist_flag',
    'time_interval', 'cnt', 'load_datetime', 'assignment_result_emp_id', 'has_defect_ab', 'tarif'
]

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
    send_ntfy_alert("Запускаю VPN-клиент для синхронизации...", title="VPN Connect", priority="default", tags="lock")
    
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

# === Справочник diagnostic_code: версии до/после 2026-06-01 ===
# До 2026-06-01 действуют старые названия услуг/тарифы, с 2026-06-01 включительно - новые
# (более гранулярные по числу зон КТ/МРТ). Обе версии нужны одновременно, т.к. окно
# синхронизации DAYS_TO_SYNC может захватывать обе стороны границы.
_TARIFF_BOUNDARY_DATE = '2026-06-01'

_DIAGNOSTIC_DICT_OLD_SQL = """
            SELECT '1' AS diagnostic_code, 'РГ' AS service_type, 1 AS anatomical_areas
            UNION ALL SELECT '10', 'МРТ 1 зона', 1
            UNION ALL SELECT '101', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '102', 'КТ 1 зона', 1
            UNION ALL SELECT '103', 'КТ 2 и более зон', 2
            UNION ALL SELECT '104', 'КТ с КУ 2 и более зон', 2
            UNION ALL SELECT '105', 'КТ 1 зона', 1
            UNION ALL SELECT '1058', 'КТ 1 зона', 1
            UNION ALL SELECT '106', 'КТ 1 зона', 1
            UNION ALL SELECT '1060', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1061', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1062', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1063', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1065', 'КТ с КУ 2 и более зон', 2
            UNION ALL SELECT '1066', 'КТ с КУ 2 и более зон', 2
            UNION ALL SELECT '1068', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1070', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1073', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1075', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1076', 'КТ с КУ 2 и более зон', 2
            UNION ALL SELECT '1077', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '108', 'КТ 1 зона', 1
            UNION ALL SELECT '11', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '1106', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1107', 'МРТ 2 и более зон', 3
            UNION ALL SELECT '1108', 'МРТ с КУ 2 и более зон', 3
            UNION ALL SELECT '1109', 'МРТ 2 и более зон', 2
            UNION ALL SELECT '1110', 'МРТ с КУ 2 и более зон', 2
            UNION ALL SELECT '1111', 'МРТ 2 и более зон', 2
            UNION ALL SELECT '1113', 'МРТ 2 и более зон', 2
            UNION ALL SELECT '1114', 'МРТ 2 и более зон', 2
            UNION ALL SELECT '1115', 'МРТ с КУ 2 и более зон', 2
            UNION ALL SELECT '1116', 'МРТ 2 и более зон', 3
            UNION ALL SELECT '1117', 'МРТ с КУ 2 и более зон', 3
            UNION ALL SELECT '1118', 'МРТ 2 и более зон', 3
            UNION ALL SELECT '1119', 'МРТ с КУ 2 и более зон', 3
            UNION ALL SELECT '112', 'КТ 1 зона', 1
            UNION ALL SELECT '1120', 'МРТ 2 и более зон', 2
            UNION ALL SELECT '1124', 'КТ с КУ 2 и более зон', 2
            UNION ALL SELECT '113', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1138', 'РГ', 2
            UNION ALL SELECT '1139', 'РГ', 2
            UNION ALL SELECT '114', 'КТ 1 зона', 1
            UNION ALL SELECT '1140', 'РГ', 2
            UNION ALL SELECT '1141', 'РГ', 2
            UNION ALL SELECT '1142', 'РГ', 2
            UNION ALL SELECT '1143', 'РГ', 2
            UNION ALL SELECT '1144', 'РГ', 2
            UNION ALL SELECT '115', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '116', 'КТ 1 зона', 1
            UNION ALL SELECT '117', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '118', 'КТ 2 и более зон', 2
            UNION ALL SELECT '121', 'КТ 1 зона', 1
            UNION ALL SELECT '122', 'КТ 1 зона', 1
            UNION ALL SELECT '123', 'КТ 1 зона', 1
            UNION ALL SELECT '124', 'КТ 1 зона', 1
            UNION ALL SELECT '125', 'КТ 1 зона', 1
            UNION ALL SELECT '126', 'КТ 1 зона', 1
            UNION ALL SELECT '127', 'КТ 1 зона', 1
            UNION ALL SELECT '128', 'КТ 2 и более зон', 2
            UNION ALL SELECT '129', 'КТ 1 зона', 1
            UNION ALL SELECT '130', 'КТ 2 и более зон', 2
            UNION ALL SELECT '131', 'КТ 1 зона', 1
            UNION ALL SELECT '132', 'КТ 1 зона', 1
            UNION ALL SELECT '133', 'КТ 1 зона', 1
            UNION ALL SELECT '134', 'КТ 2 и более зон', 2
            UNION ALL SELECT '135', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '137', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '138', 'КТ с КУ 2 и более зон', 2
            UNION ALL SELECT '1381', 'МРТ 1 зона', 1
            UNION ALL SELECT '1382', 'РГ', 2
            UNION ALL SELECT '1383', 'КТ с КУ 2 и более зон', 3
            UNION ALL SELECT '139', 'КТ с КУ 2 и более зон', 2
            UNION ALL SELECT '1391', 'РГ', 1
            UNION ALL SELECT '140', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '141', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1412', 'РГ', 2
            UNION ALL SELECT '143', 'МРТ 1 зона', 1
            UNION ALL SELECT '144', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '145', 'МРТ 1 зона', 1
            UNION ALL SELECT '146', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '147', 'МРТ 1 зона', 1
            UNION ALL SELECT '148', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '157', 'МРТ 1 зона', 1
            UNION ALL SELECT '159', 'МРТ 1 зона', 1
            UNION ALL SELECT '160', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '161', 'МРТ 1 зона', 1
            UNION ALL SELECT '162', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '163', 'МРТ 1 зона', 1
            UNION ALL SELECT '166', 'МРТ 1 зона', 1
            UNION ALL SELECT '167', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '170', 'МРТ 1 зона', 1
            UNION ALL SELECT '171', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '172', 'МРТ 1 зона', 1
            UNION ALL SELECT '173', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '174', 'МРТ 1 зона', 1
            UNION ALL SELECT '175', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '179', 'МРТ 1 зона', 1
            UNION ALL SELECT '180', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '183', 'МРТ 1 зона', 1
            UNION ALL SELECT '184', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '185', 'МРТ 1 зона', 1
            UNION ALL SELECT '186', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '187', 'МРТ 1 зона', 1
            UNION ALL SELECT '188', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '189', 'МРТ 1 зона', 1
            UNION ALL SELECT '190', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '191', 'МРТ 1 зона', 1
            UNION ALL SELECT '192', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '193', 'МРТ 1 зона', 1
            UNION ALL SELECT '194', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '195', 'МРТ 1 зона', 1
            UNION ALL SELECT '197', 'МРТ 1 зона', 1
            UNION ALL SELECT '198', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '199', 'МРТ 1 зона', 1
            UNION ALL SELECT '2', 'РГ', 1
            UNION ALL SELECT '200', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '201', 'МРТ 1 зона', 1
            UNION ALL SELECT '202', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '203', 'МРТ 1 зона', 1
            UNION ALL SELECT '204', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '205', 'МРТ 1 зона', 1
            UNION ALL SELECT '207', 'МРТ 1 зона', 1
            UNION ALL SELECT '208', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '209', 'МРТ 2 и более зон', 2
            UNION ALL SELECT '211', 'МРТ 1 зона', 1
            UNION ALL SELECT '212', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '213', 'МРТ 1 зона', 1
            UNION ALL SELECT '227', 'МРТ 1 зона', 1
            UNION ALL SELECT '3', 'РГ', 1
            UNION ALL SELECT '30', 'РГ', 1
            UNION ALL SELECT '31', 'РГ', 1
            UNION ALL SELECT '32', 'РГ', 1
            UNION ALL SELECT '33', 'РГ', 1
            UNION ALL SELECT '34', 'ФЛГ', 1
            UNION ALL SELECT '35', 'РГ', 1
            UNION ALL SELECT '353', 'МРТ 1 зона', 1
            UNION ALL SELECT '354', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '355', 'МРТ 1 зона', 1
            UNION ALL SELECT '356', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '357', 'МРТ 1 зона', 1
            UNION ALL SELECT '358', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '359', 'МРТ 1 зона', 1
            UNION ALL SELECT '36', 'РГ', 1
            UNION ALL SELECT '360', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '361', 'МРТ 1 зона', 1
            UNION ALL SELECT '362', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '364', 'МРТ 1 зона', 1
            UNION ALL SELECT '365', 'МРТ 1 зона', 1
            UNION ALL SELECT '366', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '367', 'МРТ 1 зона', 1
            UNION ALL SELECT '368', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '369', 'МРТ 1 зона', 1
            UNION ALL SELECT '37', 'РГ', 1
            UNION ALL SELECT '370', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '372', 'МРТ 1 зона', 1
            UNION ALL SELECT '373', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '374', 'МРТ 1 зона', 1
            UNION ALL SELECT '375', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '376', 'МРТ 1 зона', 1
            UNION ALL SELECT '377', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '378', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '384', 'КТ 1 зона', 1
            UNION ALL SELECT '385', 'КТ 1 зона', 1
            UNION ALL SELECT '386', 'КТ 1 зона', 1
            UNION ALL SELECT '387', 'КТ 1 зона', 1
            UNION ALL SELECT '388', 'КТ 1 зона', 1
            UNION ALL SELECT '389', 'КТ 2 и более зон', 2
            UNION ALL SELECT '390', 'КТ 1 зона', 1
            UNION ALL SELECT '391', 'КТ 2 и более зон', 3
            UNION ALL SELECT '392', 'КТ 2 и более зон', 2
            UNION ALL SELECT '393', 'КТ 2 и более зон', 2
            UNION ALL SELECT '394', 'КТ 2 и более зон', 2
            UNION ALL SELECT '395', 'КТ 2 и более зон', 4
            UNION ALL SELECT '396', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '397', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '398', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '4', 'РГ', 1
            UNION ALL SELECT '40', 'РГ', 1
            UNION ALL SELECT '400', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '401', 'КТ с КУ 2 и более зон', 2
            UNION ALL SELECT '404', 'КТ с КУ 2 и более зон', 3
            UNION ALL SELECT '407', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '409', 'КТ с КУ 2 и более зон', 2
            UNION ALL SELECT '410', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '411', 'КТ с КУ 2 и более зон', 3
            UNION ALL SELECT '412', 'КТ с КУ 2 и более зон', 2
            UNION ALL SELECT '415', 'РГ', 1
            UNION ALL SELECT '417', 'РГ', 1
            UNION ALL SELECT '418', 'РГ', 1
            UNION ALL SELECT '419', 'РГ', 1
            UNION ALL SELECT '421', 'РГ', 1
            UNION ALL SELECT '425', 'РГ', 1
            UNION ALL SELECT '427', 'РГ', 1
            UNION ALL SELECT '428', 'РГ', 1
            UNION ALL SELECT '43', 'РГ', 1
            UNION ALL SELECT '430', 'РГ', 1
            UNION ALL SELECT '431', 'РГ', 1
            UNION ALL SELECT '432', 'РГ', 1
            UNION ALL SELECT '433', 'РГ', 1
            UNION ALL SELECT '44', 'РГ', 1
            UNION ALL SELECT '448', 'РГ', 1
            UNION ALL SELECT '449', 'РГ', 1
            UNION ALL SELECT '45', 'РГ', 1
            UNION ALL SELECT '450', 'РГ', 1
            UNION ALL SELECT '452', 'РГ', 1
            UNION ALL SELECT '453', 'РГ', 1
            UNION ALL SELECT '454', 'РГ', 1
            UNION ALL SELECT '459', 'РГ', 1
            UNION ALL SELECT '46', 'РГ', 1
            UNION ALL SELECT '460', 'РГ', 1
            UNION ALL SELECT '461', 'РГ', 1
            UNION ALL SELECT '462', 'РГ', 1
            UNION ALL SELECT '463', 'РГ', 1
            UNION ALL SELECT '464', 'РГ', 1
            UNION ALL SELECT '465', 'РГ', 1
            UNION ALL SELECT '466', 'РГ', 1
            UNION ALL SELECT '467', 'РГ', 1
            UNION ALL SELECT '468', 'РГ', 1
            UNION ALL SELECT '469', 'РГ', 1
            UNION ALL SELECT '472', 'Денс', 1
            UNION ALL SELECT '473', 'Денс', 1
            UNION ALL SELECT '474', 'Денс', 1
            UNION ALL SELECT '475', 'МРТ с КУ 2 и более зон', 3
            UNION ALL SELECT '476', 'МРТ 2 и более зон', 2
            UNION ALL SELECT '477', 'МРТ с КУ 2 и более зон', 2
            UNION ALL SELECT '49', 'РГ', 1
            UNION ALL SELECT '5', 'КТ 1 зона', 1
            UNION ALL SELECT '51', 'РГ', 1
            UNION ALL SELECT '52', 'РГ', 1
            UNION ALL SELECT '523', 'ММГ', 1
            UNION ALL SELECT '524', 'ММГ', 1
            UNION ALL SELECT '53', 'РГ', 1
            UNION ALL SELECT '54', 'РГ', 1
            UNION ALL SELECT '55', 'РГ', 1
            UNION ALL SELECT '56', 'РГ', 1
            UNION ALL SELECT '57', 'РГ', 1
            UNION ALL SELECT '58', 'РГ', 1
            UNION ALL SELECT '59', 'РГ', 1
            UNION ALL SELECT '6', 'КТ 1 зона', 1
            UNION ALL SELECT '60', 'РГ', 1
            UNION ALL SELECT '61', 'РГ', 1
            UNION ALL SELECT '62', 'РГ', 1
            UNION ALL SELECT '63', 'РГ', 1
            UNION ALL SELECT '64', 'РГ', 1
            UNION ALL SELECT '65', 'РГ', 1
            UNION ALL SELECT '66', 'РГ', 1
            UNION ALL SELECT '67', 'РГ', 1
            UNION ALL SELECT '68', 'РГ', 1
            UNION ALL SELECT '69', 'РГ', 1
            UNION ALL SELECT '7', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '70', 'РГ', 1
            UNION ALL SELECT '72', 'РГ', 1
            UNION ALL SELECT '73', 'РГ', 1
            UNION ALL SELECT '74', 'РГ', 1
            UNION ALL SELECT '75', 'РГ', 1
            UNION ALL SELECT '76', 'РГ', 1
            UNION ALL SELECT '77', 'РГ', 1
            UNION ALL SELECT '79', 'Денс', 1
            UNION ALL SELECT '8', 'КТ 1 зона', 1
            UNION ALL SELECT '80', 'ММГ', 1
            UNION ALL SELECT '83', 'ММГ', 1
            UNION ALL SELECT '88', 'ММГ', 1
            UNION ALL SELECT '91', 'КТ 1 зона', 1
            UNION ALL SELECT '92', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '921', 'Денс', 1
            UNION ALL SELECT '93', 'КТ 1 зона', 1
            UNION ALL SELECT '94', 'КТ 1 зона', 1
            UNION ALL SELECT '95', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '96', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '97', 'КТ 1 зона', 1
            UNION ALL SELECT '98', 'КТ с КУ 1 зона', 1
"""

_DIAGNOSTIC_DICT_NEW_SQL = """
            SELECT '1' AS diagnostic_code, 'РГ' AS service_type, 1 AS anatomical_areas
            UNION ALL SELECT '10', 'МРТ 1 зона', 1
            UNION ALL SELECT '101', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '102', 'КТ 1 зона', 1
            UNION ALL SELECT '103', 'КТ 2 зоны', 2
            UNION ALL SELECT '104', 'КТ с КУ 2 зоны', 2
            UNION ALL SELECT '105', 'КТ 1 зона', 1
            UNION ALL SELECT '1058', 'КТ 1 зона', 1
            UNION ALL SELECT '106', 'КТ 1 зона', 1
            UNION ALL SELECT '1060', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1061', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1062', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1063', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1065', 'КТ с КУ 2 зоны', 2
            UNION ALL SELECT '1066', 'КТ с КУ 2 зоны', 2
            UNION ALL SELECT '1068', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1070', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1073', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1075', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1076', 'КТ с КУ 2 зоны', 2
            UNION ALL SELECT '1077', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '108', 'КТ 1 зона', 1
            UNION ALL SELECT '11', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '1106', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1107', 'МРТ 3 зоны', 3
            UNION ALL SELECT '1108', 'МРТ с КУ 3 зоны', 3
            UNION ALL SELECT '1109', 'МРТ 2 зоны', 2
            UNION ALL SELECT '1110', 'МРТ с КУ 2 зоны', 2
            UNION ALL SELECT '1111', 'МРТ 2 зоны', 2
            UNION ALL SELECT '1113', 'МРТ 2 зоны', 2
            UNION ALL SELECT '1114', 'МРТ 2 зоны', 2
            UNION ALL SELECT '1115', 'МРТ с КУ 2 зоны', 2
            UNION ALL SELECT '1116', 'МРТ 3 зоны', 3
            UNION ALL SELECT '1117', 'МРТ с КУ 3 зоны', 3
            UNION ALL SELECT '1118', 'МРТ 3 зоны', 3
            UNION ALL SELECT '1119', 'МРТ с КУ 3 зоны', 3
            UNION ALL SELECT '112', 'КТ 1 зона', 1
            UNION ALL SELECT '1120', 'МРТ 2 зоны', 2
            UNION ALL SELECT '1124', 'КТ с КУ 2 зоны', 2
            UNION ALL SELECT '113', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1138', 'РГ', 2
            UNION ALL SELECT '1139', 'РГ', 2
            UNION ALL SELECT '114', 'КТ 1 зона', 1
            UNION ALL SELECT '1140', 'РГ', 2
            UNION ALL SELECT '1141', 'РГ', 2
            UNION ALL SELECT '1142', 'РГ', 2
            UNION ALL SELECT '1143', 'РГ', 2
            UNION ALL SELECT '1144', 'РГ', 2
            UNION ALL SELECT '115', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '116', 'КТ 1 зона', 1
            UNION ALL SELECT '117', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '118', 'КТ 2 зоны', 2
            UNION ALL SELECT '121', 'КТ 1 зона', 1
            UNION ALL SELECT '122', 'КТ 1 зона', 1
            UNION ALL SELECT '123', 'КТ 1 зона', 1
            UNION ALL SELECT '124', 'КТ 1 зона', 1
            UNION ALL SELECT '125', 'КТ 1 зона', 1
            UNION ALL SELECT '126', 'КТ 1 зона', 1
            UNION ALL SELECT '127', 'КТ 1 зона', 1
            UNION ALL SELECT '128', 'КТ 2 зоны', 2
            UNION ALL SELECT '129', 'КТ 1 зона', 1
            UNION ALL SELECT '130', 'КТ 2 зоны', 2
            UNION ALL SELECT '131', 'КТ 1 зона', 1
            UNION ALL SELECT '132', 'КТ 1 зона', 1
            UNION ALL SELECT '133', 'КТ 1 зона', 1
            UNION ALL SELECT '134', 'КТ 2 зоны', 2
            UNION ALL SELECT '135', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '137', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '138', 'КТ с КУ 2 зоны', 2
            UNION ALL SELECT '1381', 'МРТ 1 зона', 1
            UNION ALL SELECT '1382', 'РГ', 2
            UNION ALL SELECT '1383', 'КТ с КУ 3 зоны', 3
            UNION ALL SELECT '139', 'КТ с КУ 2 зоны', 2
            UNION ALL SELECT '1391', 'РГ', 1
            UNION ALL SELECT '140', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '141', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '1412', 'РГ', 2
            UNION ALL SELECT '143', 'МРТ 1 зона', 1
            UNION ALL SELECT '144', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '145', 'МРТ 1 зона', 1
            UNION ALL SELECT '146', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '147', 'МРТ 1 зона', 1
            UNION ALL SELECT '148', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '157', 'МРТ 1 зона', 1
            UNION ALL SELECT '159', 'МРТ 1 зона', 1
            UNION ALL SELECT '160', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '161', 'МРТ 1 зона', 1
            UNION ALL SELECT '162', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '163', 'МРТ 1 зона', 1
            UNION ALL SELECT '166', 'МРТ 1 зона', 1
            UNION ALL SELECT '167', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '170', 'МРТ 1 зона', 1
            UNION ALL SELECT '171', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '172', 'МРТ 1 зона', 1
            UNION ALL SELECT '173', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '174', 'МРТ 1 зона', 1
            UNION ALL SELECT '175', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '179', 'МРТ 1 зона', 1
            UNION ALL SELECT '180', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '183', 'МРТ 1 зона', 1
            UNION ALL SELECT '184', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '185', 'МРТ 1 зона', 1
            UNION ALL SELECT '186', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '187', 'МРТ 1 зона', 1
            UNION ALL SELECT '188', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '189', 'МРТ 1 зона', 1
            UNION ALL SELECT '190', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '191', 'МРТ 1 зона', 1
            UNION ALL SELECT '192', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '193', 'МРТ 1 зона', 1
            UNION ALL SELECT '194', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '195', 'МРТ 1 зона', 1
            UNION ALL SELECT '197', 'МРТ 1 зона', 1
            UNION ALL SELECT '198', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '199', 'МРТ 1 зона', 1
            UNION ALL SELECT '2', 'РГ', 1
            UNION ALL SELECT '200', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '201', 'МРТ 1 зона', 1
            UNION ALL SELECT '202', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '203', 'МРТ 1 зона', 1
            UNION ALL SELECT '204', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '205', 'МРТ 1 зона', 1
            UNION ALL SELECT '207', 'МРТ 1 зона', 1
            UNION ALL SELECT '208', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '209', 'МРТ 2 зоны', 2
            UNION ALL SELECT '211', 'МРТ 1 зона', 1
            UNION ALL SELECT '212', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '213', 'МРТ 1 зона', 1
            UNION ALL SELECT '227', 'МРТ 1 зона', 1
            UNION ALL SELECT '3', 'РГ', 1
            UNION ALL SELECT '30', 'РГ', 1
            UNION ALL SELECT '31', 'РГ', 1
            UNION ALL SELECT '32', 'РГ', 1
            UNION ALL SELECT '33', 'РГ', 1
            UNION ALL SELECT '34', 'ФЛГ', 1
            UNION ALL SELECT '35', 'РГ', 1
            UNION ALL SELECT '353', 'МРТ 1 зона', 1
            UNION ALL SELECT '354', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '355', 'МРТ 1 зона', 1
            UNION ALL SELECT '356', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '357', 'МРТ 1 зона', 1
            UNION ALL SELECT '358', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '359', 'МРТ 1 зона', 1
            UNION ALL SELECT '36', 'РГ', 1
            UNION ALL SELECT '360', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '361', 'МРТ 1 зона', 1
            UNION ALL SELECT '362', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '364', 'МРТ 1 зона', 1
            UNION ALL SELECT '365', 'МРТ 1 зона', 1
            UNION ALL SELECT '366', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '367', 'МРТ 1 зона', 1
            UNION ALL SELECT '368', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '369', 'МРТ 1 зона', 1
            UNION ALL SELECT '37', 'РГ', 1
            UNION ALL SELECT '370', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '372', 'МРТ 1 зона', 1
            UNION ALL SELECT '373', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '374', 'МРТ 1 зона', 1
            UNION ALL SELECT '375', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '376', 'МРТ 1 зона', 1
            UNION ALL SELECT '377', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '378', 'МРТ с КУ 1 зона', 1
            UNION ALL SELECT '384', 'КТ 1 зона', 1
            UNION ALL SELECT '385', 'КТ 1 зона', 1
            UNION ALL SELECT '386', 'КТ 1 зона', 1
            UNION ALL SELECT '387', 'КТ 1 зона', 1
            UNION ALL SELECT '388', 'КТ 1 зона', 1
            UNION ALL SELECT '389', 'КТ 2 зоны', 2
            UNION ALL SELECT '390', 'КТ 1 зона', 1
            UNION ALL SELECT '391', 'КТ 3 зоны', 3
            UNION ALL SELECT '392', 'КТ 2 зоны', 2
            UNION ALL SELECT '393', 'КТ 2 зоны', 2
            UNION ALL SELECT '394', 'КТ 2 зоны', 2
            UNION ALL SELECT '395', 'КТ 4 зоны', 4
            UNION ALL SELECT '396', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '397', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '398', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '4', 'РГ', 1
            UNION ALL SELECT '40', 'РГ', 1
            UNION ALL SELECT '400', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '401', 'КТ с КУ 2 зоны', 2
            UNION ALL SELECT '404', 'КТ с КУ 3 зоны', 3
            UNION ALL SELECT '407', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '409', 'КТ с КУ 2 зоны', 2
            UNION ALL SELECT '410', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '411', 'КТ с КУ 3 зоны', 3
            UNION ALL SELECT '412', 'КТ с КУ 2 зоны', 2
            UNION ALL SELECT '415', 'РГ', 1
            UNION ALL SELECT '417', 'РГ', 1
            UNION ALL SELECT '418', 'РГ', 1
            UNION ALL SELECT '419', 'РГ', 1
            UNION ALL SELECT '421', 'РГ', 1
            UNION ALL SELECT '425', 'РГ', 1
            UNION ALL SELECT '427', 'РГ', 1
            UNION ALL SELECT '428', 'РГ', 1
            UNION ALL SELECT '43', 'РГ', 1
            UNION ALL SELECT '430', 'РГ', 1
            UNION ALL SELECT '431', 'РГ', 1
            UNION ALL SELECT '432', 'РГ', 1
            UNION ALL SELECT '433', 'РГ', 1
            UNION ALL SELECT '44', 'РГ', 1
            UNION ALL SELECT '448', 'РГ', 1
            UNION ALL SELECT '449', 'РГ', 1
            UNION ALL SELECT '45', 'РГ', 1
            UNION ALL SELECT '450', 'РГ', 1
            UNION ALL SELECT '452', 'РГ', 1
            UNION ALL SELECT '453', 'РГ', 1
            UNION ALL SELECT '454', 'РГ', 1
            UNION ALL SELECT '459', 'РГ', 1
            UNION ALL SELECT '46', 'РГ', 1
            UNION ALL SELECT '460', 'РГ', 1
            UNION ALL SELECT '461', 'РГ', 1
            UNION ALL SELECT '462', 'РГ', 1
            UNION ALL SELECT '463', 'РГ', 1
            UNION ALL SELECT '464', 'РГ', 1
            UNION ALL SELECT '465', 'РГ', 1
            UNION ALL SELECT '466', 'РГ', 1
            UNION ALL SELECT '467', 'РГ', 1
            UNION ALL SELECT '468', 'РГ', 1
            UNION ALL SELECT '469', 'РГ', 1
            UNION ALL SELECT '472', 'Денс', 1
            UNION ALL SELECT '473', 'Денс', 1
            UNION ALL SELECT '474', 'Денс', 1
            UNION ALL SELECT '475', 'МРТ с КУ 3 зоны', 3
            UNION ALL SELECT '476', 'МРТ 2 зоны', 2
            UNION ALL SELECT '477', 'МРТ с КУ 2 зоны', 2
            UNION ALL SELECT '49', 'РГ', 1
            UNION ALL SELECT '5', 'КТ 1 зона', 1
            UNION ALL SELECT '51', 'РГ', 1
            UNION ALL SELECT '52', 'РГ', 1
            UNION ALL SELECT '523', 'ММГ', 1
            UNION ALL SELECT '524', 'ММГ', 1
            UNION ALL SELECT '53', 'РГ', 1
            UNION ALL SELECT '54', 'РГ', 1
            UNION ALL SELECT '55', 'РГ', 1
            UNION ALL SELECT '56', 'РГ', 1
            UNION ALL SELECT '57', 'РГ', 1
            UNION ALL SELECT '58', 'РГ', 1
            UNION ALL SELECT '59', 'РГ', 1
            UNION ALL SELECT '6', 'КТ 1 зона', 1
            UNION ALL SELECT '60', 'РГ', 1
            UNION ALL SELECT '61', 'РГ', 1
            UNION ALL SELECT '62', 'РГ', 1
            UNION ALL SELECT '63', 'РГ', 1
            UNION ALL SELECT '64', 'РГ', 1
            UNION ALL SELECT '65', 'РГ', 1
            UNION ALL SELECT '66', 'РГ', 1
            UNION ALL SELECT '67', 'РГ', 1
            UNION ALL SELECT '68', 'РГ', 1
            UNION ALL SELECT '69', 'РГ', 1
            UNION ALL SELECT '7', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '70', 'РГ', 1
            UNION ALL SELECT '72', 'РГ', 1
            UNION ALL SELECT '73', 'РГ', 1
            UNION ALL SELECT '74', 'РГ', 1
            UNION ALL SELECT '75', 'РГ', 1
            UNION ALL SELECT '76', 'РГ', 1
            UNION ALL SELECT '77', 'РГ', 1
            UNION ALL SELECT '79', 'Денс', 1
            UNION ALL SELECT '8', 'КТ 1 зона', 1
            UNION ALL SELECT '80', 'ММГ', 1
            UNION ALL SELECT '83', 'ММГ', 1
            UNION ALL SELECT '88', 'ММГ', 1
            UNION ALL SELECT '91', 'КТ 1 зона', 1
            UNION ALL SELECT '92', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '921', 'Денс', 1
            UNION ALL SELECT '93', 'КТ 1 зона', 1
            UNION ALL SELECT '94', 'КТ 1 зона', 1
            UNION ALL SELECT '95', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '96', 'КТ с КУ 1 зона', 1
            UNION ALL SELECT '97', 'КТ 1 зона', 1
            UNION ALL SELECT '98', 'КТ с КУ 1 зона', 1
"""

_SUMMARY_QUERY_BASE = (
    """
        WITH diagnostic_dict_old AS (
"""
    + _DIAGNOSTIC_DICT_OLD_SQL +
    """        ),
        diagnostic_dict_new AS (
"""
    + _DIAGNOSTIC_DICT_NEW_SQL +
    """        )
        SELECT
            toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')) AS doc_date,
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
            multiIf(
                toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')) < '2026-06-01',
                diagnostic_ref_old.service_type,
                diagnostic_ref_new.service_type
            ) AS service_type,
            multiIf(
                toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')) < '2026-06-01',
                diagnostic_ref_old.anatomical_areas,
                diagnostic_ref_new.anatomical_areas
            ) AS anatomical_areas,
            ai_res.norma_value,
            sum(
                multiIf(
                    toDate(toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow')) < '2026-06-01',
                    (                CASE
                    WHEN t2.diagnostic_code IN ('34', '33', '427') AND ai_res.norma_value = '1' THEN 0.3
                    WHEN diagnostic_ref_old.service_type = 'КТ с КУ 1 зона' THEN 30.41
                    WHEN diagnostic_ref_old.service_type = 'КТ с КУ 2 и более зон' THEN 49.75
                    WHEN diagnostic_ref_old.service_type = 'КТ 1 зона' THEN 11.58
                    WHEN diagnostic_ref_old.service_type = 'КТ 2 и более зон' THEN 11.58
                    WHEN diagnostic_ref_old.service_type = 'МРТ 1 зона' THEN 15.06
                    WHEN diagnostic_ref_old.service_type = 'МРТ 2 и более зон' THEN 15.06
                    WHEN diagnostic_ref_old.service_type = 'МРТ с КУ 1 зона' THEN 34.71
                    WHEN diagnostic_ref_old.service_type = 'МРТ с КУ 2 и более зон' THEN 60.25
                    WHEN diagnostic_ref_old.service_type = 'ММГ' THEN 3.67
                    WHEN diagnostic_ref_old.service_type = 'РГ' THEN 3.67
                    WHEN diagnostic_ref_old.service_type = 'ФЛГ' THEN 1.0
                    WHEN diagnostic_ref_old.service_type = 'Денс' THEN 2.15
                    ELSE 1.0
                END),
                    (                CASE
                    WHEN t2.diagnostic_code IN ('34', '33', '427') AND ai_res.norma_value = '1' THEN 0.3
                    WHEN diagnostic_ref_new.service_type = 'КТ с КУ 1 зона' THEN 35.71
                    WHEN diagnostic_ref_new.service_type = 'КТ с КУ 2 зоны' THEN 43.01
                    WHEN diagnostic_ref_new.service_type = 'КТ с КУ 3 зоны' THEN 50.30
                    WHEN diagnostic_ref_new.service_type = 'КТ 1 зона' THEN 14.59
                    WHEN diagnostic_ref_new.service_type = 'КТ 2 зоны' THEN 21.88
                    WHEN diagnostic_ref_new.service_type = 'КТ 3 зоны' THEN 29.17
                    WHEN diagnostic_ref_new.service_type = 'КТ 4 зоны' THEN 36.47
                    WHEN diagnostic_ref_new.service_type = 'МРТ 1 зона' THEN 17.22
                    WHEN diagnostic_ref_new.service_type = 'МРТ 2 зоны' THEN 25.86
                    WHEN diagnostic_ref_new.service_type = 'МРТ 3 зоны' THEN 34.44
                    WHEN diagnostic_ref_new.service_type = 'МРТ с КУ 1 зона' THEN 39.92
                    WHEN diagnostic_ref_new.service_type = 'МРТ с КУ 2 зоны' THEN 48.50
                    WHEN diagnostic_ref_new.service_type = 'МРТ с КУ 3 зоны' THEN 57.14
                    WHEN diagnostic_ref_new.service_type = 'ММГ' THEN 3.67
                    WHEN diagnostic_ref_new.service_type = 'РГ' THEN 3.67
                    WHEN diagnostic_ref_new.service_type = 'ФЛГ' THEN 1.0
                    WHEN diagnostic_ref_new.service_type = 'Денс' THEN 2.15
                    ELSE 1.0
                END)
                )
            ) AS total_coefficient,
            multiIf(
                ie.patient_age >= 18, 'Взрослые',
                ie.patient_age < 18 AND ie.patient_age IS NOT NULL, 'Дети',
                'Не указано'
            ) AS age_group,
            multiIf(pin_res.max_task_pin_end_date IS NOT NULL, 1, 0) AS has_task_pin_record,
            multiIf(trauma_res.accession_number IS NOT NULL, 1, 0) AS trauma_after_orthopedist_flag,
            multiIf(
                intDiv(dateDiff('second',
                    COALESCE(
                        pin_res.max_task_pin_end_date,
                        (toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow'))
                    ),
                    (toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow'))
                ), 3600) < 24, 'Менее 24 часов',
                intDiv(dateDiff('second',
                    COALESCE(
                        pin_res.max_task_pin_end_date,
                        (toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow'))
                    ),
                    (toTimeZone(toDateTime(t2.assignment_result_doc_created_date), 'Europe/Moscow'))
                ), 3600) < 48, 'Более 24 часов',
                'Более 48 часов'
            ) AS time_interval,
            countDistinct(t2.accession_number) AS cnt,
            toTimeZone(now(), 'Europe/Moscow') AS load_datetime,
            toString(t2.assignment_result_emp_id) AS assignment_result_emp_id,
            ai_res_2.has_defect_ab,
            0.0 AS tarif
        FROM (
            -- Дедупликация: одно исследование (accession_number) = одна строка = один тариф.
            -- Убирает пересмотры (assessment_result_type_code = 3) и вторые коды услуг
            -- (например 44+45 прямая/боковая проекции) на одном исследовании.
            SELECT *
            FROM data_views.v_eris_assignment_results
            WHERE accession_number != ''
              AND assessment_result_type_code != 3
              AND toDate(toTimeZone(toDateTime(assignment_result_doc_created_date), 'Europe/Moscow')) >= '{date_from}'
              AND toDate(toTimeZone(toDateTime(assignment_result_doc_created_date), 'Europe/Moscow')) <= '{date_to}'
            ORDER BY assignment_result_doc_created_date, diagnostic_code
            LIMIT 1 BY accession_number
        ) t2
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
        LEFT JOIN (
            SELECT studyIUID, 1 AS has_defect_ab
            FROM (
                SELECT studyIUID,
                    if(modelId IN (1150, 1099, 1181, 1186, 1187, 1182, 1190, 1185, 1253),
                        time_diff_min >= 3, time_diff_min >= 6.5) AS defect_a,
                    defect_b
                FROM (
                    SELECT studyIUID, modelId,
                        max(has_error OR is_raw_null) AS defect_b,
                        dateDiff('second', min(ready_time), max(greatest(sent_time, latest_time))) / 60.0 AS time_diff_min
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
                            parseDateTimeBestEffortOrNull(
                                replaceRegexpAll(JSONExtractString(computed_data, 'pumStudyReadyForAiTime'), ':(60)(\\.\\d+)?', ':59.000')
                            ) AS ready_time,
                            parseDateTimeBestEffortOrNull(
                                replaceRegexpAll(JSONExtractString(computed_data, 'pumReportSentTime'), ':(60)(\\.\\d+)?', ':59.000')
                            ) AS sent_time,
                            parseDateTimeBestEffortOrNull(
                                replaceRegexpAll(JSONExtractString(computed_data, 'pumReportLatestDicomInstanceTime'), ':(60)(\\.\\d+)?', ':59.000')
                            ) AS latest_time,
                            raw_data = 'null' AS is_raw_null,
                            if(raw_data = 'null', 1, JSONExtractString(raw_data, 'aiResult', 'error') != '') AS has_error
                        FROM dwh_views.v_eris_report
                        WHERE app_source = 'CDS'
                        AND parseDateTimeBestEffortOrNull(
                            JSONExtractString(computed_data, 'pumStudyReadyForAiTime')
                        ) >= '2025-09-01 00:00:00'
                    )
                    GROUP BY studyIUID, modelId
                )
            )
            WHERE defect_a = 1 OR defect_b = 1
            GROUP BY studyIUID
        ) ai_res_2 ON t2.study_uid = ai_res_2.studyIUID
        LEFT JOIN (
            SELECT DISTINCT accession_number
            FROM data_views.v_route_eris_trauma
            WHERE accession_number != ''
        ) trauma_res ON t2.accession_number = trauma_res.accession_number
        LEFT JOIN (
            SELECT accession_number,
                max(task_pin_end_date) AS max_task_pin_end_date_raw,
                toTimeZone(toDateTime(max(task_pin_end_date)), 'Europe/Moscow') AS max_task_pin_end_date
            FROM data_views.v_task_pin
            WHERE accession_number != ''
            GROUP BY accession_number
        ) pin_res ON t2.accession_number = pin_res.accession_number
        LEFT JOIN diagnostic_dict_old AS diagnostic_ref_old ON t2.diagnostic_code = diagnostic_ref_old.diagnostic_code
        LEFT JOIN diagnostic_dict_new AS diagnostic_ref_new ON t2.diagnostic_code = diagnostic_ref_new.diagnostic_code
        WHERE t2.accession_number != ''
          AND t2.assessment_result_type_code != 3
"""
)

_SUMMARY_QUERY_TAIL = """
        GROUP BY
            doc_date, t2.assignment_result_emp_fio, t2.payment_source, t2.device_type,
            t2.diagnostic_name, t2.assessment_result_type_code, t2.conduct_mo_name,
            t2.conduct_mu_name, t2.conduct_district_name, t2.patient_gender,
            t2.assignment_describe_mu_id, service_type,
            anatomical_areas, ai_res.norma_value, ai_res_2.has_defect_ab,
            age_group, has_task_pin_record, trauma_after_orthopedist_flag, time_interval,
            load_datetime, t2.assignment_result_emp_id, tarif
        ORDER BY doc_date, t2.assignment_result_emp_fio, t2.payment_source
"""


def _build_summary_query(n_days_ago, today):
    """Собирает единый SQL-запрос instrumental_summary (общий для export_instrumental_summary и extract_phase).
    Фильтр по датам применяется внутри дедуп-подзапроса t2 через {date_from}/{date_to}."""
    query = _SUMMARY_QUERY_BASE + _SUMMARY_QUERY_TAIL
    return query.format(date_from=n_days_ago.isoformat(), date_to=today.isoformat())


def export_instrumental_summary():
    """
    Загрузка данных за последние N дней из v_eris_assignment_results
    в новую ClickHouse базу через SQLite буфер.
    Returns:
        bool: True если успешно, False если ошибка
    """
    print(f"📊 Загрузка instrumental_summary за последние {DAYS_TO_SYNC} дней...")
    send_ntfy_alert(
        f"Начинаю синхронизацию за {DAYS_TO_SYNC} дней...",
        title="concl_click Start",
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
        
        delete_query = f"ALTER TABLE instrumental_summary DELETE WHERE doc_date >= '{n_days_ago.isoformat()}'"
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
    temp_db_name = os.path.join(
        _SCRIPTS_DIR,
        f'temp_buffer_summary_{datetime.now().strftime("%Y%m%d_%H%M%S_%f")}_{os.getpid()}.db'
    )
    
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
        final_query = _build_summary_query(n_days_ago, today)
        
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
            send_ntfy_alert(f"Нет данных для синхронизации за {DAYS_TO_SYNC} дней", title="concl_click Empty", priority="default", tags="inbox")
            disconnect_vpn()
            return True
        
        # --- Шаг 4: Создание и заполнение SQLite буфера ---
        print(f"💾 Создаю SQLite буфер ({temp_db_name})...")
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("DROP TABLE IF EXISTS temp_instrumental_summary_data;")
        
        create_table_sql = """
        CREATE TABLE temp_instrumental_summary_data (
            doc_date TEXT, assignment_result_emp_fio TEXT, payment_source TEXT,
            device_type TEXT, diagnostic_name TEXT, assessment_result_type_code TEXT,
            conduct_mo_name TEXT, conduct_mu_name TEXT, conduct_district_name TEXT,
            patient_gender TEXT, assignment_describe_mu_id TEXT, service_type TEXT,
            anatomical_areas INTEGER, norma_value TEXT, total_coefficient REAL,
            age_group TEXT, has_task_pin_record INTEGER, trauma_after_orthopedist_flag INTEGER,
            time_interval TEXT, cnt INTEGER, load_datetime TEXT,
            assignment_result_emp_id TEXT, has_defect_ab INTEGER, tarif REAL
        );
        """
        sqlite_cursor.execute(create_table_sql)
        insert_sql = f"INSERT INTO temp_instrumental_summary_data VALUES ({', '.join(['?' for _ in column_names])});"
        sqlite_cursor.executemany(insert_sql, raw_rows)
        sqlite_conn.commit()
        sqlite_conn.close()
        print(f"✅ SQLite буфер заполнен.")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при работе с исходной ClickHouse или SQLite: {e}"
        print(error_msg)
        send_ntfy_alert(f"Сбой синхронизации: {str(e)[:80]}", title="concl_click Error", priority="urgent", tags="fire")
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
        sqlite_cursor.execute("SELECT * FROM temp_instrumental_summary_data;")
        sqlite_rows = sqlite_cursor.fetchall()
        total_rows = len(sqlite_rows)
        print(f"   Прочитано {total_rows} строк из SQLite.")
        sqlite_conn.close()
        
        # Обработка типов данных
        processed_column_names = [
            'doc_date', 'assignment_result_emp_fio', 'payment_source',
            'device_type', 'diagnostic_name', 'assessment_result_type_code',
            'conduct_mo_name', 'conduct_mu_name', 'conduct_district_name',
            'patient_gender', 'assignment_describe_mu_id', 'service_type',
            'anatomical_areas', 'norma_value', 'total_coefficient', 'age_group',
            'has_task_pin_record', 'trauma_after_orthopedist_flag', 'time_interval',
            'cnt', 'load_datetime', 'assignment_result_emp_id', 'has_defect_ab', 'tarif'
        ]
        
        processed_rows = []
        for row in sqlite_rows:
            processed_row = []
            for i, value in enumerate(row):
                col_name = processed_column_names[i]
                
                if col_name == 'doc_date':
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
                    'assignment_describe_mu_id', 'service_type', 'norma_value',
                    'age_group', 'time_interval', 'assignment_result_emp_id'
                ]:
                    processed_value = value if value is not None else ''
                elif col_name in [
                    'anatomical_areas', 'has_task_pin_record',
                    'trauma_after_orthopedist_flag', 'cnt', 'has_defect_ab'
                ]:
                    processed_value = value if value is not None else 0
                elif col_name in ['total_coefficient', 'tarif']:
                    processed_value = value if value is not None else 0.0
                elif col_name == 'load_datetime':
                    if value is not None:
                        if isinstance(value, str) and value == 'now()':
                            processed_value = datetime.now()
                        elif isinstance(value, str):
                            try:
                                processed_value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
                            except ValueError:
                                processed_value = datetime.now()
                        else:
                            processed_value = value
                    else:
                        processed_value = None
                else:
                    processed_value = value
                processed_row.append(processed_value)
            processed_rows.append(processed_row)
        
        table_name = 'instrumental_summary'
        print(f"📤 Загружаю {len(processed_rows)} строк в {CH_DATABASE_TARGET}.{table_name}...")
        client_target.insert(table_name, processed_rows, column_names=processed_column_names)
        
        success_msg = f"✅ Синхронизировано {len(processed_rows)} строк за {DAYS_TO_SYNC} дней."
        print(success_msg)
        send_ntfy_alert(success_msg, title="concl_click Success", priority="high", tags="white_check_mark")
        
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
        send_ntfy_alert(f"Ошибка выгрузки: {str(e)[:80]}", title="concl_click Insert Error", priority="urgent", tags="database")
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
    print(f"🚀 Запуск синхронизации instrumental_summary (последние {DAYS_TO_SYNC} дней)")
    send_ntfy_alert(
        f"Запускаю синхронизацию за {DAYS_TO_SYNC} дней...",
        title="concl_click Start",
        priority="default",
        tags="robot"
    )
    
    success = export_instrumental_summary()
    
    # Финальные уведомления
    if success:
        send_ntfy_alert(
            f"✅ Обновление Дашборд 24 часа и Аналитический уровень завершена успешно!",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895"
        )
        print("🏁 Синхронизация завершена успешно.")
    else:
        send_ntfy_alert(
            "❌ Обновление Дашборд 24 часа и Аналитический уровень завершился с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895" 
        )
        print("❌ Синхронизация завершена с ошибками.")
    
    return success


def extract_phase():
    """Фаза 1 (VPN включён): source ClickHouse → SQLite буфер."""
    print(f"📥 [concl] extract_phase: запрос к source ClickHouse за последние {DAYS_TO_SYNC} дней...")
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
        final_query = _build_summary_query(n_days_ago, today)
        result = client_source.query(final_query)
        raw_rows = result.result_rows
        client_source.close()
        client_source = None

        if not raw_rows:
            print(f"  ⚠️ [concl] extract_phase: нет данных за {DAYS_TO_SYNC} дней.")
            open(_BUFFER_CONCL, 'w').close()
            return True

        sqlite_conn = sqlite3.connect(_BUFFER_CONCL)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("DROP TABLE IF EXISTS temp_instrumental_summary_data;")
        sqlite_cursor.execute("""
            CREATE TABLE temp_instrumental_summary_data (
                doc_date TEXT, assignment_result_emp_fio TEXT, payment_source TEXT,
                device_type TEXT, diagnostic_name TEXT, assessment_result_type_code TEXT,
                conduct_mo_name TEXT, conduct_mu_name TEXT, conduct_district_name TEXT,
                patient_gender TEXT, assignment_describe_mu_id TEXT, service_type TEXT,
                anatomical_areas INTEGER, norma_value TEXT, total_coefficient REAL,
                age_group TEXT, has_task_pin_record INTEGER, trauma_after_orthopedist_flag INTEGER,
                time_interval TEXT, cnt INTEGER, load_datetime TEXT,
                assignment_result_emp_id TEXT, has_defect_ab INTEGER, tarif REAL
            );
        """)
        sqlite_cursor.executemany(f"INSERT INTO temp_instrumental_summary_data VALUES ({','.join(['?']*24)});", raw_rows)
        sqlite_conn.commit()
        sqlite_conn.close()
        print(f"  ✅ [concl] extract_phase: сохранено {len(raw_rows)} строк в буфер.")
        return True
    except Exception as e:
        print(f"  ❌ [concl] extract_phase: {e}")
        if client_source:
            client_source.close()
        return False


def load_phase():
    """Фаза 2 (VPN выключен): DELETE target CH + load from SQLite буфер."""
    print(f"📤 [concl] load_phase: загрузка из буфера в target ClickHouse...")
    setup_sqlite_adapters()
    today = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)

    if not os.path.exists(_BUFFER_CONCL):
        print("  ❌ [concl] load_phase: буфер не найден.")
        return False

    client_target = None
    try:
        client_target = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )
        client_target.command(f"ALTER TABLE instrumental_summary DELETE WHERE doc_date >= '{n_days_ago.isoformat()}'")
        print(f"  ✅ [concl] load_phase: DELETE выполнен для дат >= {n_days_ago.isoformat()}.")

        sqlite_conn = sqlite3.connect(_BUFFER_CONCL)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM temp_instrumental_summary_data;")
        sqlite_rows = sqlite_cursor.fetchall()
        sqlite_conn.close()

        if not sqlite_rows:
            print("  ⚠️ [concl] load_phase: буфер пуст, вставка не требуется.")
            client_target.close()
            return True

        processed_rows = []
        for row in sqlite_rows:
            processed_row = []
            for i, value in enumerate(row):
                col = _CONCL_COLUMNS[i]
                if col == 'doc_date':
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
                              'service_type', 'norma_value', 'age_group', 'time_interval', 'assignment_result_emp_id']:
                    processed_row.append(value if value is not None else '')
                elif col in ['anatomical_areas', 'has_task_pin_record', 'trauma_after_orthopedist_flag', 'cnt', 'has_defect_ab']:
                    processed_row.append(value if value is not None else 0)
                elif col in ['total_coefficient', 'tarif']:
                    processed_row.append(value if value is not None else 0.0)
                elif col == 'load_datetime':
                    if value is not None and isinstance(value, str):
                        try:
                            processed_row.append(datetime.strptime(value, '%Y-%m-%d %H:%M:%S'))
                        except ValueError:
                            processed_row.append(datetime.now())
                    else:
                        processed_row.append(datetime.now())
                else:
                    processed_row.append(value)
            processed_rows.append(processed_row)

        client_target.insert('instrumental_summary', processed_rows, column_names=_CONCL_COLUMNS)
        client_target.close()
        print(f"  ✅ [concl] load_phase: вставлено {len(processed_rows)} строк.")
        return True
    except Exception as e:
        print(f"  ❌ [concl] load_phase: {e}")
        if client_target:
            client_target.close()
        return False
    finally:
        try:
            if os.path.exists(_BUFFER_CONCL):
                os.remove(_BUFFER_CONCL)
                print(f"  🧹 [concl] load_phase: буфер {_BUFFER_CONCL} удалён.")
        except Exception:
            pass


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)