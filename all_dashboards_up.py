"""
all_dashboards_up.py
Единый скрипт для обновления всех дашбордов.
VPN подключается ОДИН РАЗ, все запросы выполняются последовательно, затем VPN отключается.

Включает следующие задачи:
  1.  стади_up              → instrumental_examinations          (Target CH)
  2.  расхождение_ии_up     → ai_norma_comparing                 (Target CH)
  3.  рабочие_списки_up     → v_instrumental_task_lists          (Target CH)
  4.  instrumental_3w_up    → instrumental_examinations_3w       (Target CH)  [новый не_описанные]
  5.  ии1_up                → validation_ai_results              (PostgreSQL)
  6.  ии2_up                → validation_eris_report             (PostgreSQL)
  7.  conclusion_komet_up   → cometa_conclusions_summary         (Target CH)
  8.  concl_click_up        → instrumental_summary               (Target CH)
  9.  concl_npkc_up         → instrumental_summary_npkc          (Target CH)
  10. caop_export_up        → count_caop                         (PostgreSQL)
  11. описания_не_нпкц     → assignment_results_other_mo        (Target CH)
  12. загрузка_амб_дни     → workload_komet_day                (PostgreSQL)
  13. загрузка_амб_месяц   → workload_komet_month              (PostgreSQL)
  14. загрузка_амб_неделя  → workload_komet_week               (PostgreSQL)
  15. vitrina_sync          → v_task_pin, v_undescribed + матвью (PostgreSQL)
  16. kis_eris_up           → kis_eris                         (Target CH)
  17. oko_saurona_up        → overdue_studies_monitoring        (Target CH)
  18. svo_eris_llo_up       → svo_eris_llo_examinations         (Target CH)
  19. guide_dismissed_up    → guide_doctors_dismissal_v2        (Target CH, без VPN)

Исключён (старый скрипт):
  не_описанные_up.py          → instrumental_examinations_queue_v3  (заменён на instrumental_3w_up)
  validation_summary_expor.py → validation_summary (3 таблицы)      (заменён на concl_click_up.py)
"""

import os
import sys
import json
import glob
import time
import threading
import subprocess
import importlib.util
import traceback

import pyautogui
import requests
from datetime import datetime, timedelta

from ntfy_notifier import send_ntfy_alert
import personal_config as cfg

# === Битрикс24 ===
_BITRIX_WEBHOOK    = cfg.BITRIX_WEBHOOK
_BX_LOG_DIALOG     = "chat145691"   # детальный итог по задачам
_BX_SUMMARY_DIALOG = "chat145721"   # одна строка-сводка

# Дашборды: ключ совпадает с ключами extract_results / load_results
_DASHBOARD_URLS = {
    'стади / instrumental_examinations':
        ('Медицина_ДКЦЛД — Кол-во исследований',
         'https://datalens.ru/0vu92i4d9co0k-medicina-dkcld-analitika?tab=BW4'),
    'расхождение_ии / ai_norma_comparing':
        ('Медицина_ДКЦЛД — Расхождение ИИ и Врача',
         'https://datalens.ru/0vu92i4d9co0k-medicina-dkcld-analitika?tab=Oeg'),
    'рабочие_списки / v_instrumental_task_lists':
        ('Медицина_ДКЦЛД — Рабочие списки врачей',
         'https://datalens.ru/7n8p0x71xmuur-medicina-dkcld-rabochie-spiski-vrachey'),
    'инстр_3w / instrumental_examinations_3w':
        ('Медицина_ДКЦЛД — Не описанные исследования',
         'https://datalens.ru/4p1hmidf0tc8o-medicina-dkcld-ne-opisannye-issledovaniya'),
    'ии1 / validation_ai_results':
        ('Медицина_ИИ — Поток исследований',
         'https://datalens.ru/s8tathzr83tgc-medicina-ii-potok-issledovaniy'),
    'ии2 / validation_eris_report':
        ('Медицина_ИИ — Поток исследований',
         'https://datalens.ru/s8tathzr83tgc-medicina-ii-potok-issledovaniy'),
    'conclusion_komet / cometa_conclusions_summary':
        ('—', ''),
    'concl_click / instrumental_summary':
        ('Медицина_ДКЦЛД — 24 часа / Аналитический уровень',
         'https://datalens.ru/0vu92i4d9co0k-medicina-dkcld-analitika'),
    'concl_npkc / instrumental_summary_npkc':
        ('Медицина_Общие — Поток исследований',
         'https://datalens.ru/zic343hcd77kk-medicina-obshie-potok-issledovaniy'),
    'caop / count_caop':
        ('Медицина_ДКЦЛД — ЦАОП',
         'https://datalens.ru/0vu92i4d9co0k-medicina-dkcld-analitika?tab=X9K'),
    'описания_не_нпкц / assignment_results_other_mo':
        ('Медицина_ДКЦЛД — Аналитический уровень',
         'https://datalens.ru/0vu92i4d9co0k-medicina-dkcld-analitika?tab=w1'),
    'загрузка_амб_дни / workload_komet_day':
        ('Публичный — Загрузка оборудования',
         'https://datalens.ru/0y0bcncz4wh8k-publichnyy-zagruzka-oborudovaniya-agfa-amb-eris-kis-er'),
    'загрузка_амб_месяц / workload_komet_month':
        ('Публичный — Загрузка оборудования',
         'https://datalens.ru/0y0bcncz4wh8k-publichnyy-zagruzka-oborudovaniya-agfa-amb-eris-kis-er'),
    'загрузка_амб_неделя / workload_komet_week':
        ('Публичный — Загрузка оборудования',
         'https://datalens.ru/0y0bcncz4wh8k-publichnyy-zagruzka-oborudovaniya-agfa-amb-eris-kis-er'),
    'vitrina_sync / v_task_pin + v_undescribed':
        ('Публичный — Рейтинг МО (Пин) / Аудит Дефектура',
         'https://datalens.ru/uawp3bzpyjese-publichnyy-reyting-mopin'),
    'кис_врачи / kis_workload_doc':
        ('—', ''),
    'kis_eris / kis_eris':
        ('Медицина_Общие — Стационарные исследования',
         'https://datalens.ru/k35ptvlvixue5-medicina-obshie-stacionarnye-issledovaniya'),
    'oko_saurona / overdue_studies_monitoring':
        ('Медицина — Контроль отложенных исследований',
         'https://datalens.ru/izpccvuv9vrk2-medicina-kontrol-otlozhennyh-issledovaniy'),
    'svo_eris_llo / svo_eris_llo_examinations':
        ('—', ''),
    'guide_dismissed / guide_doctors_dismissal_v2':
        ('—', ''),
}


