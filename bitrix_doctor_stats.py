"""
bitrix_doctor_stats.py
Отправка персональных показателей врача (ClickHouse dwh_test_db) в личный чат Битрикс24.

Логика:
    1. По bitrix_id находим fio в guide_doctors_upd
    2. Выполняем основной запрос показателей за текущий месяц
    3. Форматируем в текст и отправляем через im.message.add

Режимы запуска:
    py -3 bitrix_doctor_stats.py --bitrix-id 353              # dry-run: вывод без отправки
    py -3 bitrix_doctor_stats.py --bitrix-id 353 --send       # отправить конкретному врачу
    py -3 bitrix_doctor_stats.py --all --send                  # отправить всем с bitrix_id

# INPUT:  dwh_test_db.dynamic_current_month, dwh_test_db.instrumental_union,
#          dwh_test_db.guide_doctors_upd
# OUTPUT: личное сообщение в Битрикс24 каждому врачу (DIALOG_ID = 'U{bitrix_id}')
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import clickhouse_connect
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import personal_config as cfg

# ─── Логирование ──────────────────────────────────────────────────────────────
_LOG_FILE = Path(__file__).resolve().parent / "bitrix_doctor_stats.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─── ClickHouse (целевая, dwh_test_db) ────────────────────────────────────────
CH_HOST     = cfg.CH_HOST_TARGET
CH_PORT     = cfg.CH_PORT_TARGET
CH_USER     = cfg.CH_USER_TARGET
CH_PASSWORD = cfg.CH_PASSWORD_TARGET
CH_DATABASE = "dwh_test_db"

# ─── Битрикс24 ────────────────────────────────────────────────────────────────
BITRIX_WEBHOOK = cfg.BITRIX_WEBHOOK  # https://portal.bitrix24.ru/rest/USER/TOKEN

# ─── Groq AI ──────────────────────────────────────────────────────────────────
GROQ_API_KEY        = cfg.GROQ_API_KEY
GROQ_API_KEY_BACKUP = getattr(cfg, "GROQ_API_KEY_BACKUP", "")
GROQ_URL            = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL          = "llama-3.3-70b-versatile"
_proxy              = getattr(cfg, "TELEGRAM_PROXY", "")
GROQ_PROXIES        = {"http": _proxy, "https": _proxy} if _proxy else None


# ─── SQL: список врачей с bitrix_id ───────────────────────────────────────────
SQL_DOCTORS = """
SELECT name, bitrix_id
FROM dwh_test_db.guide_doctors_upd
WHERE bitrix_id IS NOT NULL
  AND bitrix_id != 0
{where_extra}
ORDER BY name
"""

# ─── SQL: показатели врача за текущий месяц ───────────────────────────────────
SQL_STATS = """
SELECT
    t1.fio                         AS fio,
    t1.rate                        AS rate,
    t1.author_modality             AS modality,
    t1.otdel                       AS otdel,
    t1.ispolnenie                  AS ispolnenie,
    t1.done_hours                  AS done_hours,
    t1.monthly_hours               AS monthly_hours,
    if(isNull(t1.done_hours),
        if(toYear(t1.start_work) = toYear(toDate(now()))
           AND toMonth(t1.start_work) = toMonth(toDate(now())),
           toFloat64((3801 * t1.rate * t1.all_days) / 21),
           toFloat64(3801 * t1.rate)),
        toFloat64((3801 * if(toFloat64(t1.monthly_hours) > toFloat64(162.8),
                             toFloat64(162.8), toFloat64(t1.monthly_hours)))
                  / toFloat64(162.8)))  AS tarif,
    t1.sum_total_coefficient        AS sum_coef,
    toFloat64(round(if(NOT isNull(t1.monthly_hours),
        (t1.sum_total_coefficient * t1.monthly_hours) / t1.done_hours,
        (t1.sum_total_coefficient * t1.all_days) / t1.days_done),
        2) + 0)                     AS calc_coef,
    toFloat64(toFloat64(round(if(NOT isNull(t1.monthly_hours),
        (t1.sum_total_coefficient * t1.monthly_hours) / t1.done_hours,
        (t1.sum_total_coefficient * t1.all_days) / t1.days_done),
        2) + 0)
    / if(isNull(t1.done_hours),
        if(toYear(t1.start_work) = toYear(toDate(now()))
           AND toMonth(t1.start_work) = toMonth(toDate(now())),
           toFloat64((3801 * t1.rate * t1.all_days) / 21),
           toFloat64(3801 * t1.rate)),
        toFloat64((3801 * if(toFloat64(t1.monthly_hours) > toFloat64(162.8),
                             toFloat64(162.8), toFloat64(t1.monthly_hours)))
                  / toFloat64(162.8))))  AS pct_tarif,
    t1.sum_total_coefficient
    / if(isNull(t1.done_hours),
        if(toYear(t1.start_work) = toYear(toDate(now()))
           AND toMonth(t1.start_work) = toMonth(toDate(now())),
           toFloat64((3801 * t1.rate * t1.all_days) / 21),
           toFloat64(3801 * t1.rate)),
        toFloat64((3801 * if(toFloat64(t1.monthly_hours) > toFloat64(162.8),
                             toFloat64(162.8), toFloat64(t1.monthly_hours)))
                  / toFloat64(162.8)))   AS coef_to_tarif
