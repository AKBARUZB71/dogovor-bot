# Dogovor Drop

18 ta menejer va adminga **bitta umumiy navbatdan** shartnoma raqamlarini beruvchi Telegram bot.
Oxirgi ishlatilgan raqam: 2729, birinchi yangi raqam: **2730**.

Texnologiyalar: Python 3.12, aiogram 3.31, SQLite (WAL, `BEGIN IMMEDIATE`).

---

## 1. Loyiha tuzilishi

```
app/
  bot.py            dispatcher, xato ishlovchisi, ishga tushirish
  config.py         .env dan sozlamalar
  db.py             sxema, tranzaksiyalar, raqam ajratish, outbox, audit
  outbox.py         yuborilmagan xabarlar navbati (qayta urinishlar bilan)
  middlewares.py    faqat shaxsiy chat, foydalanuvchi bo'yicha navbat, admin filtri
  handlers/user.py  /start, ro'yxatdan o'tish, menejer tugmalari
  handlers/admin.py admin paneli
  texts.py          o'zbekcha matnlar
  managers.py       18 menejer ro'yxati
scripts/backup.py   zaxira nusxa va tiklash
tests/              26 ta avtomatik test
deploy/             systemd unit
Dockerfile, docker-compose.yml, .env.example, requirements*.txt
```

## 2. Raqamlar qanday kafolatlanadi

| Talab | Yechim |
|---|---|
| Bitta umumiy hisoblagich | `counter` jadvalida yagona qator (`id = 1`). Menejer ham, admin ham shu qatordan oladi. |
| Parallel so'rovlarda dublikat yo'q | Har bir ajratish `BEGIN IMMEDIATE` tranzaksiyasida: yozish blokirovkasi olinadi → `last_number + 1` → `contracts` ga yoziladi → xabarlar outbox'ga qo'yiladi → `COMMIT`. `busy_timeout` = 15 s. |
| UNIQUE | `contracts.number UNIQUE`, `contracts.request_key UNIQUE`, `managers.telegram_id UNIQUE`. |
| Xatoda raqam sarflanmaydi | Tranzaksiyaning istalgan bosqichida xato → `ROLLBACK`, hisoblagich ham orqaga qaytadi. |
| Qayta kelgan update | `request_key = upd:<update_id>`. Shu kalit bazada bo'lsa, yangi raqam emas — mavjud natija. |
| Tez takroriy bosish | Foydalanuvchi so'rovlari navbat bilan (per-user lock). Oxirgi raqamdan keyin 5 s ichida bosilsa — o'sha raqam qaytariladi. |
| Restart / server o'chishi | Hammasi SQLite faylida (`synchronous = FULL`). Boshlang'ich 2729 faqat `counter` qatori umuman yo'q bo'lganda qo'yiladi. |
| Hisoblagichni buzishdan himoya | Trigger: hisoblagich kamaya olmaydi; `contracts` dan o'chirish taqiqlangan. |
| Xabar yetmasa | Outbox: natija va har bir admin bildirishnomasi alohida holatga ega (`pending/sent/failed`), cheklangan qayta urinish (5 s, 10 s, 20 s … ≤10 daqiqa). Menejer yetkazilmagan raqam bo'lsa tugmani bossa — **o'sha raqam** qayta yuboriladi, yangisi ajratilmaydi. |

Telegram'dan javob yo'qolsa (xabar borgan, lekin tasdiq kelmagan) xabar ikki marta borishi mumkin — bu Telegram cheklovi. Lekin bitta so'rovga bazada **faqat bitta raqam** ajratiladi.

**Muhim:** bot faqat **bitta nusxada** ishlashi kerak (bitta server, bitta jarayon). Ikki nusxa bir vaqtda ishlasa Telegram polling'da konflikt bo'ladi.

## 3. Sozlash

`.env.example` dan nusxa olib `.env` yarating:

```
BOT_TOKEN=...            # BotFather tokeni
ADMIN_IDS=111,222        # admin Telegram user ID'lari
DB_PATH=data/dogovor.db
INITIAL_LAST_NUMBER=2729 # faqat yangi bazada qo'llanadi
DEBOUNCE_SECONDS=5
OUTBOX_MAX_ATTEMPTS=10
LOG_LEVEL=INFO
```

Loyiha papkasidagi `.env` da haqiqiy token va 2 ta admin ID allaqachon yozilgan. `.env` git'ga va Docker image'ga kirmaydi (`.gitignore`, `.dockerignore`).

