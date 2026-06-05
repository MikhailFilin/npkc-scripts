import telebot
import telebot.apihelper
import subprocess
import os
import sys
import io
from datetime import datetime, time
import threading
import time as time_module
import html
import telebot.types as types
from functools import wraps
import pyautogui

import personal_config as cfg

# === Основные настройки ===
API_TOKEN        = cfg.BOT_TOKEN_REMOTE
ALLOWED_USER_IDS = cfg.ALLOWED_USER_IDS
ADMIN_USER_ID    = cfg.ADMIN_USER_ID
PYTHON_EXECUTABLE = cfg.PYTHON_EXECUTABLE
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

VPN_TRAC = cfg.VPN_TRAC_PATH
VPN_SITE = cfg.VPN_SITE

# === Настройки прокси для Telegram ===
TELEGRAM_PROXY = cfg.TELEGRAM_PROXY

if TELEGRAM_PROXY:
    telebot.apihelper.proxy = {'https': TELEGRAM_PROXY, 'http': TELEGRAM_PROXY}
    print(f"[INFO] Прокси Telegram: {TELEGRAM_PROXY}")

bot = telebot.TeleBot(API_TOKEN, parse_mode='HTML')

# === Скрипты для всех разрешённых пользователей ===
SCRIPTS_PUBLIC = {
    'defect_bot': ('defect_bot_1m.py', 'Бот дефектуры'),
}

# === Скрипты только для ADMIN_USER_ID ===
SCRIPTS_ADMIN = {
    'all_dashboards':  ('all_dashboards_up.py',   'Все дашборды'),
    'kis_eris':        ('kis_eris_up.py',          'KIS ЕРИС'),
    'ari':             ('ари_up.py',               'АРИ'),
    'ari_kis':         ('ари_кис_up.py',           'АРИ КИС'),
    'instrumental_3w': ('instrumental_3w_up.py',   'Инструм. 3 нед.'),
    'concl_click':     ('concl_click_up.py',       'Заключения Комета'),
    'concl_komet':     ('conclusion_komet_up.py',  'Заключения Клик'),
}

def get_scripts(user_id: int) -> dict:
    if user_id == ADMIN_USER_ID:
        return {**SCRIPTS_PUBLIC, **SCRIPTS_ADMIN}
    return SCRIPTS_PUBLIC

# === Кнопки системного управления ===
BTN_SCREENSHOT = 'Скриншот'
BTN_STATUS     = 'Процессы Python'
BTN_VPN_OFF    = 'VPN выкл'
BTN_VPN_ON     = 'VPN вкл'
BTN_VPN_STATUS = 'VPN статус'


def is_time_allowed():
    current_time = datetime.now().time()
    return not (time(2, 0) <= current_time <= time(10, 40))


def allowed_users_only(func):
    @wraps(func)
    def wrapper(message):
        if message.from_user.id not in ALLOWED_USER_IDS:
            bot.reply_to(message, "Доступ запрещён")
            return
        return func(message)
    return wrapper


def build_keyboard(user_id: int):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    for _, (_, desc) in get_scripts(user_id).items():
        markup.add(types.KeyboardButton(desc))
    if user_id == ADMIN_USER_ID:
        markup.row(types.KeyboardButton(BTN_SCREENSHOT), types.KeyboardButton(BTN_STATUS))
        markup.row(
            types.KeyboardButton(BTN_VPN_ON),
            types.KeyboardButton(BTN_VPN_OFF),
            types.KeyboardButton(BTN_VPN_STATUS),
        )
    markup.row(types.KeyboardButton("Помощь"), types.KeyboardButton("Текущее время"))
    return markup


# ──────────────────────────────────────────────
# Команды
# ──────────────────────────────────────────────

@bot.message_handler(commands=['start'])
@allowed_users_only
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "Добро пожаловать!\n\nВыберите задачу из меню.\nЗапуск скриптов разрешён с 10:40 до 02:00",
        reply_markup=build_keyboard(message.from_user.id),
    )


@bot.message_handler(commands=['screenshot'])
@allowed_users_only
def cmd_screenshot(message):
    _do_screenshot(message.chat.id)


