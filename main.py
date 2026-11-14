"""Yalan haber önleme ve topluluk doğrulama platformu (Streamlit)."""

import sqlite3

import streamlit as st

VERITABANI_DOSYASI = "haber_platformu.db"

ROL_NORMAL = "normal"
ROL_MODERATOR = "moderator"

DURUM_BEKLEMEDE = "beklemede"
DURUM_ONAYLANDI = "onaylandi"
DURUM_REDDEDILDI = "reddedildi"

# Bu üç değer kaynak kodda açık duruyor; yayına almadan önce
# st.secrets veya ortam değişkenine taşınmalı.
YONETICI_KULLANICI_ADI = "yonetici"
YONETICI_SIFRESI = "12345"
MODERATOR_KAYIT_KODU = "hackathon2026"

BASLANGIC_GUVEN_PUANI = 50
BASLANGIC_KIDEM_PUANI = 10
ONAY_GUVEN_DEGISIMI = 5
ONAY_KIDEM_DEGISIMI = 2
RED_GUVEN_DEGISIMI = -10
INCELEME_SIKAYET_ESIGI = 3

MENU_ANA_SAYFA = "Ana Sayfa / Akış"
MENU_GIRIS = "Giriş Yap"
MENU_KAYIT = "Kayıt Ol"
MENU_HABER_PAYLASIM = "Haber Paylaş"
MENU_PUANLAR = "Kullanıcı Puanları"
MENU_MODERATOR = "🛡️ Moderatör Paneli"
MENU_CIKIS = "🚪 Çıkış Yap"

EKSIK_ALAN_MESAJI = "Lütfen tüm alanları doldurun."
HABER_SUTUNLARI = "id, baslik, icerik, yazar, sikayet_sayisi"


@st.cache_resource(show_spinner=False)
def veritabani_baglantisi_ac():
    # Streamlit scripti her etkileşimde baştan çalıştırır; önbellek
    # olmasaydı bağlantı her tıklamada yeniden açılırdı.
    # check_same_thread=False: oturumlar farklı thread'lerde çalışabilir.
    baglanti = sqlite3.connect(VERITABANI_DOSYASI, check_same_thread=False)

    with baglanti:
        baglanti.execute(f"""
            CREATE TABLE IF NOT EXISTS kullanicilar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kadi TEXT UNIQUE,
                sifre TEXT,
                rol TEXT,
                guven_puani INTEGER DEFAULT {BASLANGIC_GUVEN_PUANI},
                kidem_puani INTEGER DEFAULT {BASLANGIC_KIDEM_PUANI}
            )
        """)
        baglanti.execute(f"""
            CREATE TABLE IF NOT EXISTS haberler (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                baslik TEXT,
                icerik TEXT,
                yazar TEXT,
                durum TEXT DEFAULT '{DURUM_BEKLEMEDE}',
                sikayet_sayisi INTEGER DEFAULT 0
            )
        """)
        # kadi UNIQUE olduğu için yönetici varsa ekleme sessizce atlanır.
        baglanti.execute(
            "INSERT OR IGNORE INTO kullanicilar (kadi, sifre, rol) "
            "VALUES (?, ?, ?)",
            (YONETICI_KULLANICI_ADI, YONETICI_SIFRESI, ROL_MODERATOR),
        )

    return baglanti


def sorgu_calistir(sql, parametreler=()):
    baglanti = veritabani_baglantisi_ac()
    return baglanti.execute(sql, parametreler).fetchall()


def komut_calistir(sql, parametreler=()):
    # "with" bloğu başarıda commit, hatada rollback yapar.
    baglanti = veritabani_baglantisi_ac()
    with baglanti:
        baglanti.execute(sql, parametreler)


def haberi_degerlendir(
    haber_id, yazar, yeni_durum, guven_degisimi, kidem_degisimi=0
):
    # İki güncelleme birlikte yapılmalı; biri başarısız olursa puanlar
    # ile haber durumu birbirinden kopmasın.
    baglanti = veritabani_baglantisi_ac()
    with baglanti:
        baglanti.execute(
            "UPDATE haberler SET durum = ? WHERE id = ?",
            (yeni_durum, haber_id),
        )
        baglanti.execute(
            "UPDATE kullanicilar "
            "SET guven_puani = guven_puani + ?, "
            "kidem_puani = kidem_puani + ? "
            "WHERE kadi = ?",
            (guven_degisimi, kidem_degisimi, yazar),
        )


