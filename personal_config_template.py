import os
for _var in ['HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'https_proxy', 'http_proxy', 'all_proxy']:
    os.environ.pop(_var, None)

# === Скопируйте этот файл в personal_config.py и заполните своими данными ===

# === Исходная ClickHouse (источник данных) ===
CH_HOST = 'your-source-clickhouse-host'
CH_PORT = 8888
CH_USER = 'your_username'
CH_PASSWORD = 'your_password'
CH_DATABASE = 'data_views'
CH_DATABASE_KIS = 'kis'

# === Целевая ClickHouse (Yandex Cloud или своя) ===
CH_HOST_TARGET = 'your-target-clickhouse-host.mdb.yandexcloud.net'
CH_PORT_TARGET = 8443
CH_USER_TARGET = 'your_target_username'
CH_PASSWORD_TARGET = 'your_target_password'
CH_DATABASE_TARGET = 'your_target_db'
CH_DATABASE_DIT = 'your_dit_db'

# === PostgreSQL ===
PG_HOST = 'your-pg-host.rw.mdb.yandexcloud.net'
PG_PORT = 6432
PG_USER = 'your_pg_user'
PG_PASSWORD = 'your_pg_password'
PG_DATABASE = 'your_pg_database'
PG_SCHEMA = 'datalake'


# --- VPN (CheckPoint Endpoint Connect) ---
VPN_APP_PATH  = r"C:\Program Files (x86)\CheckPoint\Endpoint Connect\TrGUI.exe"
VPN_TRAC_PATH = r"C:\Program Files (x86)\CheckPoint\Endpoint Connect\trac.exe"
VPN_USERNAME  = 'your_vpn_login'
VPN_SITE      = 'MyVPN'
VPN_PASSWORD  = 'your_vpn_password'

# --- VPN GUI Coordinates (настройте под своё разрешение/масштаб) ---
# Пример для 2560x1600 масштаб 2/3:
PASSWORD_FIELD_X,        PASSWORD_FIELD_Y        = (1124,  785)
CONNECT_BUTTON_X,        CONNECT_BUTTON_Y        = (1033,  897)
RIGHT_CLICK_MENU_X,      RIGHT_CLICK_MENU_Y      = (2169, 1572)
DISCONNECT_MENU_ITEM_X,  DISCONNECT_MENU_ITEM_Y  = (2218, 1557)
CONFIRMATION_CLICK_X,    CONFIRMATION_CLICK_Y    = (1312,  807)


# --- Groq AI ---
GROQ_API_KEY = "gsk_your_groq_api_key_here"

# --- Telegram Bot (основной / уведомления) ---
BOT_TOKEN = "0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
CHAT_ID = "your_chat_id"

LOG_BOT_TOKEN = "0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
LOG_CHAT_ID = "your_log_chat_id"

DASHBOARD_BOT_TOKEN = "0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
DASHBOARD_CHAT_ID = "-your_group_chat_id"

# --- Remote Control Bot (bot copyv2.py) ---
BOT_TOKEN_REMOTE = '0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
ALLOWED_USER_IDS = [123456789]          # список разрешённых Telegram user_id
ADMIN_USER_ID    = 123456789            # единственный администратор
PYTHON_EXECUTABLE = r'C:\Users\your_user\AppData\Local\Programs\Python\Python3XX\python.exe'

# --- Defect Bot (defect_bot_1m.py) ---
BOT_TOKEN_DEFECT = '0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'

# --- Telegram Proxy (если нужен) ---
# Оставьте пустую строку '', если прокси не нужен
TELEGRAM_PROXY = ''

# --- Bitrix24 ---
# Создайте входящий webhook в Битрикс24: Приложения → Вебхуки → Входящий вебхук
BITRIX_WEBHOOK = "https://your-portal.bitrix24.ru/rest/USER_ID/your_webhook_token"

# --- NTFY Push Notifications ---
# Зарегистрируйте уникальный топик на ntfy.sh (или своём сервере)
NTFY_SERVER      = "https://ntfy.sh"
NTFY_TOPIC       = "your_unique_topic_name"
NTFY_TOPIC_FINAL = "your_unique_final_topic_name"

# --- IT Support Dashboard (it_support_dashboard_up.py) ---
# OAuth-токен и Org ID создаются в Яндекс.Трекере: Профиль → Токены API
TRACKER_TOKEN  = 'y0_your_yandex_tracker_oauth_token'
TRACKER_ORG_ID = 'your_org_id'
PG_USER_ITSUPPORT     = 'your_pg_username'
PG_PASSWORD_ITSUPPORT = 'your_pg_password'
