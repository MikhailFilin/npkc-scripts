"""
vitrina_sync.py
Экспорт двух витрин из ClickHouse в PostgreSQL.

  data_views.v_task_pin              → datalake.v_task_pin
  dwh_views.v_undescribed_researches → datalake.v_undescribed_researches

Порядок: VPN вкл → выгрузка из ClickHouse в память → VPN выкл → заливка в PostgreSQL.
PostgreSQL (Yandex Cloud) недоступен через VPN, поэтому отключаем VPN перед заливкой.

Запуск: py -3 vitrina_sync.py
Планировщик задач: утром, ежедневно.

# INPUT:  ClickHouse datalake.emias.ru (через VPN)
# OUTPUT: PostgreSQL iao-database schemas datalake
"""

import subprocess
import pyautogui
import time
import sys
import os
import re

import clickhouse_connect
import pandas as pd
from sqlalchemy import create_engine, text

_project = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(_project, "scripts"))
sys.path.insert(0, _project)
import personal_config as cfg
from ntfy_notifier import send_ntfy_alert

# === Таблицы ===
VITRINA_VTP  = "data_views.v_task_pin"
VITRINA_VUR  = "dwh_views.v_undescribed_researches"
PG_TABLE_VTP = f"{cfg.PG_SCHEMA}.v_task_pin"
PG_TABLE_VUR = f"{cfg.PG_SCHEMA}.v_undescribed_researches"

# Столбцы с текстовыми комментариями — требуют очистки
CLEAN_COL_VTP = "task_pin_created_comment"
CLEAN_COL_VUR = "comments"


# ---------------------------------------------------------------------------
# VPN
# ---------------------------------------------------------------------------

def connect_vpn():
    print("🔄 Запускаю VPN...")
    send_ntfy_alert("Запуск VPN для синхронизации витрин Pin...", title="Pin VPN", priority="default", tags="lock")
    try:
        subprocess.Popen(cfg.VPN_APP_PATH)
    except Exception as e:
        send_ntfy_alert(f"Ошибка запуска VPN: {e}", title="Pin VPN Error", priority="urgent", tags="warning")
        raise
    time.sleep(15)
    windows = pyautogui.getWindowsWithTitle('')
    for window in windows:
        if any(kw in window.title.lower() for kw in ['check point', 'trgui', 'endpoint']):
            try:
                window.activate()
                time.sleep(2)
                break
            except Exception:
                pass
    pyautogui.click(cfg.PASSWORD_FIELD_X, cfg.PASSWORD_FIELD_Y)
    time.sleep(0.5)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.3)
    pyautogui.press('delete')
    time.sleep(0.3)
    for char in cfg.VPN_PASSWORD:
        pyautogui.write(char)
        time.sleep(0.1)
    time.sleep(1)
    pyautogui.click(cfg.CONNECT_BUTTON_X, cfg.CONNECT_BUTTON_Y)
    time.sleep(15)
    print("✅ VPN подключён.")
    send_ntfy_alert("VPN подключён", title="Pin VPN", priority="default", tags="key")


