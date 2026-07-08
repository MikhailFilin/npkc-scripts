"""
ai_model_usage_up.py  →  dwh_test_db.ai_model_usage_daily  (Target CH)

Частота использования конкретных моделей ИИ по модальностям (КТ/МРТ/РГ/ММГ) в НПКЦ.
model_id достаётся напрямую из kis.v_ai_log.series_iuid (предпоследний сегмент UID,
разделённый точками) — проверено, что это даёт 100% совпадение с model_id из
raw_data.aiResult.modelId витрины dwh_views.v_eris_report, без необходимости
подключаться к самой витрине.

device_type (модальность) для РГ/ММГ нельзя определить только по kis.v_ai_log.modality
(оба используют общий generic тег 'ASMT') — поэтому study_uid берутся из
data_views.v_eris_assignment_results, как в ai_using_up.py.

Фаза 1 (VPN ON):  extract_phase()  — запросы к datalake, результат в _buffer
Фаза 2 (VPN OFF): load_phase()     — вставка из _buffer в dwh_test_db

Интегрируется в all_dashboards_up.py.
Глубина: DAYS_TO_SYNC (по умолчанию 30, задаётся из all_dashboards_up).

# INPUT:  data_views.v_eris_assignment_results, kis.v_ai_log  (datalake.emias.ru)
# OUTPUT: dwh_test_db.ai_model_usage_daily  (Yandex Cloud CH)
"""

import sys
import subprocess
import time
import traceback
import numpy as np
import pandas as pd
import pyautogui
import clickhouse_connect
from datetime import date, timedelta

sys.path.insert(0, r'C:\Users\risen\Desktop\MySecondBrain\project\scripts')
import personal_config as cfg
from ntfy_notifier import send_ntfy_alert

# Задаётся из all_dashboards_up.py
DAYS_TO_SYNC = 30

MU_ID = '100001912827'

MODALITY_CONFIG = {
    'КТ':  {'device_type': 'КТ',  'ai_log_modalities': ('CT', 'CT_ASMT')},
    'МРТ': {'device_type': 'МРТ', 'ai_log_modalities': ('MR', 'MR_ASMT')},
    # У РГ/ММГ нет отдельного суффикса *_ASMT — ИИ-серия в kis.v_ai_log
    # помечена тем же generic modality='ASMT', что и обычный просмотр.
    # Различить их можно только через device_type из v_eris_assignment_results.
    'РГ':  {'device_type': 'РГ',  'ai_log_modalities': ('ASMT', 'CR', 'DX')},
    'ММГ': {'device_type': 'ММГ', 'ai_log_modalities': ('ASMT', 'MG')},
}

# Буфер между фазами
_buffer: pd.DataFrame | None = None


# ===========================================================================
# VPN (подменяются заглушками в all_dashboards_up.py)
# ===========================================================================
VPN_APP_PATH            = cfg.VPN_APP_PATH
VPN_PASSWORD            = cfg.VPN_PASSWORD
PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y


def connect_vpn():
    print('[ai_model_usage] Запускаю TrGUI...')
    send_ntfy_alert('Запускаю VPN для ai_model_usage...', title='VPN Connect', priority='default', tags='lock')
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f'   PID: {process.pid}')
    except Exception as e:
        msg = f'Ошибка запуска VPN: {e}'
        print(msg)
        send_ntfy_alert(msg, title='VPN Error', priority='urgent', tags='warning')
        raise
    time.sleep(15)

    for window in pyautogui.getWindowsWithTitle(''):
        if any(kw in window.title.lower() for kw in ['check point', 'trgui', 'endpoint']):
            try:
                window.activate()
                time.sleep(2)
                print(f"   Окно '{window.title}' активировано.")
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
    for char in VPN_PASSWORD:
        pyautogui.write(char)
        time.sleep(0.1)
    time.sleep(1)
    pyautogui.click(CONNECT_BUTTON_X, CONNECT_BUTTON_Y)
    time.sleep(15)
    print('[ai_model_usage] VPN подключён.')
    send_ntfy_alert('VPN подключён', title='VPN Connected', priority='high', tags='key')


