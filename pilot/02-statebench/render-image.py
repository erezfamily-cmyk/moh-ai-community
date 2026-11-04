"""Reproduce an exact Hebrew educational diagram locally, without a service API."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, features

assert features.check_feature('raqm'), 'Hebrew requires Pillow RAQM'
ROOT = Path(__file__).resolve().parent
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
im = Image.new('RGB', (1080, 1350), '#F3F5F0')
d = ImageDraw.Draw(im)

def text(s, y, size=34, color='#193D32', x=985):
    f = ImageFont.truetype(FONT, size)
    b = d.textbbox((x,y), s, font=f, anchor='ra', direction='rtl')
    assert b[0] >= 80 and b[2] <= 1000 and b[3] < 1310, (s,b)
    d.text((x,y), s, font=f, anchor='ra', direction='rtl', language='he', fill=color)

text('מוח AI · לומדים דרך דוגמה', 65, 30)
text('מה השתנה בדרך?', 153, 65)
text('שלושה עדכונים. מצב נוכחי אחד.', 255, 36, '#52685C')
rows = [
    ('נקודת ההתחלה', 'יום שני · שעה 10:00'),
    ('השעה השתנתה', 'יום שני · שעה 12:00'),
    ('היום השתנה', 'יום שלישי · שעה 12:00'),
]
for i,(label,value) in enumerate(rows):
    y = 355 + 200*i
    d.rounded_rectangle((80,y,1000,y+165), radius=26, fill='#FFFFFF')
    text(f'{i+1}. {label}', y+23, 29, '#617469', x=955)
    text(value, y+81, 43, x=955)
d.rounded_rectangle((80,1000,1000,1210), radius=26, fill='#DDE9DA')
text('לפני שממשיכים עם AI:', 1030, 34, x=955)
text('מבקשים מצב עדכני', 1090, 43, x=955)
text('ובודקים מול השינויים שכתבנו.', 1155, 31, x=955)
text('המחשה מקורית · אינה מבחן אמינות לכלי', 1265, 25, '#52685C')
im.save(ROOT/'image.png')
print('Rendered 1080x1350; all text bounds within canvas')