@bot.message_handler(commands=['status'])
@allowed_users_only
def cmd_status(message):
    _do_status(message.chat.id)


@bot.message_handler(commands=['vpn_off'])
@allowed_users_only
def cmd_vpn_off(message):
    _do_vpn('disconnect', message.chat.id)


@bot.message_handler(commands=['vpn_on'])
@allowed_users_only
def cmd_vpn_on(message):
    _do_vpn('connect', message.chat.id)


@bot.message_handler(commands=['vpn_status'])
@allowed_users_only
def cmd_vpn_status(message):
    _do_vpn('info', message.chat.id)


# ──────────────────────────────────────────────
# Кнопки меню
# ──────────────────────────────────────────────

@bot.message_handler(func=lambda m: True)
@allowed_users_only
def handle_menu_buttons(message):
    text = message.text.strip()

    if text == "Помощь":
        help_text = (
            "<b>Команды:</b>\n"
            "/screenshot — скриншот экрана\n"
            "/status — запущенные Python-процессы\n"
            "/vpn_on — подключить VPN\n"
            "/vpn_off — отключить VPN\n"
            "/vpn_status — статус VPN\n\n"
            "<b>Скрипты:</b> кнопки в меню"
        )
        bot.reply_to(message, help_text)
        return

    if text == "Текущее время":
        bot.reply_to(message, f"Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        return

    is_admin = (message.from_user.id == ADMIN_USER_ID)

    if text == BTN_SCREENSHOT:
        if is_admin:
            _do_screenshot(message.chat.id)
        return

    if text == BTN_STATUS:
        if is_admin:
            _do_status(message.chat.id)
        return

    if text in (BTN_VPN_OFF, BTN_VPN_ON, BTN_VPN_STATUS):
        if is_admin:
            action = {'VPN выкл': 'disconnect', 'VPN вкл': 'connect', 'VPN статус': 'info'}[text]
            _do_vpn(action, message.chat.id)
        return

    # Поиск по скриптам (только доступные пользователю)
    for cmd, (file, desc) in get_scripts(message.from_user.id).items():
        if text == desc.strip():
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("Да, запустить", callback_data=f'run_{cmd}'),
                types.InlineKeyboardButton("Отмена", callback_data='cancel'),
            )
            bot.send_message(
                message.chat.id,
                f"Запустить <b>{desc}</b>?\nФайл: {file}\nРазрешено с 10:40 до 02:00",
                reply_markup=markup,
            )
            return

    bot.reply_to(message, f"Неизвестная команда: <code>{html.escape(text)}</code>")


# ──────────────────────────────────────────────
# Callback-кнопки (подтверждение запуска)
# ──────────────────────────────────────────────

