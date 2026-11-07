import sqlite3
import streamlit as st

# Veritabanı ve Tablo Kurulumu
def veritabani_kur():
    baglanti = sqlite3.connect("haber_platformu.db", check_same_thread=False)
    cursor = baglanti.cursor()
    
    # Kullanıcılar Tablosu (Şifre sütunu eklendi)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS kullanicilar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kadi TEXT UNIQUE,
            sifre TEXT,
            rol TEXT,
            guven_puani INTEGER DEFAULT 50,
            kidem_puani INTEGER DEFAULT 10
        )
    ''')
    
    # Haberler Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS haberler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            baslik TEXT,
            icerik TEXT,
            yazar TEXT,
            durum TEXT DEFAULT 'beklemede',
            sikayet_sayisi INTEGER DEFAULT 0
        )
    ''')
    baglanti.commit()
    return baglanti

baglanti = veritabani_kur()
cursor = baglanti.cursor()

# Senaryo için ana moderatör hesabını otomatik oluşturalım (Senin hesabın)
# Kullanıcı Adı: yonetici | Şifre: 12345
cursor.execute("SELECT COUNT(*) FROM kullanicilar WHERE kadi = 'yonetici'")
if cursor.fetchone()[0] == 0:
    cursor.execute("INSERT INTO kullanicilar (kadi, sifre, rol) VALUES ('yonetici', '12345', 'moderator')")
    baglanti.commit()

# --- STREAMLIT ARAYÜZÜ ---
st.set_page_config(page_title="Doğru Haber Platformu", page_icon="📰", layout="wide")

# Oturum Yönetimi (Session State)
if "giris_yapildi" not in st.session_state:
    st.session_state.giris_yapildi = False
if "kullanici_adi" not in st.session_state:
    st.session_state.kullanici_adi = ""
if "rol" not in st.session_state:
    st.session_state.rol = ""

st.title("📰 Yalan Haber Önleme ve Topluluk Doğrulama Platformu")
st.write("Topluluk tabanlı doğrulama, güven puanları ve moderatör denetimiyle güvenilir haber ağı.")

# --- DÜZENLENEN MENÜ SİSTEMİ ---
st.sidebar.title("📌 Navigasyon Menüsü")

if not st.session_state.giris_yapildi:
    menu = st.sidebar.selectbox("Gitmek İstediğiniz Yer", ["Ana Sayfa / Akış", "Giriş Yap", "Kayıt Ol"])
else:
    st.sidebar.success(f"Aktif Hesap: {st.session_state.kullanici_adi} ({st.session_state.rol.upper()})")
    
    secenekler = ["Ana Sayfa / Akış", "Haber Paylaş", "Kullanıcı Puanları"]
    if st.session_state.rol == "moderator":
        secenekler.append("🛡️ Moderatör Paneli")
    secenekler.append("🚪 Çıkış Yap")
    
    menu = st.sidebar.selectbox("Gitmek İstediğiniz Yer", secenekler)

    if menu == "🚪 Çıkış Yap":
        st.session_state.giris_yapildi = False
        st.session_state.kullanici_adi = ""
        st.session_state.rol = ""
        st.rerun()

# 1. GİRİŞ YAP SAYFASI
if menu == "Giriş Yap":
    st.header("🔑 Hesabınıza Giriş Yapın")
    kadi = st.text_input("Kullanıcı Adı")
    sifre = st.text_input("Şifre", type="password")
    
    if st.button("Giriş Yap"):
        cursor.execute("SELECT rol FROM kullanicilar WHERE kadi = ? AND sifre = ?", (kadi, sifre))
        sonuc = cursor.fetchone()
        if sonuc:
            st.session_state.giris_yapildi = True
            st.session_state.kullanici_adi = kadi
            st.session_state.rol = sonuc[0]
            st.success(f"Hoş geldin {kadi}!")
            st.rerun()
        else:
            st.error("Kullanıcı adı veya şifre hatalı!")

# 2. KAYIT OL SAYFASI
elif menu == "Kayıt Ol":
    st.header("📝 Yeni Hesap Oluştur")
    yeni_kadi = st.text_input("Kullanıcı Adı Belirleyin")
    yeni_sifre = st.text_input("Şifre Belirleyin", type="password")
    
    # Senin kendi moderatörlüğün için gizli kod (İstersen arkadaşlarına da bu kodu verip moderatör yapabilirsin)
    moderator_kodu = st.text_input("Moderatör Gizli Kodu (Normal üyeler boş bıraksın)", type="password")
    
    if st.button("Kayıt Ol"):
        if yeni_kadi and yeni_sifre:
            rol = "normal"
            if moderator_kodu == "hackathon2026": # Gizli moderatör anahtarın
                rol = "moderator"
            
            try:
                cursor.execute("INSERT INTO kullanicilar (kadi, sifre, rol) VALUES (?, ?, ?)", (yeni_kadi, yeni_sifre, rol))
                baglanti.commit()
                st.success("Hesabınız başarıyla oluşturuldu! Şimdi sol menüden 'Giriş Yap' seçeneğine gidebilirsiniz.")
            except sqlite3.IntegrityError:
                st.error("Bu kullanıcı adı zaten alınmış. Lütfen başka bir tane deneyin.")
        else:
            st.error("Lütfen tüm alanları doldurun.")

