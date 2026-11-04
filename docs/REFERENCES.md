# תיעוד רשמי ומגבלות אימות

נבדק ברשת ב־4 בנובמבר 2026. יש לאמת מחדש בזמן התקנה; ממשקי Codex, SDK ותצורות יכולים להשתנות.

| מקור רשמי | מה נתמך בו | שימוש בחבילה |
|---|---|---|
| [Customization](https://learn.chatgpt.com/docs/customization/overview) | ההבחנה בין AGENTS, skills, MCP ו־subagents | חלוקה לשכבות; לא כל תפקיד הוא סקיל |
| [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) | קובצי `.codex/agents/*.toml`, שדות name/description/developer_instructions, ירושת הרשאות וצריכת משאבי מודל | תצורות התחלתיות ותפקידים מצומצמים |
| [OpenAI Docs MCP](https://developers.openai.com/learn/docs-mcp) | שרת ציבורי read-only לתיעוד; URL ופקודות חיבור; אינו מפעיל API בשם המשתמש | חיבור חיצוני מוצע והוכחת שימוש |
| [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/) | SDK רשמי וכלי MCP | בסיס לשרת מקומי עתידי |
| [MCP stdio](https://py.sdk.modelcontextprotocol.io/get-started/real-host/) | שרת מקומי כתהליך ולקוח בדיקה | בדיקת פרוטוקול ולא רק פונקציה |

## רמת הוודאות

התיעוד לעיל נבדק; אין בחבילה אישור שכל ספרייה/מודל/גופן אפשריים חינמיים לכל שימוש. אין כאן benchmark, בדיקת עלות חשבון, טעינת agents בפועל או חיבור MCP מתוך הפרויקט. אין להשתמש בכתובות של דפי תיעוד שלא אומתו כהוראות התקנה סופיות.

לפני נעילת המימוש Codex יוסיף קישורי תיעוד רשמי לגרסאות שנבחרו: Python, framework, רינדור דפדפן, FFmpeg, model runtime, מודל וגופן. כל אחד עם רישיון ותאריך בדיקה. אין להשתמש ברשימת תיעוד כתחליף לבדיקת ריצה אמיתית.