def oturumu_hazirla():
    st.session_state.setdefault("giris_yapildi", False)
    st.session_state.setdefault("kullanici_adi", "")
    st.session_state.setdefault("rol", "")


def oturumu_ac(kullanici_adi, rol):
    st.session_state.giris_yapildi = True
    st.session_state.kullanici_adi = kullanici_adi
    st.session_state.rol = rol
    # Menü yeni oturum durumuna göre yeniden çizilmeli.
    st.rerun()


def oturumu_kapat():
    st.session_state.giris_yapildi = False
    st.session_state.kullanici_adi = ""
    st.session_state.rol = ""
    st.rerun()


def menu_secenekleri_olustur():
    if not st.session_state.giris_yapildi:
        return [MENU_ANA_SAYFA, MENU_GIRIS, MENU_KAYIT]

    secenekler = [MENU_ANA_SAYFA, MENU_HABER_PAYLASIM, MENU_PUANLAR]
    if st.session_state.rol == ROL_MODERATOR:
        secenekler.append(MENU_MODERATOR)
    secenekler.append(MENU_CIKIS)
    return secenekler


def kenar_cubugunu_goster():
    st.sidebar.title("📌 Navigasyon Menüsü")

    if st.session_state.giris_yapildi:
        st.sidebar.success(
            f"Aktif Hesap: {st.session_state.kullanici_adi} "
            f"({st.session_state.rol.upper()})"
        )

    return st.sidebar.selectbox(
        "Gitmek İstediğiniz Yer", menu_secenekleri_olustur()
    )


def giris_sayfasi():
    st.header("🔑 Hesabınıza Giriş Yapın")
    kullanici_adi = st.text_input("Kullanıcı Adı")
    sifre = st.text_input("Şifre", type="password")

    if not st.button("Giriş Yap"):
        return

    sonuc = sorgu_calistir(
        "SELECT rol FROM kullanicilar WHERE kadi = ? AND sifre = ?",
        (kullanici_adi, sifre),
    )
    if not sonuc:
        st.error("Kullanıcı adı veya şifre hatalı!")
        return

    oturumu_ac(kullanici_adi, sonuc[0][0])


def kayit_sayfasi():
    st.header("📝 Yeni Hesap Oluştur")
    kullanici_adi = st.text_input("Kullanıcı Adı Belirleyin")
    sifre = st.text_input("Şifre Belirleyin", type="password")
    moderator_kodu = st.text_input(
        "Moderatör Gizli Kodu (Normal üyeler boş bıraksın)", type="password"
    )

    if not st.button("Kayıt Ol"):
        return
    if not (kullanici_adi and sifre):
        st.error(EKSIK_ALAN_MESAJI)
        return

    kod_dogru = moderator_kodu == MODERATOR_KAYIT_KODU
    rol = ROL_MODERATOR if kod_dogru else ROL_NORMAL
    try:
        komut_calistir(
            "INSERT INTO kullanicilar (kadi, sifre, rol) VALUES (?, ?, ?)",
            (kullanici_adi, sifre, rol),
        )
    except sqlite3.IntegrityError:
        st.error("Bu kullanıcı adı zaten alınmış. Başka bir tane deneyin.")
        return

    st.success(
        "Hesabınız oluşturuldu! Sol menüden 'Giriş Yap' seçeneğine gidin."
    )


def sikayet_dugmesini_goster(haber_id):
    if not st.session_state.giris_yapildi:
        st.info("Haberi şikayet etmek için lütfen giriş yapın.")
        return

    dugme_basildi = st.button(
        f"🚨 Bu Haberi Şikayet Et (ID: {haber_id})",
        key=f"sikayet_{haber_id}",
    )
    if dugme_basildi:
        komut_calistir(
            "UPDATE haberler SET sikayet_sayisi = sikayet_sayisi + 1 "
            "WHERE id = ?",
            (haber_id,),
        )
        st.rerun()


def ana_sayfa():
    st.header("📢 Yayınlanan Güvenilir Haberler")

    haberler = sorgu_calistir(
        f"SELECT {HABER_SUTUNLARI} FROM haberler WHERE durum = ?",
        (DURUM_ONAYLANDI,),
    )
    if not haberler:
        st.info(
            "Henüz onaylanmış bir haber bulunmuyor. "
            "Moderatör onayından sonra burada görünecektir."
        )

    for haber_id, baslik, icerik, yazar, sikayet_sayisi in haberler:
        with st.expander(f"📌 {baslik} (Yazar: {yazar})"):
            st.write(icerik)
            st.caption(f"Şikayet Sayısı: {sikayet_sayisi}")
            sikayet_dugmesini_goster(haber_id)


