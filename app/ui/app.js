/* Aco Recycling — Thermal Printer Service UI
   Single-page vanilla JS. State is a global `state` object; `render(state)`
   redraws the panels. No frameworks (per plan R8).

   I18n
   ----
   Every visible string lives in the `I18N` dictionary keyed by language
   code (`tr`, `en`). HTML elements opt in via `data-i18n="<key>"` (text
   content), `data-i18n-html="<key>"` (innerHTML for keys that embed
   <strong>/<code>), or `data-i18n-placeholder="<key>"` (input
   placeholder). `applyLanguage()` walks the DOM and patches them; the
   choice is persisted in localStorage. Dynamic strings (banner messages,
   status panel value mapping) use the `t(key)` helper.
*/

const I18N = {
  en: {
    page_title:               "Aco Recycling — Thermal Printer Service",
    brand_sub:                "Thermal Printer Service",

    // Connection
    conn_title:               "Connection",
    conn_mode:                "Mode",
    conn_mode_lan_direct:     "LAN — auto-detect direct cable",
    conn_mode_lan:            "LAN (manual host)",
    conn_mode_usb:            "USB",
    conn_btn_connect:         "Connect",
    conn_btn_disconnect:      "Disconnect",
    conn_btn_refresh:         "↻ Refresh",
    conn_scanning:            "— scanning… —",
    conn_eth_detected:        "Detected Ethernet devices",
    conn_eth_hint:            "Plug the printer into your Ethernet port. The service scans every wired interface and lists Cashino-shaped listeners here — pick one and press <strong>Connect</strong>. In <code>DEV_MODE</code> the local mock device shows up so you can rehearse the flow without a cable.",
    conn_lan_host:            "Host (IP)",
    conn_lan_host_ph:         "192.168.1.50 or 127.0.0.1",
    conn_lan_port:            "Port",
    conn_lan_discover_btn:    "⇲ Discover devices on LAN",
    conn_usb_cdc_detected:    "Detected USB-CDC devices",
    conn_usb_cdc_path:        "USB-CDC device path",
    conn_usb_cdc_path_ph:     "/tmp/mock-printer-usb or /dev/cu.usbserial-XXXX",
    conn_usb_cdc_hint:        "Pick from the dropdown above or type a path manually. Uses pyserial (USB-CDC), works for most Cashino USB printers.",
    conn_usb_libusb_detected: "Detected libusb devices",
    conn_usb_vid:             "Vendor ID (hex)",
    conn_usb_pid:             "Product ID (hex)",
    conn_usb_libusb_hint:     "libusb fallback for vendor-class devices (no USB-CDC endpoint). Leave the path above empty to use this.",
    conn_usb_none_cdc:        "(no USB-CDC devices detected)",
    conn_usb_none_libusb:     "(no libusb devices visible)",
    conn_btn_connect_use:     "Use",
    conn_env_fallback_hint:   "Empty fields fall back to <code>.env</code> values. On mock backend the parameters are recorded but ignored (one simulated device serves both modes).",

    // Print Receipt
    print_title:              "Print Receipt",
    print_machine_id:         "Machine ID",
    print_receipt_lang:       "Receipt language",
    print_field_title:        "Title",
    print_total_reward:       "Total Reward (TL)",
    print_th_product:         "Product",
    print_th_qty:             "Qty",
    print_th_reward:          "Reward",
    print_btn_add_item:       "+ item",
    print_qr_content:         "QR content (optional)",
    print_image_field:        "Image (optional, < 2MB PNG/JPG)",
    print_btn_text:           "Print Text",
    print_btn_image:          "Print with Image",
    print_btn_reprint:        "Reprint last failed",
    reprint_section:          "Reprint",
    reprint_recent_label:     "Recent failures",
    reprint_recent_loading:   "— loading… —",
    reprint_recent_empty:     "— no recent failures —",
    reprint_recent_failed:    "(failed to load)",
    reprint_uuid_label:       "Job UUID",
    reprint_uuid_ph:          "8580b2d9-… (paste or pick from above)",
    reprint_btn_by_uuid:      "Reprint by UUID",
    reprint_hint_last:        "No UUID needed — server picks the most recent failure.",
    reprint_need_uuid:        "Enter or pick a job UUID first.",

    // Status
    status_title:             "Status",
    status_mode:              "Mode",
    status_connected:         "Connected",
    status_disconnect_reason: "Disconnect reason",
    status_paper:             "Paper",
    status_cover:             "Cover",
    status_temp:              "Temperature",
    status_overheated:        "Overheated",
    status_jammed:            "Jammed",
    status_last_seen:         "Last seen",
    status_last_job:          "Last Job",
    status_lj_id:             "ID",
    status_lj_op:             "Op",
    status_lj_status:         "Status",
    status_lj_error:          "Error",
    status_lj_at:             "At",
    status_eta:               "ETA",
    status_eta_avg:           "moving avg ·10",
    status_eta_text:          "Print (text)",
    status_eta_image:         "Print (image)",
    status_eta_samples:       "samples",
    status_eta_no_samples:    "—  (no samples yet)",
    status_reconnect:         "Auto-reconnect",
    status_rc_attempt:        "Attempt",
    status_rc_next:           "Next retry in",
    status_rc_last_ts:        "Last try at",
    status_rc_last_error:     "Last error",

    // Preview
    preview_title:            "Last Receipt",
    preview_btn_refresh:      "Refresh",
    preview_btn_raw:          "Raw",
    preview_hint:             "Visual preview of what the thermal printer would emit (center / bold / double-size applied, QR shown as a box). Updates after each print.",
    preview_empty:            "(no receipt yet)",

    // Dev tools
    dev_title:                "Dev Tools",
    dev_mock_device_label:    "mock device: …",
    dev_hint:                 "Inject scenarios into the mock printer.",
    dev_mock_offline:         "Mock device service is configured but offline. Start it with <code>python scripts/mock_device_server.py</code>.",
    dev_paper:                "Paper lines",
    dev_temp:                 "Temperature °C",
    dev_cover:                "Cover",
    dev_cover_closed:         "closed",
    dev_cover_open:           "open",
    dev_jammed:               "Jammed",
    dev_yes:                  "yes",
    dev_no:                   "no",
    dev_comm_error:           "Comm error",
    dev_on:                   "on",
    dev_off:                  "off",
    dev_scenario:             "Scenario",
    dev_sc_paper_out:         "Paper out",
    dev_sc_cover_open:        "Cover open",
    dev_sc_jam:               "Jam",
    dev_sc_overheat:          "Overheat",
    dev_sc_comm_drop:         "Comm drop",
    dev_sc_recover_all:       "Recover all",
    dev_auto_recover:         "Auto-recover after (ms)",
    dev_btn_apply:            "Apply",
    dev_btn_run_scenario:     "Run scenario",
    dev_mock_chip_base:       "mock device",
    dev_mock_chip_in_process: "mock: in-process",
    dev_mock_online:          "ONLINE",
    dev_mock_offline:         "OFFLINE",
    dev_mock_hint_in_process: "Mock printer runs inside this service process.",
    dev_mock_hint_external_online:  "External mock device at {url}. Controls forwarded over HTTP.",
    dev_mock_hint_external_offline: "Configured at {url} but unreachable.",

    // Logs
    logs_title:               "Logs",
    logs_btn_export:          "Export CSV",
    logs_loading:             "loading…",

    // Status value mapping (paper, cover, etc — driven by status JSON)
    val_paper_ok:             "ok",
    val_paper_low:            "low",
    val_paper_out:            "out",
    val_cover_open:           "open",
    val_cover_closed:         "closed",
    val_yes:                  "yes",
    val_no:                   "no",
    val_true:                 "true",
    val_false:                "false",
    val_unknown:              "—",

    // Dynamic banner phrases (composed in JS)
    banner_connected_via:     "Connected via",
    banner_disconnected:      "Disconnected.",
    banner_printed:           "Printed",
    banner_printed_image:     "Printed image",
    banner_reprinted_from:    "Reprinted from",
    banner_last_failed:       "last failed",
    banner_no_job_to_reprint: "No job to reprint yet.",
    banner_already_done:      "ℹ️ This job was already printed successfully — no reprint needed:",
    banner_pick_image:        "Pick an image file first.",
    banner_rate_limit:        "⛔ Rate limit hit — try again in",
    banner_seconds:           "s",
    banner_print_failed:      "Print failed",
    banner_print_image_failed:"Print image failed",
    banner_reprint_failed:    "Reprint failed",
    banner_multiple_devices:  "Multiple devices found.",
    banner_connect_failed:    "Connect failed",

    // LAN discover status messages
    discover_scanning:        "Scanning local /24…",
    discover_failed:          "Discover failed",
    discover_local_ip_undet:  "Local IP undetected — no usable network route. Enter the host manually.",
    discover_scanned:         "Scanned",
    discover_hosts_on:        "hosts on",
    discover_tcp_open:        "TCP-open,",
    discover_cashino_shaped:  "Cashino-shaped.",
    discover_selected:        "Selected",
    discover_press_connect:   "Press Connect.",
    discover_multiple_pick:   "Multiple devices found — pick one then press Connect.",
    discover_no_cashino:      "no Cashino detected",
    discover_interfaces:      "interface(s) scanned",
    discover_pick_device:     "— pick a device —",
    discover_via:             "via",
    discover_ms:              "ms",
  },

  tr: {
    page_title:               "Aco Recycling — Termal Yazıcı Servisi",
    brand_sub:                "Termal Yazıcı Servisi",

    // Connection
    conn_title:               "Bağlantı",
    conn_mode:                "Mod",
    conn_mode_lan_direct:     "LAN — direkt kablo otomatik tespit",
    conn_mode_lan:            "LAN (manuel host)",
    conn_mode_usb:            "USB",
    conn_btn_connect:         "Bağlan",
    conn_btn_disconnect:      "Bağlantıyı kes",
    conn_btn_refresh:         "↻ Yenile",
    conn_scanning:            "— taranıyor… —",
    conn_eth_detected:        "Tespit edilen Ethernet cihazları",
    conn_eth_hint:            "Yazıcıyı Ethernet portuna takın. Servis tüm kablo arayüzlerini tarar ve Cashino-uyumlu adayları burada listeler — birini seçip <strong>Bağlan</strong>'a basın. <code>DEV_MODE</code> açıkken yerel mock cihaz da listede çıkar, böylece akışı kablo olmadan deneyebilirsiniz.",
    conn_lan_host:            "Host (IP)",
    conn_lan_host_ph:         "192.168.1.50 veya 127.0.0.1",
    conn_lan_port:            "Port",
    conn_lan_discover_btn:    "⇲ LAN üzerindeki cihazları bul",
    conn_usb_cdc_detected:    "Tespit edilen USB-CDC cihazları",
    conn_usb_cdc_path:        "USB-CDC cihaz yolu",
    conn_usb_cdc_path_ph:     "/tmp/mock-printer-usb veya /dev/cu.usbserial-XXXX",
    conn_usb_cdc_hint:        "Yukarıdaki listeden seçin ya da elle bir yol yazın. pyserial (USB-CDC) kullanır, çoğu Cashino USB yazıcıda çalışır.",
    conn_usb_libusb_detected: "Tespit edilen libusb cihazları",
    conn_usb_vid:             "Vendor ID (hex)",
    conn_usb_pid:             "Product ID (hex)",
    conn_usb_libusb_hint:     "USB-CDC endpoint'i olmayan vendor-class cihazlar için libusb yedeği. Bunu kullanmak için yukarıdaki yolu boş bırakın.",
    conn_usb_none_cdc:        "(USB-CDC cihaz tespit edilmedi)",
    conn_usb_none_libusb:     "(libusb cihaz görünmüyor)",
    conn_btn_connect_use:     "Kullan",
    conn_env_fallback_hint:   "Boş alanlar <code>.env</code> değerlerine düşer. Mock backend'de parametreler kaydedilir ama yoksayılır (tek simüle cihaz iki modu da karşılar).",

    // Print Receipt
    print_title:              "Fiş Bas",
    print_machine_id:         "Cihaz ID",
    print_receipt_lang:       "Fiş dili",
    print_field_title:        "Başlık",
    print_total_reward:       "Toplam Ödül (TL)",
    print_th_product:         "Ürün",
    print_th_qty:             "Adet",
    print_th_reward:          "Ödül",
    print_btn_add_item:       "+ kalem",
    print_qr_content:         "QR içeriği (opsiyonel)",
    print_image_field:        "Görsel (opsiyonel, < 2MB PNG/JPG)",
    print_btn_text:           "Metin Bas",
    print_btn_image:          "Görselli Bas",
    print_btn_reprint:        "Son hatalıyı tekrar bas",
    reprint_section:          "Yeniden bas",
    reprint_recent_label:     "Son hatalı işler",
    reprint_recent_loading:   "— yükleniyor… —",
    reprint_recent_empty:     "— son hatalı iş yok —",
    reprint_recent_failed:    "(yüklenemedi)",
    reprint_uuid_label:       "İş UUID",
    reprint_uuid_ph:          "8580b2d9-… (yapıştır veya yukarıdan seç)",
    reprint_btn_by_uuid:      "UUID ile yeniden bas",
    reprint_hint_last:        "UUID gerekmez — sunucu son hatalıyı bulup basar.",
    reprint_need_uuid:        "Önce bir UUID yazın veya listeden seçin.",

    // Status
    status_title:             "Durum",
    status_mode:              "Mod",
    status_connected:         "Bağlı",
    status_disconnect_reason: "Kopma sebebi",
    status_paper:             "Kağıt",
    status_cover:             "Kapak",
    status_temp:              "Sıcaklık",
    status_overheated:        "Aşırı ısınma",
    status_jammed:            "Sıkışma",
    status_last_seen:         "Son görüşme",
    status_last_job:          "Son İş",
    status_lj_id:             "Kimlik",
    status_lj_op:             "İşlem",
    status_lj_status:         "Durum",
    status_lj_error:          "Hata",
    status_lj_at:             "Zaman",
    status_eta:               "Tahmini Süre",
    status_eta_avg:           "kayan ort ·10",
    status_eta_text:          "Bas (metin)",
    status_eta_image:         "Bas (görsel)",
    status_eta_samples:       "örnek",
    status_eta_no_samples:    "—  (henüz örnek yok)",
    status_reconnect:         "Otomatik yeniden bağlanma",
    status_rc_attempt:        "Deneme",
    status_rc_next:           "Sonraki deneme",
    status_rc_last_ts:        "Son deneme",
    status_rc_last_error:     "Son hata",

    // Preview
    preview_title:            "Son Fiş",
    preview_btn_refresh:      "Yenile",
    preview_btn_raw:          "Ham",
    preview_hint:             "Termal yazıcının basacağı şeyin görsel önizlemesi (ortalama / kalın / çift-boy uygulanmış, QR kutu olarak). Her print sonrası güncellenir.",
    preview_empty:            "(henüz fiş yok)",

    // Dev tools
    dev_title:                "Geliştirici Araçları",
    dev_mock_device_label:    "mock cihaz: …",
    dev_hint:                 "Mock yazıcıya senaryo enjekte et.",
    dev_mock_offline:         "Mock cihaz servisi yapılandırılmış ama erişilebilir değil. Şu komutla başlatın: <code>python scripts/mock_device_server.py</code>.",
    dev_paper:                "Kağıt satırı",
    dev_temp:                 "Sıcaklık °C",
    dev_cover:                "Kapak",
    dev_cover_closed:         "kapalı",
    dev_cover_open:           "açık",
    dev_jammed:               "Sıkışma",
    dev_yes:                  "evet",
    dev_no:                   "hayır",
    dev_comm_error:           "Bağlantı hatası",
    dev_on:                   "açık",
    dev_off:                  "kapalı",
    dev_scenario:             "Senaryo",
    dev_sc_paper_out:         "Kağıt bitti",
    dev_sc_cover_open:        "Kapak açık",
    dev_sc_jam:               "Sıkışma",
    dev_sc_overheat:          "Aşırı ısınma",
    dev_sc_comm_drop:         "Bağlantı koptu",
    dev_sc_recover_all:       "Hepsini düzelt",
    dev_auto_recover:         "Otomatik düzelt (ms)",
    dev_btn_apply:            "Uygula",
    dev_btn_run_scenario:     "Senaryoyu çalıştır",
    dev_mock_chip_base:       "mock cihaz",
    dev_mock_chip_in_process: "mock: bu süreçte",
    dev_mock_online:          "AÇIK",
    dev_mock_offline:         "ERİŞİLEMEZ",
    dev_mock_hint_in_process: "Mock yazıcı bu servis süreci içinde çalışıyor.",
    dev_mock_hint_external_online:  "Harici mock cihaz {url} adresinde. Kontroller HTTP üzerinden yönlendiriliyor.",
    dev_mock_hint_external_offline: "{url} olarak yapılandırılmış ama erişilemez.",

    // Logs
    logs_title:               "Loglar",
    logs_btn_export:          "CSV İndir",
    logs_loading:             "yükleniyor…",

    // Status value mapping
    val_paper_ok:             "tam",
    val_paper_low:            "az",
    val_paper_out:            "bitti",
    val_cover_open:           "açık",
    val_cover_closed:         "kapalı",
    val_yes:                  "evet",
    val_no:                   "hayır",
    val_true:                 "evet",
    val_false:                "hayır",
    val_unknown:              "—",

    // Dynamic banner phrases
    banner_connected_via:     "Bağlandı:",
    banner_disconnected:      "Bağlantı kesildi.",
    banner_printed:           "Basıldı",
    banner_printed_image:     "Görsel basıldı",
    banner_reprinted_from:    "Tekrar bas:",
    banner_last_failed:       "son hatalı",
    banner_no_job_to_reprint: "Tekrar basılacak iş yok.",
    banner_already_done:      "ℹ️ Bu iş zaten başarıyla basıldı — tekrar bastırmaya gerek yok:",
    banner_pick_image:        "Önce bir görsel dosya seçin.",
    banner_rate_limit:        "⛔ İstek limiti aşıldı — şu kadar saniye sonra dene:",
    banner_seconds:           "sn",
    banner_print_failed:      "Print başarısız",
    banner_print_image_failed:"Görsel print başarısız",
    banner_reprint_failed:    "Tekrar print başarısız",
    banner_multiple_devices:  "Birden fazla cihaz bulundu.",
    banner_connect_failed:    "Bağlantı başarısız",

    // LAN discover status messages
    discover_scanning:        "Yerel /24 taranıyor…",
    discover_failed:          "Tarama başarısız",
    discover_local_ip_undet:  "Yerel IP tespit edilemedi — kullanılabilir ağ yolu yok. Host'u elle girin.",
    discover_scanned:         "Tarandı:",
    discover_hosts_on:        "host /",
    discover_tcp_open:        "TCP-açık,",
    discover_cashino_shaped:  "Cashino uyumlu.",
    discover_selected:        "Seçildi:",
    discover_press_connect:   "Bağlan'a basın.",
    discover_multiple_pick:   "Birden fazla cihaz bulundu — birini seçip Bağlan'a basın.",
    discover_no_cashino:      "Cashino tespit edilmedi",
    discover_interfaces:      "arayüz tarandı",
    discover_pick_device:     "— bir cihaz seçin —",
    discover_via:             "via",
    discover_ms:              "ms",
  },
};