def disconnect_vpn():
    print('[ai_model_usage] Отключаю VPN...')
    send_ntfy_alert('Отключаюсь от VPN...', title='VPN Disconnect', priority='default', tags='unlock')
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print('[ai_model_usage] VPN отключён.')
    send_ntfy_alert('VPN отключён', title='VPN Disconnected', priority='default', tags='check')


# ===========================================================================
# Подключения
# ===========================================================================
def _source_client():
    return clickhouse_connect.get_client(
        host=cfg.CH_HOST, port=cfg.CH_PORT,
        username=cfg.CH_USER, password=cfg.CH_PASSWORD,
        secure=True, verify=False,
        connect_timeout=15, send_receive_timeout=900,
    )

def _target_client():
    return clickhouse_connect.get_client(
        host=cfg.CH_HOST_TARGET, port=cfg.CH_PORT_TARGET,
        username=cfg.CH_USER_TARGET, password=cfg.CH_PASSWORD_TARGET,
        secure=True, verify=False,
        connect_timeout=15, send_receive_timeout=600,
    )


# ===========================================================================
# SQL-анализ одной модальности
# ===========================================================================
def _analyse_one(client, date_from: str, date_to: str, modality: str) -> pd.DataFrame:
    conf = MODALITY_CONFIG[modality]
    device_type   = conf['device_type']
    ai_modalities = ", ".join(f"'{m}'" for m in conf['ai_log_modalities'])

    sql = f"""
        WITH
        base_study AS (
            -- буфер ±10 дней: assignment_result_doc_created_date не обязан
            -- совпадать день-в-день с opened_at из лога
            SELECT DISTINCT study_uid
            FROM data_views.v_eris_assignment_results
            WHERE assignment_describe_mu_id = '{MU_ID}'
              AND device_type = '{device_type}'
              AND toDate(assignment_result_doc_created_date)
                  BETWEEN '{date_from}'::Date - INTERVAL 10 DAY AND '{date_to}'::Date + INTERVAL 10 DAY
        ),
        ai_log_msk AS (
            SELECT
                l.study_iuid,
                l.opened_at + INTERVAL 3 HOUR AS ts_msk,
                l.procedure_name,
                l.access_method,
                -- model_id = предпоследний сегмент series_iuid, напр.
                -- '...202607061936370545.1003.1' -> 1003
                toInt64OrNull(arrayElement(splitByChar('.', assumeNotNull(l.series_iuid)), -2)) AS model_id
            FROM kis.v_ai_log AS l
            INNER JOIN base_study AS b ON l.study_iuid = b.study_uid
            WHERE l.modality IN ({ai_modalities})
              AND toDate(l.opened_at) BETWEEN '{date_from}' AND '{date_to}'
        )
        SELECT
            '{modality}'                AS modality,
            toDate(ts_msk)               AS date,
            model_id,
            procedure_name,
            access_method,
            count()                      AS event_count
        FROM ai_log_msk
        -- часть series_iuid (в основном РГ/CR и часть ММГ/MG) — это родной UID
        -- устройства без суффикса модели ИИ; предпоследний сегмент там может
        -- оказаться датой-временем (20260706141218) или другим большим числом.
        -- Реальные model_id по всей системе лежат в диапазоне 101-1300.
        WHERE model_id BETWEEN 1 AND 9999
        GROUP BY modality, date, model_id, procedure_name, access_method
        ORDER BY date, model_id
    """
    print(f'  [{modality}] запрос за {date_from} — {date_to}...')
    df = client.query_df(sql)
    print(f'  [{modality}] получено {len(df)} строк')
    return df


# ===========================================================================
# ФАЗА 1: извлечение (VPN ON, datalake)
# ===========================================================================
def extract_phase() -> bool:
    global _buffer
    today     = date.today()
    date_to   = today - timedelta(days=1)
    date_from = date_to - timedelta(days=DAYS_TO_SYNC - 1)
    d_from, d_to = str(date_from), str(date_to)

    print(f'\n[ai_model_usage] ФАЗА 1 — период {d_from} — {d_to}')
    try:
        client = _source_client()
        frames = [_analyse_one(client, d_from, d_to, mod) for mod in MODALITY_CONFIG]
        _buffer = pd.concat(frames, ignore_index=True)
        _buffer.attrs['date_from'] = d_from
        _buffer.attrs['date_to']   = d_to
        total = len(_buffer)
        print(f'[ai_model_usage] извлечено: {total} строк ({" + ".join(MODALITY_CONFIG)})')
        return total > 0
    except Exception as e:
        print(f'[ai_model_usage] ❌ extract_phase: {e}')
        return False


