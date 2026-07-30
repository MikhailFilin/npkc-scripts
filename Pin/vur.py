import csv

# Имя входного и выходного файла
input_file = 'vur.csv'   # замените на имя вашего файла
output_file = 'vur_result.csv' # можно указать то же имя, если хотите перезаписать

# Чтение и обработка
with open(input_file, mode='r', encoding='utf-8') as infile:
    reader = csv.DictReader(infile, delimiter=';')
    fieldnames = reader.fieldnames

    # Проверим, существует ли нужный столбец
    if 'comments' not in fieldnames:
        raise ValueError("Столбец 'comments' не найден в файле")

    # Подготовка для записи
    with open(output_file, mode='w', encoding='utf-8', newline='') as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()

        for row in reader:
            # Очищаем значение в столбце
            comment = row['comments']
            if comment is not None:
                # Удаляем кавычки ", обрезаем пробелы и переносы строк
                comment = comment.replace('"', '')          # удаляем кавычки
                comment = comment.replace('\r', ' ')        # заменяем \r на пробел (или можно удалить)
                comment = comment.replace('\n', ' ')        # заменяем \n на пробел
                comment = ' '.join(comment.split())         # удаляем лишние пробелы (в т.ч. множественные)
                row['comments'] = comment

            writer.writerow(row)

print(f"Обработка завершена. Результат сохранён в '{output_file}'")