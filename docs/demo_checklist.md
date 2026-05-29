# Demo Hazırlık Checklist'i (TR)

Bu dosya sana özel — her demo öncesi 10 dakikalık göz gezdirme.

## A. Mock-only demo (firmaya ilk gösterim)

### Hazırlık seçeneği 1: Docker compose (tek komut, en hızlı)
- [ ] Docker Desktop / Engine açık
- [ ] `docker compose up -d` → service + mock-device ayağa kalkar
- [ ] `docker compose ps` → ikisi de `healthy` olmalı
- [ ] Tarayıcıdan **http://localhost:8000/ui/** aç (service UI)
- [ ] Tarayıcıdan **http://localhost:9101/** aç (mock device dashboard)
- [ ] Service UI'da sağ üstte "IDLE" (DEFAULT_MODE=lan compose'da set) görmelisin

### Hazırlık seçeneği 2: Kaynak koddan (geleneksel)
- [ ] `git pull` veya zip aç
- [ ] `./setup.sh` (veya `.\setup.ps1`) çalıştır → `.venv` + `.env` + bağımlılıklar
- [ ] `.env` aç, `DEV_MODE=true`, `TRANSPORT_BACKEND=mock` olduğunu doğrula
- [ ] `.venv/bin/pytest tests/` → **160 passed** görmeli
- [ ] (PTY/LAN testi istiyorsan) `.venv/bin/python scripts/mock_device_server.py &`
- [ ] `.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000` başlat
- [ ] Tarayıcıdan **http://127.0.0.1:8000/ui/** aç
- [ ] Sağ üstte "DISCONNECTED" chip görüyor olmalısın

### Demo akışı (~5 dakika)

1. **Connect**
   - Mode: LAN seç → **Connect** bas → state IDLE olmalı, banner yeşil
   - Status panelinde: Paper=ok, Cover=closed, Temperature=~25°C

2. **Mutlu yol — Türkçe fiş**
   - **Print Text** bas
   - UI'da last_job DONE → status panel günceleniyor
   - Logs panelinde print_text satırı görünüyor
   - `paper_consumed_mm` arttı, `remaining_mm` azaldı (prediction çalışıyor)

3. **Resim eki**
   - Image dosyası seç (PNG, ≤2MB)
   - **Print with Image** bas → DONE
   - ETA panelinde image_ms doldu

4. **Reprint (mutlu)**
   - **Reprint last failed** ya da herhangi bir job → bytes birebir saklanıyor

5. **Hata senaryoları (Dev Tools panel)**
   - **Scenario: Paper out, Auto-recover: 3000** → Run
     - Bir sonraki print 503, error_code PAPER_OUT
     - 3 sn sonra otomatik kurtulur, state IDLE
     - **Reprint last failed** → başarılı (paper consumed sıfırlandı!)
   - **Scenario: Cover open** → Run → print 503 → **Recover all** → IDLE
   - **Scenario: Overheat** → Run → 503 → Manuel Temperature slider 25°C → IDLE
   - **Scenario: Comm drop** → Run → 503 → reconcile loop otomatik reconnect

6. **Logs CSV**
   - **Export CSV** bas → indirilen logs.csv'yi terminalde aç

### Sunum kaslarını esnetebileceğin teknik noktalar (CTO duyacak)
- "Mock UI **production'da kapalı** — DEV_MODE=false → /mock/* 404"
- "Render edilmiş ESC/POS byte'lar SQLite BLOB olarak saklanıyor; reprint birebir aynı çıktıyı veriyor"
- "ERROR_POLICY tablosu — yeni hata eklemek bir satırlık iş"
- "cp857 + transliterasyon: ş, ğ, ₺ glyph hatası demo'da görünmeyebilir ama gerçek cihaza özel"
- "Üç model tek codebase — `.env` overheat_resume_c=55 (KP-302) veya 60 (KP-301H)"
- "Print-done detection için **GS r 1 fence** kullanıyoruz — buffer drain bariyeri; gerçek bitişi yakalar (Cashino KP-300 manual s.63)"
- "Mock parser **ESC/POS-aware state machine** — variable-length komutların data bölgesinde kalan tesadüfi `10 04` byte'larını gerçek cihaz gibi atlıyor; PAPER_JAM false-positive bug'ını bu yüzden kapattık (`docs/escpos.md §5`)"

