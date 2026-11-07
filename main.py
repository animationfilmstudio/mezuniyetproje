import sqlite3
import pyttsx3

class DogruHaberPlatformu:
    def __init__(self, db_name="haber_sistemi.db"):
        self.db_name = db_name
        # pyttsx3 ses motorunu başlatma
        try:
            self.engine = pyttsx3.init()
        except Exception:
            self.engine = None
        self.veritabani_olustur()

    def sesli_mesaj_soyle(self, mesaj):
        """Sistemin sesli geri bildirim vermesini sağlar."""
        if self.engine:
            try:
                self.engine.say(mesaj)
                self.engine.runAndWait()
            except Exception as e:
                print(f"Sesli uyarı hatası: {e}")

    def veritabani_olustur(self):
        baglanti = sqlite3.connect(self.db_name)
        cursor = baglanti.cursor()
        
        # Kullanıcılar Tablosu (Kıdem, Güven Puanı ve Rol)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS kullanicilar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kadi TEXT UNIQUE,
                rol TEXT, -- 'normal', 'moderator', 'admin'
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
                yazar_id INTEGER,
                durum TEXT DEFAULT 'beklemede', -- 'beklemede', 'onaylandi', 'reddedildi'
                sikayet_sayisi INTEGER DEFAULT 0,
                FOREIGN KEY(yazar_id) REFERENCES kullanicilar(id)
            )
        ''')
        baglanti.commit()
        baglanti.close()

    def kullanici_ekle(self, kadi, rol='normal'):
        baglanti = sqlite3.connect(self.db_name)
        cursor = baglanti.cursor()
        try:
            cursor.execute("INSERT INTO kullanicilar (kadi, rol) VALUES (?, ?)", (kadi, rol))
            baglanti.commit()
            print(f"Kullanıcı eklendi: {kadi} ({rol})")
        except sqlite3.IntegrityError:
            print(f"Bu kullanıcı adı zaten mevcut: {kadi}")
        finally:
            baglanti.close()

    def haber_paylas(self, yazar_id, baslik, icerik):
        baglanti = sqlite3.connect(self.db_name)
        cursor = baglanti.cursor()
        cursor.execute("INSERT INTO haberler (baslik, icerik, yazar_id) VALUES (?, ?, ?)", (baslik, icerik, yazar_id))
        baglanti.commit()
        baglanti.close()
        print(f"\n[HABER] Başarıyla paylaşıldı (Onay bekliyor): '{baslik}'")
        self.sesli_mesaj_soyle("Yeni bir haber inceleme kuyruğuna eklendi.")

    def haber_incele_ve_karar_ver(self, haber_id, karar):
        """
        karar: 'onaylandi' veya 'reddedildi'
        """
        baglanti = sqlite3.connect(self.db_name)
        cursor = baglanti.cursor()
        
        cursor.execute("SELECT yazar_id, baslik FROM haberler WHERE id = ?", (haber_id,))
        haber = cursor.fetchone()
        
        if not haber:
            print("Haber bulunamadı!")
            baglanti.close()
            return

        yazar_id, baslik = haber
        cursor.execute("UPDATE haberler SET durum = ? WHERE id = ?", (karar, haber_id))
        
        if karar == 'onaylandi':
            cursor.execute("UPDATE kullanicilar SET guven_puani = guven_puani + 5, kidem_puani = kidem_puani + 2 WHERE id = ?", (yazar_id,))
            print(f"[MODERATÖR] Haber ONAYLANDI: {baslik}")
            self.sesli_mesaj_soyle("Haber moderatör tarafından onaylandı ve yayınlandı.")
        else:
            cursor.execute("UPDATE kullanicilar SET guven_puani = guven_puani - 10 WHERE id = ?", (yazar_id,))
            print(f"[MODERATÖR] Haber REDDEDİLDİ (Yalan Şüphesi): {baslik}")
            self.sesli_mesaj_soyle("Haber yalan içerik şüphesiyle reddedildi.")
            
        baglanti.commit()
        baglanti.close()

    def haber_sikayet_et(self, haber_id):
        baglanti = sqlite3.connect(self.db_name)
        cursor = baglanti.cursor()
        cursor.execute("UPDATE haberler SET sikayet_sayisi = sikayet_sayisi + 1 WHERE id = ?", (haber_id,))
        baglanti.commit()
        
        cursor.execute("SELECT sikayet_sayisi, baslik FROM haberler WHERE id = ?", (haber_id,))
        sikayet_bilgi = cursor.fetchone()
        baglanti.close()
        
        print(f"[ŞİKAYET] Haber rapor edildi! Toplam şikayet: {sikayet_bilgi[0]}")
        
        if sikayet_bilgi[0] >= 3:
            self.sesli_mesaj_soyle("Dikkat! Bir haber çok fazla şikayet aldı, acil inceleme gerekiyor.")

# --- SİSTEMİ TEST ETME ---
if __name__ == "__main__":
    platform = DogruHaberPlatformu()

    print("--- Doğru Haber Platformu Başlatıldı ---")
    platform.kullanici_ekle("ahmet_muhabir", "normal")
    platform.kullanici_ekle("sevgi_moderator", "moderator")

    # Ahmet haber paylaşır
    platform.haber_paylas(
        yazar_id=1, 
        baslik="Mars'ta Su Bulundu İddiası", 
        icerik="Bilim insanları son verilerle..."
    )

    # Moderatör haberi onaylar
    platform.haber_incele_ve_karar_ver(haber_id=1, karar="onaylandi")

    # Kullanıcı haberi şikayet eder
    platform.haber_sikayet_et(haber_id=1)