# ===========================================================================
# ФАЗА 2: загрузка (VPN OFF, target CH)
# ===========================================================================
def _prepare(df: pd.DataFrame, date_from: str, date_to: str) -> pd.DataFrame:
    upload = df.copy()

    upload['period_from'] = date.fromisoformat(date_from)
    upload['period_to']   = date.fromisoformat(date_to)

    col_order = [
        'modality', 'date', 'model_id', 'procedure_name', 'access_method',
        'event_count', 'period_from', 'period_to',
    ]
    upload = upload[col_order]

    upload['model_id']    = pd.to_numeric(upload['model_id'], errors='coerce').astype('Int64')
    upload['event_count'] = pd.to_numeric(upload['event_count'], errors='coerce').fillna(0).astype(int)

    def _clean(v):
        if v is None:
            return None
        if isinstance(v, float) and np.isnan(v):
            return None
        return v

    for col in upload.select_dtypes(include=['object']).columns:
        upload[col] = upload[col].apply(_clean)

    return upload


def _dedup_mutation(tc):
    """ALTER TABLE ... DELETE — оставляет по каждому ключу (modality, date,
    model_id, procedure_name, access_method) только самую свежую версию
    (по loaded_at). Кидает исключение при ошибке.
    Nullable-колонки в ключе дедупа сравниваются через ifNull(...), т.к.
    NULL NOT IN (...) в ClickHouse всегда unknown и строки бы не удалялись."""
    tc.command(
        '''
        ALTER TABLE dwh_test_db.ai_model_usage_daily
        DELETE WHERE (modality, date, model_id,
                       ifNull(procedure_name, ''), ifNull(access_method, ''), loaded_at) NOT IN (
            SELECT modality, date, model_id,
                   ifNull(procedure_name, ''), ifNull(access_method, ''), max(loaded_at)
            FROM dwh_test_db.ai_model_usage_daily
            GROUP BY modality, date, model_id, ifNull(procedure_name, ''), ifNull(access_method, '')
        )
        ''',
        settings={'mutations_sync': '1'},
    )


def dedup_check(verbose: bool = True) -> bool:
    """Безопасная самостоятельная проверка/устранение дублей в
    dwh_test_db.ai_model_usage_daily. Не зависит от _buffer — можно вызывать
    отдельно (в конце main() или из all_dashboards_up.py) как страховку."""
    try:
        tc = _target_client()
        total = int(tc.query_df(
            "SELECT count() AS cnt FROM dwh_test_db.ai_model_usage_daily"
        ).iloc[0, 0])
        uniq = int(tc.query_df(
            """SELECT count() AS cnt FROM (
                   SELECT modality, date, model_id, procedure_name, access_method
                   FROM dwh_test_db.ai_model_usage_daily
                   GROUP BY modality, date, model_id, procedure_name, access_method
               )"""
        ).iloc[0, 0])
        dup = total - uniq

        if dup <= 0:
            if verbose:
                print(f'[ai_model_usage] dedup_check: дублей нет ({total} строк)')
            return True

        if verbose:
            print(f'[ai_model_usage] dedup_check: найдено {dup} дублей ключа '
                  f'({total} строк, {uniq} уникальных) — устраняю...')
        _dedup_mutation(tc)

        total_after = int(tc.query_df(
            "SELECT count() AS cnt FROM dwh_test_db.ai_model_usage_daily"
        ).iloc[0, 0])
        if verbose:
            print(f'[ai_model_usage] dedup_check: удалено {total - total_after} строк, '
                  f'осталось {total_after}')
        return True
    except Exception as e:
        print(f'[ai_model_usage] ❌ dedup_check: {e}')
        print(traceback.format_exc())
        send_ntfy_alert(
            f'ai_model_usage dedup_check: ошибка — {e}',
            title='AI Model Usage Dedup Check Failed', priority='urgent', tags='warning',
        )
        return False