def haber_paylasim_sayfasi():
    st.header("✍️ Yeni Haber Paylaş")
    st.info(
        f"Şu an **{st.session_state.kullanici_adi}** olarak "
        "haber paylaşıyorsunuz."
    )
    baslik = st.text_input("Haber Başlığı")
    icerik = st.text_area("Haber İçeriği")

    if not st.button("Haberi Gönder"):
        return
    if not (baslik and icerik):
        st.error(EKSIK_ALAN_MESAJI)
        return

    komut_calistir(
        "INSERT INTO haberler (baslik, icerik, yazar) VALUES (?, ?, ?)",
        (baslik, icerik, st.session_state.kullanici_adi),
    )
    st.success(
        "Haber gönderildi! Moderatör onay kuyruğuna eklendi."
    )


def moderator_paneli():
    st.header("🛡️ Moderatör Onay Kuyruğu")

    # Çok şikayet alan haber yayında olsa bile yeniden incelenir.
    incelenecekler = sorgu_calistir(
        f"SELECT {HABER_SUTUNLARI} FROM haberler "
        "WHERE durum = ? OR sikayet_sayisi >= ?",
        (DURUM_BEKLEMEDE, INCELEME_SIKAYET_ESIGI),
    )
    if not incelenecekler:
        st.info("İnceleme bekleyen veya şikayet edilen haber bulunmuyor.")

    for haber_id, baslik, icerik, yazar, sikayet_sayisi in incelenecekler:
        st.subheader(f"İnceleme ID: {haber_id} - {baslik}")
        st.write(f"**Yazar:** {yazar} | **Şikayet:** {sikayet_sayisi}")
        st.write(icerik)

        onay_sutunu, red_sutunu = st.columns(2)
        onay_istendi = onay_sutunu.button(
            f"✅ Onayla (ID: {haber_id})", key=f"onay_{haber_id}"
        )
        red_istendi = red_sutunu.button(
            f"❌ Reddet / Yalan (ID: {haber_id})", key=f"red_{haber_id}"
        )

        if onay_istendi:
            haberi_degerlendir(
                haber_id,
                yazar,
                DURUM_ONAYLANDI,
                ONAY_GUVEN_DEGISIMI,
                ONAY_KIDEM_DEGISIMI,
            )
            st.rerun()
        if red_istendi:
            haberi_degerlendir(
                haber_id, yazar, DURUM_REDDEDILDI, RED_GUVEN_DEGISIMI
            )
            st.rerun()

        st.divider()


def puan_tablosu_sayfasi():
    st.header("🏆 Kullanıcı Güven ve Kıdem Sıralaması")

    kullanicilar = sorgu_calistir(
        "SELECT kadi, rol, guven_puani, kidem_puani "
        "FROM kullanicilar ORDER BY guven_puani DESC"
    )
    st.table([
        {
            "Kullanıcı Adı": kadi,
            "Rol": rol,
            "Güven Puanı": guven_puani,
            "Kıdem Puanı": kidem_puani,
        }
        for kadi, rol, guven_puani, kidem_puani in kullanicilar
    ])


SAYFALAR = {
    MENU_ANA_SAYFA: ana_sayfa,
    MENU_GIRIS: giris_sayfasi,
    MENU_KAYIT: kayit_sayfasi,
    MENU_HABER_PAYLASIM: haber_paylasim_sayfasi,
    MENU_PUANLAR: puan_tablosu_sayfasi,
    MENU_MODERATOR: moderator_paneli,
}


def main():
    # set_page_config, diğer tüm Streamlit çağrılarından önce gelmeli.
    st.set_page_config(
        page_title="Doğru Haber Platformu", page_icon="📰", layout="wide"
    )
    oturumu_hazirla()

    st.title("📰 Yalan Haber Önleme ve Topluluk Doğrulama Platformu")
    st.write(
        "Topluluk tabanlı doğrulama, güven puanları ve moderatör "
        "denetimiyle güvenilir haber ağı."
    )

    secilen_menu = kenar_cubugunu_goster()
    if secilen_menu == MENU_CIKIS:
        oturumu_kapat()

    SAYFALAR[secilen_menu]()


if __name__ == "__main__":
    main()