def _bx_send(dialog_id: str, text: str) -> None:
    """Отправляет сообщение в чат Битрикс24 с чанкованием по 3900 символов."""
    url    = f"{_BITRIX_WEBHOOK.rstrip('/')}/im.message.add.json"
    chunks = [text[i:i + 3900] for i in range(0, max(len(text), 1), 3900)]
    for chunk in chunks:
        try:
            resp = requests.post(url, json={"DIALOG_ID": dialog_id, "MESSAGE": chunk}, timeout=30)
            if not resp.ok:
                print(f"⚠️ Битрикс [{dialog_id}] {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"⚠️ Битрикс [{dialog_id}]: {e}")


class _Tee:
    """Дублирует вывод одновременно в несколько потоков (консоль + файл)."""
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass

    def fileno(self):
        return self._streams[0].fileno()


def _ntfy_start_notify():
    """Уведомление о старте скрипта с retry в фоновом потоке — не блокирует основной процесс."""
    def _send():
        msg = f"Запуск единого экспорта дашбордов ({datetime.now().strftime('%H:%M:%S')})"
        for attempt in range(1, 4):
            try:
                ok = send_ntfy_alert(
                    msg,
                    title="Export Started",
                    priority="default",
                    tags="rocket",
                    topic_override="push_mrc_dashboards_7895"
                )
                if ok:
                    print(f"🔔 ntfy старт: отправлено (попытка {attempt})")
                    return
            except Exception as e:
                print(f"⚠️ ntfy старт: попытка {attempt} — {e}")
            if attempt < 3:
                time.sleep(10)
        print("⚠️ ntfy старт: все 3 попытки не удались, продолжаю работу")

    t = threading.Thread(target=_send, daemon=True)
    t.start()


def _send_ntfy_chunked(message: str, title: str, priority: str = "default",
                       tags: str = None, topic_override: str = None):
    CHUNK = 3500
    enc = message.encode('utf-8')
    if len(enc) <= CHUNK:
        send_ntfy_alert(message, title=title, priority=priority, tags=tags,
                        topic_override=topic_override)
        return
    part1 = enc[:CHUNK].decode('utf-8', errors='ignore')
    part2 = enc[CHUNK:].decode('utf-8', errors='ignore')
    send_ntfy_alert(part1, title=f"{title} (1/2)", priority=priority, tags=tags,
                    topic_override=topic_override)
    send_ntfy_alert(part2, title=f"{title} (2/2)", priority=priority,
                    topic_override=topic_override)


# ===========================================================================
# ███████████████████████████████████████████████████████████████████████████
#  КОНФИГУРАЦИЯ — ГЛУБИНА СИНХРОНИЗАЦИИ (дней)
#  Измените эти значения перед запуском
# ███████████████████████████████████████████████████████████████████████████

DAYS_СТАДИ            = 20   # стади_up:             instrumental_examinations (Target CH)
                              #   удаляет и перегружает данные за последние N дней

DAYS_РАБОЧИЕ          = 5    # рабочие_списки_up:    v_instrumental_task_lists (PostgreSQL)
                              #   берёт записи за последние N дней
                              #   (встроено в SQL-запрос)

DAYS_ИИ1              = 4    # ии1_up:               validation_ai_results (PostgreSQL)
                              #   берёт conduct_date за последние N дней

DAYS_ИИ2              = 4    # ии2_up:               validation_eris_report (PostgreSQL)
                              #   берёт conduct_date за последние N дней

DAYS_KOMET            = 15   # conclusion_komet_up:  cometa_conclusions_summary (Target CH)
                              #   удаляет и перегружает данные за последние N дней

DAYS_CONCL_CLICK      = 30  # concl_click_up:       instrumental_summary (Target CH)

DAYS_CONCL_NPKC       = 20   # concl_npkc_up:        instrumental_summary_npkc (Target CH)
                              #   фильтр: assignment_describe_mu_id = '100001912827'

DAYS_ОПИСАНИЯ         = 3    # описания_не_нпкц:     assignment_results_other_mo (Target CH)
                              #   удаляет и перегружает данные за последние N дней

DAYS_КИС_ЕРИС         = 20   # kis_eris_up:           kis_eris (Target CH)
                              #   удаляет и перегружает данные за последние N дней

DAYS_КИС_ВРАЧИ        = 30  # кис_загрузка_врачей:   kis_workload_doc (Target CH)
                              #   удаляет и перегружает данные за последние N дней

DAYS_ОКО_САУРОНА      = 20   # oko_saurona_up:        overdue_studies_monitoring

DAYS_SVO_ERIS_LLO     = 20   # svo_eris_llo_up:       svo_eris_llo_examinations
                              #   удаляет и перегружает данные за последние N дней

DAYS_АИ_ЮЗИНГ         = 3    # ai_using_up:           ai_using_studies (Target CH)
                              #   ReplacingMergeTree: вставляет поверх, дубли схлопываются (Target CH)
                              #   удаляет и перегружает данные за последние N дней

DAYS_АИ_МОДЕЛИ        = 3    # ai_model_usage_up:     ai_model_usage_daily (Target CH)
                              #   частота использования моделей ИИ по модальностям
                              #   дубли по (modality, date, model_id, procedure_name, access_method) схлопываются

# Примечания:
#   расхождение_ии_up   (ai_norma_comparing, PG):  фиксированная дата '2025-10-01' в исходном скрипте
#   caop_export_up      (count_caop, PG):           фиксированная дата '2024-01-01' в исходном скрипте
#   instrumental_3w_up  (instrumental_examinations_3w): полный срез за 7 дней, DAYS_TO_SYNC не используется

# ===========================================================================
# Настройки VPN (один раз для всех)
# ===========================================================================
VPN_APP_PATH = cfg.VPN_APP_PATH
VPN_PASSWORD  = cfg.VPN_PASSWORD

PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Файловый лог: стартует сразу, до загрузки модулей ---
_log_start  = datetime.now()
_log_path   = os.path.join(SCRIPTS_DIR, f"dashboard_log_{_log_start.strftime('%Y-%m-%d_%H-%M-%S')}.log")
_log_file   = open(_log_path, 'w', encoding='utf-8')
_orig_stdout = sys.stdout
_orig_stderr = sys.stderr
sys.stdout  = _Tee(sys.__stdout__, _log_file)
sys.stderr  = _Tee(sys.__stderr__, _log_file)
print(f"📝 Лог: {_log_path}")
_ntfy_start_notify()
# ---


# ===========================================================================
# Единые функции VPN
# ===========================================================================

def _kill_vpn_process():
    """Принудительно завершает процесс TrGUI для чистого перезапуска."""
    result = subprocess.run(
        ['taskkill', '/F', '/IM', 'TrGUI.exe'],
        capture_output=True, text=True
    )
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
    """Проверяет реальную работу VPN через тестовый запрос к source ClickHouse."""
    try:
        import clickhouse_connect
        client = clickhouse_connect.get_client(
            host=cfg.CH_HOST, port=cfg.CH_PORT,
            username=cfg.CH_USER, password=cfg.CH_PASSWORD,
            database=cfg.CH_DATABASE, secure=True, verify=False,
            connect_timeout=10, send_receive_timeout=10
        )
        client.query("SELECT 1")
        client.close()
        print("   ✅ VPN проверка: ClickHouse отвечает.")
        return True
    except Exception as e:
        print(f"   ❌ VPN проверка: ClickHouse не отвечает — {e}")
        return False


def connect_vpn():
    MAX_ATTEMPTS = 3
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"🔄 Запускаю TrGUI (попытка {attempt}/{MAX_ATTEMPTS})...")
        send_ntfy_alert(
            f"Единый экспорт: подключаю VPN (попытка {attempt}/{MAX_ATTEMPTS})...",
            title="Unified VPN Connect", priority="default", tags="lock"
        )

        # Чистый старт: убиваем старый TrGUI перед каждой попыткой
        _kill_vpn_process()

        try:
            process = subprocess.Popen(VPN_APP_PATH)
            print(f"   PID: {process.pid}")
        except Exception as e:
            err = f"❌ Ошибка запуска TrGUI: {e}"
            print(err)
            if attempt == MAX_ATTEMPTS:
                send_ntfy_alert(err, title="VPN Fatal", priority="urgent", tags="fire",
                                topic_override="push_mrc_dashboards_7895")
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

        print(f"   ⏳ Ждём подключения VPN (20 сек)...")
        time.sleep(20)

        if _check_vpn_connectivity():
            print(f"✅ VPN подключён и проверен (попытка {attempt}).")
            send_ntfy_alert(
                f"VPN подключён (попытка {attempt})",
                title="Unified VPN Connected", priority="high", tags="key"
            )
            return  # Успех

        warn = f"⚠️ VPN попытка {attempt}/{MAX_ATTEMPTS}: CH не отвечает"
        print(warn)
        send_ntfy_alert(warn, title="VPN Retry", priority="default", tags="warning",
                        topic_override="push_mrc_dashboards_7895")

        if attempt < MAX_ATTEMPTS:
            # Отключаем VPN перед следующей попыткой
            try:
                disconnect_vpn()
            except Exception:
                _kill_vpn_process()
            print(f"   ⏳ Жду 30 сек перед попыткой {attempt + 1}...")
            time.sleep(30)

    fatal = f"❌ VPN не подключился после {MAX_ATTEMPTS} попыток — прерываю экспорт"
    print(fatal)
    send_ntfy_alert(fatal, title="VPN Fatal — Export Aborted", priority="urgent", tags="fire",
                    topic_override="push_mrc_dashboards_7895")
    raise RuntimeError(fatal)


