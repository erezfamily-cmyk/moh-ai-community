"""Local explanatory cards. SVG requires stdlib; PNG uses optional Pillow+RAQM."""
from html import escape
from pathlib import Path
import textwrap


def card_lines(body):
    paragraphs=[p.strip() for p in body.split('\n') if p.strip() and not p.startswith('מקור:')]
    text=' '.join(paragraphs)
    # Cards are previews, not a replacement for the complete accompanying post.
    lines=textwrap.wrap(text, width=36,break_long_words=True,break_on_hyphens=False)
    return lines[:10],len(lines)>10


def render_svg(title,body):
    lines,truncated=card_lines(body)
    title_lines=textwrap.wrap(title,width=23,break_long_words=True)
    if len(title_lines)>3:raise ValueError('Card title exceeds three lines')
    t=''.join(f'<text x="965" y="{260+i*78}" class="title">{escape(line)}</text>' for i,line in enumerate(title_lines))
    start=300+len(title_lines)*78
    content=''.join(f'<text x="965" y="{start+i*54}" class="body">{escape(line)}</text>' for i,line in enumerate(lines))
    foot='ההסבר המלא והמקור בפוסט המצורף' if truncated else 'קוראים, מנסים ובודקים את התוצאה'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1350" viewBox="0 0 1080 1350" role="img" aria-label="{escape(title,quote=True)}">
<rect width="1080" height="1350" fill="#F4F4F0"/><rect x="56" y="56" width="968" height="1238" rx="36" fill="white"/>
<rect x="850" y="92" width="112" height="7" rx="3" fill="#008C78"/>
<style>text{{font-family:Arial,'DejaVu Sans',sans-serif;direction:rtl;text-anchor:start;unicode-bidi:plaintext;fill:#17213A}}.title{{font-size:59px;font-weight:700}}.body{{font-size:37px}}</style>
<text x="965" y="168" font-size="35" font-weight="bold">מוח AI · לומדים דרך דוגמה</text>
{t}{content}<text x="965" y="1135" font-size="22" fill="#7B6950">תמונת טיוטה · נדרשת בדיקה חזותית לפני פרסום</text><line x1="115" x2="965" y1="1168" y2="1168" stroke="#E2E8E4"/>
<text x="965" y="1225" font-size="27">{foot}</text></svg>'''


def render_png(title,body,path,font_path=None):
    try:
        from PIL import Image,ImageDraw,ImageFont,features
    except ImportError as e:raise ValueError('PNG needs optional Pillow; SVG works without it') from e
    if not features.check_feature('raqm'):raise ValueError('PNG Hebrew rendering requires Pillow with RAQM; use SVG otherwise')
    options=[font_path] if font_path else [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/System/Library/Fonts/Supplemental/Arial.ttf','C:/Windows/Fonts/arial.ttf']
    font_path=next((p for p in options if p and Path(p).is_file()),None)
    if font_path is None:raise ValueError('No Hebrew font found; supply --font pointing to an appropriately licensed font')
    img=Image.new('RGB',(1080,1350),'#F4F4F0');d=ImageDraw.Draw(img)
    d.rounded_rectangle((56,56,1024,1294),radius=36,fill='white')
    d.rounded_rectangle((850,92,962,99),radius=3,fill='#008C78')
    def draw(text,y,size,color='#17213A'):
        font=ImageFont.truetype(font_path,size)
        if d.textbbox((965,y),text,font=font,anchor='ra',direction='rtl')[0]<105:
            raise ValueError('Card text is too wide for layout; shorten title/body')
        d.text((965,y),text,font=font,fill=color,anchor='ra',direction='rtl',language='he')
    draw('מוח AI · לומדים דרך דוגמה',137,35)
    titles=textwrap.wrap(title,width=23,break_long_words=False)
    if len(titles)>3:raise ValueError('Card title exceeds three lines')
    for i,line in enumerate(titles):draw(line,210+i*78,59)
    start=280+len(titles)*78
    lines,truncated=card_lines(body)
    for i,line in enumerate(lines):draw(line,start+i*54,37)
    d.line((115,1168,965,1168),fill='#E2E8E4',width=2)
    draw('ההסבר המלא והמקור בפוסט המצורף' if truncated else 'קוראים, מנסים ובודקים את התוצאה',1200,27)
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    img.save(path,'PNG')
    return {'path':str(path),'width':1080,'height':1350,'font':Path(font_path).name,'status':'rendered'}


def render_explainer_png(title,steps,example,path,font_path=None):
    """Render a three-step infographic from a manually reviewed visual brief."""
    from PIL import Image,ImageDraw,ImageFont,features
    if not features.check_feature('raqm'):raise ValueError('Pillow RAQM required for Hebrew')
    font_path=font_path or '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    if not Path(font_path).is_file():raise ValueError('Supply a locally installed Hebrew font')
    if len(steps)!=3:raise ValueError('Exactly three reviewed steps are required')
    img=Image.new('RGB',(1080,1350),'#F4F4EF');d=ImageDraw.Draw(img)
    def draw(text,x,y,size,color='#17213A'):
        f=ImageFont.truetype(font_path,size)
        box=d.textbbox((x,y),text,font=f,anchor='ra',direction='rtl')
        if box[0]<75:raise ValueError('Infographic line is too long; revise visual brief')
        d.text((x,y),text,font=f,anchor='ra',direction='rtl',language='he',fill=color)
    draw('מוח AI · לומדים דרך דוגמה',995,72,31,'#247766')
    draw(title,995,160,66)
    draw('שלושה פרטים שכדאי להוסיף לבקשה',995,257,33,'#586474')
    for i,(label,detail) in enumerate(steps):
        y=350+i*200
        d.rounded_rectangle((75,y,1005,y+165),radius=22,fill='white')
        d.ellipse((905,y+30,965,y+90),fill='#175B4D')
        f=ImageFont.truetype(font_path,32)
        d.text((935,y+60),str(i+1),font=f,anchor='mm',fill='white')
        draw(label,870,y+32,42)
        draw(detail,870,y+103,31,'#586474')
    d.rounded_rectangle((75,990,1005,1210),radius=22,fill='#E2EBDF')
    draw('אפשר לנסות כך:',965,1025,32,'#175B4D')
    for i,line in enumerate(textwrap.wrap(example,width=35,break_long_words=False)):
        if i>2:raise ValueError('Example exceeds three lines')
        draw(line,965,1080+i*44,33)
    draw('מנסים, קוראים ומשפרים · המקור בפוסט המצורף',995,1272,25,'#586474')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);img.save(path,'PNG')
    return {'path':str(path),'width':1080,'height':1350,'status':'rendered','origin':'reviewed_visual_brief'}
