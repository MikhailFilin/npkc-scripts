"""
kis_doctors_export_clickhouse_sqlite_with_ntfy.py
Синхронизация данных о врачах из KIS (ClickHouse) в целевую ClickHouse базу
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
import certifi
import urllib3

# Отключаем предупреждения InsecureRequestWarning, если verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import personal_config as cfg
from ntfy_notifier import send_ntfy_alert

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUFFER_PATH = os.path.join(_SCRIPTS_DIR, 'temp_buffer_kis_doctors.db')

_COLUMNS = [
    'sign_date', 'modal', 'pay_type_name', 'doctor_id', 'doctor_fullname',
    'naz_name', 'naz_code', 'dept_name_comp', 'facility_short_name', 'total_records'
]

_MODAL_MAPPING = [
    ('472','ДЕНС'), ('473','ДЕНС'), ('474','ДЕНС'), ('79','ДЕНС'), ('921','ДЕНС'),
    ('100','КТ'), ('101','КТ'), ('102','КТ'), ('103','КТ'), ('104','КТ'),
    ('105','КТ'), ('1058','КТ'), ('1059','КТ'), ('106','КТ'), ('1060','КТ'),
    ('1061','КТ'), ('1062','КТ'), ('1063','КТ'), ('1064','КТ'), ('1065','КТ'),
    ('1066','КТ'), ('1067','КТ'), ('1068','КТ'), ('1069','КТ'), ('1070','КТ'),
    ('1071','КТ'), ('1072','КТ'), ('1073','КТ'), ('1074','КТ'), ('1075','КТ'),
    ('1076','КТ'), ('1077','КТ'), ('1078','КТ'), ('1079','КТ'), ('108','КТ'),
    ('109','КТ'), ('110','КТ'), ('1106','КТ'), ('112','КТ'), ('1123','КТ'),
    ('1124','КТ'), ('1125','КТ'), ('1126','КТ'), ('1127','КТ'), ('1128','КТ'),
    ('1129','КТ'), ('113','КТ'), ('1130','КТ'), ('1131','КТ'), ('1132','КТ'),
    ('1133','КТ'), ('1134','КТ'), ('1135','КТ'), ('1136','КТ'), ('114','КТ'),
    ('1145','КТ'), ('115','КТ'), ('1151','КТ'), ('116','КТ'), ('117','КТ'),
    ('118','КТ'), ('119','КТ'), ('120','КТ'), ('121','КТ'), ('122','КТ'),
    ('123','КТ'), ('124','КТ'), ('125','КТ'), ('126','КТ'), ('127','КТ'),
    ('128','КТ'), ('129','КТ'), ('130','КТ'), ('131','КТ'), ('132','КТ'),
    ('133','КТ'), ('134','КТ'), ('135','КТ'), ('137','КТ'), ('138','КТ'),
    ('1383','КТ'), ('1384','КТ'), ('139','КТ'), ('140','КТ'), ('1414','КТ'),
    ('1415','КТ'), ('1416','КТ'), ('1417','КТ'), ('1418','КТ'), ('142','КТ'),
    ('384','КТ'), ('385','КТ'), ('386','КТ'), ('387','КТ'), ('388','КТ'),
    ('389','КТ'), ('390','КТ'), ('391','КТ'), ('392','КТ'), ('393','КТ'),
    ('394','КТ'), ('395','КТ'), ('396','КТ'), ('397','КТ'), ('398','КТ'),
    ('399','КТ'), ('400','КТ'), ('401','КТ'), ('402','КТ'), ('403','КТ'),
    ('404','КТ'), ('405','КТ'), ('406','КТ'), ('407','КТ'), ('408','КТ'),
    ('409','КТ'), ('410','КТ'), ('411','КТ'), ('412','КТ'), ('413','КТ'),
    ('5','КТ'), ('521','КТ'), ('6','КТ'), ('7','КТ'), ('8','КТ'), ('9','КТ'),
    ('90','КТ'), ('91','КТ'), ('918','КТ'), ('919','КТ'), ('92','КТ'),
    ('920','КТ'), ('93','КТ'), ('94','КТ'), ('95','КТ'), ('96','КТ'),
    ('97','КТ'), ('98','КТ'), ('99','КТ'), ('A06.08.007.003','КТ'),
    ('523','ММГ'), ('524','ММГ'), ('80','ММГ'), ('82','ММГ'), ('83','ММГ'),
    ('88','ММГ'), ('89','ММГ'),
    ('10','МРТ'), ('11','МРТ'), ('1107','МРТ'), ('1108','МРТ'), ('1109','МРТ'),
    ('1110','МРТ'), ('1111','МРТ'), ('1112','МРТ'), ('1113','МРТ'), ('1114','МРТ'),
    ('1115','МРТ'), ('1116','МРТ'), ('1117','МРТ'), ('1118','МРТ'), ('1119','МРТ'),
    ('1120','МРТ'), ('1121','МРТ'), ('112','МРТ'), ('1381','МРТ'), ('1419','МРТ'),
    ('1420','МРТ'), ('143','МРТ'), ('144','МРТ'), ('145','МРТ'), ('146','МРТ'),
    ('147','МРТ'), ('148','МРТ'), ('149','МРТ'), ('151','МРТ'), ('152','МРТ'),
    ('153','МРТ'), ('154','МРТ'), ('155','МРТ'), ('156','МРТ'), ('157','МРТ'),
    ('158','МРТ'), ('159','МРТ'), ('160','МРТ'), ('161','МРТ'), ('162','МРТ'),
    ('163','МРТ'), ('164','МРТ'), ('165','МРТ'), ('166','МРТ'), ('167','МРТ'),
    ('169','МРТ'), ('170','МРТ'), ('171','МРТ'), ('172','МРТ'), ('173','МРТ'),
    ('174','МРТ'), ('175','МРТ'), ('178','МРТ'), ('179','МРТ'), ('180','МРТ'),
    ('183','МРТ'), ('184','МРТ'), ('185','МРТ'), ('186','МРТ'), ('187','МРТ'),
    ('188','МРТ'), ('189','МРТ'), ('190','МРТ'), ('191','МРТ'), ('192','МРТ'),
    ('193','МРТ'), ('194','МРТ'), ('195','МРТ'), ('196','МРТ'), ('197','МРТ'),
    ('198','МРТ'), ('199','МРТ'), ('200','МРТ'), ('201','МРТ'), ('202','МРТ'),
    ('203','МРТ'), ('204','МРТ'), ('205','МРТ'), ('206','МРТ'), ('207','МРТ'),
    ('208','МРТ'), ('209','МРТ'), ('210','МРТ'), ('211','МРТ'), ('212','МРТ'),
    ('213','МРТ'), ('214','МРТ'), ('215','МРТ'), ('216','МРТ'), ('218','МРТ'),
    ('221','МРТ'), ('222','МРТ'), ('223','МРТ'), ('224','МРТ'), ('225','МРТ'),
    ('226','МРТ'), ('227','МРТ'), ('228','МРТ'), ('229','МРТ'), ('230','МРТ'),
    ('231','МРТ'), ('353','МРТ'), ('354','МРТ'), ('355','МРТ'), ('356','МРТ'),
    ('357','МРТ'), ('358','МРТ'), ('359','МРТ'), ('360','МРТ'), ('361','МРТ'),
    ('362','МРТ'), ('363','МРТ'), ('364','МРТ'), ('365','МРТ'), ('366','МРТ'),
    ('367','МРТ'), ('368','МРТ'), ('369','МРТ'), ('370','МРТ'), ('372','МРТ'),
    ('373','МРТ'), ('374','МРТ'), ('375','МРТ'), ('376','МРТ'), ('377','МРТ'),
    ('378','МРТ'), ('379','МРТ'), ('380','МРТ'), ('381','МРТ'), ('382','МРТ'),
    ('383','МРТ'), ('475','МРТ'), ('476','МРТ'), ('477','МРТ'), ('917','МРТ'),
    ('1055','ПЭТ'), ('1057','ПЭТ'),
    ('1','РГ'), ('1027','РГ'), ('1028','РГ'), ('1137','РГ'), ('1138','РГ'),
    ('1139','РГ'), ('1140','РГ'), ('1141','РГ'), ('1142','РГ'), ('1143','РГ'),
    ('1144','РГ'), ('1146','РГ'), ('1148','РГ'), ('1382','РГ'), ('1391','РГ'),
    ('1412','РГ'), ('1413','РГ'), ('2','РГ'), ('3','РГ'), ('30','РГ'), ('31','РГ'),
    ('32','РГ'), ('33','РГ'), ('35','РГ'), ('36','РГ'), ('37','РГ'), ('38','РГ'),
    ('39','РГ'), ('40','РГ'), ('41','РГ'), ('414','РГ'), ('415','РГ'), ('416','РГ'),
    ('417','РГ'), ('418','РГ'), ('419','РГ'), ('42','РГ'), ('420','РГ'), ('421','РГ'),
    ('422','РГ'), ('424','РГ'), ('425','РГ'), ('426','РГ'), ('427','РГ'), ('428','РГ'),
    ('429','РГ'), ('43','РГ'), ('430','РГ'), ('431','РГ'), ('432','РГ'), ('433','РГ'),
    ('434','РГ'), ('435','РГ'), ('436','РГ'), ('437','РГ'), ('438','РГ'), ('439','РГ'),
    ('44','РГ'), ('440','РГ'), ('441','РГ'), ('442','РГ'), ('443','РГ'), ('444','РГ'),
    ('445','РГ'), ('446','РГ'), ('447','РГ'), ('448','РГ'), ('449','РГ'), ('45','РГ'),
    ('450','РГ'), ('451','РГ'), ('452','РГ'), ('453','РГ'), ('454','РГ'), ('456','РГ'),
    ('457','РГ'), ('459','РГ'), ('46','РГ'), ('460','РГ'), ('461','РГ'), ('462','РГ'),
    ('463','РГ'), ('464','РГ'), ('465','РГ'), ('466','РГ'), ('467','РГ'), ('468','РГ'),
    ('469','РГ'), ('47','РГ'), ('470','РГ'), ('471','РГ'), ('48','РГ'), ('49','РГ'),
    ('50','РГ'), ('51','РГ'), ('52','РГ'), ('522','РГ'), ('524','РГ'), ('53','РГ'),
    ('54','РГ'), ('55','РГ'), ('56','РГ'), ('7','РГ'), ('58','РГ'), ('59','РГ'),
    ('60','РГ'), ('61','РГ'), ('62','РГ'), ('63','РГ'), ('64','РГ'), ('65','РГ'),
    ('66','РГ'), ('67','РГ'), ('68','РГ'), ('69','РГ'), ('70','РГ'), ('72','РГ'),
    ('73','РГ'), ('74','РГ'), ('75','РГ'), ('76','РГ'), ('77','РГ'), ('78','РГ'),
    ('84','РГ'), ('85','РГ'), ('86','РГ'), ('922','РГ'),
    ('A06.03.001.001','РГ'), ('A06.09.007.004','РГ'), ('A06.28.001','РГ'),
    ('A06.28.001.1','РГ'), ('A06.28.002','РГ'), ('A06.28.003','РГ'), ('A06.28.004','РГ'),
    ('A06.28.005','РГ'), ('A06.28.006','РГ'), ('A06.28.007','РГ'), ('A06.28.008','РГ'),
    ('A06.28.010','РГ'), ('A06.28.011','РГ'), ('A06.28.012','РГ'), ('A06.28.013','РГ'),
    ('1032','РНД'), ('1033','РНД'), ('1034','РНД'), ('1035','РНД'), ('1036','РНД'),
    ('1037','РНД'), ('1038','РНД'), ('1039','РНД'), ('1040','РНД'), ('1042','РНД'),
    ('1043','РНД'), ('1045','РНД'), ('1046','РНД'), ('1047','РНД'), ('1048','РНД'),
    ('1049','РНД'), ('1050','РНД'), ('1051','РНД'), ('1052','РНД'), ('1053','РНД'),
    ('1054','РНД'), ('1150','РНД'), ('A07.06.005','РНД'),
    ('34','ФЛГ'),
]

_VALUES_STR = ', '.join(
    f"('{c.replace(chr(39), chr(39)*2)}', '{m.replace(chr(39), chr(39)*2)}')"
    for c, m in _MODAL_MAPPING
)

# === Настройки исходной ClickHouse базы (источник - KIS) ===
CH_HOST_SOURCE = cfg.CH_HOST
CH_PORT_SOURCE = cfg.CH_PORT
CH_USER_SOURCE = cfg.CH_USER
CH_PASSWORD_SOURCE = cfg.CH_PASSWORD
CH_DATABASE_SOURCE = cfg.CH_DATABASE_KIS  # Источник - база kis

# === Настройки целевой ClickHouse базы ===
CH_HOST_TARGET = cfg.CH_HOST_TARGET
CH_PORT_TARGET = cfg.CH_PORT_TARGET
CH_USER_TARGET = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_TARGET

# === Настройки для синхронизации (плавающие даты) ===
DAYS_TO_SYNC = 120  # Количество дней для синхронизации

# === Настройки для VPN ===
VPN_APP_PATH = cfg.VPN_APP_PATH
VPN_PASSWORD = cfg.VPN_PASSWORD

# Координаты
PASSWORD_FIELD_X, PASSWORD_FIELD_Y = cfg.PASSWORD_FIELD_X, cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X, CONNECT_BUTTON_Y = cfg.CONNECT_BUTTON_X, cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y = cfg.RIGHT_CLICK_MENU_X, cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y = cfg.CONFIRMATION_CLICK_X, cfg.CONFIRMATION_CLICK_Y

# === Функция: Исправление SQLite DeprecationWarning ===
def setup_sqlite_adapters():
    """Настраивает адаптеры и конвертеры для sqlite3, чтобы избежать DeprecationWarning в Python 3.13."""
    def adapt_date_iso(val):
        return val.isoformat()
    def adapt_datetime_iso(val):
        return val.isoformat()
    def convert_date(val):
        return datetime_module.date.fromisoformat(val.decode())
    def convert_timestamp(val):
        return datetime_module.datetime.fromisoformat(val.decode())
    # Регистрируем адаптеры и конвертеры
    sqlite3.register_adapter(datetime_module.date, adapt_date_iso)
    sqlite3.register_adapter(datetime_module.datetime, adapt_datetime_iso)
    sqlite3.register_converter("date", convert_date)
    sqlite3.register_converter("timestamp", convert_timestamp)


# === Функция: Подключение к VPN ===
def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    send_ntfy_alert("🔄 Запуск VPN клиента для экспорта врачей из KIS...", topic_override="kis-doctors-export-vpn", priority="default", tags="lock")
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
    except Exception as e:
        error_msg = f"❌ Ошибка запуска VPN: {e}"
        print(error_msg)
        send_ntfy_alert(error_msg, topic_override="kis-doctors-export-errors", priority="urgent", tags="warning")
        raise
    time.sleep(15)
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
    send_ntfy_alert("✅ Подключение к VPN инициировано.", topic_override="kis-doctors-export-vpn", priority="default", tags="globe_with_meridians")

# === Функция: Отключение от VPN ===
def disconnect_vpn():
    print("🛑 Начинаю отключение...")
    send_ntfy_alert("🛑 Начинаю отключение от VPN...", topic_override="kis-doctors-export-disconnect", priority="default", tags="stop_sign")
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print("🛑 Отключение завершено.")
    send_ntfy_alert("🛑 Отключение от VPN выполнено.", topic_override="kis-doctors-export-disconnect", priority="default", tags="lock")

# === Основная функция: Экспорт данных через SQLite буфер с плавающими датами ===
def export_kis_doctors():
    """Загрузка данных о врачах за последние N дней из KIS в новую ClickHouse базу через SQLite буфер"""
    print(f"📊 Загрузка данных о врачах из KIS за последние {DAYS_TO_SYNC} дней в новую ClickHouse (SQLite буфер)...")
    send_ntfy_alert(f"📊 Загрузка данных о врачах из KIS за последние {DAYS_TO_SYNC} дней в новую ClickHouse (SQLite буфер)...", topic_override="kis-doctors-export-progress", priority="default", tags="bar_chart")

    # --- Исправление DeprecationWarning для SQLite ---
    setup_sqlite_adapters()
    # --- Конец исправления ---

    # --- Вычисляем даты ---
    today = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)
    print(f"   Синхронизация за период: {n_days_ago} - {today}")

    # --- Справочник модальностей ---
    # Теперь определяем его один раз и используем для генерации VALUES в SQL
    modal_mapping_data = [
        ('472','ДЕНС'), ('473','ДЕНС'), ('474','ДЕНС'), ('79','ДЕНС'), ('921','ДЕНС'),
        ('100','КТ'), ('101','КТ'), ('102','КТ'), ('103','КТ'), ('104','КТ'),
        ('105','КТ'), ('1058','КТ'), ('1059','КТ'), ('106','КТ'), ('1060','КТ'),
        ('1061','КТ'), ('1062','КТ'), ('1063','КТ'), ('1064','КТ'), ('1065','КТ'),
        ('1066','КТ'), ('1067','КТ'), ('1068','КТ'), ('1069','КТ'), ('1070','КТ'),
        ('1071','КТ'), ('1072','КТ'), ('1073','КТ'), ('1074','КТ'), ('1075','КТ'),
        ('1076','КТ'), ('1077','КТ'), ('1078','КТ'), ('1079','КТ'), ('108','КТ'),
        ('109','КТ'), ('110','КТ'), ('1106','КТ'), ('112','КТ'), ('1123','КТ'),
        ('1124','КТ'), ('1125','КТ'), ('1126','КТ'), ('1127','КТ'), ('1128','КТ'),
        ('1129','КТ'), ('113','КТ'), ('1130','КТ'), ('1131','КТ'), ('1132','КТ'),
        ('1133','КТ'), ('1134','КТ'), ('1135','КТ'), ('1136','КТ'), ('114','КТ'),
        ('1145','КТ'), ('115','КТ'), ('1151','КТ'), ('116','КТ'), ('117','КТ'),
        ('118','КТ'), ('119','КТ'), ('120','КТ'), ('121','КТ'), ('122','КТ'),
        ('123','КТ'), ('124','КТ'), ('125','КТ'), ('126','КТ'), ('127','КТ'),
        ('128','КТ'), ('129','КТ'), ('130','КТ'), ('131','КТ'), ('132','КТ'),
        ('133','КТ'), ('134','КТ'), ('135','КТ'), ('137','КТ'), ('138','КТ'),
        ('1383','КТ'), ('1384','КТ'), ('139','КТ'), ('140','КТ'), ('1414','КТ'),
        ('1415','КТ'), ('1416','КТ'), ('1417','КТ'), ('1418','КТ'), ('142','КТ'),
        ('384','КТ'), ('385','КТ'), ('386','КТ'), ('387','КТ'), ('388','КТ'),
        ('389','КТ'), ('390','КТ'), ('391','КТ'), ('392','КТ'), ('393','КТ'),
        ('394','КТ'), ('395','КТ'), ('396','КТ'), ('397','КТ'), ('398','КТ'),
        ('399','КТ'), ('400','КТ'), ('401','КТ'), ('402','КТ'), ('403','КТ'),
        ('404','КТ'), ('405','КТ'), ('406','КТ'), ('407','КТ'), ('408','КТ'),
        ('409','КТ'), ('410','КТ'), ('411','КТ'), ('412','КТ'), ('413','КТ'),
        ('5','КТ'), ('521','КТ'), ('6','КТ'), ('7','КТ'), ('8','КТ'), ('9','КТ'),
        ('90','КТ'), ('91','КТ'), ('918','КТ'), ('919','КТ'), ('92','КТ'),
        ('920','КТ'), ('93','КТ'), ('94','КТ'), ('95','КТ'), ('96','КТ'),
        ('97','КТ'), ('98','КТ'), ('99','КТ'), ('A06.08.007.003','КТ'),
        ('523','ММГ'), ('524','ММГ'), ('80','ММГ'), ('82','ММГ'), ('83','ММГ'),
        ('88','ММГ'), ('89','ММГ'),
        ('10','МРТ'), ('11','МРТ'), ('1107','МРТ'), ('1108','МРТ'), ('1109','МРТ'),
        ('1110','МРТ'), ('1111','МРТ'), ('1112','МРТ'), ('1113','МРТ'), ('1114','МРТ'),
        ('1115','МРТ'), ('1116','МРТ'), ('1117','МРТ'), ('1118','МРТ'), ('1119','МРТ'),
        ('1120','МРТ'), ('1121','МРТ'), ('112','МРТ'), ('1381','МРТ'), ('1419','МРТ'),
        ('1420','МРТ'), ('143','МРТ'), ('144','МРТ'), ('145','МРТ'), ('146','МРТ'),
        ('147','МРТ'), ('148','МРТ'), ('149','МРТ'), ('151','МРТ'), ('152','МРТ'),
        ('153','МРТ'), ('154','МРТ'), ('155','МРТ'), ('156','МРТ'), ('157','МРТ'),
        ('158','МРТ'), ('159','МРТ'), ('160','МРТ'), ('161','МРТ'), ('162','МРТ'),
        ('163','МРТ'), ('164','МРТ'), ('165','МРТ'), ('166','МРТ'), ('167','МРТ'),
        ('169','МРТ'), ('170','МРТ'), ('171','МРТ'), ('172','МРТ'), ('173','МРТ'),
        ('174','МРТ'), ('175','МРТ'), ('178','МРТ'), ('179','МРТ'), ('180','МРТ'),
        ('183','МРТ'), ('184','МРТ'), ('185','МРТ'), ('186','МРТ'), ('187','МРТ'),
        ('188','МРТ'), ('189','МРТ'), ('190','МРТ'), ('191','МРТ'), ('192','МРТ'),
        ('193','МРТ'), ('194','МРТ'), ('195','МРТ'), ('196','МРТ'), ('197','МРТ'),
        ('198','МРТ'), ('199','МРТ'), ('200','МРТ'), ('201','МРТ'), ('202','МРТ'),
        ('203','МРТ'), ('204','МРТ'), ('205','МРТ'), ('206','МРТ'), ('207','МРТ'),
        ('208','МРТ'), ('209','МРТ'), ('210','МРТ'), ('211','МРТ'), ('212','МРТ'),
        ('213','МРТ'), ('214','МРТ'), ('215','МРТ'), ('216','МРТ'), ('218','МРТ'),
        ('221','МРТ'), ('222','МРТ'), ('223','МРТ'), ('224','МРТ'), ('225','МРТ'),
        ('226','МРТ'), ('227','МРТ'), ('228','МРТ'), ('229','МРТ'), ('230','МРТ'),
        ('231','МРТ'), ('353','МРТ'), ('354','МРТ'), ('355','МРТ'), ('356','МРТ'),
        ('357','МРТ'), ('358','МРТ'), ('359','МРТ'), ('360','МРТ'), ('361','МРТ'),
        ('362','МРТ'), ('363','МРТ'), ('364','МРТ'), ('365','МРТ'), ('366','МРТ'),
        ('367','МРТ'), ('368','МРТ'), ('369','МРТ'), ('370','МРТ'), ('372','МРТ'),
        ('373','МРТ'), ('374','МРТ'), ('375','МРТ'), ('376','МРТ'), ('377','МРТ'),
        ('378','МРТ'), ('379','МРТ'), ('380','МРТ'), ('381','МРТ'), ('382','МРТ'),
        ('383','МРТ'), ('475','МРТ'), ('476','МРТ'), ('477','МРТ'), ('917','МРТ'),
        ('1055','ПЭТ'), ('1057','ПЭТ'),
        ('1','РГ'), ('1027','РГ'), ('1028','РГ'), ('1137','РГ'), ('1138','РГ'),
        ('1139','РГ'), ('1140','РГ'), ('1141','РГ'), ('1142','РГ'), ('1143','РГ'),
        ('1144','РГ'), ('1146','РГ'), ('1148','РГ'), ('1382','РГ'), ('1391','РГ'),
        ('1412','РГ'), ('1413','РГ'), ('2','РГ'), ('3','РГ'), ('30','РГ'), ('31','РГ'),
        ('32','РГ'), ('33','РГ'), ('35','РГ'), ('36','РГ'), ('37','РГ'), ('38','РГ'),
        ('39','РГ'), ('40','РГ'), ('41','РГ'), ('414','РГ'), ('415','РГ'), ('416','РГ'),
        ('417','РГ'), ('418','РГ'), ('419','РГ'), ('42','РГ'), ('420','РГ'), ('421','РГ'),
        ('422','РГ'), ('424','РГ'), ('425','РГ'), ('426','РГ'), ('427','РГ'), ('428','РГ'),
        ('429','РГ'), ('43','РГ'), ('430','РГ'), ('431','РГ'), ('432','РГ'), ('433','РГ'),
        ('434','РГ'), ('435','РГ'), ('436','РГ'), ('437','РГ'), ('438','РГ'), ('439','РГ'),
        ('44','РГ'), ('440','РГ'), ('441','РГ'), ('442','РГ'), ('443','РГ'), ('444','РГ'),
        ('445','РГ'), ('446','РГ'), ('447','РГ'), ('448','РГ'), ('449','РГ'), ('45','РГ'),
        ('450','РГ'), ('451','РГ'), ('452','РГ'), ('453','РГ'), ('454','РГ'), ('456','РГ'),
        ('457','РГ'), ('459','РГ'), ('46','РГ'), ('460','РГ'), ('461','РГ'), ('462','РГ'),
        ('463','РГ'), ('464','РГ'), ('465','РГ'), ('466','РГ'), ('467','РГ'), ('468','РГ'),
        ('469','РГ'), ('47','РГ'), ('470','РГ'), ('471','РГ'), ('48','РГ'), ('49','РГ'),
        ('50','РГ'), ('51','РГ'), ('52','РГ'), ('522','РГ'), ('524','РГ'), ('53','РГ'),
        ('54','РГ'), ('55','РГ'), ('56','РГ'), ('7','РГ'), ('58','РГ'), ('59','РГ'),
        ('60','РГ'), ('61','РГ'), ('62','РГ'), ('63','РГ'), ('64','РГ'), ('65','РГ'),
        ('66','РГ'), ('67','РГ'), ('68','РГ'), ('69','РГ'), ('70','РГ'), ('72','РГ'),
        ('73','РГ'), ('74','РГ'), ('75','РГ'), ('76','РГ'), ('77','РГ'), ('78','РГ'),
        ('84','РГ'), ('85','РГ'), ('86','РГ'), ('922','РГ'),
        ('A06.03.001.001','РГ'), ('A06.09.007.004','РГ'), ('A06.28.001','РГ'),
        ('A06.28.001.1','РГ'), ('A06.28.002','РГ'), ('A06.28.003','РГ'), ('A06.28.004','РГ'),
        ('A06.28.005','РГ'), ('A06.28.006','РГ'), ('A06.28.007','РГ'), ('A06.28.008','РГ'),
        ('A06.28.010','РГ'), ('A06.28.011','РГ'), ('A06.28.012','РГ'), ('A06.28.013','РГ'),
        ('1032','РНД'), ('1033','РНД'), ('1034','РНД'), ('1035','РНД'), ('1036','РНД'),
        ('1037','РНД'), ('1038','РНД'), ('1039','РНД'), ('1040','РНД'), ('1042','РНД'),
        ('1043','РНД'), ('1045','РНД'), ('1046','РНД'), ('1047','РНД'), ('1048','РНД'),
        ('1049','РНД'), ('1050','РНД'), ('1051','РНД'), ('1052','РНД'), ('1053','РНД'),
        ('1054','РНД'), ('1150','РНД'), ('A07.06.005','РНД'),
        ('34','ФЛГ')
    ]

    # --- Генерация строки VALUES для SQL ---
    values_parts = []
    for code, modal in modal_mapping_data:
        # Экранируем одинарные кавычки в строках для SQL
        escaped_code = code.replace("'", "''")
        escaped_modal = modal.replace("'", "''")
        sql_tuple = f"('{escaped_code}', '{escaped_modal}')"
        values_parts.append(sql_tuple)
    values_str = ", ".join(values_parts)

    # --- Шаг 1: Подключаемся к целевой базе и удаляем данные за последние N дней ---
    client_target_delete = None
    try:
        print("🧹 Подключаюсь к целевой базе для очистки старых данных...")
        client_target_delete = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET, username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET,
            secure=True,
            verify=False
        )
        # Удаляем данные за последние N дней
        # Используем ALTER TABLE ... DELETE
        delete_query = f"ALTER TABLE `kis_workload_doc` DELETE WHERE sign_date >= '{n_days_ago.isoformat()}'"
        print(f"   Выполняю DELETE для sign_date >= {n_days_ago.isoformat()}")
        client_target_delete.command(delete_query) # command() для DML
        print("✅ Старые данные за последние N дней удалены из целевой базы.")
        client_target_delete.close()
        client_target_delete = None

    except Exception as e:
        error_msg = f"❌ Ошибка при удалении старых данных из целевой базы: {e}"
        print(error_msg)
        send_ntfy_alert(error_msg, topic_override="kis-doctors-export-errors", priority="urgent", tags="warning")
        if client_target_delete:
            client_target_delete.close()
        return False

    # --- Шаг 2: Подключаемся к исходной базе через VPN ---
    client_source = None
    temp_db_name = _BUFFER_PATH
    try:
        connect_vpn()

        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE, username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200,  # 20 минут на отправку/получение данных
            connect_timeout=999999
        )
        print("✅ Исходная ClickHouse (KIS) подключена.")

        # --- Шаг 3: Формируем основной запрос с JOIN на VALUES ---
        base_query = f"""
        SELECT
            sign_date,
            modal, -- Берем modal из m
            pay_type_name,
            base.sign_emp_id AS doctor_id, -- Переименовываем
            trim(concat(
                e.surname, ' ',
                e.name,
                if(e.patron = '' OR e.patron IS NULL, '', concat(' ', e.patron))
            )) AS doctor_fullname,
            base.naz_name,
            base.naz_code,
            base.dept_name_comp,
            f.short_name AS facility_short_name,
            COUNT(*) AS total_records
        FROM (
            -- Шаг 1: Устранение дублей в instrumental
            SELECT
                toDate(sign_dt) AS sign_date,
                sign_emp_id,
                protocol_id,
                pay_type_name,
                naz_code,
                naz_name,
                dept_name_comp,
                facility_id
            FROM kis.instrumental
            WHERE
                sign_dt >= today() - {DAYS_TO_SYNC}
                AND sign_emp_id IS NOT NULL
                AND sign_dt IS NOT NULL
            GROUP BY
                sign_date,
                sign_emp_id,
                protocol_id,
                pay_type_name,
                naz_code,
                naz_name,
                dept_name_comp,
                facility_id
        ) base
        -- Шаг 2: Подключаем справочник модальностей через VALUES
        INNER JOIN (
            SELECT * FROM values('naz_code String, modal String', {values_str})
        ) m ON base.naz_code = m.naz_code -- Используем ON, а не USING
        -- Шаг 3: Подключаем врачей с УНИКАЛЬНЫМИ записями
        LEFT JOIN (
            SELECT
                id,
                any(surname) AS surname,
                any(name) AS name,
                any(patron) AS patron
            FROM kis.emp
            WHERE
                end_dt IS NULL
                OR end_dt > now()
            GROUP BY id  -- ⭐️ УНИКАЛЬНОСТЬ ВРАЧА ПО ID
        ) e ON base.sign_emp_id = e.id
        -- Шаг 4: Подключаем организацию (facility) с УНИКАЛЬНЫМИ записями
        LEFT JOIN (
            SELECT
                id,
                any(short_name) AS short_name
            FROM kis.facility
            GROUP BY id  -- ⭐️ УНИКАЛЬНОСТЬ ОРГАНИЗАЦИИ ПО ID
        ) f ON base.facility_id = f.id
        -- Шаг 5: Группировка
        GROUP BY
            sign_date,           -- Имя столбца из подзапроса base
            modal,               -- Имя столбца из подзапроса m
            pay_type_name,       -- Имя столбца из подзапроса base
            base.sign_emp_id,    -- ⭐️ ИСПРАВЛЕНО: Используем ИСХОДНОЕ имя столбца из подзапроса base
            trim(concat(         -- ⭐️ ИСПРАВЛЕНО: Используем полное выражение, как в SELECT
                e.surname, ' ',
                e.name,
                if(e.patron = '' OR e.patron IS NULL, '', concat(' ', e.patron))
            )),                  -- Это alias doctor_fullname
            base.naz_name,       -- Имя столбца из подзапроса base
            base.naz_code,       -- Имя столбца из подзапроса base
            base.dept_name_comp, -- Имя столбца из подзапроса base
            f.short_name         -- Имя столбца из подзапроса f
        ORDER BY
            sign_date DESC,
            total_records DESC
        """

        print("📥 Выполняю запрос к исходной ClickHouse...")
        result = client_source.query(base_query)

        raw_rows = result.result_rows
        # Явно определяем имена столбцов, соответствующие структуре целевой таблицы
        expected_column_names = [
            'sign_date', 'modal', 'pay_type_name', 'doctor_id', 'doctor_fullname',
            'naz_name', 'naz_code', 'dept_name_comp', 'facility_short_name', 'total_records'
        ]
        # ВАЖНО: `result.column_names` может не совпадать с `expected_column_names` из-за `AS` в запросе.
        # Мы используем `expected_column_names` для вставки в целевую таблицу.
        column_names_from_query = result.column_names # ['sign_date', 'modal', ...] - имена из AS запроса
        print(f"   Имена столбцов из запроса: {column_names_from_query}")

        client_source.close()
        client_source = None
        print(f"📥 Получено {len(raw_rows)} строк данных о врачах.")

        # Если данных нет, можно завершить успешно (после удаления старых)
        if not raw_rows:
             print(f" nors Нет данных о врачах за последние {DAYS_TO_SYNC} дней для синхронизации.")
             send_ntfy_alert(f" nors Нет данных о врачах за последние {DAYS_TO_SYNC} дней для синхронизации.", topic_override="kis-doctors-export-progress", priority="default", tags="information_source")
             disconnect_vpn() # Отключаем VPN, так как работа завершена
             return True

        # --- Шаг 4: Создание и заполнение SQLite буфера ---
        print(f"💾 Создаю и заполняю SQLite буфер ({temp_db_name})...")
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()

        sqlite_cursor.execute("DROP TABLE IF EXISTS temp_kis_doctors_data;")

        # Создаём таблицу с именами, соответствующими итоговому результату запроса
        columns_def_sql_parts = [f"`{col}` TEXT" for col in expected_column_names]
        columns_def_sql = ", ".join(columns_def_sql_parts)

        create_table_sql = f"CREATE TABLE temp_kis_doctors_data ({columns_def_sql});"
        sqlite_cursor.execute(create_table_sql)

        # Подготавливаем плейсхолдеры для вставки
        placeholders_sql = ", ".join(["?" for _ in expected_column_names])
        insert_sql = f"INSERT INTO temp_kis_doctors_data VALUES ({placeholders_sql});"

        # Преобразуем данные для SQLite (например, даты в строки)
        processed_rows = []
        for row in raw_rows:
            processed_row = []
            for i, value in enumerate(row):
                col_name = expected_column_names[i] # Берем имя столбца из ожидаемого списка
                if col_name == 'sign_date':
                    # Преобразуем date/datetime в строку ISO для SQLite
                    if value is not None:
                        if hasattr(value, 'isoformat'):
                            processed_value = value.isoformat()
                        else:
                            processed_value = str(value)
                    else:
                        processed_value = None
                elif col_name in ['modal', 'pay_type_name', 'doctor_id', 'doctor_fullname',
                                  'naz_name', 'naz_code', 'dept_name_comp', 'facility_short_name']:
                    # Строковые поля: None -> пустая строка
                    processed_value = value if value is not None else ''
                elif col_name == 'total_records':
                    # Целочисленные поля: None -> 0
                    processed_value = int(value) if value is not None else 0
                else:
                    # Остальные поля как есть
                    processed_value = value
                processed_row.append(processed_value)
            processed_rows.append(processed_row)

        sqlite_cursor.executemany(insert_sql, processed_rows)
        sqlite_conn.commit()
        sqlite_conn.close()
        print(f"✅ SQLite буфер ({temp_db_name}) заполнен.")
        send_ntfy_alert(f"✅ SQLite буфер ({temp_db_name}) заполнен.", topic_override="kis-doctors-export-progress", priority="default", tags="floppy_disk")

    except Exception as e:
        error_msg = f"❌ Ошибка при работе с исходной ClickHouse или SQLite буфером: {e}"
        print(error_msg)
        send_ntfy_alert(error_msg, topic_override="kis-doctors-export-errors", priority="urgent", tags="skull_and_crossbones")
        if client_source:
            client_source.close()
        try:
            if os.path.exists(temp_db_name):
                os.remove(temp_db_name)
                print(f"🧹 Временный файл {temp_db_name} удалён после ошибки.")
        except Exception as cleanup_e:
            print(f"⚠️ Ошибка при удалении временного файла {temp_db_name}: {cleanup_e}")
        try:
            disconnect_vpn()
        except Exception as vpn_e:
            send_ntfy_alert(f"⚠️ Ошибка отключения VPN: {vpn_e}", topic_override="kis-doctors-export-errors", priority="default", tags="warning")
        return False

    # --- Шаг 5: Отключаемся от VPN ---
    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f"⚠️ Ошибка отключения VPN: {e}", topic_override="kis-doctors-export-errors", priority="default", tags="warning")

    # --- Шаг 6: Подключаемся к целевой базе и вставляем новые данные ---
    client_target = None
    max_retries_target = 3
    retry_count_target = 0
    success_target = False
    while retry_count_target < max_retries_target and not success_target:
        try:
            print(f"🔌 Подключаюсь к целевой ClickHouse (новая база), попытка {retry_count_target + 1}...")
            # Добавлены secure=True, verify=False
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET, username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET,
                secure=True,  # Убедитесь, что используется HTTPS
                verify=False  # Отключает проверку SSL сертификата
            )
            print("✅ Целевая ClickHouse подключена.")
            success_target = True # Успешно подключились
        except Exception as e:
            retry_count_target += 1
            error_msg_conn = f"❌ Ошибка подключения к целевой ClickHouse (попытка {retry_count_target}): {e}"
            print(error_msg_conn)
            send_ntfy_alert(error_msg_conn, topic_override="kis-doctors-export-errors", priority="default", tags="warning")
            if retry_count_target < max_retries_target:
                print("⏳ Ждем 5 секунд перед повторной попыткой...")
                time.sleep(5) # Ждем перед повторной попыткой
            else:
                print("❌ Все попытки подключения исчерпаны.")
                send_ntfy_alert("❌ Все попытки подключения к целевой ClickHouse исчерпаны.", topic_override="kis-doctors-export-errors", priority="urgent", tags="boom")

    # Проверяем, удалось ли подключиться перед продолжением
    if not success_target:
        # Если подключиться не удалось после всех попыток
        error_msg_no_conn = "❌ Не удалось подключиться к целевой ClickHouse после нескольких попыток."
        print(error_msg_no_conn)
        send_ntfy_alert(error_msg_no_conn, topic_override="kis-doctors-export-errors", priority="urgent", tags="x")
        try:
            if os.path.exists(temp_db_name):
                os.remove(temp_db_name)
                print(f"🧹 Временный файл {temp_db_name} удалён из-за ошибки подключения.")
        except Exception as cleanup_e:
            print(f"⚠️ Ошибка при удаления временного файла {temp_db_name}: {cleanup_e}")
        return False # Завершаем функцию с ошибкой

    try:
        print(f"📤 Читаю данные из SQLite буфера ({temp_db_name}) и вставляю в целевую ClickHouse...")
        sqlite_conn = sqlite3.connect(temp_db_name)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM temp_kis_doctors_data;")
        sqlite_rows = sqlite_cursor.fetchall()
        total_rows = len(sqlite_rows)
        print(f"   Прочитано {total_rows} строк из SQLite.")
        sqlite_conn.close()

        # --- Шаг 7: Обработка и вставка (с учётом даты и нового столбца) ---
        processed_rows = []
        for row in sqlite_rows:
            processed_row = []
            for i, value in enumerate(row):
                col_name = expected_column_names[i] # Берем имя столбца из результата запроса
                if col_name == 'sign_date':
                    # Преобразуем строку 'YYYY-MM-DD' в datetime.date
                    if value is not None and value != '':
                        try:
                            processed_value = datetime.strptime(value, '%Y-%m-%d').date()
                        except ValueError:
                            # Если не удалось распарсить, ставим безопасную дату
                            processed_value = datetime(1900, 1, 1).date()
                    else:
                        processed_value = None
                elif col_name in ['modal', 'pay_type_name', 'doctor_id', 'doctor_fullname',
                                  'naz_name', 'naz_code', 'dept_name_comp', 'facility_short_name']:
                    # Строковые поля: None -> пустая строка
                    processed_value = value if value is not None else ''
                elif col_name == 'total_records':
                    # Целочисленные поля: None -> 0
                    processed_value = int(value) if value is not None else 0
                else:
                    # Остальные поля как есть
                    processed_value = value
                processed_row.append(processed_value)
            processed_rows.append(processed_row)

        table_name = 'kis_workload_doc' # Убедимся, что имя таблицы правильно указано
        print(f"📤 Загружаю {len(processed_rows)} строк в таблицу {CH_DATABASE_TARGET}.{table_name}...")
        # ИСПОЛЬЗУЕМ expected_column_names для вставки
        client_target.insert(table_name, processed_rows, column_names=expected_column_names)

        success_msg = f"✅ Успешно синхронизировано {len(processed_rows)} строк за последние {DAYS_TO_SYNC} дней в {CH_DATABASE_TARGET}.{table_name}."
        print(success_msg)
        send_ntfy_alert(success_msg, topic_override="kis-doctors-export-success", priority="default", tags="white_check_mark")

        client_target.close()
        client_target = None

        try:
            if os.path.exists(temp_db_name):
                os.remove(temp_db_name)
                print(f"🧹 Временный файл {temp_db_name} удалён.")
        except Exception as cleanup_e:
            print(f"⚠️ Ошибка при удалении временного файла {temp_db_name}: {cleanup_e}")

        return True

    except Exception as e:
        error_msg = f"❌ Ошибка выгрузки из SQLite буфера в целевую ClickHouse: {e}"
        print(error_msg)
        send_ntfy_alert(error_msg, topic_override="kis-doctors-export-errors", priority="urgent", tags="rotating_light")
        if client_target:
            client_target.close()
        try:
            if os.path.exists(temp_db_name):
                os.remove(temp_db_name)
                print(f"🧹 Временный файл {temp_db_name} удалён после ошибки.")
        except Exception as cleanup_e:
            print(f"⚠️ Ошибка при удаления временного файла {temp_db_name}: {cleanup_e}")
        return False


def _ensure_table_exists(client) -> None:
    """Создаёт таблицу kis_workload_doc в dwh_test_db, если её ещё нет."""
    client.command("""
        CREATE TABLE IF NOT EXISTS kis_workload_doc
        (
            sign_date           Date,
            modal               String,
            pay_type_name       String,
            doctor_id           String,
            doctor_fullname     String,
            naz_name            String,
            naz_code            String,
            dept_name_comp      String,
            facility_short_name String,
            total_records       UInt64
        )
        ENGINE = MergeTree()
        ORDER BY (sign_date, doctor_id, naz_code)
        SETTINGS index_granularity = 8192;
    """)
    print("✅ Таблица kis_workload_doc проверена/создана.")


def extract_phase() -> bool:
    """VPN включён — source CH → SQLite буфер (для all_dashboards_up.py)."""
    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)
    print(f"📥 [кис_врачи] extract_phase: за последние {DAYS_TO_SYNC} дней (с {n_days_ago})...")
    setup_sqlite_adapters()

    client_source = None
    try:
        connect_vpn()  # nooped в all_dashboards_up

        client_source = clickhouse_connect.get_client(
            host=CH_HOST_SOURCE, port=CH_PORT_SOURCE,
            username=CH_USER_SOURCE, password=CH_PASSWORD_SOURCE,
            database=CH_DATABASE_SOURCE, secure=True, verify=False,
            send_receive_timeout=94200, connect_timeout=999999,
        )
        print("✅ [кис_врачи] Исходная ClickHouse подключена.")

        query = f"""
        SELECT
            sign_date,
            modal,
            pay_type_name,
            base.sign_emp_id AS doctor_id,
            trim(concat(
                e.surname, ' ',
                e.name,
                if(e.patron = '' OR e.patron IS NULL, '', concat(' ', e.patron))
            )) AS doctor_fullname,
            base.naz_name,
            base.naz_code,
            base.dept_name_comp,
            f.short_name AS facility_short_name,
            COUNT(*) AS total_records
        FROM (
            SELECT
                toDate(sign_dt) AS sign_date,
                sign_emp_id,
                protocol_id,
                pay_type_name,
                naz_code,
                naz_name,
                dept_name_comp,
                facility_id
            FROM kis.instrumental
            WHERE
                sign_dt >= today() - {DAYS_TO_SYNC}
                AND sign_emp_id IS NOT NULL
                AND sign_dt IS NOT NULL
            GROUP BY
                sign_date, sign_emp_id, protocol_id, pay_type_name,
                naz_code, naz_name, dept_name_comp, facility_id
        ) base
        INNER JOIN (
            SELECT * FROM values('naz_code String, modal String', {_VALUES_STR})
        ) m ON base.naz_code = m.naz_code
        LEFT JOIN (
            SELECT id, any(surname) AS surname, any(name) AS name, any(patron) AS patron
            FROM kis.emp
            WHERE end_dt IS NULL OR end_dt > now()
            GROUP BY id
        ) e ON base.sign_emp_id = e.id
        LEFT JOIN (
            SELECT id, any(short_name) AS short_name
            FROM kis.facility
            GROUP BY id
        ) f ON base.facility_id = f.id
        GROUP BY
            sign_date, modal, pay_type_name, base.sign_emp_id,
            trim(concat(e.surname, ' ', e.name, if(e.patron = '' OR e.patron IS NULL, '', concat(' ', e.patron)))),
            base.naz_name, base.naz_code, base.dept_name_comp, f.short_name
        ORDER BY sign_date DESC, total_records DESC
        """

        print("📥 [кис_врачи] Выполняю запрос...")
        result   = client_source.query(query)
        raw_rows = result.result_rows
        client_source.close()
        client_source = None
        print(f"📥 [кис_врачи] Получено {len(raw_rows)} строк.")

        conn   = sqlite3.connect(_BUFFER_PATH)
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS temp_kis_doctors_data;")
        cursor.execute(f"CREATE TABLE temp_kis_doctors_data ({', '.join(f'`{c}` TEXT' for c in _COLUMNS)});")

        if raw_rows:
            processed = []
            for row in raw_rows:
                pr = []
                for i, v in enumerate(row):
                    col = _COLUMNS[i]
                    if col == 'sign_date':
                        pr.append(v.isoformat() if v is not None and hasattr(v, 'isoformat') else (str(v) if v is not None else None))
                    elif col == 'total_records':
                        pr.append(int(v) if v is not None else 0)
                    else:
                        pr.append(v if v is not None else '')
                processed.append(pr)
            placeholders = ', '.join(['?' for _ in _COLUMNS])
            cursor.executemany(f"INSERT INTO temp_kis_doctors_data VALUES ({placeholders});", processed)

        conn.commit()
        conn.close()
        print(f"✅ [кис_врачи] SQLite буфер заполнен ({len(raw_rows)} строк).")
        return True

    except Exception as e:
        msg = f"❌ [кис_врачи] extract_phase: {e}"
        print(msg)
        send_ntfy_alert(str(e)[:120], title="кис_врачи Extract Error", priority="urgent", tags="fire")
        if client_source:
            client_source.close()
        if os.path.exists(_BUFFER_PATH):
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
        return False


def load_phase() -> bool:
    """VPN выключен — target CH CREATE/DELETE + INSERT из SQLite буфера."""
    today      = datetime.now().date()
    n_days_ago = today - timedelta(days=DAYS_TO_SYNC)
    setup_sqlite_adapters()

    if not os.path.exists(_BUFFER_PATH):
        print(f"❌ [кис_врачи] Буфер не найден: {_BUFFER_PATH}")
        return False

    client_target = None
    for attempt in range(1, 4):
        try:
            print(f"🔌 [кис_врачи] load_phase: подключаюсь к target CH (попытка {attempt})...")
            client_target = clickhouse_connect.get_client(
                host=CH_HOST_TARGET, port=CH_PORT_TARGET,
                username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
                database=CH_DATABASE_TARGET, secure=True, verify=False,
            )
            break
        except Exception as e:
            print(f"❌ [кис_врачи] Попытка {attempt}: {e}")
            if attempt < 3:
                time.sleep(5)
            else:
                if os.path.exists(_BUFFER_PATH):
                    try: os.remove(_BUFFER_PATH)
                    except Exception: pass
                return False

    try:
        _ensure_table_exists(client_target)
        print(f"🧹 [кис_врачи] DELETE sign_date >= {n_days_ago.isoformat()}")
        client_target.command(
            f"ALTER TABLE kis_workload_doc DELETE WHERE sign_date >= '{n_days_ago.isoformat()}'"
        )

        conn   = sqlite3.connect(_BUFFER_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM temp_kis_doctors_data;")
        sqlite_rows = cursor.fetchall()
        conn.close()
        print(f"📋 [кис_врачи] Прочитано {len(sqlite_rows)} строк из SQLite.")

        if not sqlite_rows:
            print(f"[кис_врачи] load_phase: 0 строк — нет данных за {DAYS_TO_SYNC} дней.")
            client_target.close()
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
            return True

        processed_rows = []
        for row in sqlite_rows:
            pr = []
            for i, v in enumerate(row):
                col = _COLUMNS[i]
                if col == 'sign_date':
                    if v is not None and v != '':
                        try:
                            pr.append(datetime.strptime(v, '%Y-%m-%d').date())
                        except ValueError:
                            pr.append(datetime(1900, 1, 1).date())
                    else:
                        pr.append(None)
                elif col == 'total_records':
                    pr.append(int(v) if v is not None else 0)
                else:
                    pr.append(v if v is not None else '')
            processed_rows.append(pr)

        print(f"📤 [кис_врачи] Загружаю {len(processed_rows)} строк в {CH_DATABASE_TARGET}.kis_workload_doc...")
        client_target.insert('kis_workload_doc', processed_rows, column_names=_COLUMNS)

        msg = f"✅ [кис_врачи] load_phase: вставлено {len(processed_rows)} строк за {DAYS_TO_SYNC} дней."
        print(msg)
        send_ntfy_alert(msg, title="кис_врачи Success", priority="high", tags="white_check_mark")

        client_target.close()
        client_target = None

        try:
            os.remove(_BUFFER_PATH)
            print("🧹 [кис_врачи] Буфер удалён.")
        except Exception:
            pass

        return True

    except Exception as e:
        msg = f"❌ [кис_врачи] load_phase: {e}"
        print(msg)
        send_ntfy_alert(str(e)[:120], title="кис_врачи Load Error", priority="urgent", tags="database")
        if client_target:
            client_target.close()
        if os.path.exists(_BUFFER_PATH):
            try: os.remove(_BUFFER_PATH)
            except Exception: pass
        return False


def main():
    """Основная функция"""
    script_name = "kis_doctors_export_clickhouse_sqlite_with_ntfy.py" # Обновленное название скрипта

    print(f"🚀 Запуск экспорта данных о врачах из KIS (SQLite буфер, плавающие даты, последние {DAYS_TO_SYNC} дней)")
    # send_ntfy_alert(f"🚀 Запуск экспорта данных о врачах из KIS (SQLite буфер, плавающие даты, последние {DAYS_TO_SYNC} дней)", topic_override="kis-doctors-export-start", priority="default", tags="rocket") # УБРАНО, т.к. функция не определена или не работает

    # Выполняем экспорт
    success = export_kis_doctors()

    # === ОБРАБОТКА РЕЗУЛЬТАТОВ ===
    if success:
        print("🏁 Экспорт данных о врачах через SQLite буфер с плавающими датами завершен успешно")
        # send_ntfy_alert("🏁 Экспорт данных о врачах через SQLite буфер с плавающими датами завершен успешно", topic_override="kis-doctors-export-completion", priority="default", tags="tada") # УБРАНО

        # Сообщение для дашборда
        # dashboard_msg = f"Дашборд по загрузке врачей из KIS (ClickHouse, SQLite буфер, плавающие даты, последние {DAYS_TO_SYNC} дней) обновлен"
        # send_ntfy_alert(dashboard_msg, topic_override="kis-doctors-dashboard-updates", priority="default", tags="chart_with_upwards_trend") # УБРАНО


    else:
        print("❌ Экспорт данных о врачах через SQLite буфер с плавающими датами завершен с ошибками")
        # send_ntfy_alert("❌ Экспорт данных о врачах через SQLite буфер с плавающими датами завершен с ошибками", topic_override="kis-doctors-export-completion", priority="urgent", tags="boom") # УБРАНО

        # Сообщение об ошибке для дашборда
        # error_msg = f"Не удалось обновить дашборд по загрузке врачей из KIS (ClickHouse, SQLite буфер, плавающие даты, последние {DAYS_TO_SYNC} дней)"
        # send_ntfy_alert(f"❌ {error_msg}", topic_override="kis-doctors-dashboard-updates", priority="urgent", tags="warning") # УБРАНО


    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)