def disconnect_vpn():
    print("🛑 Отключаю VPN...")
    pyautogui.rightClick(cfg.RIGHT_CLICK_MENU_X, cfg.RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(cfg.CONFIRMATION_CLICK_X, cfg.CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print("🛑 VPN отключён.")
    send_ntfy_alert("VPN отключён", title="Pin VPN", priority="default", tags="unlock")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_comment(value: str | None) -> str:
    """Убирает кавычки, лишние пробелы и переносы строк из текстовых полей."""
    if not value:
        return value
    value = str(value)
    value = value.replace('"', '')
    value = value.replace('\r', ' ')
    value = value.replace('\n', ' ')
    value = ' '.join(value.split())
    return value


def fix_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Убирает BOM и тройные кавычки из имён столбцов."""
    df.columns = [re.sub(r'^"{1,3}|"{1,3}$', '', c).lstrip('﻿') for c in df.columns]
    return df


def truncate_and_load(df: pd.DataFrame, pg_table: str, engine) -> int:
    """Очищает таблицу и загружает DataFrame."""
    schema, table = pg_table.split('.')
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {pg_table}"))
    df.to_sql(table, engine, schema=schema, if_exists='append', index=False, method='multi', chunksize=500)
    return len(df)


# ---------------------------------------------------------------------------
# Шаг 1: выгрузка из ClickHouse (пока VPN включён)
# ---------------------------------------------------------------------------

def fetch_from_ch(ch_client, query: str, clean_col: str | None, name: str) -> pd.DataFrame | None:
    print(f"📥 Выгружаю {name} из ClickHouse...")
    try:
        result = ch_client.query(query)
        df = result.to_pandas() if hasattr(result, 'to_pandas') else pd.DataFrame(
            result.result_rows, columns=result.column_names
        )
        df = fix_column_names(df)
        if clean_col and clean_col in df.columns:
            df[clean_col] = df[clean_col].apply(clean_comment)
        print(f"  Получено строк: {len(df)}")
        return df
    except Exception as e:
        msg = f"❌ Ошибка выгрузки {name}: {e}"
        print(msg)
        send_ntfy_alert(msg[:200], title="Pin Sync Error", priority="urgent", tags="warning")
        return None


# ---------------------------------------------------------------------------
# Шаг 2: заливка в PostgreSQL (после отключения VPN)
# ---------------------------------------------------------------------------

def load_to_pg(df: pd.DataFrame, pg_table: str, engine, name: str) -> bool:
    print(f"📤 Заливаю {name} в PostgreSQL...")
    send_ntfy_alert(f"Заливка {name} в PG...", title="Pin Sync", priority="default", tags="outbox")
    try:
        count = truncate_and_load(df, pg_table, engine)
        msg = f"✅ {name}: загружено {count} строк → {pg_table}"
        print(msg)
        send_ntfy_alert(msg, title="Pin Sync OK", priority="default", tags="white_check_mark")
        return True
    except Exception as e:
        msg = f"❌ Ошибка заливки {name}: {e}"
        print(msg)
        send_ntfy_alert(msg[:200], title="Pin Sync Error", priority="urgent", tags="warning")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("🚀 Старт vitrina_sync")
    send_ntfy_alert("Запуск синхронизации витрин Pin...", title="Pin Sync Start", priority="default", tags="rocket")

    # === Шаг 1: VPN вкл → выгрузка из ClickHouse в память ===
    connect_vpn()

    dataframes: dict = {}
    ch = None
    try:
        ch = clickhouse_connect.get_client(
            host=cfg.CH_HOST, port=cfg.CH_PORT,
            username=cfg.CH_USER, password=cfg.CH_PASSWORD,
            database=cfg.CH_DATABASE,
            secure=True, verify=False,
            send_receive_timeout=3600, connect_timeout=60,
        )
        print("✅ ClickHouse подключён.")

        dataframes["vtp"] = fetch_from_ch(
            ch,
            query=f"SELECT * FROM {VITRINA_VTP}",
            clean_col=CLEAN_COL_VTP,
            name="v_task_pin",
        )
        dataframes["vur"] = fetch_from_ch(
            ch,
            query=f"SELECT * FROM {VITRINA_VUR}",
            clean_col=CLEAN_COL_VUR,
            name="v_undescribed_researches",
        )
    except Exception as e:
        msg = f"❌ Критическая ошибка ClickHouse: {e}"
        print(msg)
        send_ntfy_alert(msg[:200], title="Pin Sync FAIL", priority="urgent", tags="sos")
    finally:
        if ch:
            ch.close()
        disconnect_vpn()  # VPN выкл — до подключения к PostgreSQL

    # === Шаг 2: VPN выкл → заливка в PostgreSQL (Yandex Cloud) ===
    pg_url = f"postgresql://{cfg.PG_USER}:{cfg.PG_PASSWORD}@{cfg.PG_HOST}:{cfg.PG_PORT}/{cfg.PG_DATABASE}"
    engine = create_engine(pg_url)

    ok = True
    df_vtp = dataframes.get("vtp")
    df_vur = dataframes.get("vur")

    if df_vtp is not None:
        ok &= load_to_pg(df_vtp, PG_TABLE_VTP, engine, "v_task_pin")
    else:
        ok = False

    if df_vur is not None:
        ok &= load_to_pg(df_vur, PG_TABLE_VUR, engine, "v_undescribed_researches")
    else:
        ok = False

    # === Шаг 3: Материализация ===
    if ok:
        print("🔄 Обновляю материализованные вью...")
        materialized_views = [
            "datalake.v_task_pin_dash",
            "datalake.defects",
            "datalake.defects_completed",
            "datalake.pin_mo_mrc",
        ]
        try:
            with engine.begin() as conn:
                for mv in materialized_views:
                    print(f"  🔄 REFRESH {mv}...")
                    conn.execute(text(f"REFRESH MATERIALIZED VIEW {mv}"))
                    print(f"  ✅ {mv}")
            send_ntfy_alert(
                "✅ Завершено обновление дашборда ПИН и Рейтинг МО (без сегодняшних решотов)",
                title="Pin Dashboard Updated",
                priority="high",
                tags="bar_chart",
            )
            print("✅ Готово.")
        except Exception as e:
            msg = f"❌ Ошибка рефреша матвью: {e}"
            print(msg)
            send_ntfy_alert(msg[:200], title="Pin Refresh Error", priority="urgent", tags="sos")
    else:
        send_ntfy_alert("⚠️ vitrina_sync завершён с ошибками", title="Pin Sync FAIL", priority="urgent", tags="warning")
        print("⚠️ Завершено с ошибками.")

    return ok


# ---------------------------------------------------------------------------
# Фазовый интерфейс для all_dashboards_up.py
# ---------------------------------------------------------------------------

_dataframes: dict = {}


def extract_phase() -> bool:
    """VPN включён (или nooped) — выгружает обе витрины из CH в модульный буфер."""
    global _dataframes
    _dataframes = {}
    connect_vpn()
    ch = None
    try:
        ch = clickhouse_connect.get_client(
            host=cfg.CH_HOST, port=cfg.CH_PORT,
            username=cfg.CH_USER, password=cfg.CH_PASSWORD,
            database=cfg.CH_DATABASE,
            secure=True, verify=False,
            send_receive_timeout=3600, connect_timeout=60,
        )
        print("✅ ClickHouse подключён (vitrina extract_phase).")
        _dataframes["vtp"] = fetch_from_ch(ch, f"SELECT * FROM {VITRINA_VTP}", CLEAN_COL_VTP, "v_task_pin")
        _dataframes["vur"] = fetch_from_ch(ch, f"SELECT * FROM {VITRINA_VUR}", CLEAN_COL_VUR, "v_undescribed_researches")
        return _dataframes.get("vtp") is not None and _dataframes.get("vur") is not None
    except Exception as e:
        msg = f"❌ Критическая ошибка ClickHouse (vitrina extract_phase): {e}"
        print(msg)
        send_ntfy_alert(msg[:200], title="Pin Sync FAIL", priority="urgent", tags="sos")
        return False
    finally:
        if ch:
            ch.close()
        disconnect_vpn()


def load_phase() -> bool:
    """VPN выключен — заливает буфер в PG и обновляет матвью."""
    if not _dataframes:
        print("❌ load_phase (vitrina): буфер пуст")
        return False
    pg_url = f"postgresql://{cfg.PG_USER}:{cfg.PG_PASSWORD}@{cfg.PG_HOST}:{cfg.PG_PORT}/{cfg.PG_DATABASE}"
    engine = create_engine(pg_url)
    ok = True
    df_vtp = _dataframes.get("vtp")
    df_vur = _dataframes.get("vur")
    if df_vtp is not None:
        ok &= load_to_pg(df_vtp, PG_TABLE_VTP, engine, "v_task_pin")
    else:
        ok = False
    if df_vur is not None:
        ok &= load_to_pg(df_vur, PG_TABLE_VUR, engine, "v_undescribed_researches")
    else:
        ok = False
    if ok:
        print("🔄 Обновляю материализованные вью...")
        materialized_views = [
            "datalake.v_task_pin_dash",
            "datalake.defects",
            "datalake.defects_completed",
            "datalake.pin_mo_mrc",
        ]
        try:
            with engine.begin() as conn:
                for mv in materialized_views:
                    print(f"  🔄 REFRESH {mv}...")
                    conn.execute(text(f"REFRESH MATERIALIZED VIEW {mv}"))
                    print(f"  ✅ {mv}")
            send_ntfy_alert(
                "✅ Завершено обновление дашборда ПИН и Рейтинг МО (без сегодняшних решотов)",
                title="Pin Dashboard Updated",
                priority="high",
                tags="bar_chart",
            )
            print("✅ Готово.")
        except Exception as e:
            msg = f"❌ Ошибка рефреша матвью: {e}"
            print(msg)
            send_ntfy_alert(msg[:200], title="Pin Refresh Error", priority="urgent", tags="sos")
    return ok


if __name__ == "__main__":
    main()
