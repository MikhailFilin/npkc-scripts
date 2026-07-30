"""
fetch_task_files.py
Скачивает новые Excel-файлы из задачи Битрикс24 (task_id = 7577).

Файлы приходят через чат задачи: сообщение с params.FILE_ID.
Скрипт читает сообщения чата, находит новые файлы и скачивает их.

Запуск: py -3 fetch_task_files.py [--once]
  --once   однократная проверка (по умолчанию — цикл раз в POLL_INTERVAL_SEC)
"""

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time

import requests

# bitrix_config лежит в ../bitrix_monitor/, ntfy_notifier — в ../scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bitrix_monitor"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from bitrix_config import BITRIX_WEBHOOK
from ntfy_notifier import send_ntfy_alert

# === Настройки ===
TASK_ID = 7577
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
STATE_FILE = os.path.join(os.path.dirname(__file__), "fetch_state.json")

POLL_INTERVAL_SEC = 120                       # ← интервал проверки чата (секунды)
NTFY_TOPIC = "push_mrc_dashboards_7895"      # топик ntfy для уведомлений
DATALAKE_HOST = "datalake.emias.ru"           # хост для проверки доступности
DATALAKE_WAIT_SEC = 10 * 60                   # 10 минут ожидания если datalake доступен
MAX_NEW_FILES = 10                            # максимум новых файлов за один цикл

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "fetch.log"),
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

_reshot_lock = threading.Lock()  # один reshot_sync в БД одновременно


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ntfy(message: str, title: str, priority: str = "default", tags: str = None) -> None:
    """Отправляет ntfy-уведомление в фоновом потоке — не блокирует основной процесс."""
    def _send():
        try:
            send_ntfy_alert(message, title=title, priority=priority,
                            tags=tags, topic_override=NTFY_TOPIC)
        except Exception as e:
            log.warning("ntfy: ошибка отправки — %s", e)
    threading.Thread(target=_send, daemon=True).start()


