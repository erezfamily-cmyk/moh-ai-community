# הרצת התשתית שכבר קיימת

דורש Python 3.11 ומעלה. אין תלויות runtime חיצוניות ואין צורך במפתח API. הרכיב הקיים בודק סביבה ומייבא טקסט/תמלול מקומי ל־SQLite. הוא אינו מנתח כתבות ברשת ואינו יוצר פוסטים עדיין.

## macOS / Linux — משורש הפרויקט

```bash
PYTHONPATH=src python3 -m moh_ai doctor
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m moh_ai init-db
PYTHONPATH=src python3 -m moh_ai ingest --file examples/synthetic_input.txt --title 'מקור סינתטי לבדיקה'
```

## Windows PowerShell — משורש הפרויקט

```powershell
$env:PYTHONPATH = "src"
python -m moh_ai doctor
python -m unittest discover -s tests -v
python -m moh_ai init-db
python -m moh_ai ingest --file examples/synthetic_input.txt --title "מקור סינתטי לבדיקה"
```

פקודות PowerShell הן מקבילות מוצעות ולא הורצו בסביבה זו. ניתן להתקין editable ב־venv בהמשך באמצעות pyproject, אך מסלול PYTHONPATH אינו דורש הורדת build tools מהאינטרנט.

הנתונים נשמרים ב־data ומתעלמים מהם ב־git. אפשר לשנות תיקייה דרך `--data-dir` לפני תת־הפקודה או משתנה `MOH_AI_DATA_DIR`. import זהה באותו סוג אינו יוצר רשומה כפולה. אין כאן מקור חדשות אמיתי; קובץ הדוגמה סינתטי בכוונה.
