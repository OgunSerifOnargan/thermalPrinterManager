# Termal Yazıcı Servisi — Aco Challenge (Question 1)

Ekte Ürünle ilgili 3 Farklı Datasheete ve 1 adet Fiş Görseline ulaşabilirsiniz. Lütfen geliştirdiğiniz servisin tüm dosyalarını tek bir .zip içinde yükleyin.

## Paket içeriği:

- **README.md** – Kurulum/çalıştırma, mimari, kullanılan teknolojiler, test adımları
- **Core Deliverable** – Zorunlu servis (aşağıdaki gereklilikleri karşılamalı).
- **Bonus Deliverable** (opsiyonel) – Ek geliştirmeler (aşağıda).
- Gerekirse örnek veri, ekran görüntüleri veya kısa demo videosu linki.

## CORE – Zorunlu Gereklilikler

### 1. Bağlantı Seçimi & Yeniden Bağlanma
- **USB ve Ethernet** haberleşmesi (ikisi de desteklenmeli).
- UI'da aktif bağlantı modu gösterilmeli.
- Bağlantı koptuğunda **otomatik yeniden bağlanma** (reconnect/backoff).

### 2. Minimum API Uçları (localhost)
- `POST /connect` → { mode: "usb" | "lan" }
- `POST /print/text` → basit metin yazdırma
- `POST /print/image` → görsel yazdırma
- `GET /status` → bağlantı, kağıt/kapak/sıcaklık durumu, son iş, kuyruk özeti
- `GET /logs` → işlem/hata kayıtları
- `POST /reprint` → başarısız işin ID'si ile tekrar bastırma

### 3. Hata Yönetimi (UI + Log)
- Aşağıdaki senaryolar **kullanıcı dostu mesaj** ve **log** ile gösterilmeli:
  - PAPER_OUT, PAPER_JAM, COVER_OPEN, OVERHEAT, COMM_ERROR, UNKNOWN_COMMAND
- **Basılamayan görseller** kaydedilmeli ve UI'da **"Tekrar Bastır"** seçeneği olmalı.

### 4. Loglama (Örnek Şema)
```json
{
  "ts": "2025-10-02T12:34:56Z",
  "op": "print_image",
  "conn": "usb",
  "jobId": "abc123",
  "status": "error",
  "error": { "code": "PAPER_OUT", "detail": "No paper detected" }
}
```

- Başarılı/başarısız tüm işlemler kaydedilmeli; okunabilir format (JSON/CSV).
- **API entegrasyonu:**
  - Postman vb. dış tetikleyicilerle servisin çalışabildiğini gösterme
  - API üzerinden fiş basma, durum sorgulama
- **Çoklu dil desteği:**
  - Türkçe, İngilizce (isteğe bağlı ek diller) içerik bastırabilme
- **Farklı içerik tipleri:**
  - Normal metin
  - Resim bastırabilme
  - QR bastırabilme

## Bonus Deliverable (Opsiyonel, ek puan getirir)

### 1. Basit UI (localhost)
- Bağlantı modu, anlık durum, son iş ve kuyruk görünümü.
- Hata banner'ları ve "Tekrar Bastır" butonu.

### 2. Dokümantasyon – README.md zorunlu
- **Kurulum:** bağımlılıklar, .env örneği, port (örn. 3000).
- **Çalıştırma:** tek komutla başlatma (örn. npm start / python app.py).
- **Test:** nasıl deneneceği (örnek curl/request örnekleri).
- **Mimari notları:** modüler yapı, klasör düzeni, varsayımlar.

> **Not:** Fiziksel cihaz zorunlu değildir; **simülasyon/mocking** ile geliştirilebilir. Gerçek cihazla test 3. Aşamaya geçen adaylarla sağlanacaktır.

## BONUS

- **Prediction:** rulo/fiş ömrü veya basım ETA tahmini.
- **Kuyruk & idempotency:** iş tekrarları için güvenli tasarım.
- **Retry/backoff stratejisi** ve **health-check** ucu (/health).
- **Dockerfile** veya docker-compose.yml.
- **Log export** (CSV indirme) ve basit **yetkilendirme** (örn. token).

## Teslim Kuralları

- İçerik: kaynak kod, README.md, 1–2 **UI ekran görüntüsü**, örnek loglar (logs.json), varsa Dockerfile.
- **Dil/Stack serbest:** Node.js / Python / Go / C# vb.
- **Sabit yol/credential** gömülmemeli, **.env** kullanılmalı.
- Dış servis/anahtar gerektiren ücretli SDK kullanmayınız (gerekirse açıkça not ediniz).

## Değerlendirme (bilgilendirme)

- **Core işlevler:** %70 (bağlantı, API, hata yönetimi, reprint, log)
- **Kod kalitesi & README:** %20 (modülerlik, temiz kod, net talimatlar)
- **Bonus:** %10

## Initial file template

- Cashino KP300 User Manual V1 (2).pdf
- KP-301H User Manual.V1.0 (4).pdf