function t(key) {
  const lang = localStorage.getItem("ui_lang") || "en";
  const dict = I18N[lang] || I18N.en;
  return dict[key] ?? I18N.en[key] ?? key;
}

function applyLanguage(lang) {
  if (!I18N[lang]) lang = "en";
  const dict = I18N[lang];
  document.documentElement.lang = lang;

  for (const el of document.querySelectorAll("[data-i18n]")) {
    const v = dict[el.dataset.i18n];
    if (v != null) el.textContent = v;
  }
  for (const el of document.querySelectorAll("[data-i18n-html]")) {
    const v = dict[el.dataset.i18nHtml];
    if (v != null) el.innerHTML = v;
  }
  for (const el of document.querySelectorAll("[data-i18n-placeholder]")) {
    const v = dict[el.dataset.i18nPlaceholder];
    if (v != null) el.placeholder = v;
  }
  document.title = dict.page_title || document.title;
  localStorage.setItem("ui_lang", lang);
  // Re-render dynamic panels so banner / status value mapping refreshes.
  if (typeof renderStatus === "function" && state.status) {
    renderStatus(state.status);
  }
}

const state = {
  health: null,
  status: null,
  lastFailedJobId: null,
  banner: null,
  // Last image the user uploaded (data URL). Used by the preview to show
  // the actual upload instead of duplicating the logo.
  lastUploadedDataUrl: null,
  // Detected at startup — drives whether [IMAGE #1] is the logo or the upload.
  logoAvailable: true,
};