## 4. Serverga joylash (tavsiya: VPS + Docker)

**Server talablari:** Linux VPS (Ubuntu 22.04/24.04), 1 vCPU, 512 MB RAM, 1 GB disk yetarli. Kiruvchi port kerak emas (polling). Docker va Docker Compose.

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
```

Loyiha papkasini serverga ko'chiring (masalan `/opt/dogovor-drop`), `.env` faylini ham. Keyin:

```bash
cd /opt/dogovor-drop
```

```bash
mkdir -p data backups && sudo chown -R 10001:10001 data backups
```

```bash
sudo docker compose up -d --build
```

```bash
sudo docker compose logs -f
```

Logda `Run polling for bot @citric_dogovorbot` chiqishi kerak. `restart: unless-stopped` — server qayta yonganda yoki bot yiqilganda avtomatik qayta ishga tushadi.

Yangilash: kodni almashtirib `sudo docker compose up -d --build`. Baza `data/` papkasida qoladi, hisoblagich qayta o'rnatilmaydi.

### Docker'siz (systemd)

```bash
sudo useradd --system --home /opt/dogovor-drop dogovor
```

```bash
cd /opt/dogovor-drop && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
mkdir -p data backups && sudo chown -R dogovor data backups && sudo chmod 600 .env && sudo chown dogovor .env
```

```bash
sudo cp deploy/dogovor-drop.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now dogovor-drop
```

```bash
journalctl -u dogovor-drop -f
```

### Windows kompyuterda (sinov uchun)

python.org'dan Python 3.12 o'rnating, keyin loyiha papkasida:

```bash
py -3.12 -m pip install -r requirements.txt
```

```bash
py -3.12 -m app
```

Kompyuter o'chsa bot ham to'xtaydi — doimiy ishlash uchun VPS tavsiya etiladi.

## 5. Zaxira nusxa va tiklash

Zaxira bot ishlab turganda ham xavfsiz (SQLite online backup API, `integrity_check` bilan):

```bash
python3 scripts/backup.py backup --db data/dogovor.db --out backups --keep 30
```

Docker'da:

```bash
sudo docker compose exec bot python scripts/backup.py backup --db /app/data/dogovor.db --out /app/backups --keep 30
```

Har kuni 23:30 da avtomatik (crontab -e):

```
30 23 * * * cd /opt/dogovor-drop && docker compose exec -T bot python scripts/backup.py backup --db /app/data/dogovor.db --out /app/backups --keep 30
```

`backups/` papkasini vaqti-vaqti bilan serverdan tashqariga ham ko'chiring.

**Tiklash** (avval botni to'xtating):

```bash
sudo docker compose stop
```

```bash
python3 scripts/backup.py restore backups/dogovor_YYYYMMDD_HHMMSS.db --db data/dogovor.db
```

```bash
sudo docker compose start
```

Skript joriy bazani `*_before_restore_*.db` nomi bilan saqlab qo'yadi, `-wal/-shm` fayllarni tozalaydi va tiklangan bazadagi oxirgi raqamni ko'rsatadi.

## 6. Testlar

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -v
```

**Haqiqiy natija (2026-11-06, Windows 10, Python 3.12.7, aiogram 3.31.0):** `26 passed`, ketma-ket 3 marta ishga tushirildi — 3/3 o'tdi.

Bot oqimi testlari update'larni haqiqiy aiogram Dispatcher orqali o'tkazadi; Telegram API o'rniga soxta sessiya ishlaydi (u yuborilgan xabarlarni yozib boradi va tarmoq xatosini simulyatsiya qila oladi).

