# הצגת הממשק דרך GitHub Pages

ה־workflow ב־`.github/workflows/deploy-live-interface.yml` מפרסם את הקבצים הסטטיים שבתיקיית `live-web/dist` ל־GitHub Pages אחרי push ל־`main` שמשנה את `live-web/**`, או בהפעלה ידנית.

## הפעלה חד־פעמית

1. פתחו את **Settings → Pages** במאגר `moh-ai-community`.
2. תחת **Build and deployment → Source**, בחרו **GitHub Actions**.
3. לאחר מיזוג השינויים ל־`main`, פתחו **Actions → Deploy live interface to GitHub Pages** ועקבו אחר ההרצה.
4. בסיום מוצגת כתובת האתר ב־Settings → Pages וגם בפרטי ה־deployment.

הנתיבים בקבצי הממשק יחסיים כדי ש־CSS, JavaScript, תמונות וקובצי הדוגמה יעבדו בכתובת GitHub Pages של המאגר וגם באירוח השורש הקיים.

**חשיפה ועלות:** אתר GitHub Pages ציבורי נגיש לכל מי שיש לו את הקישור. בחשבון GitHub Free נדרש מאגר ציבורי כדי להשתמש ב־Pages; במאגרים פרטיים האפשרות תלויה בתוכנית GitHub. הפיכת המאגר לציבורי תחשוף את כל הקוד והיסטוריית השינויים. GitHub Pages ו־Actions כפופים למגבלות התוכנית. לא מופעל פרסום תוכן לקהילה מתוך האתר.