def load_phase() -> bool:
    global _buffer
    if _buffer is None or len(_buffer) == 0:
        print('[ai_model_usage] ⚠️ буфер пуст, пропускаю загрузку')
        return False

    date_from = _buffer.attrs.get('date_from', '')
    date_to   = _buffer.attrs.get('date_to', '')

    print(f'\n[ai_model_usage] ФАЗА 2 — загрузка {len(_buffer)} строк в dwh_test_db.ai_model_usage_daily')
    try:
        upload = _prepare(_buffer, date_from, date_to)
        tc = _target_client()
        tc.insert_df('dwh_test_db.ai_model_usage_daily', upload)
    except Exception as e:
        print(f'[ai_model_usage] ❌ load_phase (insert): {e}')
        print(traceback.format_exc())
        send_ntfy_alert(
            f'ai_model_usage load_phase: ошибка вставки — {e}',
            title='AI Model Usage Insert Failed', priority='urgent', tags='warning',
        )
        return False

    print('[ai_model_usage] дедупликация (ALTER TABLE ... DELETE)...')
    try:
        _dedup_mutation(tc)
    except Exception as e:
        print(f'[ai_model_usage] ❌ дедупликация не выполнена: {e}')
        print(traceback.format_exc())
        send_ntfy_alert(
            f'ai_model_usage load_phase: дедупликация упала — {e}',
            title='AI Model Usage Dedup Failed', priority='urgent', tags='warning',
        )
        _buffer = None
        return True

    try:
        cnt = tc.query_df(
            f"SELECT count() AS cnt FROM dwh_test_db.ai_model_usage_daily "
            f"WHERE period_from = '{date_from}' AND period_to = '{date_to}'"
        )
        total_all = tc.query_df(
            "SELECT count() AS cnt FROM dwh_test_db.ai_model_usage_daily"
        )
        print(f'[ai_model_usage] ✅ загружено за период: {cnt.iloc[0,0]} | всего в таблице: {total_all.iloc[0,0]}')
    except Exception as e:
        print(f'[ai_model_usage] ⚠️ не удалось получить итоговые счётчики: {e}')
        print(traceback.format_exc())

    _buffer = None
    return True


# ===========================================================================
# Полный цикл (standalone): VPN → extract → VPN off → load
# ===========================================================================
def main():
    print(f'[ai_model_usage] Запуск — последние {DAYS_TO_SYNC} дней')
    send_ntfy_alert(
        f'AI Model Usage: синхронизация за {DAYS_TO_SYNC} дней...',
        title='AI Model Usage Start', priority='default', tags='robot',
    )

    try:
        connect_vpn()
    except Exception as e:
        msg = f'[ai_model_usage] Не удалось подключить VPN: {e}'
        print(msg)
        send_ntfy_alert(msg, title='AI Model Usage Fatal', priority='urgent', tags='fire')
        sys.exit(1)

    ok = extract_phase()

    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f'Ошибка отключения VPN: {e}', title='VPN Warning',
                        priority='default', tags='warning')

    if not ok:
        msg = f'[ai_model_usage] Извлечение не удалось. Выход.'
        print(msg)
        send_ntfy_alert(msg, title='AI Model Usage Failed', priority='urgent', tags='warning')
        sys.exit(1)

    ok = load_phase()
    dedup_check()

    if ok:
        send_ntfy_alert(
            f'AI Model Usage: обновление ai_model_usage_daily завершено ({DAYS_TO_SYNC} дней)!',
            title='AI Model Usage Done', priority='high', tags='white_check_mark',
            topic_override='push_mrc_dashboards_7895',
        )
        print('[ai_model_usage] Готово.')
    else:
        send_ntfy_alert(
            'AI Model Usage: загрузка завершилась с ошибкой!',
            title='AI Model Usage Failed', priority='urgent', tags='warning',
        )
        sys.exit(1)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='AI model usage — загрузка в dwh_test_db')
    parser.add_argument('--days', type=int, default=DAYS_TO_SYNC,
                        help=f'Глубина анализа в днях (по умолчанию {DAYS_TO_SYNC})')
    args = parser.parse_args()
    DAYS_TO_SYNC = args.days
    main()