def _is_datalake_accessible(host: str = DATALAKE_HOST, port: int = 443, timeout: int = 5) -> bool:
    """Проверяет доступность datalake.emias.ru через TCP-соединение (порт 443)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            log.info("datalake.emias.ru доступен (TCP %s:%d)", host, port)
            return True
    except OSError:
        log.info("datalake.emias.ru недоступен (TCP %s:%d)", host, port)
        return False


def bx(method: str, params: dict | None = None) -> dict:
    url = f"{BITRIX_WEBHOOK}/{method}.json"
    try:
        r = requests.post(url, json=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("Bitrix24 API error [%s]: %s", method, e)
        return {}


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"seen_file_ids": [], "chat_id": None}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def get_task_chat_id(task_id: int) -> int | None:
    for etype in ("TASKS_TASK", "TASK"):
        data = bx("im.chat.get", {"ENTITY_TYPE": etype, "ENTITY_ID": task_id})
        result = data.get("result") or {}
        chat_id = result.get("id") or result.get("ID")
        if chat_id:
            return int(chat_id)
    return None


def get_chat_file_ids(chat_id: int) -> list[int]:
    file_ids = []
    data = bx("im.dialog.messages.get", {
        "DIALOG_ID": f"chat{chat_id}",
        "LIMIT": 50,
    })
    messages = data.get("result", {})
    if isinstance(messages, dict):
        messages = messages.get("messages", [])
    if not isinstance(messages, list):
        return file_ids
    for msg in messages:
        params = msg.get("params") or {}
        for fid in (params.get("FILE_ID") or []):
            try:
                file_ids.append(int(fid))
            except (ValueError, TypeError):
                pass
    return file_ids


def get_file_info(file_id: int) -> dict:
    data = bx("disk.file.get", {"id": file_id})
    return data.get("result") or {}


def download_file(file_name: str, download_url: str) -> str | None:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    safe_name = "".join(c if c not in r'\/:*?"<>|' else "_" for c in file_name)
    dest = os.path.join(DOWNLOAD_DIR, safe_name)
    try:
        r = requests.get(download_url, timeout=60, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        log.info("Скачан: %s (%d байт)", safe_name, os.path.getsize(dest))
        return dest
    except Exception as e:
        log.error("Ошибка скачивания %s: %s", file_name, e)
        return None


def _trigger_reshot_sync(excel_path: str) -> threading.Thread:
    """Запускает reshot_sync.py (без рефреша матвью) в фоновом потоке. Возвращает тред."""
    reshot_script = os.path.join(os.path.dirname(__file__), "reshot_sync.py")
    name = os.path.basename(excel_path)
    log.info("Запускаю reshot_sync для %s", name)

    _ntfy(f"Начинаю заливку: {name}", title="Upload Started", priority="default", tags="inbox_tray")

    def _run():
        with _reshot_lock:
            try:
                proc = subprocess.run(
                    [sys.executable, reshot_script, "--file", excel_path, "--no-refresh"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
                if proc.returncode == 0:
                    log.info("reshot_sync завершён успешно: %s", name)
                    _ntfy(f"Залит: {name}", title="Upload Done",
                          priority="default", tags="white_check_mark")
                else:
                    err = (proc.stderr or proc.stdout or "").strip()[:300]
                    log.error("reshot_sync завершён с ошибкой %d: %s", proc.returncode, err)
                    _ntfy(f"Ошибка заливки: {name}\n{err}", title="Upload Error",
                          priority="urgent", tags="x")
            except Exception as e:
                log.error("Не удалось запустить reshot_sync: %s", e)
                _ntfy(f"Ошибка запуска reshot_sync: {e}", title="Upload Error",
                      priority="urgent", tags="x")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _run_final_refresh() -> None:
    """Один рефреш матвью после всех заливок цикла."""
    reshot_script = os.path.join(os.path.dirname(__file__), "reshot_sync.py")
    log.info("Запускаю финальный рефреш матвью...")
    try:
        proc = subprocess.run(
            [sys.executable, reshot_script, "--refresh-only"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode == 0:
            log.info("Финальный рефреш матвью завершён успешно.")
        else:
            err = (proc.stderr or proc.stdout or "").strip()[:300]
            log.error("Ошибка финального рефреша матвью: %s", err)
    except Exception as e:
        log.error("Не удалось запустить финальный рефреш: %s", e)


def check_and_download() -> list[str]:
    state = load_state()
    seen = set(state.get("seen_file_ids", []))
    downloaded = []
    sync_threads: list[threading.Thread] = []

    chat_id = state.get("chat_id")
    if not chat_id:
        chat_id = get_task_chat_id(TASK_ID)
        if not chat_id:
            log.error("Не удалось найти чат задачи #%d", TASK_ID)
            return []
        state["chat_id"] = chat_id
        log.info("chat_id задачи #%d: %d", TASK_ID, chat_id)

    log.info("Проверяю чат %d задачи #%d...", chat_id, TASK_ID)
    file_ids = get_chat_file_ids(chat_id)
    log.info("Найдено файлов в чате: %d", len(file_ids))
    file_ids = file_ids[:MAX_NEW_FILES]

    # При первом запуске (нет state-файла) — только запоминаем, не скачиваем
    first_run = not state.get("initialized")
    if first_run:
        log.info("Первый запуск — запоминаю существующие файлы без скачивания.")

    for fid in file_ids:
        if fid in seen:
            continue

        if first_run:
            seen.add(fid)
            continue

        info = get_file_info(fid)
        if not info:
            log.warning("Нет инфо о файле #%d", fid)
            seen.add(fid)
            continue

        name = info.get("NAME") or info.get("name") or f"file_{fid}"
        download_url = info.get("DOWNLOAD_URL") or info.get("downloadUrl") or ""

        if not download_url:
            log.warning("Нет ссылки для файла #%d (%s)", fid, name)
            seen.add(fid)
            continue

        if not name.lower().endswith((".xlsx", ".xls", ".xlsm")):
            log.info("Пропускаю не-Excel: %s", name)
            seen.add(fid)
            continue

        log.info("Новый файл обнаружен: %s", name)
        _ntfy(f"Файл обнаружен: {name}", title="File Detected",
              priority="default", tags="inbox_tray")

        # Проверяем доступность datalake — если доступен, ждём перед заливкой
        if _is_datalake_accessible():
            wait_min = DATALAKE_WAIT_SEC // 60
            log.info("datalake.emias.ru доступен — жду %d мин перед заливкой...", wait_min)
            _ntfy(
                f"datalake.emias.ru доступен. Жду {wait_min} мин перед заливкой: {name}",
                title="Waiting Before Upload",
                priority="default",
                tags="hourglass_flowing_sand",
            )
            time.sleep(DATALAKE_WAIT_SEC)
        else:
            log.info("datalake.emias.ru недоступен — заливаю сразу.")

        path = download_file(name, download_url)
        if path:
            downloaded.append(path)
            sync_threads.append(_trigger_reshot_sync(path))
        seen.add(fid)

    state["seen_file_ids"] = list(seen)
    state["initialized"] = True
    save_state(state)

    if sync_threads:
        log.info("Жду завершения %d заливок...", len(sync_threads))
        for t in sync_threads:
            t.join()
        log.info("Все заливки завершены. Запускаю финальный рефреш матвью.")
        _run_final_refresh()

    return downloaded


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Скачивает новые Excel-файлы из задачи Битрикс24")
    parser.add_argument("--once", action="store_true", help="Однократная проверка и выход")
    args = parser.parse_args()

    if args.once:
        files = check_and_download()
        if files:
            print(f"Скачано файлов: {len(files)}")
            for f in files:
                print(f"  {f}")
        else:
            print("Новых файлов нет.")
        return

    log.info("Запуск в режиме мониторинга. Интервал: %d сек.", POLL_INTERVAL_SEC)
    while True:
        try:
            check_and_download()
        except Exception as e:
            log.error("Неожиданная ошибка: %s", e)
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