FROM (
    SELECT
        dcm.*,
        COALESCE(agg.sum_tarif, 0)             AS sum_tarif,
        COALESCE(agg.sum_cnt, 0)               AS sum_cnt,
        COALESCE(agg.sum_total_coefficient, 0) AS sum_total_coefficient
    FROM dwh_test_db.dynamic_current_month dcm
    LEFT JOIN (
        SELECT
            id_author,
            sum(tarif)             AS sum_tarif,
            sum(cnt)               AS sum_cnt,
            sum(total_coefficient) AS sum_total_coefficient
        FROM dwh_test_db.instrumental_union
        WHERE doc_date >= toStartOfMonth(yesterday())
          AND doc_date <= yesterday()
          AND id_author IS NOT NULL
        GROUP BY id_author
    ) agg ON dcm.author_id = agg.id_author
) AS t1
WHERE t1.sick IN (0)
  AND t1.fio = {fio:String}
GROUP BY fio, rate, modality, otdel, ispolnenie,
         done_hours, monthly_hours, tarif,
         sum_coef, calc_coef, pct_tarif, coef_to_tarif
ORDER BY pct_tarif DESC NULLS LAST
LIMIT 1000
"""


# ─── ClickHouse ───────────────────────────────────────────────────────────────

def _ch_client():
    return clickhouse_connect.get_client(
        host=CH_HOST,
        port=CH_PORT,
        username=CH_USER,
        password=CH_PASSWORD,
        database=CH_DATABASE,
        secure=True,
        verify=False,
    )


def fetch_doctors(bitrix_id: int | None = None) -> list[dict]:
    """Возвращает [{fio, bitrix_id}] из guide_doctors_upd."""
    if bitrix_id:
        where = f"AND bitrix_id = {int(bitrix_id)}"
    else:
        where = ""
    sql = SQL_DOCTORS.format(where_extra=where)
    client = _ch_client()
    try:
        result = client.query(sql)
        rows = [{"fio": r[0], "bitrix_id": r[1]} for r in result.result_rows if r[0]]
        log.info("Врачей с bitrix_id: %d", len(rows))
        return rows
    except Exception as e:
        log.error("Ошибка запроса guide_doctors_upd: %s", e)
        return []
    finally:
        client.close()


def fetch_stats(fio: str) -> list[dict] | None:
    """Возвращает список строк показателей для конкретного врача."""
    client = _ch_client()
    try:
        result = client.query(SQL_STATS, parameters={"fio": fio})
        cols = [
            "fio", "rate", "modality", "otdel", "ispolnenie",
            "done_hours", "monthly_hours", "tarif",
            "sum_coef", "calc_coef", "pct_tarif", "coef_to_tarif",
        ]
        rows = [dict(zip(cols, r)) for r in result.result_rows]
        log.info("  %s — строк данных: %d", fio, len(rows))
        return rows
    except Exception as e:
        log.error("  Ошибка запроса показателей для '%s': %s", fio, e)
        return None
    finally:
        client.close()


# ─── Форматирование сообщения ──────────────────────────────────────────────────

def _fmt(v, decimals: int = 2) -> str:
    """Число в русском формате: разделитель тысяч — пробел, дробная — запятая."""
    if v is None:
        return "—"
    try:
        fval = float(v)
        formatted = f"{fval:,.{decimals}f}"          # 3,801.00 / 1,741.23
        formatted = formatted.replace(",", " ").replace(".", ",")  # 3 801,00
        return formatted
    except (TypeError, ValueError):
        return str(v)


def _pct(v) -> str:
    """Доля (0–1) → процент в русском формате, напр. 1.7441 → 174,41%."""
    if v is None:
        return "—"
    try:
        return _fmt(float(v) * 100, 2) + "%"
    except (TypeError, ValueError):
        return str(v)


def _hours_pct(done, total) -> str:
    """done_hours / monthly_hours * 100, напр. 54.6 / 162.8 → 33,54%."""
    try:
        d, t = float(done), float(total)
        if t == 0:
            return "—"
        return _fmt(d / t * 100, 2) + "%"
    except (TypeError, ValueError):
        return "—"


def ai_summary(r: dict) -> str:
    """Генерирует мотивирующее заключение через Groq на основе показателей врача."""
    try:
        done   = float(r['done_hours'] or 0)
        total  = float(r['monthly_hours'] or 0)
        hours_pct  = round(done / total * 100, 1) if total else 0
        remaining  = round(total - done, 1) if total else 0
        norm_uet   = round(float(r['tarif'] or 0), 2)
        desc_now   = round(float(r['sum_coef'] or 0), 2)
        desc_pace  = round(float(r['calc_coef'] or 0), 2)
        norms_pct  = round(float(r['pct_tarif'] or 0) * 100, 2)
        desc_pct   = round(float(r['coef_to_tarif'] or 0) * 100, 2)

        prompt = (
            f"Ты — аналитик, пишешь персональный итог врачу-рентгенологу по его показателям за текущий месяц. "
            f"Тон: профессиональный, поддерживающий, конкретный. "
            f"Обращение на «вы». Длина: 2–4 предложения. Без приветствия. Без заголовков.\n\n"
            f"Показатели:\n"
            f"- Часов отработано: {done} из {total} ({hours_pct}%), осталось {remaining} ч.\n"
            f"- Норма УЕТ врача: {norm_uet}\n"
            f"- Описано УЕТ сейчас: {desc_now} ({desc_pct}% от нормы)\n"
            f"- При текущем темпе опишет к концу месяца: {desc_pace} ({norms_pct}% нормы)\n\n"
            f"Если показатели хорошие (нормы >90%) — похвали и отметь результат. "
            f"Если средние (60–90%) — подбодри, укажи сколько нужно добавить. "
            f"Если низкие (<60%) — мягко, но настойчиво попроси обратить внимание и напряч усилия."
        )

        body = {
            "model":       GROQ_MODEL,
            "messages":    [{"role": "user", "content": prompt}],
            "max_tokens":  200,
            "temperature": 0.6,
        }
        keys = [k for k in [GROQ_API_KEY, GROQ_API_KEY_BACKUP] if k]
        for i, key in enumerate(keys, 1):
            r_resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
                proxies=GROQ_PROXIES,
                timeout=15,
            )
            if r_resp.ok:
                log.info("  Groq OK (ключ #%d)", i)
                return r_resp.json()["choices"][0]["message"]["content"].strip()
            log.warning("  Groq ключ #%d вернул %s: %s", i, r_resp.status_code, r_resp.text[:200])
    except Exception as e:
        log.warning("Groq AI summary failed: %s", e)
    return ""


def format_message(rows: list[dict]) -> str:
    from datetime import date
    today = date.today().strftime("%d.%m.%Y")
    if not rows:
        return f"[{today}] Данных за текущий месяц не найдено."

    r = rows[0]
    Q = ">> "
    lines = [
        f"Ваши показатели на {today}",
        Q,
        f"{Q}ФИО врача: {r['fio']}",
        f"{Q}Ставка: {_fmt(r['rate'])}",
        f"{Q}Ваша модальность: {r['modality'] or '—'}",
        f"{Q}Отделение: {r['otdel'] or '—'}",
        f"{Q}Исполнение: {r['ispolnenie'] or '—'}",
        Q,
        f"{Q}В текущем месяце:",
        f"{Q}Часов отработано: {_fmt(r['done_hours'])}",
        f"{Q}Всего запланировано часов: {_fmt(r['monthly_hours'])}",
        f"{Q}% выработки часов: {_hours_pct(r['done_hours'], r['monthly_hours'])}",
        f"{Q}Норма УЕТ на ставку: {_fmt(r['tarif'])}",
        Q,
        f"{Q}Описано УЕТ сейчас: {_fmt(r['sum_coef'])}",
        f"{Q}Описано УЕТ сейчас: {_pct(r['coef_to_tarif'])}",
        f"{Q}Опишете в текущем темпе, УЕТ: {_fmt(r['calc_coef'])}",
        f"{Q}Опишете нормы: {_pct(r['pct_tarif'])}",
    ]

    summary = ai_summary(r)
    if summary:
        summary_lines = [f"{Q}{line}" for line in summary.splitlines() if line.strip()]
        lines += [Q, Q, f"{Q}Итог:"] + summary_lines

    return "\n".join(lines)


# ─── Битрикс24 ────────────────────────────────────────────────────────────────

def _bx(method: str, params: dict | None = None) -> dict:
    url = f"{BITRIX_WEBHOOK.rstrip('/')}/{method}.json"
    try:
        r = requests.post(url, json=params or {}, timeout=30)
        if not r.ok:
            log.error("Bitrix24 [%s] %s: %s", method, r.status_code, r.text[:400])
            return {}
        return r.json()
    except Exception as e:
        log.error("Bitrix24 [%s]: %s", method, e)
        return {}


def send_to_user(bitrix_id: int, text: str) -> bool:
    """Отправляет личное сообщение пользователю bitrix_id.
    Перебирает форматы DIALOG_ID пока один не сработает."""
    dialog_variants = [str(bitrix_id), f"u{bitrix_id}", f"U{bitrix_id}"]
    for dialog_id in dialog_variants:
        result = _bx("im.message.add", {
            "DIALOG_ID": dialog_id,
            "MESSAGE":   text,
        })
        ok = bool(result.get("result"))
        if ok:
            log.info("  Отправлено (DIALOG_ID=%s, message_id=%s)", dialog_id, result["result"])
            return True
        err = result.get("error", "")
        if err != "USER_ID_EMPTY":
            log.error("  Ошибка (DIALOG_ID=%s): %s", dialog_id, result)
            break
    log.error("  Не удалось отправить сообщение U%d ни одним из форматов", bitrix_id)
    return False


# ─── Основная логика ──────────────────────────────────────────────────────────

def run(bitrix_id: int | None, send: bool) -> None:
    doctors = fetch_doctors(bitrix_id)
    if not doctors:
        log.warning("Список врачей пуст — завершение.")
        return

    ok_count = 0
    for doc in doctors:
        fio  = doc["fio"]
        bid  = doc["bitrix_id"]
        log.info("Обработка: %s (bitrix_id=%s)", fio, bid)

        rows = fetch_stats(fio)
        if rows is None:
            continue

        text = format_message(rows)
        print(f"\n{'─'*60}")
        print(f"→ U{bid}  {fio}")
        print(text)

        if send:
            if send_to_user(bid, text):
                ok_count += 1
        else:
            ok_count += 1

    mode = "отправлено" if send else "обработано (dry-run)"
    log.info("Итого %s: %d / %d", mode, ok_count, len(doctors))


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(description="Отправка показателей врача в Битрикс24")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--bitrix-id", type=int, metavar="ID",
                       help="bitrix_id конкретного врача (напр. 353)")
    group.add_argument("--all", action="store_true",
                       help="отправить всем врачам с заполненным bitrix_id")
    p.add_argument("--send", action="store_true",
                   help="реально отправить в Битрикс24 (без флага — dry-run)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if not args.send:
        log.info("Режим dry-run (без --send): сообщения выводятся, но не отправляются")
    run(
        bitrix_id=args.bitrix_id if not args.all else None,
        send=args.send,
    )