const POLL_MS = parseInt(document.querySelector("meta[name='poll']")?.content || "1500", 10);

// ---------- Default items ----------
const DEFAULT_ITEMS = [
  { product: "Glass",    quantity: 0, reward: 0 },
  { product: "Plastic",  quantity: 2, reward: 2 },
  { product: "Metal",    quantity: 1, reward: 1 },
  { product: "Tetrapak", quantity: 0, reward: 0 },
];
let items = [...DEFAULT_ITEMS];

// ---------- Helpers ----------
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const text = await r.text();
  let body = {};
  try { body = JSON.parse(text); } catch {}
  return { ok: r.ok, status: r.status, body };
}

// `sticky=true` means the banner must stay until another button action
// explicitly replaces it. Status-poll-driven banners (printer ERROR, breaker
// open) are non-sticky and may be cleared when the device returns to IDLE.
function setBanner(kind, msg, { sticky = false } = {}) {
  state.banner = msg ? { kind, msg, sticky } : null;
  const el = document.getElementById("banner");
  if (!msg) { el.className = "banner hidden"; el.textContent = ""; return; }
  el.className = "banner " + kind;
  el.textContent = msg;
}

function formatMs(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return ms + " ms";
  return (ms / 1000).toFixed(1) + " s";
}
function formatRelativeTs(iso) {
  try {
    const dt = new Date(iso);
    const diff = (Date.now() - dt.getTime()) / 1000;
    if (diff < 60) return `${diff.toFixed(1)}s ago`;
    if (diff < 3600) return `${(diff / 60).toFixed(1)}m ago`;
    return dt.toLocaleTimeString();
  } catch { return iso; }
}

