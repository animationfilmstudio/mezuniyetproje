"""Flask tabanlı haber doğrulama ve topluluk platformu."""

from functools import wraps
import hashlib
import hmac
import os
import re
import sqlite3
import time
from collections import namedtuple
from contextlib import contextmanager, nullcontext
from pathlib import Path
from secrets import token_bytes, token_hex, token_urlsafe
from threading import Lock

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

try:
    import psycopg2
except ImportError:  # PostgreSQL kullanilmiyorsa yerel SQLite yeterlidir.
    psycopg2 = None

YEREL_VERITABANI_DOSYASI = Path(__file__).with_name("haber_platformu_v2.db")

ROL_NORMAL = "normal"
ROL_MODERATOR = "moderator"

DURUM_BEKLEMEDE = "beklemede"
DURUM_ONAYLANDI = "onaylandi"
DURUM_REDDEDILDI = "reddedildi"

GEREKLI_AYARLAR = (
    "YONETICI_KULLANICI_ADI",
    "YONETICI_SIFRESI",
    "MODERATOR_KAYIT_KODU",
)

BASLANGIC_GUVEN_PUANI = 50
BASLANGIC_KIDEM_PUANI = 10
ONAY_GUVEN_DEGISIMI = 5
ONAY_KIDEM_DEGISIMI = 2
RED_GUVEN_DEGISIMI = -10
INCELEME_SIKAYET_ESIGI = 3

KULLANICI_ADI_DESENI = re.compile(r"^\w{3,30}$")
MIN_SIFRE_UZUNLUGU = 8
MAKS_BASLIK_UZUNLUGU = 120
MAKS_ICERIK_UZUNLUGU = 3000
MAKS_BEKLEYEN_HABER = 5
AKISTAKI_HABER_SAYISI = 50

MAKS_BASARISIZ_GIRIS = 5
GIRIS_KILIDI_SURESI_SANIYE = 300

PBKDF2_TEKRAR_SAYISI = 600_000
TUZ_BAYT_SAYISI = 16

EKSIK_ALAN_MESAJI = "Lütfen tüm alanları doldurun."

Haber = namedtuple(
    "Haber", "id baslik icerik yazar sikayet_sayisi olusturma_zamani"
)
HABER_SUTUNLARI = ", ".join(Haber._fields)

_veritabani = None
_veritabani_kilidi = Lock()
_giris_denemeleri = {}
_giris_denemeleri_kilidi = Lock()


def gizli_deger_oku(anahtar, varsayilan=None):
    return os.environ.get(anahtar, varsayilan)


class Imlec:
    """SQL parametrelerini SQLite ve PostgreSQL arasında ortaklaştırır."""

    def __init__(self, imlec, parametre_isareti):
        self._imlec = imlec
        self._parametre_isareti = parametre_isareti

    def calistir(self, sql, parametreler=()):
        self._imlec.execute(
            sql.replace("?", self._parametre_isareti), parametreler
        )
        return self

    def hepsini_getir(self):
        return self._imlec.fetchall()

    @property
    def etkilenen_satir(self):
        return self._imlec.rowcount


class Veritabani:
    def __init__(
        self,
        baglanti_acici,
        parametre_isareti,
        otomatik_artan_sutun,
        benzersizlik_hatasi,
        baglantiyi_kapat,
    ):
        self._baglanti_acici = baglanti_acici
        self._parametre_isareti = parametre_isareti
        self.otomatik_artan_sutun = otomatik_artan_sutun
        self.benzersizlik_hatasi = benzersizlik_hatasi
        self._baglantiyi_kapat = baglantiyi_kapat
        self._kilit = nullcontext() if baglantiyi_kapat else Lock()

    @contextmanager
    def islem(self):
        with self._kilit:
            baglanti = self._baglanti_acici()
            try:
                with baglanti:
                    yield Imlec(baglanti.cursor(), self._parametre_isareti)
            finally:
                if self._baglantiyi_kapat:
                    baglanti.close()

    def sorgu(self, sql, parametreler=()):
        with self.islem() as imlec:
            return imlec.calistir(sql, parametreler).hepsini_getir()

    def komut(self, sql, parametreler=()):
        with self.islem() as imlec:
            return imlec.calistir(sql, parametreler).etkilenen_satir


