# OmniBridge — پروکسی هوش مصنوعی با API سازگار با OpenAI

<div align="center">
<img src="docs/banner.png" alt="Free Gemini Pro API" width="100%">
<br>
<a href="README.md"><strong>🇺🇸 Click here for English README</strong></a>
<br>

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Telegram](https://img.shields.io/badge/Telegram-Config__Vortex55-blue?style=flat-square&logo=telegram)](https://t.me/Config_Vortex55)
[![مجوز](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker&logoColor=white)](Dockerfile)

**یک پروکسی‌سرور خودمیزبان فوق‌پیشرفته و پرسرعت که نشست‌های وب Google Gemini، DeepSeek (نسخه‌های V3 و R1 با حل‌کننده خودکار PoW) و ChatGPT را به یک API کاملاً سازگار با استانداردهای OpenAI و Anthropic تبدیل می‌کند — همراه با استریم لحظه‌ای تفکر (`reasoning_content`) و داشبورد مدیریت مدرن و زیبای فارسی.**

<br>
<img src="docs/dashboard.jpg" alt="پنل داشبورد" width="800">
<br><br>

**📢 کانال تلگرام:** [@Config_Vortex55](https://t.me/Config_Vortex55)

[ویژگی‌ها](#-ویژگیها) · [شروع سریع](#-شروع-سریع) · [مرجع API](#-مرجع-api) · [داشبورد مدیریت](#️-داشبورد-مدیریت) · [دیپلوی](#-دیپلوی-با-یک-کلیک)

</div>

---

## ✨ ویژگی‌ها

| ویژگی | جزئیات |
|--------|---------|
| 🌐 **موتور چند-پروایدر** | اتصال همزمان به **Google Gemini**, **DeepSeek (V3 & R1)** و **ChatGPT** |
| 🧠 **حل‌کننده خودکار PoW** | حل‌کننده اختصاصی ۲۳ راند Keccak-f[1600] پایتون برای چالش‌های `DeepSeekHashV1` |
| 💡 **استریم فرآیند تفکر** | ارسال زنده توکن‌های استدلال (`reasoning_content`) برای DeepSeek-R1 و Gemini Extended |
| 🤝 **سازگار با OpenAI** | جایگزین کامل برای `POST /v1/chat/completions` و `GET /v1/models` |
| 🔶 **سازگار با Anthropic** | پشتیبانی کامل از `POST /anthropic/v1/messages` |
| 🌊 **استریم بلادرنگ** | ارسال توکن‌به‌توکن (SSE) روی تمام endpoint‌ها با حفظ کامل فاصله‌ها و فرمت‌ها |
| 🔒 **جعل اثرانگشت TLS** | شبیه‌سازی Chrome-120 با `curl-cffi` برای دور زدن bot-detection |
| 🔄 **استخر اکانت‌ها** | توزیع بار Round-Robin با Cooldown خودکار بین اکانت‌های متعدد |
| 🍪 **تمدید خودکار نشست** | وظیفه پس‌زمینه برای تمدید خودکار و هوشمند کوکی‌ها |
| 🗝️ **احراز هویت کلید API** | ساخت و لغو کلیدهای `sk-omni-...` با لاگ کامل |
| 📊 **داشبورد مدیریت فارسی** | رابط کاربری مدرن تیره با افکت شیشه‌ای، نمودار ترافیک و زمین بازی چت |
| 🖼️ **چندوجهی (Multimodal)** | ورودی تصویر برای مدل‌های Gemini Vision |
| 🐋 **آماده Docker** | Dockerfile چندمرحله‌ای + compose + Render Blueprint |

## 🚀 شروع سریع

### محلی (Python 3.11+)

```bash
git clone https://github.com/exelite-dev/Free-Gemini-pro-API
cd Free-Gemini-pro-API

# نصب وابستگی‌ها
pip install -r requirements.txt

# تنظیم محیط
cp .env.example .env
# فایل .env را باز کرده و ADMIN_PASSWORD و ADMIN_SECRET_KEY را تغییر دهید

# راه‌اندازی
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

پنل مدیریت: `http://localhost:8000/admin` — ورود: `admin` / `changeme` — سپس اکانت Gemini خود را اضافه کنید.

### Docker Compose

```bash
docker compose up -d
```

### Docker (تنها کانتینر)

```bash
docker build -t omnibridge .
docker run -d -p 8000:8000 \
  -e ADMIN_PASSWORD=رمزعبور_قوی \
  -e ADMIN_SECRET_KEY=$(openssl rand -hex 32) \
  -v $(pwd)/data:/app/data \
  omnibridge
```

## 🎛️ پیکربندی

### متغیرهای محیطی

| متغیر | پیش‌فرض | توضیح |
|-------|---------|-------|
| `ADMIN_USERNAME` | `admin` | نام کاربری پنل مدیریت |
| `ADMIN_PASSWORD` | `changeme` | رمز عبور پنل مدیریت (**حتماً تغییر دهید!**) |
| `ADMIN_SECRET_KEY` | `supersecretkey` | کلید امضای session (یک رشته تصادفی بلند قرار دهید) |
| `GEMINI_ENABLED` | `true` | فعال/غیرفعال کردن سرویس Gemini |
| `DATABASE_PATH` | `./data/omnibridge.db` | مسیر پایگاه داده SQLite |
| `PORT` | `8000` | پورت سرور |
| `LOG_LEVEL` | `info` | سطح لاگ‌گیری |

تمام تنظیمات را می‌توان در فایل [`config.yaml`](config.yaml) نیز پیکربندی کرد.

## 📡 مرجع API

### احراز هویت

تمام endpoint‌های API نیاز به Bearer token دارند:

```
Authorization: Bearer sk-omni-<کلید-شما>
```

کلیدها را از پنل مدیریت → تب **کلیدهای دسترسی** بسازید.

### لیست مدل‌ها

```bash
GET /v1/models
Authorization: Bearer sk-omni-...
```

### تکمیل چت (فرمت OpenAI)

```bash
POST /v1/chat/completions
Content-Type: application/json
Authorization: Bearer sk-omni-...

{
  "model": "gemini-3.6-flash",
  "messages": [{"role": "user", "content": "سلام!"}],
  "stream": true
}
```

**مدل‌های Gemini موجود:**

| شناسه مدل | توضیح |
|-----------|-------|
| `gemini-3.6-flash` | سریع‌ترین و هوشمندترین (پیش‌فرض پیشنهادی) |
| `gemini-3.1-pro` | بهترین برای کدنویسی و ریاضیات پیچیده |
| `gemini-pro-extended` | حالت تفکر عمیق (Extended Thinking) |
| `gemini-3.5-flash-lite` | سبک‌ترین و سریع‌ترین مدل |
| `gemini-3-pro` | Gemini 3 Pro |
| `gemini-3-flash` | Gemini 3 Flash |
| `gemini-2.5-pro` | Gemini 2.5 Pro |
| `gemini-2.5-flash` | Gemini 2.5 Flash |
| `gemini-1.5-pro` | Gemini 1.5 Pro |
| `gemini-1.5-flash` | Gemini 1.5 Flash |

### پیام‌رسانی (فرمت Anthropic)

```bash
POST /anthropic/v1/messages
Content-Type: application/json
Authorization: Bearer sk-omni-...

{
  "model": "gemini-3.6-flash",
  "messages": [{"role": "user", "content": "سلام!"}],
  "max_tokens": 2048
}
```

### نمونه فراخوانی با cURL

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-omni-xxxx" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-3.6-flash","messages":[{"role":"user","content":"سلام"}],"stream":false}'
```

## 🛠️ داشبورد مدیریت

آدرس دسترسی: `http://localhost:8000/admin`

| تب | توضیح |
|----|-------|
| **داشبورد** | نمودار حجم درخواست‌ها، نرخ موفقیت، زمان پاسخ، وضعیت سرویس‌ها |
| **مدیریت اکانت‌ها** | افزودن، ویرایش، حذف و تست اتصال اکانت‌های Gemini |
| **راهنمای کوکی** | راهنمای گام‌به‌گام تصویری استخراج کوکی‌ها از مرورگر |
| **کلیدهای دسترسی** | ساخت کلیدهای `sk-omni-...`، مشاهده آخرین استفاده، لغو کلیدها |
| **اتصال به نرم‌افزارها** | تنظیمات آماده برای Cursor IDE، NextChat و اسکریپت‌های پایتون |
| **Playground** | تست زنده و استریمینگ مدل‌ها بدون نیاز به ابزار جانبی |
| **راهنمای دیپلوی** | آموزش میزبانی روی Render، Hugging Face Spaces و سرور اختصاصی |

## 🍪 نحوه دریافت اطلاعات اکانت Gemini

۱. وارد حساب گوگل خود در [gemini.google.com](https://gemini.google.com) شوید.
۲. کلید `F12` را زده تا DevTools باز شود ← به تب **Application** ← **Cookies** ← `gemini.google.com` بروید.
۳. مقادیر دو کوکی `__Secure-1PSID` و `__Secure-1PSIDTS` را کپی کنید.
۴. در پنل مدیریت OmniBridge به بخش **مدیریت اکانت‌ها** رفته و آن‌ها را وارد کنید.

> **نکته:** راهنمای تصویری کامل همراه با اسکرین‌شات داخل تب **راهنمای استخراج کوکی** در پنل مدیریت موجود است.

## 🚢 دیپلوی با یک کلیک

### Render.com (پیشنهادی — رایگان)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/exelite-dev/Free-Gemini-pro-API)

۱. این ریپازیتوری را Fork کنید.
۲. روی دکمه بالا کلیک کنید (یا در [render.com](https://dashboard.render.com) یک Web Service جدید بسازید و به Fork خود متصل کنید).
۳. Runtime را روی **Docker** بگذارید.
۴. متغیرهای محیطی `ADMIN_PASSWORD` و `ADMIN_SECRET_KEY` را تنظیم کنید.
۵. دکمه **Create Web Service** را بزنید — پروکسی شما آماده استفاده است!

### Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/exelite-dev/Free-Gemini-pro-API)

### سرور اختصاصی / VPS

```bash
git clone https://github.com/exelite-dev/Free-Gemini-pro-API
cd Free-Gemini-pro-API
cp .env.example .env
nano .env  # رمز عبور و سکرت کی را تغییر دهید
docker compose up -d --build
```

### Hugging Face Spaces (Docker SDK)

۱. در HuggingFace یک Space جدید با SDK: **Docker** بسازید.
۲. فایل‌های پروژه را آپلود یا push کنید.
۳. در Dockerfile پورت را از `8000` به `7860` تغییر داده و CMD را به‌روز کنید.
۴. در بخش Space Secrets مقادیر `ADMIN_PASSWORD` و `ADMIN_SECRET_KEY` را تعریف کنید.

## 📁 ساختار پروژه

```
OmniBridge/
├── app/
│   ├── main.py              # ساخت برنامه FastAPI و چرخه حیات
│   ├── config.py            # پیکربندی متمرکز (محیطی + YAML)
│   ├── database.py          # لایه پایگاه داده SQLite غیرهمزمان
│   ├── models.py            # مدل‌های Pydantic (OpenAI و Anthropic)
│   ├── auth.py              # احراز هویت کلیدها و Session
│   ├── providers/
│   │   ├── base.py          # کلاس انتزاعی موتورها
│   │   ├── pool.py          # استخر اکانت‌ها، توزیع بار و failover
│   │   ├── registry.py      # رجیستری موتورها
│   │   └── gemini/
│   │       ├── engine.py    # موتور جمنای با اثرانگشت TLS و استریمینگ
│   │       └── cookie_refresh.py  # تمدید خودکار دوره‌ای کوکی‌ها
│   ├── routes/
│   │   ├── openai.py        # مسیرهای OpenAI (/v1/...)
│   │   ├── anthropic.py     # مسیرهای Anthropic (/anthropic/...)
│   │   ├── google_proxy.py  # پروکسی عبور مستقیم گوگل
│   │   └── admin.py         # API پنل مدیریت
│   └── admin/
│       ├── static/css/      # استایل‌های مدرن شیشه‌ای
│       └── templates/       # قالب فرانت‌اند پنل مدیریت
├── tests/
│   └── test_app.py          # تست‌های واحد و اعتبارسنجی
├── config.yaml              # پیکربندی پیش‌فرض مدل‌ها
├── .env.example             # نمونه متغیرهای محیطی
├── requirements.txt
├── Dockerfile               # داکرفایل چندمرحله‌ای
├── docker-compose.yml
├── render.yaml              # فایل استقرار Render
├── README.md                # راهنمای انگلیسی
└── README.fa.md             # راهنمای فارسی
```

## 🔒 نکات امنیتی

- حتماً قبل از دیپلوی عمومی، `ADMIN_PASSWORD` و `ADMIN_SECRET_KEY` را تغییر دهید.
- فایل `.env` را هرگز در گیت کامیت نکنید (در `.gitignore` قرار دارد).
- کلید پیش‌فرض `sk-test` فقط برای تست محلی است — در سرور واقعی آن را حذف یا غیرفعال کنید.

## 🧪 اجرای تست‌ها

```bash
pip install pytest pytest-anyio
python -m pytest tests/ -v
```

<br><br>
<div align="center">
<img src="docs/art.jpg" alt="OmniBridge Art" width="500">
</div>

## 📄 مجوز

این پروژه تحت مجوز MIT منتشر شده است — فایل [LICENSE](LICENSE) را مشاهده کنید.

## 📢 ارتباط با جامعه کاربری

- **کانال تلگرام:** [@Config_Vortex55](https://t.me/Config_Vortex55) — اخبار، آپدیت‌ها و پشتیبانی
