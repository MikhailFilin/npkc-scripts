"""
ntfy_notifier.py
Модуль для отправки уведомлений через ntfy (self-hosted или public).
Использование: from ntfy_notifier import send_ntfy_alert

Поддерживает фильтрацию по топикам через SILENT_TOPICS.
"""

import os
import sys
import requests
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import personal_config as cfg

# ================= НАСТРОЙКИ =================
NTFY_SERVER      = getattr(cfg, 'NTFY_SERVER', 'https://ntfy.sh')
NTFY_TOPIC       = cfg.NTFY_TOPIC
NTFY_TOPIC_FINAL = cfg.NTFY_TOPIC_FINAL

# Таймаут запроса (сек)
REQUEST_TIMEOUT = 15

# Включить/выключить уведомления
ENABLED = True

# 🔕 Топики, в которые НЕ отправлять уведомления (для отладки/тишины)
# Добавьте сюда названия топиков, которые нужно "заглушить"
# Пример: SILENT_TOPICS = ["my_caop_bot_alerts_2026", "test_topic"]
SILENT_TOPICS = ["my_caop_bot_alerts_2026"]
# =============================================


def send_ntfy_alert(
    message: str,
    title: str = "Alert",
    priority: str = "default",
    tags: Optional[str] = None,
    click_url: Optional[str] = None,
    topic_override: Optional[str] = None
) -> bool:
    """
    Отправляет push-уведомление через ntfy.
    
    Args:
        message: Текст сообщения (можно русский, эмодзи, любой UTF-8)
        title: Заголовок (ТОЛЬКО латиница/English!)
        priority: min, low, default, high, urgent
        tags: Иконка (название эмодзи: "warning", "robot", "fire")
        click_url: Ссылка при клике на уведомление
        topic_override: Явное переопределение топика (если None — используется NTFY_TOPIC)
    
    Returns:
        bool: True если отправлено успешно (или пропущено по настройке), False если ошибка
    """
    if not ENABLED:
        return False
    
    # Проверка: заголовки HTTP должны быть в Latin-1
    try:
        title.encode('latin-1')
    except UnicodeEncodeError:
        print(f"⚠️ ntfy: Заголовок '{title}' содержит недопустимые символы. Используйте английский.")
        return False
    
    # Выбираем топик: если передан override — используем его, иначе — основной
    topic = topic_override if topic_override else NTFY_TOPIC
    
    # 🔕 ПРОВЕРКА: если топик в списке "тихих" — пропускаем отправку, но возвращаем True
    # (чтобы не ломать логику основного скрипта)
    if topic in SILENT_TOPICS:
        print(f"🔕 ntfy: Уведомление для топика '{topic}' пропущено (в SILENT_TOPICS)")
        return True
    
    url = f"{NTFY_SERVER}/{topic}"
    
    headers = {
        "Title": title,
        "Priority": priority,
    }
    
    if tags:
        headers["Tags"] = tags
    if click_url:
        headers["Click"] = click_url
    
    try:
        response = requests.post(
            url,
            data=message.encode('utf-8'),
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        print(f"🔔 ntfy [{topic}]: '{title}' -> {message[:50]}...")
        return True
        
    except requests.exceptions.Timeout:
        print(f"⚠️ ntfy: Таймаут (сервер не ответил за {REQUEST_TIMEOUT}с)")
        return False
    except requests.exceptions.ConnectionError:
        print(f"⚠️ ntfy: Ошибка подключения к {NTFY_SERVER}")
        return False
    except Exception as e:
        print(f"⚠️ ntfy: Ошибка отправки: {type(e).__name__}: {e}")
        return False


# === Тест модуля ===
if __name__ == "__main__":
    print("🧪 Тест модуля ntfy_notifier...")
    print(f"📋 Основной топик: {NTFY_TOPIC}")
    print(f"📋 Финальный топик: {NTFY_TOPIC_FINAL or 'не задан'}")
    print(f"🔕 Тихие топики: {SILENT_TOPICS if SILENT_TOPICS else 'нет'}")
    print("-" * 50)
    
    # Тест 1: уведомление в основной топик (должно быть пропущено, если в SILENT_TOPICS)
    print("\n📤 Тест 1: Отправка в основной топик...")
    success1 = send_ntfy_alert(
        message="🔥 Тест из отдельного модуля! Всё работает.",
        title="Test Notification",
        priority="high",
        tags="wave"
    )
    print(f"   Результат: {'✅ Отправлено / Пропущено (OK)' if success1 else '❌ Ошибка'}")
    
    # Тест 2: уведомление в финальный топик (должно уйти всегда)
    if NTFY_TOPIC_FINAL:
        print(f"\n📤 Тест 2: Отправка в финальный топик '{NTFY_TOPIC_FINAL}'...")
        success2 = send_ntfy_alert(
            message="✅ Финальный тест в отдельный топик.",
            title="Test Final",
            priority="high",
            tags="tada",
            topic_override=NTFY_TOPIC_FINAL
        )
        print(f"   Результат: {'✅ Отправлено' if success2 else '❌ Ошибка'}")
        print("\n✅ Тест завершён!" if success1 and success2 else "\n❌ Были ошибки при отправке.")
    else:
        print("\n✅ Тест завершён!" if success1 else "\n❌ Ошибка при отправке.")