def veritabani_olustur():
    adres = gizli_deger_oku("VERITABANI_URL")
    if adres:
        if psycopg2 is None:
            raise RuntimeError(
                "PostgreSQL için psycopg2-binary paketini yükleyin."
            )
        return Veritabani(
            baglanti_acici=lambda: psycopg2.connect(adres),
            parametre_isareti="%s",
            otomatik_artan_sutun="SERIAL PRIMARY KEY",
            benzersizlik_hatasi=psycopg2.IntegrityError,
            baglantiyi_kapat=True,
        )

    veritabani_yolu = Path(
        gizli_deger_oku("VERITABANI_DOSYASI", str(YEREL_VERITABANI_DOSYASI))
    )
    yerel_baglanti = sqlite3.connect(
        veritabani_yolu, check_same_thread=False
    )
    return Veritabani(
        baglanti_acici=lambda: yerel_baglanti,
        parametre_isareti="?",
        otomatik_artan_sutun="INTEGER PRIMARY KEY AUTOINCREMENT",
        benzersizlik_hatasi=sqlite3.IntegrityError,
        baglantiyi_kapat=False,
    )


def tablolari_olustur(veritabani):
    artan_sutun = veritabani.otomatik_artan_sutun
    with veritabani.islem() as imlec:
        imlec.calistir(f"""
            CREATE TABLE IF NOT EXISTS kullanicilar (
                id {artan_sutun},
                kadi TEXT UNIQUE,
                sifre_hash TEXT,
                rol TEXT,
                guven_puani INTEGER DEFAULT {BASLANGIC_GUVEN_PUANI},
                kidem_puani INTEGER DEFAULT {BASLANGIC_KIDEM_PUANI}
            )
        """)
        imlec.calistir(f"""
            CREATE TABLE IF NOT EXISTS haberler (
                id {artan_sutun},
                baslik TEXT,
                icerik TEXT,
                yazar TEXT,
                durum TEXT DEFAULT '{DURUM_BEKLEMEDE}',
                sikayet_sayisi INTEGER DEFAULT 0,
                incelenen_sikayet_sayisi INTEGER DEFAULT 0,
                olusturma_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        imlec.calistir(f"""
            CREATE TABLE IF NOT EXISTS sikayetler (
                id {artan_sutun},
                haber_id INTEGER,
                kullanici_adi TEXT,
                UNIQUE (haber_id, kullanici_adi)
            )
        """)


def kullanici_adini_duzenle(girdi):
    return girdi.strip().lower()


def sifre_hashle(sifre):
    tuz = token_bytes(TUZ_BAYT_SAYISI)
    ozet = hashlib.pbkdf2_hmac(
        "sha256", sifre.encode("utf-8"), tuz, PBKDF2_TEKRAR_SAYISI
    )
    return f"{PBKDF2_TEKRAR_SAYISI}${tuz.hex()}${ozet.hex()}"


def sifre_dogru_mu(sifre, kayitli_hash):
    tekrar_sayisi, tuz_hex, ozet_hex = kayitli_hash.split("$")
    ozet = hashlib.pbkdf2_hmac(
        "sha256",
        sifre.encode("utf-8"),
        bytes.fromhex(tuz_hex),
        int(tekrar_sayisi),
    )
    return hmac.compare_digest(ozet.hex(), ozet_hex)


def yoneticiyi_hazirla(veritabani):
    kullanici_adi = kullanici_adini_duzenle(
        gizli_deger_oku("YONETICI_KULLANICI_ADI")
    )
    sifre_hash = sifre_hashle(gizli_deger_oku("YONETICI_SIFRESI"))
    with veritabani.islem() as imlec:
        guncellenen = imlec.calistir(
            "UPDATE kullanicilar SET sifre_hash = ?, rol = ? WHERE kadi = ?",
            (sifre_hash, ROL_MODERATOR, kullanici_adi),
        ).etkilenen_satir
        if guncellenen == 0:
            imlec.calistir(
                "INSERT INTO kullanicilar (kadi, sifre_hash, rol) "
                "VALUES (?, ?, ?)",
                (kullanici_adi, sifre_hash, ROL_MODERATOR),
            )


def veritabanini_getir():
    global _veritabani
    if _veritabani is None:
        with _veritabani_kilidi:
            if _veritabani is None:
                _veritabani = veritabani_olustur()
                tablolari_olustur(_veritabani)
                yoneticiyi_hazirla(_veritabani)
    return _veritabani


def kullanici_olustur(kullanici_adi, sifre, rol):
    veritabani = veritabanini_getir()
    try:
        veritabani.komut(
            "INSERT INTO kullanicilar (kadi, sifre_hash, rol) "
            "VALUES (?, ?, ?)",
            (kullanici_adi, sifre_hashle(sifre), rol),
        )
    except veritabani.benzersizlik_hatasi:
        return False
    return True


def kullanici_getir(kullanici_adi):
    satirlar = veritabanini_getir().sorgu(
        "SELECT sifre_hash, rol FROM kullanicilar WHERE kadi = ?",
        (kullanici_adi,),
    )
    return satirlar[0] if satirlar else None


def puan_tablosunu_getir():
    return veritabanini_getir().sorgu(
        "SELECT kadi, rol, guven_puani, kidem_puani "
        "FROM kullanicilar ORDER BY guven_puani DESC"
    )


def haberleri_getir(kosul, parametreler=()):
    satirlar = veritabanini_getir().sorgu(
        f"SELECT {HABER_SUTUNLARI} FROM haberler {kosul}", parametreler
    )
    return [Haber(*satir) for satir in satirlar]


def onayli_haberleri_getir():
    return haberleri_getir(
        "WHERE durum = ? ORDER BY id DESC LIMIT ?",
        (DURUM_ONAYLANDI, AKISTAKI_HABER_SAYISI),
    )


def bekleyen_haberleri_getir():
    return haberleri_getir("WHERE durum = ? ORDER BY id", (DURUM_BEKLEMEDE,))


def sikayetli_haberleri_getir():
    return haberleri_getir(
        "WHERE durum = ? "
        "AND sikayet_sayisi - incelenen_sikayet_sayisi >= ? "
        "ORDER BY sikayet_sayisi DESC",
        (DURUM_ONAYLANDI, INCELEME_SIKAYET_ESIGI),
    )


def haber_ekle(baslik, icerik, yazar):
    with veritabanini_getir().islem() as imlec:
        bekleyen_sayisi = imlec.calistir(
            "SELECT COUNT(*) FROM haberler WHERE yazar = ? AND durum = ?",
            (yazar, DURUM_BEKLEMEDE),
        ).hepsini_getir()[0][0]
        if bekleyen_sayisi >= MAKS_BEKLEYEN_HABER:
            return False
        imlec.calistir(
            "INSERT INTO haberler (baslik, icerik, yazar) VALUES (?, ?, ?)",
            (baslik, icerik, yazar),
        )
    return True


def haber_getir(haber_id):
    satirlar = veritabanini_getir().sorgu(
        f"SELECT {HABER_SUTUNLARI}, durum FROM haberler WHERE id = ?",
        (haber_id,),
    )
    return satirlar[0] if satirlar else None


def sikayet_ekle(haber_id, kullanici_adi):
    veritabani = veritabanini_getir()
    try:
        with veritabani.islem() as imlec:
            imlec.calistir(
                "INSERT INTO sikayetler (haber_id, kullanici_adi) "
                "SELECT ?, ? WHERE EXISTS ("
                "SELECT 1 FROM haberler "
                "WHERE id = ? AND durum = ? AND yazar != ?"
                ")",
                (
                    haber_id,
                    kullanici_adi,
                    haber_id,
                    DURUM_ONAYLANDI,
                    kullanici_adi,
                ),
            )
            if imlec.etkilenen_satir == 0:
                return False
            imlec.calistir(
                "UPDATE haberler SET sikayet_sayisi = sikayet_sayisi + 1 "
                "WHERE id = ?",
                (haber_id,),
            )
    except veritabani.benzersizlik_hatasi:
        return False
    return True


def bekleyen_haberi_sonuclandir(
    haber_id, moderator, yeni_durum, guven_degisimi, kidem_degisimi=0
):
    with veritabanini_getir().islem() as imlec:
        guncellenen = imlec.calistir(
            "UPDATE haberler SET durum = ? "
            "WHERE id = ? AND durum = ? AND yazar != ?",
            (yeni_durum, haber_id, DURUM_BEKLEMEDE, moderator),
        ).etkilenen_satir
        if guncellenen == 0:
            return False
        imlec.calistir(
            "UPDATE kullanicilar "
            "SET guven_puani = guven_puani + ?, "
            "kidem_puani = kidem_puani + ? "
            "WHERE kadi = (SELECT yazar FROM haberler WHERE id = ?)",
            (guven_degisimi, kidem_degisimi, haber_id),
        )
    return True


def sikayetli_haberi_sonuclandir(haber_id, moderator, haber_kaldirilsin):
    with veritabanini_getir().islem() as imlec:
        if not haber_kaldirilsin:
            guncellenen = imlec.calistir(
                "UPDATE haberler "
                "SET incelenen_sikayet_sayisi = sikayet_sayisi "
                "WHERE id = ? AND durum = ? AND yazar != ?",
                (haber_id, DURUM_ONAYLANDI, moderator),
            ).etkilenen_satir
            return guncellenen > 0
        guncellenen = imlec.calistir(
            "UPDATE haberler SET durum = ? "
            "WHERE id = ? AND durum = ? AND yazar != ?",
            (DURUM_REDDEDILDI, haber_id, DURUM_ONAYLANDI, moderator),
        ).etkilenen_satir
        if guncellenen == 0:
            return False
        imlec.calistir(
            "UPDATE kullanicilar SET guven_puani = guven_puani + ? "
            "WHERE kadi = (SELECT yazar FROM haberler WHERE id = ?)",
            (RED_GUVEN_DEGISIMI, haber_id),
        )
    return True


def giris_kilidi_kalan_sure(kullanici_adi):
    with _giris_denemeleri_kilidi:
        kayit = _giris_denemeleri.get(kullanici_adi)
        if not kayit:
            return 0
        return max(0, int(kayit["kilit_bitis"] - time.monotonic()))


def basarisiz_girisi_kaydet(kullanici_adi):
    with _giris_denemeleri_kilidi:
        kayit = _giris_denemeleri.setdefault(
            kullanici_adi, {"sayac": 0, "kilit_bitis": 0.0}
        )
        kayit["sayac"] += 1
        if kayit["sayac"] >= MAKS_BASARISIZ_GIRIS:
            kayit["kilit_bitis"] = (
                time.monotonic() + GIRIS_KILIDI_SURESI_SANIYE
            )
            kayit["sayac"] = 0


def basarili_girisi_temizle(kullanici_adi):
    with _giris_denemeleri_kilidi:
        _giris_denemeleri.pop(kullanici_adi, None)


def giris_gerekli(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Bu sayfa için önce giriş yapmalısınız.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def moderator_gerekli(view):
    @wraps(view)
    @giris_gerekli
    def wrapped(*args, **kwargs):
        if g.user["rol"] != ROL_MODERATOR:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def csrf_kontrolu():
    saglanan = request.form.get("_csrf_token", "")
    beklenen = session.get("_csrf_token", "")
    return bool(beklenen) and hmac.compare_digest(saglanan, beklenen)


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=(
            os.environ.get("FLASK_COOKIE_SECURE", "").lower()
            in {"1", "true", "yes"}
        ),
        MAX_CONTENT_LENGTH=64 * 1024,
    )

    @app.before_request
    def prepare_request():
        g.user = None
        if request.endpoint == "static":
            return None

        eksik_ayarlar = [
            anahtar
            for anahtar in GEREKLI_AYARLAR
            if not gizli_deger_oku(anahtar)
        ]
        if eksik_ayarlar:
            return (
                render_template("configuration_error.html", missing=eksik_ayarlar),
                503,
            )

        veritabanini_getir()
        if request.method == "POST" and not csrf_kontrolu():
            return render_template("error.html", message="Geçersiz istek."), 400

        if not session.get("_csrf_token"):
            session["_csrf_token"] = token_urlsafe(32)

        kullanici_adi = session.get("kullanici_adi")
        if kullanici_adi:
            satirlar = veritabanini_getir().sorgu(
                "SELECT kadi, rol FROM kullanicilar WHERE kadi = ?",
                (kullanici_adi,),
            )
            if satirlar:
                g.user = {"kadi": satirlar[0][0], "rol": satirlar[0][1]}
            else:
                session.clear()
                g.user = None
        else:
            g.user = None

    @app.context_processor
    def template_context():
        return {"csrf_token": session.get("_csrf_token", "")}

    @app.route("/")
    def home():
        return render_template("home.html", haberler=onayli_haberleri_getir())

    @app.route("/kayit", methods=["GET", "POST"])
    def register():
        if g.user is not None:
            return redirect(url_for("home"))
        if request.method == "POST":
            kullanici_adi = kullanici_adini_duzenle(
                request.form.get("kullanici_adi", "")
            )
            sifre = request.form.get("sifre", "")
            moderator_kodu = request.form.get("moderator_kodu", "")
            if not kullanici_adi or not sifre:
                flash(EKSIK_ALAN_MESAJI, "error")
            elif not KULLANICI_ADI_DESENI.fullmatch(kullanici_adi):
                flash(
                    "Kullanıcı adı 3-30 karakter olmalı; harf, rakam ve _ olur.",
                    "error",
                )
            elif len(sifre) < MIN_SIFRE_UZUNLUGU:
                flash(
                    f"Sifre en az {MIN_SIFRE_UZUNLUGU} karakter olmali.",
                    "error",
                )
            else:
                moderator_anahtari = gizli_deger_oku("MODERATOR_KAYIT_KODU")
                rol = (
                    ROL_MODERATOR
                    if hmac.compare_digest(moderator_kodu, moderator_anahtari)
                    else ROL_NORMAL
                )
                if kullanici_olustur(kullanici_adi, sifre, rol):
                    flash(
                        "Hesabınız oluşturuldu. Şimdi giriş yapabilirsiniz.",
                        "success",
                    )
                    return redirect(url_for("login"))
                flash("Bu kullanıcı adı zaten alınmış.", "error")
        return render_template("register.html")

    @app.route("/giris", methods=["GET", "POST"])
    def login():
        if g.user is not None:
            return redirect(url_for("home"))
        if request.method == "POST":
            kullanici_adi = kullanici_adini_duzenle(
                request.form.get("kullanici_adi", "")
            )
            sifre = request.form.get("sifre", "")
            kalan_sure = giris_kilidi_kalan_sure(kullanici_adi)
            if kalan_sure > 0:
                flash(
                    f"Çok fazla hatalı deneme. {kalan_sure} saniye bekleyin.",
                    "error",
                )
            else:
                kayit = kullanici_getir(kullanici_adi)
                if not kayit or not sifre_dogru_mu(sifre, kayit[0]):
                    basarisiz_girisi_kaydet(kullanici_adi)
                    flash("Kullanıcı adı veya şifre hatalı.", "error")
                else:
                    basarili_girisi_temizle(kullanici_adi)
                    session.clear()
                    session["kullanici_adi"] = kullanici_adi
                    session["_csrf_token"] = token_urlsafe(32)
                    flash("Giriş başarılı.", "success")
                    return redirect(url_for("home"))
        return render_template("login.html")

    @app.post("/cikis")
    @giris_gerekli
    def logout():
        session.clear()
        flash("Oturumunuz kapatıldı.", "success")
        return redirect(url_for("home"))

    @app.route("/haber/paylas", methods=["GET", "POST"])
    @giris_gerekli
    def submit_news():
        if request.method == "POST":
            baslik = request.form.get("baslik", "").strip()
            icerik = request.form.get("icerik", "").strip()
            if not baslik or not icerik:
                flash(EKSIK_ALAN_MESAJI, "error")
            elif len(baslik) > MAKS_BASLIK_UZUNLUGU:
                flash(
                    f"Başlık en fazla {MAKS_BASLIK_UZUNLUGU} karakter olabilir.",
                    "error",
                )
            elif len(icerik) > MAKS_ICERIK_UZUNLUGU:
                flash(
                    f"İçerik en fazla {MAKS_ICERIK_UZUNLUGU} karakter olabilir.",
                    "error",
                )
            elif not haber_ekle(baslik, icerik, g.user["kadi"]):
                flash(
                    f"Onay bekleyen {MAKS_BEKLEYEN_HABER} haberiniz var.",
                    "error",
                )
            else:
                flash(
                    "Haberiniz moderatör onayına gönderildi.", "success"
                )
                return redirect(url_for("home"))
        return render_template("submit_news.html")

    @app.post("/haber/<int:haber_id>/sikayet")
    @giris_gerekli
    def report_news(haber_id):
        haber = haber_getir(haber_id)
        if not haber or haber[-1] != DURUM_ONAYLANDI:
            flash("Bu haber artık şikâyet edilemiyor.", "error")
        elif haber[3] == g.user["kadi"]:
            flash("Kendi haberinizi şikâyet edemezsiniz.", "error")
        elif sikayet_ekle(haber_id, g.user["kadi"]):
            flash("Şikâyetiniz alındı.", "success")
        else:
            flash("Bu haberi daha önce şikâyet ettiniz.", "error")
        return redirect(url_for("home"))

    @app.route("/puanlar")
    def scores():
        return render_template("scores.html", puanlar=puan_tablosunu_getir())

    @app.route("/moderator")
    @moderator_gerekli
    def moderator_panel():
        return render_template(
            "moderator.html",
            bekleyen_haberler=bekleyen_haberleri_getir(),
            sikayetli_haberler=sikayetli_haberleri_getir(),
        )

    @app.post("/moderator/haber/<int:haber_id>/karar")
    @moderator_gerekli
    def decide_pending_news(haber_id):
        karar = request.form.get("karar")
        if karar == "onayla":
            sonuc = bekleyen_haberi_sonuclandir(
                haber_id,
                g.user["kadi"],
                DURUM_ONAYLANDI,
                ONAY_GUVEN_DEGISIMI,
                ONAY_KIDEM_DEGISIMI,
            )
        elif karar == "reddet":
            sonuc = bekleyen_haberi_sonuclandir(
                haber_id, g.user["kadi"], DURUM_REDDEDILDI, RED_GUVEN_DEGISIMI
            )
        else:
            abort(400)
        flash(
            "Haber kararı kaydedildi."
            if sonuc
            else "Haber artık değerlendirilemiyor.",
            "success" if sonuc else "error",
        )
        return redirect(url_for("moderator_panel"))

    @app.post("/moderator/haber/<int:haber_id>/sikayet-karari")
    @moderator_gerekli
    def decide_reported_news(haber_id):
        karar = request.form.get("karar")
        if karar not in {"yayinda_tut", "kaldir"}:
            abort(400)
        sonuc = sikayetli_haberi_sonuclandir(
            haber_id, g.user["kadi"], karar == "kaldir"
        )
        flash(
            "Şikâyet incelemesi tamamlandı."
            if sonuc
            else "Haber artık incelenemiyor.",
            "success" if sonuc else "error",
        )
        return redirect(url_for("moderator_panel"))

    @app.errorhandler(403)
    def forbidden(_error):
        del _error
        return render_template("error.html", message="Erişim yetkiniz yok."), 403

    @app.errorhandler(404)
    def not_found(_error):
        del _error
        return render_template("error.html", message="Sayfa bulunamadı."), 404

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "").lower() == "1",
    )
