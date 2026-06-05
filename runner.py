
import subprocess, sys, time, logging, re, os

MAX_RETRIES = 3
DELAY = 3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE   = os.path.join(SCRIPT_DIR, 'runner.log')

def remove_emojis(text):
    """Удаляет эмодзи из текста"""
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                           "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)

if len(sys.argv) < 2:
    print("Не указан скрипт для запуска")
    sys.exit(1)

script = sys.argv[1]
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format='%(asctime)s %(message)s')

print(f"Запускаю {script}...")

for attempt in range(1, MAX_RETRIES + 1):
    print(f"Попытка {attempt}...")
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    result = subprocess.run(
        [sys.executable, script],
        capture_output=True, text=True, encoding='utf-8', errors='ignore',
        cwd=SCRIPT_DIR, env=env
    )
    
    if result.returncode == 0:
        logging.info(f'{script} — OK (попытка {attempt})')
        print("Успех!")
        break
    elif result.returncode == 1:
        clean_stderr = remove_emojis(result.stderr)
        logging.warning(f'{script} — ЧАСТИЧНАЯ ОШИБКА код=1 (попытка {attempt}): {clean_stderr}')
        print(f"Частичная ошибка (код 1), перезапуск не нужен: {clean_stderr}")
        break
    else:
        clean_stderr = remove_emojis(result.stderr)
        logging.warning(f'{script} — МАССОВАЯ ОШИБКА код={result.returncode} (попытка {attempt}): {clean_stderr}')
        print(f"Массовая ошибка (код {result.returncode}): {clean_stderr}")
        if attempt < MAX_RETRIES:
            print(f"Жду {DELAY} секунд перед повтором...")
            time.sleep(DELAY)
else:
    logging.error(f'{script} — УПАЛ {MAX_RETRIES} раз (код 2), сдаёмся')
    print("Все попытки исчерпаны")