### Bonus: bug story olarak anlatabileceğin canlı senaryo
- Dev Tools'tan paper=41 set et → Print Text → done dönüyor (eski parser'da PAPER_JAM dönerdi)
- "Bu bug paper=41 + QR + raster içinde tesadüfi `10 04` baytlarıyla birebir tetikleniyordu;
  forensic raw_n2/n3/n4 byte log'larıyla teşhis edip mock parser'a state machine ekledik"

---

## B. Gerçek cihaz demo (3. aşama — ofiste)

### Hazırlık (cihaz başında)

#### LAN ile (önerilen — en güvenli)
- [ ] Cihazı Ethernet ile aynı ağa bağla
- [ ] Cihazın IP'sini öğren (firma söyleyecek veya cihaz menüsü)
- [ ] Aynı laptop'tan ping at: `ping <printer-ip>` → cevap alıyor mu
- [ ] `nc -vz <printer-ip> 9100` → bağlanıyor mu (ESC/POS portu)
- [ ] `.env` güncelle:
  - `TRANSPORT_BACKEND=real`
  - `LAN_HOST=<printer-ip>`
  - `LAN_PORT=9100`
  - (opsiyonel) `DEFAULT_MODE=lan` → startup'ta otomatik bağlan
- [ ] Servisi başlat → UI'dan **Connect** → IDLE olmalı

#### USB ile (yedek)
**Linux:**
- [ ] `lsusb` ile cihazı bul → vendor:product id'sini al (örn 0fe6:811e)
- [ ] `.env`: `USB_VENDOR_ID=0x0fe6 USB_PRODUCT_ID=0x811e`
- [ ] Setup udev rule'unu yükledi mi: `ls -la /etc/udev/rules.d/99-escpos.rules`
- [ ] Kullanıcın `plugdev` veya `lp` grubunda mı: `groups`
- [ ] Cihazı çıkar-tak (udev kuralı uygulansın)

**macOS:**
- [ ] `brew install libusb` (kurulu mu)
- [ ] `system_profiler SPUSBDataType | grep -A 3 -i cashino` → görünür mü

**Windows:**
- [ ] Zadig (https://zadig.akeo.ie) ile **libusb-win32** sürücüsünü cihaza ata
- [ ] Aygıt yöneticisinden cihazı doğrula

### Demo akışı
1. **/connect mode=lan** (veya usb) → state IDLE
2. **/status** → paper=ok, cover=closed
3. **/print/text** ile Türkçe fiş → **fiziksel çıktı**
4. Çıktıyı incele:
   - Başlık ortalanmış, "ACO RECYCLING" çift boy bold
   - MachineID + Türkçe tarih
   - Reward satırı büyük + bold
   - Tablo hizalı (Glass/Plastic/Metal/Tetrapak)
   - QR kod (telefon ile tara, içerik doğru mu)
   - Kesim çizgisi düzgün
5. **Hata göster** (firma izniyle):
   - Kapağı aç → /status ERROR/COVER_OPEN → kapat → IDLE
   - Kağıt çıkar → bir print dene → PAPER_OUT 503 → kağıt tak → reprint → çıktı
6. **/print/image** ile küçük PNG (logo) → eklenmiş çıktı

### Risk noktaları (önceden hazır cevap)
- **₺ glyph yanlış basıyor:** "cp857 ₺ desteklemiyor, transliteration politikasıyla TL'ye düşürdük; USE_LIRA_SYMBOL flag'i ile raw byte denemesi var"
- **Türkçe ş/ğ bozuk:** "Code page seçimi cihaz firmware'ine bağlı; cp857 cp1254 alternatifi var (.env)"
- **OVERHEAT göstermek zor:** "Datasheet 65°C stop / 55°C resume. Demo'da 50 fiş bas yerine mock'la zaman enjekte ederek deterministik test ediyoruz"
- **USB Windows'ta çalışmıyor:** "Zadig + libusb-win32 setup'ı manuel — Linux ve macOS'ta birincil test ettim. LAN her platformda çalışıyor"

### Cleanup
- [ ] Servisi durdur (Ctrl+C)
- [ ] `.env` token'ları temizle (varsa)
- [ ] `jobs.db` sıfırla (`rm jobs.db`) — yeni demo için temiz başlangıç