def disconnect_vpn():
    print("🛑 Отключаю VPN (единое отключение)...")
    send_ntfy_alert("Единый экспорт: отключаю VPN...", title="Unified VPN Disconnect", priority="default", tags="unlock")

    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)

    print("🛑 VPN отключён.")
    send_ntfy_alert("VPN отключён (единый экспорт)", title="Unified VPN Disconnected", priority="default", tags="check")


# ===========================================================================
# Заглушки VPN для подмены в модулях
# ===========================================================================

def _noop_connect(*args, **kwargs):
    print("  ↳ [unified] connect_vpn() — пропускаю (VPN уже подключён)")


def _noop_disconnect(*args, **kwargs):
    print("  ↳ [unified] disconnect_vpn() — пропускаю (отключение будет в конце)")


# ===========================================================================
# Загрузка модуля по имени файла
# ===========================================================================

def _load(filename, modname):
    path = os.path.join(SCRIPTS_DIR, filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# Загрузка всех модулей и подмена VPN-функций
# ===========================================================================

print("📦 Загружаю модули...")

_modules_map = {
    'стади':       'стади_up.py',
    'расхождение': 'расхождение_ии_up.py',
    'рабочие':     'рабочие_списки_up.py',
    'инстр_3w':    'instrumental_3w_up.py',
    'ии1':         'ии1_up.py',
    'ии2':         'ии2_up.py',
    'komet':       'conclusion_komet_up.py',
    'concl':       'concl_click_up.py',
    'concl_npkc':  'concl_npkc_up.py',
    'caop':        'caop_export_up.py',
    'описания':    'описания не нпкц.py',
    'загрузка_день':  'загрузка_амб_дни_up.py',
    'загрузка_месяц': 'загрузка_месяцы_амб.py',
    'загрузка_неделя':'загрузка_недели_амб_up.py',
    'vitrina':        '../Pin/vitrina_sync.py',
    'kis_eris':       'kis_eris_up.py',
    'кис_врачи':      'кис_загрузка_врачей.py',
    'oko_saurona':    'oko_saurona_up.py',
    'svo_eris_llo':   'svo_eris_llo_up.py',
    'ai_using':       'ai_using_up.py',
    'ai_model_usage': 'ai_model_usage_up.py',
    'guide_dismissed':'guide_dismissed_up.py',
}

_mods = {}
for alias, fname in _modules_map.items():
    try:
        _mods[alias] = _load(fname, alias)
        print(f"  ✅ {fname}")
    except Exception as e:
        print(f"  ❌ {fname}: {e}")
        _mods[alias] = None

# Подменяем VPN-функции заглушками во всех модулях
for alias, mod in _mods.items():
    if mod is not None:
        mod.connect_vpn    = _noop_connect
        mod.disconnect_vpn = _noop_disconnect

# Передаём значения DAYS из конфигурации в модули (там, где используется DAYS_TO_SYNC)
if _mods['стади']:    _mods['стади'].DAYS_TO_SYNC    = DAYS_СТАДИ
if _mods['рабочие']:  _mods['рабочие'].DAYS_TO_SYNC  = DAYS_РАБОЧИЕ
if _mods['ии1']:      _mods['ии1'].DAYS_TO_SYNC      = DAYS_ИИ1
if _mods['ии2']:      _mods['ии2'].DAYS_TO_SYNC      = DAYS_ИИ2
if _mods['komet']:    _mods['komet'].DAYS_TO_SYNC    = DAYS_KOMET
if _mods['concl']:      _mods['concl'].DAYS_TO_SYNC      = DAYS_CONCL_CLICK
if _mods['concl_npkc']: _mods['concl_npkc'].DAYS_TO_SYNC = DAYS_CONCL_NPKC
if _mods['описания']:  _mods['описания'].DAYS_TO_SYNC  = DAYS_ОПИСАНИЯ
if _mods['kis_eris']:   _mods['kis_eris'].DAYS_TO_SYNC   = DAYS_КИС_ЕРИС
if _mods['кис_врачи']:    _mods['кис_врачи'].DAYS_TO_SYNC    = DAYS_КИС_ВРАЧИ
if _mods['oko_saurona']: _mods['oko_saurona'].DAYS_TO_SYNC = DAYS_ОКО_САУРОНА
if _mods['ai_using']:    _mods['ai_using'].DAYS_TO_SYNC    = DAYS_АИ_ЮЗИНГ
if _mods['ai_model_usage']: _mods['ai_model_usage'].DAYS_TO_SYNC = DAYS_АИ_МОДЕЛИ

print("📦 Все модули загружены.\n")


# ===========================================================================
# Вспомогательная обёртка выполнения задачи
# ===========================================================================

def _call_guide_dismissed(mod):
    """guide_dismissed_up.main() делает sys.exit(1) при ошибке — гасим это, чтобы не уронить весь скрипт."""
    try:
        mod.main()
        return True
    except SystemExit:
        return False


def _run_task(name, func):
    print(f"\n{'='*60}")
    print(f"▶️  Задача: {name}")
    print(f"{'='*60}")
    try:
        result = func()
        status = "✅ успешно" if result else "⚠️ завершено с флагом False"
        print(f"{status}: {name}")
        return bool(result)
    except Exception as e:
        print(f"❌ Ошибка в задаче '{name}': {e}")
        traceback.print_exc()
        return False


# ===========================================================================
# Главная функция
# ===========================================================================

def main():
    start_time = datetime.now()

    # Lock-файл: сигнал для watchdog что скрипт работает
    _lock_path = os.path.join(SCRIPTS_DIR, "dashboard.lock")
    with open(_lock_path, 'w', encoding='utf-8') as _lf:
        json.dump({"pid": os.getpid(), "start": start_time.isoformat()}, _lf)
    print(f"🔒 Lock: {_lock_path} (PID {os.getpid()})")

    print(f"\n{'#'*60}")
    print(f"🚀 Единый экспорт дашбордов — старт: {start_time.strftime('%H:%M:%S')}")
    print(f"{'#'*60}\n")

    send_ntfy_alert(
        "Запускаю единый экспорт всех дашбордов...",
        title="Unified Export Start",
        priority="default",
        tags="rocket"
    )

    m = _mods

    # -----------------------------------------------------------------------
    # ФАЗА 1: VPN включён — извлекаем данные из source ClickHouse в буферы
    # -----------------------------------------------------------------------
    try:
        connect_vpn()
    except Exception as e:
        crit = f"❌ Не удалось подключить VPN. Прерываю: {e}"
        print(crit)
        send_ntfy_alert(crit, title="Unified Export FATAL", priority="urgent", tags="fire")
        sys.exit(1)

    print(f"\n{'='*60}")
    print("📦 ФАЗА 1: Извлечение данных из source CH (VPN включён)")
    print(f"{'='*60}")

    extract_results = {}

    if m['стади']:
        extract_results['стади / instrumental_examinations'] = \
            _run_task("стади [extract]", lambda: m['стади'].extract_phase())

    if m['расхождение']:
        extract_results['расхождение_ии / ai_norma_comparing'] = \
            _run_task("расхождение [extract]", lambda: m['расхождение'].extract_phase())

    if m['рабочие']:
        extract_results['рабочие_списки / v_instrumental_task_lists'] = \
            _run_task("рабочие [extract]", lambda: m['рабочие'].extract_and_buffer_data())

    if m['инстр_3w']:
        extract_results['инстр_3w / instrumental_examinations_3w'] = \
            _run_task("инстр_3w [extract]", lambda: m['инстр_3w'].extract_phase())

    if m['ии1']:
        extract_results['ии1 / validation_ai_results'] = \
            _run_task("ии1 [extract]", lambda: m['ии1'].extract_phase())

    if m['ии2']:
        extract_results['ии2 / validation_eris_report'] = \
            _run_task("ии2 [extract]", lambda: m['ии2'].extract_phase())

    if m['komet']:
        extract_results['conclusion_komet / cometa_conclusions_summary'] = \
            _run_task("komet [extract]", lambda: m['komet'].extract_phase())

    if m['concl']:
        extract_results['concl_click / instrumental_summary'] = \
            _run_task("concl [extract]", lambda: m['concl'].extract_phase())

    if m['concl_npkc']:
        extract_results['concl_npkc / instrumental_summary_npkc'] = \
            _run_task("concl_npkc [extract]", lambda: m['concl_npkc'].extract_phase())

    if m['caop']:
        extract_results['caop / count_caop'] = \
            _run_task("caop [extract]", lambda: m['caop'].extract_phase())

    if m['описания']:
        extract_results['описания_не_нпкц / assignment_results_other_mo'] = \
            _run_task("описания_не_нпкц [extract]", lambda: m['описания'].extract_phase())

    if m['загрузка_день']:
        extract_results['загрузка_амб_дни / workload_komet_day'] = \
            _run_task("загрузка_день [extract]", lambda: m['загрузка_день'].extract_phase())

    if m['загрузка_месяц']:
        extract_results['загрузка_амб_месяц / workload_komet_month'] = \
            _run_task("загрузка_месяц [extract]", lambda: m['загрузка_месяц'].extract_phase())

    if m['загрузка_неделя']:
        extract_results['загрузка_амб_неделя / workload_komet_week'] = \
            _run_task("загрузка_неделя [extract]", lambda: m['загрузка_неделя'].extract_phase())

    if m['vitrina']:
        extract_results['vitrina_sync / v_task_pin + v_undescribed'] = \
            _run_task("vitrina [extract]", lambda: m['vitrina'].extract_phase())

    if m['кис_врачи']:
        extract_results['кис_врачи / kis_workload_doc'] = \
            _run_task("кис_врачи [extract]", lambda: m['кис_врачи'].extract_phase())

    if m['kis_eris']:
        extract_results['kis_eris / kis_eris'] = \
            _run_task("kis_eris [extract]", lambda: m['kis_eris'].extract_phase())

    if m['oko_saurona']:
        extract_results['oko_saurona / overdue_studies_monitoring'] = \
            _run_task("oko_saurona [extract]", lambda: m['oko_saurona'].extract_phase())

    if m['ai_using']:
        extract_results['ai_using / ai_using_studies'] = \
            _run_task("ai_using [extract]", lambda: m['ai_using'].extract_phase())

    if m['ai_model_usage']:
        extract_results['ai_model_usage / ai_model_usage_daily'] = \
            _run_task("ai_model_usage [extract]", lambda: m['ai_model_usage'].extract_phase())

    # -----------------------------------------------------------------------
    # Отключение VPN
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("🛑 Отключаю VPN перед загрузкой в целевые БД...")
    try:
        disconnect_vpn()
    except Exception as e:
        print(f"⚠️ Ошибка отключения VPN: {e}")
        send_ntfy_alert(f"⚠️ Ошибка отключения VPN: {e}", title="VPN Warn", priority="default", tags="warning")

    # -----------------------------------------------------------------------
    # ФАЗА 2: VPN выключен — загружаем из буферов в target ClickHouse / PostgreSQL
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("📤 ФАЗА 2: Загрузка из буферов в target БД (VPN выключен)")
    print(f"{'='*60}")

    load_results = {}

    if m['стади'] and extract_results.get('стади / instrumental_examinations'):
        load_results['стади / instrumental_examinations'] = \
            _run_task("стади [load]", lambda: m['стади'].load_phase())

    if m['расхождение'] and extract_results.get('расхождение_ии / ai_norma_comparing'):
        load_results['расхождение_ии / ai_norma_comparing'] = \
            _run_task("расхождение [load]", lambda: m['расхождение'].load_phase())

    if m['рабочие'] and extract_results.get('рабочие_списки / v_instrumental_task_lists'):
        load_results['рабочие_списки / v_instrumental_task_lists'] = \
            _run_task("рабочие [load]", lambda: m['рабочие'].load_buffer_to_ch())

    if m['инстр_3w'] and extract_results.get('инстр_3w / instrumental_examinations_3w'):
        load_results['инстр_3w / instrumental_examinations_3w'] = \
            _run_task("инстр_3w [load]", lambda: m['инстр_3w'].load_phase())

    if m['ии1'] and extract_results.get('ии1 / validation_ai_results'):
        load_results['ии1 / validation_ai_results'] = \
            _run_task("ии1 [load]", lambda: m['ии1'].load_phase())

    if m['ии2'] and extract_results.get('ии2 / validation_eris_report'):
        load_results['ии2 / validation_eris_report'] = \
            _run_task("ии2 [load]", lambda: m['ии2'].load_phase())

    if m['komet'] and extract_results.get('conclusion_komet / cometa_conclusions_summary'):
        load_results['conclusion_komet / cometa_conclusions_summary'] = \
            _run_task("komet [load]", lambda: m['komet'].load_phase())

    if m['concl'] and extract_results.get('concl_click / instrumental_summary'):
        load_results['concl_click / instrumental_summary'] = \
            _run_task("concl [load]", lambda: m['concl'].load_phase())

    if m['concl_npkc'] and extract_results.get('concl_npkc / instrumental_summary_npkc'):
        load_results['concl_npkc / instrumental_summary_npkc'] = \
            _run_task("concl_npkc [load]", lambda: m['concl_npkc'].load_phase())

    if m['caop'] and extract_results.get('caop / count_caop'):
        load_results['caop / count_caop'] = \
            _run_task("caop [load]", lambda: m['caop'].load_phase())

    if m['описания'] and extract_results.get('описания_не_нпкц / assignment_results_other_mo'):
        load_results['описания_не_нпкц / assignment_results_other_mo'] = \
            _run_task("описания_не_нпкц [load]", lambda: m['описания'].load_phase())

    if m['загрузка_день'] and extract_results.get('загрузка_амб_дни / workload_komet_day'):
        load_results['загрузка_амб_дни / workload_komet_day'] = \
            _run_task("загрузка_день [load]", lambda: m['загрузка_день'].load_phase())

    if m['загрузка_месяц'] and extract_results.get('загрузка_амб_месяц / workload_komet_month'):
        load_results['загрузка_амб_месяц / workload_komet_month'] = \
            _run_task("загрузка_месяц [load]", lambda: m['загрузка_месяц'].load_phase())

    if m['загрузка_неделя'] and extract_results.get('загрузка_амб_неделя / workload_komet_week'):
        load_results['загрузка_амб_неделя / workload_komet_week'] = \
            _run_task("загрузка_неделя [load]", lambda: m['загрузка_неделя'].load_phase())

    if m['vitrina'] and extract_results.get('vitrina_sync / v_task_pin + v_undescribed'):
        load_results['vitrina_sync / v_task_pin + v_undescribed'] = \
            _run_task("vitrina [load]", lambda: m['vitrina'].load_phase())

    if m['кис_врачи'] and extract_results.get('кис_врачи / kis_workload_doc'):
        load_results['кис_врачи / kis_workload_doc'] = \
            _run_task("кис_врачи [load]", lambda: m['кис_врачи'].load_phase())

    if m['kis_eris'] and extract_results.get('kis_eris / kis_eris'):
        load_results['kis_eris / kis_eris'] = \
            _run_task("kis_eris [load]", lambda: m['kis_eris'].load_phase())

    if m['oko_saurona'] and extract_results.get('oko_saurona / overdue_studies_monitoring'):
        load_results['oko_saurona / overdue_studies_monitoring'] = \
            _run_task("oko_saurona [load]", lambda: m['oko_saurona'].load_phase())

    if m['ai_using'] and extract_results.get('ai_using / ai_using_studies'):
        load_results['ai_using / ai_using_studies'] = \
            _run_task("ai_using [load]", lambda: m['ai_using'].load_phase())
        # страховочная проверка дублей study_uid — всегда, независимо от результата load
        _run_task("ai_using [dedup_check]", lambda: m['ai_using'].dedup_check())

    if m['ai_model_usage'] and extract_results.get('ai_model_usage / ai_model_usage_daily'):
        load_results['ai_model_usage / ai_model_usage_daily'] = \
            _run_task("ai_model_usage [load]", lambda: m['ai_model_usage'].load_phase())
        # страховочная проверка дублей ключа — всегда, независимо от результата load
        _run_task("ai_model_usage [dedup_check]", lambda: m['ai_model_usage'].dedup_check())

    if m['guide_dismissed']:
        # Не требует VPN (Bitrix24 API + target CH) — выполняется целиком здесь
        ok = _run_task("guide_dismissed", lambda: _call_guide_dismissed(m['guide_dismissed']))
        extract_results['guide_dismissed / guide_doctors_dismissal_v2'] = True
        load_results['guide_dismissed / guide_doctors_dismissal_v2'] = ok

    # -----------------------------------------------------------------------
    # Итог
    # -----------------------------------------------------------------------
    results = {}
    all_keys = set(extract_results) | set(load_results)
    for k in all_keys:
        ext_ok = extract_results.get(k, False)
        load_ok = load_results.get(k, ext_ok if k not in load_results else False)
        results[k] = bool(ext_ok and load_ok)

    duration = datetime.now() - start_time
    ok_tasks     = sorted(k for k, v in results.items() if v)
    failed_tasks = sorted(k for k, v in results.items() if not v)

    print(f"\n{'#'*60}")
    print(f"📊 Итог единого экспорта (время: {duration})")
    print(f"  ✅ Успешно ({len(ok_tasks)}):")
    for t in ok_tasks:
        print(f"      • {t}")
    if failed_tasks:
        print(f"  ❌ С ошибкой ({len(failed_tasks)}):")
        for t in failed_tasks:
            print(f"      • {t}")
    else:
        print(f"  ✅ Ошибок нет")
    print(f"{'#'*60}\n")

    ok_lines     = '\n'.join(f'  ✅ {t}' for t in ok_tasks)
    failed_lines = '\n'.join(f'  ❌ {t}' for t in failed_tasks)

    if not failed_tasks:
        msg = (f"Время: {str(duration).split('.')[0]}\n\n"
               f"Обновлено ({len(ok_tasks)}):\n{ok_lines}")
        _send_ntfy_chunked(msg, title="All dashboards updated", priority="high", tags="tada",
                           topic_override="push_mrc_dashboards_7895")
    else:
        msg = (f"Время: {str(duration).split('.')[0]}\n\n"
               f"OK ({len(ok_tasks)}):\n{ok_lines}\n\n"
               f"FAIL ({len(failed_tasks)}):\n{failed_lines}")
        _send_ntfy_chunked(msg, title=f"Export: {len(failed_tasks)} errors of {len(results)}",
                           priority="urgent", tags="warning",
                           topic_override="push_mrc_dashboards_7895")

    # --- Битрикс24 ---
    now_str = start_time.strftime("%d.%m.%Y")
    dur_str = str(duration).split('.')[0]

    # chat145691: полный лог из файла (все print()-выводы всех задач)
    try:
        _log_file.flush()
        with open(_log_path, encoding='utf-8') as _lf:
            full_log_text = _lf.read()
        bx_full_msg = f"[all_dashboards] {now_str} | ⏱ {dur_str}\n\n" + full_log_text
    except Exception as _e:
        bx_full_msg = f"[all_dashboards] {now_str} | ⏱ {dur_str}\n(лог недоступен: {_e})"

    # chat145721: структурированный отчёт с BB-кодами Битрикс24
    bx_sum_lines = [
        f"[B]Единый экспорт дашбордов[/B]  {now_str}",
        f"Время выполнения: {dur_str}",
        "",
    ]
    if ok_tasks:
        bx_sum_lines.append(f"[B]✅ Успешно ({len(ok_tasks)}/{len(results)}):[/B]")
        for t in ok_tasks:
            dash_name, dash_url = _DASHBOARD_URLS.get(t, ('', ''))
            bx_sum_lines.append(f"  ✅  {t}")
            if dash_name and dash_name != '—':
                if dash_url:
                    bx_sum_lines.append(f"       [B][URL={dash_url}]{dash_name}[/URL][/B]")
                else:
                    bx_sum_lines.append(f"       [B]{dash_name}[/B]")
    if failed_tasks:
        bx_sum_lines.append("")
        bx_sum_lines.append(f"[B]❌ С ошибкой ({len(failed_tasks)}/{len(results)}):[/B]")
        for t in failed_tasks:
            dash_name, dash_url = _DASHBOARD_URLS.get(t, ('', ''))
            bx_sum_lines.append(f"  ❌  {t}")
            if dash_name and dash_name != '—':
                if dash_url:
                    bx_sum_lines.append(f"       [B][URL={dash_url}]{dash_name}[/URL][/B]")
                else:
                    bx_sum_lines.append(f"       [B]{dash_name}[/B]")
    bx_summary = "\n".join(bx_sum_lines)

    _bx_send(_BX_LOG_DIALOG, bx_full_msg)
    _bx_send(_BX_SUMMARY_DIALOG, bx_summary)
    # ---

    # Удаляем lock-файл
    try:
        os.remove(_lock_path)
        print("🔓 Lock удалён.")
    except Exception:
        pass

    # --- Закрываем лог-файл ---
    sys.stdout = _orig_stdout
    sys.stderr = _orig_stderr
    _log_file.close()
    print(f"📝 Лог сохранён: {_log_path}")
    # ---

    if not failed_tasks:
        sys.exit(0)
    elif len(failed_tasks) / len(results) >= 0.7:
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