| Majburiy tekshiruv | Test |
|---|---|
| Birinchi raqam 2730 | `test_first_number_is_2730`, `test_registration_requires_admin_approval` |
| Menejer va admin bitta ketma-ketlikda | `test_managers_and_admin_share_one_sequence`, `test_manager_and_admin_share_sequence_via_bot` (Sanjar 2730, Nodir 2731, Admin 2732, Sanjar 2733) |
| 19 parallel so'rovda dublikat yo'q | `test_19_parallel_requests_get_unique_consecutive_numbers` (19 thread, alohida ulanishlar), `test_19_parallel_requests_through_dispatcher` → 2730…2748 |
| Bir xil update qayta kelsa yangi raqam yo'q | `test_same_request_key_does_not_create_new_number`, `test_replayed_update_does_not_create_new_number` |
| Tez takroriy bosishda oxirgi raqam | `test_rapid_repeat_returns_last_number_then_allows_new`, `test_rapid_double_press_returns_last_number` |
| Restartdan keyin davom etadi | `test_restart_continues_and_initial_value_not_reapplied` |
| Tranzaksiya xatosida raqam sarflanmaydi | `test_transaction_failure_does_not_consume_number` |
| Telegram xatosida raqam qayta ajratilmaydi | `test_telegram_failure_does_not_reallocate_number`, `test_outbox_gives_up_after_limit_and_user_can_trigger_redelivery` |
| Begona/bloklangan raqam ololmaydi | `test_stranger_and_blocked_cannot_get_number`, `test_stranger_blocked_and_non_admin_are_forbidden` |
| Menejer admin amallarini bajara olmaydi | `test_manager_cannot_perform_admin_actions` |

Qo'shimcha: band profilni egallab bo'lmasligi, bitta profil — bitta ID, ID almashtirish va audit, CSV tarkibi, guruh chatini e'tiborsiz qoldirish, admin o'ziga ikki marta xabar olmasligi, Toshkent vaqti (09:00 UTC → 14:00:00).

Jonli sinov: haqiqiy token bilan bot ishga tushirildi, Telegram'ga ulandi (`@citric_dogovorbot`), 15 soniya xatosiz ishladi, logda token ko'rinmadi. Telegram'da qo'lda bosib tekshirish hali qilinmagan.

## 7. Audit jurnali

`audit_log` jadvaliga yoziladi: baza yaratilishi, ro'yxat arizalari, tasdiqlash/rad etish, bloklash/blokdan chiqarish, Telegram ID almashtirish (eski → yangi), CSV eksport, yetkazilmagan xabarlar, handler xatolari.

```bash
sqlite3 data/dogovor.db "SELECT at, actor_id, action, details FROM audit_log ORDER BY id DESC LIMIT 30;"
```

---

## Menejer uchun qo'llanma

1. Botga kiring: **@citric_dogovorbot** → **Start**.
2. Ro'yxatdan o'z ismingizni tanlang. Ariza adminga ketadi.
3. Admin tasdiqlagach, «Arizangiz tasdiqlandi» xabari keladi va pastda ikki tugma paydo bo'ladi.
4. **«Dogovor raqami olish»** — keyingi raqamni beradi. Raqamni bosib nusxalash mumkin.
5. **«Mening oxirgi raqamlarim»** — sizning oxirgi 10 ta raqamingiz.

Bir marta bosish yetarli. 5 soniya ichida qayta bossangiz, yangi raqam emas, o'sha raqam qayta ko'rsatiladi.
Telefon raqami yoki Telegram akkauntingiz o'zgarsa, adminga murojaat qiling.

## Admin uchun qo'llanma

/start — admin paneli ochiladi.

- **O'zimiz uchun dogovor raqami olish** — menejerlar bilan bir xil umumiy navbatdan raqam. Bazaga «Admin — o'zimiz uchun» deb yoziladi. Ikkinchi adminga bildirishnoma boradi.
- **Oxirgi berilgan raqam** — kim, qachon, qaysi raqamni olgani.
- **Bugungi raqamlar** — Toshkent vaqti bo'yicha bugun berilganlar.
- **Menejer bo'yicha tarix** — menejer (yoki «Admin — o'zimiz uchun») tanlanadi, oxirgi 20 ta raqam.
- **Arizalar** — kutilayotgan arizalar, «Tasdiqlash» / «Rad etish». Yangi ariza kelganda bot o'zi ham xabar beradi.
- **Menejerlar** — menejer tanlanadi:
  - **Bloklash / Blokdan chiqarish** — bloklangan menejer raqam ololmaydi.
  - **Telegram ID'ni almashtirish** — yangi ID yuboriladi; `0` — profilni bo'shatish (menejer qayta ro'yxatdan o'tishi mumkin bo'ladi); `bekor` — bekor qilish.
  - **Tarix**.
- **CSV eksport** — barcha raqamlar tarixi fayl ko'rinishida (Excel'da ochiladi).

Har bir menejer raqam olganda adminlarga «Yangi dogovor raqami olindi» xabari keladi.
Panelda hisoblagichni kamaytirish yoki tarixni o'chirish imkoniyati ataylab yo'q.