# 3. ANA SAYFA / AKIŞ
elif menu == "Ana Sayfa / Akış":
    st.header("📢 Yayınlanan Güvenilir Haberler")
    
    cursor.execute("SELECT id, baslik, icerik, yazar, sikayet_sayisi FROM haberler WHERE durum = 'onaylandi'")
    haberler = cursor.fetchall()
    
    if not haberler:
        st.info("Henüz onaylanmış bir haber bulunmuyor. Moderatör onayından sonra burada görünecektir.")
    
    for h in haberler:
        haber_id, baslik, icerik, yazar, sikayet = h
        with st.expander(f"📌 {baslik} (Yazar: {yazar})"):
            st.write(icerik)
            st.caption(f"Şikayet Sayısı: {sikayet}")
            
            if st.session_state.giris_yapildi:
                if st.button(f"🚨 Bu Haberi Şikayet Et (ID: {haber_id})", key=f"sikayet_{haber_id}"):
                    cursor.execute("UPDATE haberler SET sikayet_sayisi = sikayet_sayisi + 1 WHERE id = ?", (haber_id,))
                    baglanti.commit()
                    st.warning("Haber şikayet edildi ve incelemeye alındı!")
                    st.rerun()
            else:
                st.info("Haberi şikayet etmek için lütfen giriş yapın.")

# 4. HABER PAYLAŞ
elif menu == "Haber Paylaş":
    st.header("✍️ Yeni Haber Paylaş")
    st.info(f"Şu an **{st.session_state.kullanici_adi}** olarak haber paylaşıyorsunuz.")
    
    baslik = st.text_input("Haber Başlığı")
    icerik = st.text_area("Haber İçeriği")
    
    if st.button("Haberi Gönder"):
        if baslik and icerik:
            cursor.execute("INSERT INTO haberler (baslik, icerik, yazar) VALUES (?, ?, ?)", (baslik, icerik, st.session_state.kullanici_adi))
            baglanti.commit()
            st.success("Haber başarıyla gönderildi! Moderatör onay kuyruğuna eklendi.")
        else:
            st.error("Lütfen tüm alanları doldurun.")

# 5. MODERATÖR PANELI (Sadece Sen / Moderatörler Görebilir)
elif menu == "🛡️ Moderatör Paneli":
    st.header("🛡️ Moderatör Onay Kuyruğu")
    
    cursor.execute("SELECT id, baslik, icerik, yazar, sikayet_sayisi FROM haberler WHERE durum = 'beklemede' OR sikayet_sayisi >= 3")
    bekleyenler = cursor.fetchall()
    
    if not bekleyenler:
        st.info("İnceleme bekleyen veya şikayet edilen haber bulunmuyor.")
    
    for b in bekleyenler:
        h_id, baslik, icerik, yazar, sikayet = b
        with st.container():
            st.subheader(f"İnceleme ID: {h_id} - {baslik}")
            st.write(f"**Yazar:** {yazar} | **Şikayet:** {sikayet}")
            st.write(icerik)
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button(f"✅ Onayla (ID: {h_id})", key=f"onay_{h_id}"):
                    cursor.execute("UPDATE haberler SET durum = 'onaylandi' WHERE id = ?", (h_id,))
                    cursor.execute("UPDATE kullanicilar SET guven_puani = guven_puani + 5, kidem_puani = kidem_puani + 2 WHERE kadi = ?", (yazar,))
                    baglanti.commit()
                    st.success("Haber onaylandı ve yayınlandı!")
                    st.rerun()
            with col2:
                if st.button(f"❌ Reddet / Yalan (ID: {h_id})", key=f"red_{h_id}"):
                    cursor.execute("UPDATE haberler SET durum = 'reddedildi' WHERE id = ?", (h_id,))
                    cursor.execute("UPDATE kullanicilar SET guven_puani = guven_puani - 10 WHERE kadi = ?", (yazar,))
                    baglanti.commit()
                    st.error("Haber reddedildi (yalan içerik olarak işaretlendi).")
                    st.rerun()
            st.divider()

# 6. KULLANICI PUANLARI
elif menu == "Kullanıcı Puanları":
    st.header("🏆 Kullanıcı Güven ve Kıdem Sıralaması")
    
    cursor.execute("SELECT kadi, rol, guven_puani, kidem_puani FROM kullanicilar ORDER BY guven_puani DESC")
    kullanicilar = cursor.fetchall()
    
    st.table([
        {"Kullanıcı Adı": k[0], "Rol": k[1], "Güven Puanı": k[2], "Kıdem Puanı": k[3]} 
        for k in kullanicilar
    ])