@bot.callback_query_handler(func=lambda call: call.data.startswith('run_'))
def handle_run_command(call):
    if call.from_user.id not in ALLOWED_USER_IDS:
        return

    if not is_time_allowed():
        bot.answer_callback_query(
            call.id,
            f"Запрещено по расписанию (сейчас {datetime.now().strftime('%H:%M')})",
            show_alert=True,
        )
        return

    command = call.data[len('run_'):]
    available = get_scripts(call.from_user.id)
    if command not in available:
        bot.answer_callback_query(call.id, "Недоступно", show_alert=True)
        return

    script_file, description = available[command]
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    msg = bot.send_message(
        call.message.chat.id,
        f"Запуск: <b>{description}</b>\nФайл: {script_file}\n"
        f"Начало: {datetime.now().strftime('%H:%M:%S')}\n\nСтатус: Подготовка...",
    )
    threading.Thread(
        target=execute_script,
        args=(call.message.chat.id, msg.message_id, script_file, description),
        daemon=True,
    ).start()
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'cancel')
def handle_cancel(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    bot.send_message(call.message.chat.id, "Запуск отменён")
    bot.answer_callback_query(call.id)


# ──────────────────────────────────────────────
# Системные функции
# ──────────────────────────────────────────────

def _do_screenshot(chat_id: int):
    try:
        bot.send_chat_action(chat_id, 'upload_photo')
        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        bot.send_photo(chat_id, buf, caption=f"Скриншот {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка скриншота: {html.escape(str(e))}")


def _do_vpn(action: str, chat_id: int):
    """
    action: 'connect' | 'disconnect' | 'info'
    trac.exe — CLI CheckPoint Endpoint Connect
    """
    try:
        if action == 'connect':
            args = [VPN_TRAC, 'connect', VPN_SITE]
            label = 'Подключение VPN...'
        elif action == 'disconnect':
            args = [VPN_TRAC, 'disconnect']
            label = 'Отключение VPN...'
        else:
            args = [VPN_TRAC, 'info']
            label = 'Статус VPN'

        bot.send_message(chat_id, label)
        result = subprocess.run(
            args,
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=30,
        )
        output = (result.stdout + result.stderr).strip() or '(нет вывода)'
        bot.send_message(chat_id, f"<code>{html.escape(output[:3000])}</code>")
    except subprocess.TimeoutExpired:
        bot.send_message(chat_id, "Таймаут команды VPN (30 сек)")
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка VPN: {html.escape(str(e))}")


def _do_status(chat_id: int):
    try:
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, encoding='cp866', errors='replace', timeout=10,
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip() and 'python' in l.lower()]
        if lines:
            text = f"Запущено Python-процессов: {len(lines)}\n\n" + "\n".join(lines[:20])
        else:
            text = "Python-процессов не найдено"
        bot.send_message(chat_id, f"<code>{html.escape(text)}</code>")
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка: {html.escape(str(e))}")


def execute_script(chat_id: int, msg_id: int, script_file: str, description: str):
    abs_path = os.path.join(SCRIPTS_DIR, script_file)
    try:
        if not os.path.exists(abs_path):
            bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=f"Файл не найден: {abs_path}",
            )
            return

        bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=f"Выполняется: <b>{description}</b>\n"
                 f"Запуск: {datetime.now().strftime('%H:%M:%S')}\nСтатус: Выполняется...",
        )

        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONLEGACYWINDOWSSTDIO": "1"})

        start_time = datetime.now()
        process = subprocess.Popen(
            [PYTHON_EXECUTABLE, abs_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
            cwd=SCRIPTS_DIR, env=env,
        )
        stdout, stderr = process.communicate(timeout=1800)

        duration = (datetime.now() - start_time).total_seconds()
        status = "Успешно" if process.returncode == 0 else f"Ошибка ({process.returncode})"
        safe_out = html.escape(stdout[:2000]) if stdout else ""
        safe_err = html.escape(stderr[:2000]) if stderr else ""

        text = (
            f"<b>{status}</b> — {description}\n"
            f"Завершено: {datetime.now().strftime('%H:%M:%S')} ({duration:.1f} сек)\n"
        )
        if safe_out:
            text += f"\nВывод:\n<code>{safe_out}</code>"
        if safe_err:
            text += f"\nОшибки:\n<code>{safe_err}</code>"

        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text)

    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            pass
        bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text="Таймаут: скрипт работал более 30 минут и был остановлен",
        )
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=f"Критическая ошибка: {html.escape(str(e))}",
        )


def keep_alive():
    while True:
        try:
            print(f"[{datetime.now()}] keep_alive: бот активен")
        except Exception as e:
            print(f"[DEBUG] keep_alive error: {e}")
        time_module.sleep(300)


if __name__ == '__main__':
    if not os.path.exists(PYTHON_EXECUTABLE):
        print(f"[WARNING] Python не найден по пути: {PYTHON_EXECUTABLE}")
        PYTHON_EXECUTABLE = sys.executable
        print(f"[INFO] Используем: {PYTHON_EXECUTABLE}")

    print(f"[INFO] Рабочая директория: {SCRIPTS_DIR}")
    print(f"[INFO] Скрипты: {list(SCRIPTS.keys())}")

    threading.Thread(target=keep_alive, daemon=True).start()

    while True:
        try:
            print(f"[{datetime.now()}] Бот запущен")
            bot.polling(none_stop=True, interval=1, timeout=20)
        except Exception as e:
            print(f"[ERROR] {e}")
            time_module.sleep(5)
