# Ek Ayarlar

[English documentation](README.en.md)

Ek Ayarlar, NVDA ekran okuyucusunun günlük kullanımını kolaylaştıran özellikleri tek bir ayar kategorisinde bir araya getirir. Her özellik ihtiyaç doğrultusunda etkinleştirilebilir. Ana seçenekler ilk kurulumda işaretsizdir.

## Sürüm ve uyumluluk

- Eklenti sürümü: 2026.10.20
- En düşük NVDA sürümü: 2026.2
- Son sınanan NVDA sürümü: 2026.2
- Arayüz ve yardım dilleri: Türkçe ve İngilizce

## Geliştirici

Mehmet Aykurt

[Mehmet Aykurt](https://mehmetaykurt.com.tr)

Öneri, görüş ve sorun bildirimleri: [m.aykurt38@gmail.com](mailto:m.aykurt38@gmail.com)

## Özellikler

- Web sayfalarında hızlı gezinmeyi ilk ve son eşleşme arasında döngüye alma.
- Web sayfalarında ok tuşlarıyla gezinirken boş satırları atlama.
- Kesme, kopyalama ve yapıştırma işlemlerine ilişkin bildirimler.
- Bellekte tutulan, okunabilen ve kopyalanabilen konuşma geçmişi.
- Uygulama tuşu yerine başka bir tuş veya birleşim kullanma.
- Seçili metinleri panoya art arda ekleme.
- Seçili metnin cümle, sözcük, karakter ve boş satır sayılarını görüntüleme.
- Panodaki metni, HTML tablolarını ve dosya/klasör bilgilerini inceleme.
- Web sayfaları ve klasörler için yer imleri oluşturma.
- Hızlı bul ile desteklenen sayfa ve belgelerde metin konumları kaydetme; kayıtlara belgeye özgü kısayollar atama.
- Sayısal tuş takımını gezinme tuşları olarak kullanma.
- İmlecin bulunduğu satırı seçilen dile çevirme ve otomatik çeviri oturumu sırasında satırları sırayla seslendirme.

## Kurulum ve ilk kullanım

Yayımlanmış `.nvda-addon` paketini NVDA çalışırken açın ve kurulum yönergelerini izleyin. İstendiğinde NVDA’yı yeniden başlatın. NVDA menüsünden Tercihler, Ayarlar, Ek Ayarlar yolunu izleyin. Kullanılacak özellikleri işaretleyip Uygula veya Tamam düğmesine basın.

[Kurulum paketini Releases bölümünden indirin](https://github.com/MehmetAykurt/extraSettings/releases/latest). NVDA Eklenti Mağazası başvurusu ayrıca yapılacaktır.

## Varsayılan kısayollar

| İşlem | Kısayol |
| --- | --- |
| Konuşma geçmişini göster | NVDA+G |
| Panoya ekle | Windows+C |
| Sözcük ve karakter sayısını duyur | NVDA+Shift+K |
| Panoyu incele | NVDA+Shift+C |
| Yer imleri | NVDA+Shift+Y |
| Hızlı bul | NVDA+Shift+H |
| Bulunulan satırı çevir | NVDA+Shift+T |
| Otomatik çeviri oturumunu başlat veya durdur | NVDA+Shift+O |

İlgili özellik önceden etkinleştirilmiş olmalıdır. Komut atamaları Tercihler, Girdi Hareketleri, NVDA Ek Ayarları bölümünden değiştirilebilir. Hızlı bul kayıtlarının özel kısayolları kayıt penceresinden atanır. NVDA+Shift+C, Word ve Excel’in sütun başlığı komutuyla çakışabileceğinden gerektiğinde değiştirilmelidir.

## Ayrıntılı yardım

- [Türkçe kullanım kılavuzu](addon/doc/tr/readme.html)
- [İngilizce kullanım kılavuzu](addon/doc/en/readme.html)
- [Sürüm geçmişi](CHANGELOG.md)
- [Geliştirme, sınama ve yayımlama](DEVELOPMENT.md)

GitHub, HTML dosyasını kaynak metin olarak gösterebilir. Biçimlendirilmiş yardım, kurulu eklentinin NVDA yardım komutundan veya indirilen HTML dosyası tarayıcıda açılarak okunabilir.

## Verilerin saklanması ve çeviri

Yer imleri, Hızlı bul kayıtları ve yedekleri NVDA kullanıcı ayar klasörünün `extraSettings` alt klasöründe tutulur. Eski konumdaki dosyalar otomatik taşınır. Onay kutuları gibi tercihler ile Girdi Hareketleri atamaları NVDA’nın kendi yapılandırma sistemi tarafından yönetilir. Konuşma geçmişi ve çeviri önbellekleri bellekte tutulur.

Çeviri internet bağlantısı gerektirir; çevrilecek satır Google’a gönderilir. Hesap veya API anahtarı gerekmez. Resmî ücretli geliştirici hizmeti kullanılmadığından hizmetteki değişiklikler uyarlama gerektirebilir. Otomatik çeviri kutusu kullanım iznidir; oturum kısayolla başlatılır.

`update.json`, diğer depolardaki sürüm bilgi düzenini sürdürür. Eklenti bu dosyayı okuyarak kendi başına güncelleme indirmez. Mağaza dağıtımı için her sürüm NVDA Eklenti Mağazası’na ayrıca sunulmalıdır.

## Lisans

[GNU Genel Kamu Lisansı, sürüm 2](LICENSE). Lisans metni, geliştiricinin Kurum Rehberi deposundaki LICENSE dosyasından alınmıştır. Sınamalarda kullanılan NVDA kaynak dosyalarının kendi telif bildirimleri korunmuştur; kökenleri [sınama kaynakları açıklamasında](tests/fixtures/nvda-source/README.md) belirtilmiştir.