function chip(stateStr) {
  const map = {
    idle: { cls: "ok", text: "IDLE" },
    printing: { cls: "ok", text: "PRINTING" },
    error: { cls: "err", text: "ERROR" },
    disconnected: { cls: "warn", text: "DISCONNECTED" },
    connecting: { cls: "warn", text: "CONNECTING" },
  };
  const m = map[stateStr] || { cls: "", text: stateStr || "—" };
  return `<span class="chip ${m.cls}">${m.text}</span>`;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      const base64 = result.split(",")[1];   // strip data: URL prefix
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ---------- Items table ----------
function renderItems() {
  const body = document.getElementById("itemsBody");
  body.innerHTML = "";
  items.forEach((it, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input value="${it.product}" data-i="${i}" data-k="product"></td>
      <td><input type="number" value="${it.quantity}" min="0" data-i="${i}" data-k="quantity"></td>
      <td><input type="number" step="0.01" value="${it.reward}" min="0" data-i="${i}" data-k="reward"></td>
      <td><button class="secondary small" data-rm="${i}">×</button></td>
    `;
    body.appendChild(tr);
  });
  body.querySelectorAll("input").forEach(inp => {
    inp.addEventListener("input", e => {
      const i = +e.target.dataset.i, k = e.target.dataset.k;
      items[i][k] = k === "product" ? e.target.value : parseFloat(e.target.value) || 0;
    });
  });
  body.querySelectorAll("button[data-rm]").forEach(b => {
    b.addEventListener("click", () => { items.splice(+b.dataset.rm, 1); renderItems(); });
  });
}

document.getElementById("addItemBtn").addEventListener("click", () => {
  items.push({ product: "New", quantity: 0, reward: 0 });
  renderItems();
});

// ---------- Render status ----------
// Map the raw JSON value to the localized display string. Falls back
// to the raw value so unrecognised values (e.g. a new state in the
// future) are still visible, just untranslated.
function localizeVal(v, ...keys) {
  if (v === true) return t("val_yes");
  if (v === false) return t("val_no");
  if (v == null) return t("val_unknown");
  for (const k of keys) {
    const translated = t(k);
    if (translated && translated !== k) return translated;
  }
  return String(v);
}

function renderStatus() {
  const s = state.status;
  if (!s) return;
  document.getElementById("stateChip").outerHTML =
    '<span id="stateChip">' + chip(s.printer_state) + "</span>";

  document.getElementById("sMode").textContent = s.connection.mode ?? t("val_unknown");
  document.getElementById("sConnected").textContent =
    s.connection.connected ? t("val_yes") : t("val_no");
  document.getElementById("sReason").textContent = s.connection.disconnect_reason ?? t("val_unknown");
  document.getElementById("sPaper").textContent =
    localizeVal(s.device.paper, `val_paper_${s.device.paper}`);
  document.getElementById("sCover").textContent =
    localizeVal(s.device.cover, `val_cover_${s.device.cover}`);
  document.getElementById("sTemp").textContent =
    s.device.temperature_c != null ? s.device.temperature_c.toFixed(1) + " °C" : t("val_unknown");
  document.getElementById("sOverheat").textContent =
    s.device.overheated ? t("val_yes") : t("val_no");
  document.getElementById("sJam").textContent =
    s.device.jammed ? t("val_yes") : t("val_no");
  document.getElementById("sLastSeen").textContent = s.connection.last_seen_ts ?? t("val_unknown");

  document.getElementById("ljId").textContent = s.last_job.job_id ?? t("val_unknown");
  document.getElementById("ljOp").textContent = s.last_job.op ?? t("val_unknown");
  document.getElementById("ljStatus").textContent = s.last_job.status ?? t("val_unknown");
  document.getElementById("ljError").textContent = s.last_job.error_code ?? t("val_unknown");
  document.getElementById("ljTs").textContent = s.last_job.ts ?? t("val_unknown");

  // ----- ETA (moving average over last 10 successful prints, per type) -----
  if (s.eta) {
    const e = s.eta;
    const sampleWord = t("status_eta_samples") || "samples";
    const noSamples  = t("status_eta_no_samples") || "—  (no samples yet)";
    const textVal = e.text_ms != null
      ? `${e.text_ms} ms  ·  ${e.samples_text} ${sampleWord}`
      : noSamples;
    const imageVal = e.image_ms != null
      ? `${e.image_ms} ms  ·  ${e.samples_image} ${sampleWord}`
      : noSamples;
    document.getElementById("etaText").textContent = textVal;
    document.getElementById("etaImage").textContent = imageVal;
  }

  // ----- Auto-reconnect panel -----
  const rcHeader = document.getElementById("reconnectHeader");
  const rcDl = document.getElementById("reconnectDl");
  const rcChip = document.getElementById("reconnectChip");
  if (s.reconnect) {
    rcHeader.classList.remove("hidden");
    rcDl.classList.remove("hidden");
    const r = s.reconnect;
    const max = r.breaker_threshold;
    document.getElementById("rcAttempt").textContent =
      r.breaker_open
        ? `${r.failure_count}/${max} (breaker OPEN — manual /connect required)`
        : `${r.failure_count}/${max}`;
    // Track the countdown locally so it ticks visibly between polls
    if (r.breaker_open) {
      rcChip.className = "chip err";
      rcChip.textContent = "BREAKER OPEN";
      state.retryDeadline = null;
      document.getElementById("rcNext").textContent = "(stopped)";
    } else if (r.next_retry_in_ms != null) {
      rcChip.className = "chip warn";
      rcChip.textContent = "RETRYING";
      state.retryDeadline = Date.now() + r.next_retry_in_ms;
      document.getElementById("rcNext").textContent = formatMs(r.next_retry_in_ms);
    } else {
      rcChip.className = "chip muted";
      rcChip.textContent = "—";
      state.retryDeadline = null;
      document.getElementById("rcNext").textContent = "—";
    }
    document.getElementById("rcLastTs").textContent =
      r.last_attempt_ts ? formatRelativeTs(r.last_attempt_ts) : "—";
    document.getElementById("rcError").textContent = r.last_attempt_error ?? "—";
  } else {
    rcHeader.classList.add("hidden");
    rcDl.classList.add("hidden");
    state.retryDeadline = null;
  }

  if (s.last_job.status === "error" && s.last_job.job_id) {
    state.lastFailedJobId = s.last_job.job_id;
  }

  // Show error banner if printer is in ERROR.
  // NOTE: only auto-clear non-sticky banners. Sticky ones (rate-limit, validation
  // errors set from button handlers) stay until the next button action.
  if (s.printer_state === "error" && s.last_job.error_code) {
    setBanner("error", `Error: ${s.last_job.error_code}`);
  } else if (s.printer_state === "disconnected" && s.connection.disconnect_reason === "breaker_open") {
    setBanner("warn", "Circuit breaker open — call /connect to retry.");
  } else if (state.banner && state.banner.kind === "error"
             && !state.banner.sticky && s.printer_state === "idle") {
    setBanner(null, null);
  }
}

// ---------- Polling ----------
async function pollStatus() {
  try {
    const r = await api("/status");
    if (r.ok) { state.status = r.body; renderStatus(); }
  } catch (e) { /* ignore transient */ }
}
async function pollLogs() {
  try {
    const r = await api("/logs?limit=80");
    if (r.ok) {
      const lines = r.body.entries.map(e => {
        const lvl = (e.level || "info").toLowerCase();
        const msg = `${e.ts || ""}  ${lvl.toUpperCase().padEnd(7)} ${e.op || ""}  ${e.message || ""}`;
        return `<span class="level-${lvl}">${msg.replace(/[<>&]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]))}</span>`;
      }).reverse().join("\n");
      document.getElementById("logs").innerHTML = lines || "(no entries)";
    }
  } catch {}
}

setInterval(pollStatus, POLL_MS);
setInterval(pollLogs, POLL_MS * 2);

// Local countdown tick — interpolates between server polls so the
// "next retry in" value visibly ticks down each second.
setInterval(() => {
  if (state.retryDeadline == null) return;
  const remaining = Math.max(0, state.retryDeadline - Date.now());
  document.getElementById("rcNext").textContent = formatMs(remaining);
}, 250);

// ---------- Buttons ----------
// Toggle per-mode parameter rows when the mode dropdown changes.
// Each "params-<mode>" element (row, hint, discover button) is shown
// only when its mode matches.
function syncConnectParams() {
  const mode = document.getElementById("modeSelect").value;
  for (const el of document.querySelectorAll(".params-lan")) {
    el.hidden = (mode !== "lan");
  }
  for (const el of document.querySelectorAll(".params-usb")) {
    el.hidden = (mode !== "usb");
  }
  for (const el of document.querySelectorAll(".params-lan_direct")) {
    el.hidden = (mode !== "lan_direct");
  }
  if (mode === "usb") refreshUsbDevices();
  if (mode === "lan_direct") refreshLanDirectDevices();
}
document.getElementById("modeSelect").addEventListener("change", syncConnectParams);

// ----- LAN-direct device picker -----
//
// Calls GET /discover/cable (read-only, no connect) and populates the
// dropdown so the user can pick the cable-attached Cashino. The picked
// host/port travel in the Connect request body as mode=lan + overrides
// — that route already exists and behaves identically to the manual
// mode=lan flow downstream.
async function refreshLanDirectDevices() {
  const sel = document.getElementById("lanDirectSelect");
  sel.innerHTML = `<option value="">${t("conn_scanning")}</option>`;
  sel.disabled = true;
  const r = await api("/discover/cable", { method: "GET" });
  sel.disabled = false;
  if (!r.ok) {
    sel.innerHTML = `<option value="">${t("discover_failed")}: ${r.status}</option>`;
    return;
  }
  const cands = r.body.candidates || [];
  if (cands.length === 0) {
    const ifc = (r.body.interfaces || []).length;
    sel.innerHTML =
      `<option value="">— ${t("discover_no_cashino")} (${ifc} ${t("discover_interfaces")}) —</option>`;
    return;
  }
  sel.innerHTML = `<option value="">${t("discover_pick_device")}</option>` +
    cands.map((c) =>
      `<option value='${JSON.stringify({host:c.host,port:c.port})}'>` +
      `${c.host}:${c.port}` +
      (c.via_interface ? ` (${t("discover_via")} ${c.via_interface}` : "") +
      `, ${c.rtt_ms} ${t("discover_ms")})` +
      `</option>`
    ).join("");
}
document.getElementById("refreshLanDirectBtn")
        .addEventListener("click", refreshLanDirectDevices);

// ----- USB device picker -----
async function refreshUsbDevices() {
  const cdcSel = document.getElementById("usbCdcSelect");
  const libSel = document.getElementById("usbLibSelect");
  cdcSel.innerHTML = `<option value="">${t("conn_scanning")}</option>`;
  libSel.innerHTML = `<option value="">${t("conn_scanning")}</option>`;

  const r = await api("/usb/devices");
  if (!r.ok) {
    cdcSel.innerHTML = `<option value="">(${t("discover_failed")}: ${r.status})</option>`;
    libSel.innerHTML = `<option value="">(${t("discover_failed")})</option>`;
    return;
  }
  const { serial_ports, libusb_devices } = r.body;
  const pickLabel = t("discover_pick_device");
  const noneCdc = t("conn_usb_none_cdc") || "(no USB-CDC devices detected)";
  const noneLib = t("conn_usb_none_libusb") || "(no libusb devices visible)";

  // USB-CDC dropdown
  cdcSel.innerHTML = '';
  if (!serial_ports.length) {
    cdcSel.innerHTML = `<option value="">${noneCdc}</option>`;
  } else {
    cdcSel.appendChild(new Option(pickLabel, ""));
    for (const p of serial_ports) {
      const label = p.is_mock
        ? `${p.label}  →  ${p.path}`
        : `${p.label}${p.manufacturer ? "  ·  " + p.manufacturer : ""}  (${p.path})`;
      const opt = new Option(label, p.path);
      if (p.is_mock) opt.style.color = "#41c47a";
      cdcSel.appendChild(opt);
    }
  }

  // libusb dropdown
  libSel.innerHTML = '';
  if (!libusb_devices.length) {
    libSel.innerHTML = `<option value="">${noneLib}</option>`;
  } else {
    libSel.appendChild(new Option(pickLabel, ""));
    for (const d of libusb_devices) {
      const name = d.product || d.manufacturer || "USB device";
      const label = `${name}  (${d.vendor_id}:${d.product_id})`;
      libSel.appendChild(new Option(label, `${d.vendor_id}|${d.product_id}`));
    }
  }
}

document.getElementById("usbCdcSelect").addEventListener("change", e => {
  if (e.target.value) {
    document.getElementById("usbDevicePath").value = e.target.value;
  }
});
document.getElementById("usbLibSelect").addEventListener("change", e => {
  if (!e.target.value) return;
  const [vid, pid] = e.target.value.split("|");
  document.getElementById("usbVendor").value = vid;
  document.getElementById("usbProduct").value = pid;
  // If user picks libusb, blank the CDC path so factory falls back to libusb
  document.getElementById("usbDevicePath").value = "";
});
document.getElementById("refreshUsbBtn").addEventListener("click", refreshUsbDevices);

// ----- LAN auto-discover -----
//
// Calls /discover/lan, renders the result list, lets the user click
// "Use" on a row to fill the host + port fields. We deliberately do
// NOT auto-trigger /connect; the user still presses the Connect
// button so the intent is explicit.
async function discoverLan() {
  const btn = document.getElementById("lanDiscoverBtn");
  const statusEl = document.getElementById("lanDiscoverStatus");
  const list = document.getElementById("lanDiscoverResults");
  btn.disabled = true;
  statusEl.textContent = t("discover_scanning");
  list.classList.add("hidden");
  list.innerHTML = "";
  const r = await api("/discover/lan", { method: "GET" });
  btn.disabled = false;
  if (!r.ok) {
    statusEl.textContent = t("discover_failed") + ": " + (r.body?.detail || r.status);
    return;
  }
  const d = r.body;
  if (!d.local_ip) {
    statusEl.textContent = t("discover_local_ip_undet");
    return;
  }
  statusEl.textContent =
    `${t("discover_scanned")} ${d.scanned} ${t("discover_hosts_on")} ${d.subnet} — ` +
    `${d.tcp_open} ${t("discover_tcp_open")} ${d.candidates.length} ${t("discover_cashino_shaped")}`;
  if (d.candidates.length === 0) return;
  list.classList.remove("hidden");
  for (const c of d.candidates) {
    const li = document.createElement("li");
    li.innerHTML =
      `<code>${c.host}:${c.port}</code>` +
      ` <span class="hint">(${c.rtt_ms} ${t("discover_ms")} · status ${c.status_byte})</span>` +
      ` <button class="secondary small use-discovered" type="button">${t("conn_btn_connect_use") || "Use"}</button>`;
    li.querySelector(".use-discovered").addEventListener("click", () => {
      document.getElementById("lanHost").value = c.host;
      document.getElementById("lanPort").value = c.port;
      list.classList.add("hidden");
      statusEl.textContent = `${t("discover_selected")} ${c.host}:${c.port}. ${t("discover_press_connect")}`;
    });
    list.appendChild(li);
  }
}
document.getElementById("lanDiscoverBtn").addEventListener("click", discoverLan);

function buildConnectBody() {
  const mode = document.getElementById("modeSelect").value;
  const body = { mode };
  if (mode === "lan") {
    const host = document.getElementById("lanHost").value.trim();
    const port = parseInt(document.getElementById("lanPort").value, 10);
    if (host) body.lan_host = host;
    if (port) body.lan_port = port;
  } else if (mode === "usb") {
    const devPath = document.getElementById("usbDevicePath").value.trim();
    const vid = document.getElementById("usbVendor").value.trim();
    const pid = document.getElementById("usbProduct").value.trim();
    if (devPath) body.usb_device_path = devPath;
    if (vid) body.usb_vendor_id = vid;
    if (pid) body.usb_product_id = pid;
  } else if (mode === "lan_direct") {
    // The user pre-selected a device in the dropdown — translate it
    // into a manual LAN connect so the service uses the already-known
    // host. (Empty selection falls through with mode=lan_direct, and
    // the backend will auto-discover.)
    const raw = document.getElementById("lanDirectSelect").value;
    if (raw) {
      try {
        const picked = JSON.parse(raw);
        body.mode = "lan";
        body.lan_host = picked.host;
        body.lan_port = picked.port;
      } catch (e) {
        /* keep mode=lan_direct, backend will discover */
      }
    }
  }
  return body;
}

// Render a MULTIPLE_CANDIDATES result list (409 from lan_direct) into
// the existing discover-list UI. Clicking "Use" pivots the user to
// the manual lan mode pre-populated with the picked host.
function showLanDirectCandidates(candidates) {
  const list = document.getElementById("lanDiscoverResults");
  const statusEl = document.getElementById("lanDiscoverStatus");
  // Switch to manual LAN so the user can review the picked host
  // before pressing Connect again.
  const modeSel = document.getElementById("modeSelect");
  modeSel.value = "lan";
  syncConnectParams();
  list.classList.remove("hidden");
  list.innerHTML = "";
  statusEl.textContent = t("discover_multiple_pick");
  for (const c of candidates) {
    const li = document.createElement("li");
    li.innerHTML =
      `<code>${c.host}:${c.port}</code>` +
      ` <span class="hint">(${c.rtt_ms} ${t("discover_ms")} · ${t("discover_via")} ${c.via_interface || "?"})</span>` +
      ` <button class="secondary small use-discovered" type="button">Use</button>`;
    li.querySelector(".use-discovered").addEventListener("click", () => {
      document.getElementById("lanHost").value = c.host;
      document.getElementById("lanPort").value = c.port;
      list.classList.add("hidden");
      statusEl.textContent = `${t("discover_selected")} ${c.host}:${c.port}. ${t("discover_press_connect")}`;
    });
    list.appendChild(li);
  }
}

document.getElementById("connectBtn").addEventListener("click", async () => {
  const body = buildConnectBody();
  const r = await api("/connect", { method: "POST", body: JSON.stringify(body) });

  if (r.ok) {
    let target;
    if (body.mode === "lan_direct") {
      const res = r.body.resolved || {};
      target = `${res.host}:${res.port}${res.via_interface ? ` via ${res.via_interface}` : ""}`;
    } else if (body.mode === "lan") {
      target = body.lan_host ? `${body.lan_host}:${body.lan_port || 9100}` : "lan (env)";
    } else {
      target = body.usb_vendor_id ? `${body.usb_vendor_id}:${body.usb_product_id || "?"}` : "usb (env)";
    }
    setBanner("ok", `${t("banner_connected_via")} ${body.mode} → ${target}`);
  } else if (body.mode === "lan_direct" && r.status === 409
             && Array.isArray(r.body.candidates)) {
    // MULTIPLE_CANDIDATES — let the user pick from a list.
    const lang = localStorage.getItem("ui_lang") || "en";
    const serverMsg = r.body[lang === "tr" ? "message_tr" : "message_en"]
                      || r.body.message_en;
    setBanner("warn", serverMsg || t("banner_multiple_devices"),
              { sticky: true });
    showLanDirectCandidates(r.body.candidates);
  } else {
    const lang = localStorage.getItem("ui_lang") || "en";
    const serverMsg = r.body[lang === "tr" ? "message_tr" : "message_en"]
                      || r.body.message_en;
    const msg = serverMsg || r.body.detail?.detail
              || r.body.detail || JSON.stringify(r.body);
    setBanner("error", `${t("banner_connect_failed")} (${r.status}): ${msg}`,
              { sticky: true });
  }
  pollStatus();
});

document.getElementById("disconnectBtn").addEventListener("click", async () => {
  await api("/disconnect", { method: "POST" });
  setBanner("warn", t("banner_disconnected"));
  pollStatus();
});

function buildPrintBody(image_base64 = null) {
  return {
    machine_id: document.getElementById("machineId").value,
    items,
    total_reward: parseFloat(document.getElementById("totalReward").value) || 0,
    timestamp: new Date().toISOString(),
    qr_content: document.getElementById("qrContent").value || null,
    lang: document.getElementById("lang").value,
    title: document.getElementById("title").value || null,
    ...(image_base64 ? { image_base64 } : {}),
  };
}

// Render an error from any POST that may return either {detail: {...}} (our
// custom error_handlers payload) or {detail: "..."} (FastAPI's default).
// `failedLabel` is the localized "Print failed" / "Reprint failed" prefix.
function describePrintError(r, failedLabel) {
  const d = r.body?.detail;
  if (d && typeof d === "object") {
    if (d.error_code === "RATE_LIMITED") {
      return `${t("banner_rate_limit")} ${d.retry_after_s ?? "?"}${t("banner_seconds")}.  ` +
             `(${d.detail ?? ""})`;
    }
    const code = d.error_code || r.body.error_code || "ERROR";
    const lang = localStorage.getItem("ui_lang") || "en";
    const localizedKey = lang === "tr" ? "message_tr" : "message_en";
    const msg = d[localizedKey] || d.message_en || d.detail || JSON.stringify(d);
    return `${failedLabel} (${r.status} ${code}): ${msg}`;
  }
  return `${failedLabel} (${r.status}): ${d ?? JSON.stringify(r.body)}`;
}

document.getElementById("printTextBtn").addEventListener("click", async () => {
  const r = await api("/print/text", { method: "POST", body: JSON.stringify(buildPrintBody()) });
  if (r.ok) setBanner("ok", `${t("banner_printed")}: ${r.body.job_id} (${r.body.duration_ms}ms)`);
  else setBanner("error", describePrintError(r, t("banner_print_failed")), { sticky: true });
  pollStatus();
  refreshPreview(r.ok ? r.body.job_id : null);
  refreshFailedJobs();
});

document.getElementById("printImageBtn").addEventListener("click", async () => {
  const file = document.getElementById("imageFile").files[0];
  if (!file) { setBanner("warn", t("banner_pick_image"), { sticky: true }); return; }
  const b64 = await fileToBase64(file);
  // Stash the full data URL so the preview can show the actual upload.
  state.lastUploadedDataUrl = `data:${file.type || "image/png"};base64,${b64}`;
  const r = await api("/print/image", { method: "POST", body: JSON.stringify(buildPrintBody(b64)) });
  if (r.ok) setBanner("ok", `${t("banner_printed_image")}: ${r.body.job_id} (${r.body.duration_ms}ms)`);
  else setBanner("error", describePrintError(r, t("banner_print_image_failed")), { sticky: true });
  pollStatus();
  refreshPreview(r.ok ? r.body.job_id : null);
  refreshFailedJobs();
});

// ----- Recent failed-jobs picker -----
//
// Fetches GET /jobs/failed?limit=5 and populates the dropdown so the
// user can pick a specific UUID to retry. The picker fills the UUID
// input field on selection; the Reprint-by-UUID button reads from
// there. Auto-refreshes after every print attempt so a fresh failure
// shows up immediately.
async function refreshFailedJobs() {
  const sel = document.getElementById("failedJobSelect");
  sel.innerHTML = `<option value="">${t("reprint_recent_loading")}</option>`;
  const r = await api("/jobs/failed?limit=5", { method: "GET" });
  if (!r.ok) {
    sel.innerHTML = `<option value="">${t("reprint_recent_failed")}</option>`;
    return;
  }
  const jobs = r.body.jobs || [];
  if (jobs.length === 0) {
    sel.innerHTML = `<option value="">${t("reprint_recent_empty")}</option>`;
    return;
  }
  // Compose option label: "8580b2d9 · PAPER_OUT · 22:53"
  sel.innerHTML = `<option value="">${t("discover_pick_device")}</option>` +
    jobs.map((j) => {
      const short = j.job_id.slice(0, 8);
      const when  = j.ts ? new Date(j.ts).toLocaleTimeString() : "";
      return `<option value="${j.job_id}">${short} · ${j.error_code || "?"} · ${when}</option>`;
    }).join("");
}
document.getElementById("refreshFailedBtn")
        .addEventListener("click", refreshFailedJobs);
document.getElementById("failedJobSelect")
        .addEventListener("change", (e) => {
  if (e.target.value) document.getElementById("reprintUuidInput").value = e.target.value;
});

// ----- Reprint by UUID -----
// Caller-supplied job_id path. Used when the user has a specific failure
// in mind — either pasted from logs or picked from the dropdown above.
// If the supplied UUID belongs to a job that already succeeded, the
// service returns status="already_done" instead of duplicating the
// receipt; the UI surfaces that as a sticky info banner so the user
// can see it was *intentional*, not a silent no-op.
document.getElementById("reprintByUuidBtn").addEventListener("click", async () => {
  const id = document.getElementById("reprintUuidInput").value.trim();
  if (!id) {
    setBanner("warn", t("reprint_need_uuid"), { sticky: true });
    return;
  }
  const r = await api("/reprint", {
    method: "POST", body: JSON.stringify({ job_id: id }),
  });
  if (r.ok && r.body.status === "already_done") {
    const dur = r.body.duration_ms != null
              ? ` (${r.body.duration_ms}ms)`
              : "";
    setBanner("ok", `${t("banner_already_done")} ${id.slice(0, 8)}${dur}`,
              { sticky: true });
  } else if (r.ok) {
    setBanner("ok", `${t("banner_reprinted_from")} ${id.slice(0, 8)} → ${r.body.job_id}`);
  } else {
    setBanner("error", describePrintError(r, t("banner_reprint_failed")), { sticky: true });
  }
  pollStatus();
  refreshPreview(r.ok && r.body.job_id ? r.body.job_id : null);
  refreshFailedJobs();
});

// "Reprint last failed" — server-side lookup, no UUID round-trip.
// The button is enabled whenever the user feels like firing it; the
// backend tells us "nothing to reprint" via NO_FAILED_JOB (404) if the
// queue is clean. That's friendlier than the UI maintaining its own
// "do we know about a failed job?" flag.
document.getElementById("reprintBtn").addEventListener("click", async () => {
  const r = await api("/reprint/last-failed", { method: "POST" });
  if (r.ok) {
    setBanner("ok", `${t("banner_reprinted_from")} ${t("banner_last_failed")} → ${r.body.job_id}`);
  } else if (r.status === 404 && r.body?.detail?.error_code === "NO_FAILED_JOB") {
    setBanner("warn", t("banner_no_job_to_reprint"), { sticky: true });
  } else {
    setBanner("error", describePrintError(r, t("banner_reprint_failed")), { sticky: true });
  }
  pollStatus();
  refreshPreview(r.ok ? r.body.job_id : null);
  refreshFailedJobs();
});

document.getElementById("exportCsvBtn").addEventListener("click", () => {
  window.location.href = "/logs/export?format=csv";
});

// ---------- Receipt preview ----------
function escapeHtml(s) {
  return s.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// Render the bracketed mock_preview text as a thermal-paper-styled HTML block.
// Applies alignment, bold, double-size; replaces QR/cut with visual blocks.
function renderReceipt(raw) {
  // Tokenize into either [CMD] or text.
  const tokens = [];
  let i = 0;
  while (i < raw.length) {
    if (raw[i] === "[") {
      const end = raw.indexOf("]", i);
      if (end === -1) { tokens.push({ type: "text", value: raw.slice(i) }); break; }
      tokens.push({ type: "cmd", value: raw.slice(i + 1, end) });
      i = end + 1;
    } else {
      const nxt = raw.indexOf("[", i);
      const end = nxt === -1 ? raw.length : nxt;
      tokens.push({ type: "text", value: raw.slice(i, end) });
      i = end;
    }
  }

  let align = "left", bold = false, size = "normal", font = "a";
  const out = [];
  let buf = "";
  let imageIndex = 0;        // 0 → first image (usually logo), 1 → upload, …

  function flush() {
    // Each flush emits a div for the accumulated text under current style.
    const classes = [`align-${align}`];
    if (bold) classes.push("bold");
    if (size === "double") classes.push("size-double");
    if (font === "b") classes.push("font-b");
    // Split on \n so each line is its own div (better wrapping/centering).
    const lines = buf.split("\n");
    for (let k = 0; k < lines.length; k++) {
      const text = lines[k];
      // Lines that are nothing but horizontal box-drawing become a real <hr>.
      const trimmed = text.trim();
      if (trimmed && /^─+$/.test(trimmed)) {
        out.push('<hr class="row">');
      } else {
        out.push(`<div class="ln ${classes.join(" ")}">${escapeHtml(text) || "&nbsp;"}</div>`);
      }
    }
    buf = "";
  }

  for (const tok of tokens) {
    if (tok.type === "text") {
      buf += tok.value;
      continue;
    }
    const cmd = tok.value;
    // Reset / no-op commands: hidden completely
    if (cmd === "INIT" || cmd.startsWith("CODEPAGE")) continue;
    if (cmd === "ALIGN LEFT")   { flush(); align = "left";   continue; }
    if (cmd === "ALIGN CENTER") { flush(); align = "center"; continue; }
    if (cmd === "ALIGN RIGHT")  { flush(); align = "right";  continue; }
    if (cmd === "BOLD ON")      { flush(); bold = true;      continue; }
    if (cmd === "BOLD OFF")     { flush(); bold = false;     continue; }
    if (cmd === "SIZE DOUBLE")  { flush(); size = "double";  continue; }
    if (cmd === "SIZE NORMAL")  { flush(); size = "normal";  continue; }
    if (cmd === "FONT B")       { flush(); font = "b";        continue; }
    if (cmd === "FONT A")       { flush(); font = "a";        continue; }
    if (cmd === "CUT") {
      flush();
      out.push('<hr class="cut">');
      continue;
    }
    if (cmd.startsWith("QR DATA:")) {
      flush();
      const data = cmd.slice("QR DATA:".length).trim();
      const src = "/preview/qr?box=8&data=" + encodeURIComponent(data);
      out.push(
        `<div class="align-center"><img class="receipt-qr" src="${src}" alt="QR"></div>`,
      );
      continue;
    }
    if (cmd === "QR PRINT" || cmd === "QR CMD") continue;
    // Image placeholder — first image (when logo exists) is the header
    // logo; subsequent images are user uploads.
    if (cmd.startsWith("IMAGE ")) {
      flush();
      const isLogo = (imageIndex === 0) && state.logoAvailable;
      let src;
      let cls = "receipt-logo";
      if (isLogo) {
        src = "/assets/logo";
      } else if (state.lastUploadedDataUrl) {
        src = state.lastUploadedDataUrl;
        cls = "receipt-upload";
      } else {
        // No upload tracked (we're viewing a reprint of an older image job)
        src = "/assets/logo";
      }
      out.push(`<div class="align-center"><img class="${cls}" src="${src}" alt="receipt image"></div>`);
      imageIndex += 1;
      continue;
    }
    // Unknown command — drop quietly
  }
  flush();
  return out.join("");
}

function formatReceiptRaw(raw) {
  return escapeHtml(raw).replace(/\[([^\]]+)\]/g, '<span class="ctrl">[$1]</span>');
}

let previewShowRaw = false;
let lastPreviewText = "";

function paintPreview() {
  const rendered = document.getElementById("receiptRendered");
  const rawEl = document.getElementById("receiptRaw");
  const paperWrap = document.getElementById("receiptPaper");
  if (!lastPreviewText) {
    rendered.textContent = "(no receipt yet)";
    rawEl.textContent = "(no receipt yet)";
    return;
  }
  rendered.innerHTML = renderReceipt(lastPreviewText);
  rawEl.innerHTML = formatReceiptRaw(lastPreviewText);
  paperWrap.classList.toggle("hidden", previewShowRaw);
  rawEl.classList.toggle("hidden", !previewShowRaw);
  document.getElementById("toggleRawBtn").textContent = previewShowRaw ? "Rendered" : "Raw";
}

async function refreshPreview(jobId = null) {
  const url = jobId ? `/mock/preview?job_id=${encodeURIComponent(jobId)}` : "/mock/preview";
  const r = await api(url);
  const meta = document.getElementById("previewMeta");
  if (r.ok) {
    lastPreviewText = r.body.preview;
    meta.textContent = `${r.body.bytes_len} bytes`;
  } else if (r.status === 404) {
    lastPreviewText = "";
    meta.textContent = "—";
  } else {
    lastPreviewText = `preview error: ${r.status}`;
    meta.textContent = "err";
  }
  paintPreview();
}
document.getElementById("refreshPreviewBtn").addEventListener("click", () => refreshPreview());
document.getElementById("toggleRawBtn").addEventListener("click", () => {
  previewShowRaw = !previewShowRaw;
  paintPreview();
});

// ---------- Dev Tools panel ----------
async function probeDevMode() {
  const r = await api("/health");
  if (r.ok && r.body.dev_mode) {
    document.getElementById("devToolsCard").hidden = false;
    refreshDeviceStatus();
    setInterval(refreshDeviceStatus, 4000);
  }
}

async function refreshDeviceStatus() {
  const r = await api("/mock/device_status");
  const chip = document.getElementById("mockDeviceChip");
  const banner = document.getElementById("mockDeviceOfflineBanner");
  const card = document.getElementById("devToolsCard");
  const hint = document.getElementById("mockDeviceHint");
  if (!r.ok) {
    chip.className = "chip muted"; chip.textContent = `${t("dev_mock_chip_base")}: ?`;
    return;
  }
  const { mode, reachable, control_url, label } = r.body;
  if (mode === "in_process") {
    chip.className = "chip muted";
    chip.textContent = t("dev_mock_chip_in_process");
    banner.classList.add("hidden");
    card.classList.remove("offline");
    hint.textContent = t("dev_mock_hint_in_process");
  } else if (mode === "external" && reachable) {
    chip.className = "chip ok";
    chip.textContent = `${t("dev_mock_chip_base")}: ${t("dev_mock_online")}`;
    banner.classList.add("hidden");
    card.classList.remove("offline");
    hint.textContent = t("dev_mock_hint_external_online").replace("{url}", control_url);
  } else {
    chip.className = "chip err";
    chip.textContent = `${t("dev_mock_chip_base")}: ${t("dev_mock_offline")}`;
    banner.classList.remove("hidden");
    card.classList.add("offline");
    hint.textContent = t("dev_mock_hint_external_offline").replace("{url}", control_url);
  }
}

document.querySelectorAll("#devToolsCard button[data-action]").forEach(btn => {
  btn.addEventListener("click", async () => {
    const action = btn.dataset.action;
    let body = {};
    if (action === "set_paper") body = { lines: parseInt(document.getElementById("devPaper").value, 10) };
    if (action === "set_temperature") body = { celsius: parseFloat(document.getElementById("devTemp").value) };
    if (action === "set_cover") body = { open: document.getElementById("devCover").value === "true" };
    if (action === "set_jammed") body = { jammed: document.getElementById("devJam").value === "true" };
    if (action === "trigger_comm_error") body = { active: document.getElementById("devComm").value === "true" };
    const r = await api("/mock/" + action, { method: "POST", body: JSON.stringify(body) });
    setBanner(r.ok ? "ok" : "error", `mock ${action}: ${r.status}`);
    pollStatus();
  });
});

document.getElementById("runScenarioBtn").addEventListener("click", async () => {
  const scenario = document.getElementById("devScenario").value;
  const autoRecMs = parseInt(document.getElementById("devAutoRecover").value, 10);
  const body = { scenario, ...(autoRecMs ? { auto_recover_after_ms: autoRecMs } : {}) };
  const r = await api("/mock/run_scenario", { method: "POST", body: JSON.stringify(body) });
  setBanner(r.ok ? "ok" : "error", `scenario ${scenario}: ${r.status}`);
  pollStatus();
});

// Detect whether the server has a header logo configured. Drives whether
// the first [IMAGE] in a preview is treated as the logo or as a user upload.
// Use a real <img> load instead of HEAD — works regardless of how the
// FastAPI route handles HEAD requests.
function probeLogo() {
  const img = new Image();
  img.onload  = () => { state.logoAvailable = true;  };
  img.onerror = () => { state.logoAvailable = false; };
  img.src = "/assets/logo?_probe=" + Date.now();
}

// ---------- Init ----------
//
// Language: apply persisted choice (or browser default falling back to EN)
// BEFORE the first poll/render so the first paint is in the right language.
(() => {
  const saved = localStorage.getItem("ui_lang");
  const browser = (navigator.language || "en").slice(0, 2).toLowerCase();
  const lang = saved || (I18N[browser] ? browser : "en");
  const picker = document.getElementById("langPicker");
  if (picker) {
    picker.value = lang;
    picker.addEventListener("change", (e) => applyLanguage(e.target.value));
  }
  applyLanguage(lang);
})();

renderItems();
probeDevMode();
probeLogo();
syncConnectParams();
pollStatus();
pollLogs();
refreshPreview();
refreshFailedJobs();
