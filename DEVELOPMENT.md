# Geliştirme ve Yayımlama

## Klasör düzeni

- `addon/`: NVDA’ya kurulacak dosyalar, dil ve yardım dosyaları, lisans.
- `tools/`: bağımsız paketleme, dil denetimi ve sınama araçları.
- `tests/`: birim sınamaları ve yalnız sınamalarda kullanılan NVDA kaynak örnekleri.
- `dist/`: yerel olarak üretilen paketler; Git deposuna eklenmez.
- `update.json`: son hazırlanan paketin sürüm bilgisi, planlanan indirme adresi ve SHA-256 özeti.

## Gereksinimler

Python 3.13 veya sonrası ile Windows kullanın. Araçlar Python standart kitaplığını kullanır; ek Python paketi gerekmez. Sınamalar NVDA ve Windows bileşenlerini taklit eden nesneler kullanır. Dosya taşıma sınamaları Windows’un var olan hedefin üzerine yazmayan yeniden adlandırma davranışını esas alır. Bu sınamalar canlı NVDA ile erişilebilirlik denemelerinin yerine geçmez.

## Sınama

Depo kökünde çalıştırın:

```text
python tools/run_tests.py
```

## Paket üretme

```text
python tools/build.py
```

Araç Python sözdizimini denetler, Türkçe ve İngilizce PO dosyalarını MO biçimine derler, dil ve yardım denetimlerini çalıştırır, `dist/extraSettings-SÜRÜM.nvda-addon` dosyasını üretir ve `update.json` içindeki dört alanı günceller. Aynı kaynak girdileri sabit sıralama ve zaman damgasıyla paketlenir; kaynak değişmedikçe paket özeti değişmez. README ve geliştirme dosyaları kurulum paketine alınmaz. Lisans pakete dâhildir.

## Sürüm bilgileri

Sürümün ana kaynağı `addon/manifest.ini` dosyasıdır. Sürüm değiştiğinde Türkçe/İngilizce kılavuzlardaki ve PO üst bilgilerindeki sürüm ile README ve değişiklik geçmişini de güncelleyin. Araç sürüm uyuşmazlıklarını dil denetiminde yakalar.

Varsayılan planlanan depo `MehmetAykurt/extraSettings`, etiket biçimi `vSÜRÜM`dür. Farklı bir depo kullanılacaksa:

```text
python tools/build.py --repository MehmetAykurt/depo-adi
```

Bu seçenek yalnız `update.json` içindeki planlanan indirme bağlantısını düzenler; depo, etiket veya sürüm yayını oluşturmaz. Güncelleme açıklaması `--description` seçeneğiyle değiştirilebilir.

## GitHub ve mağazada yayımlama

1. Depo adı ile README, lisans ve sürüm bilgilerini gözden geçirin.
2. Sınamaları ve paket üretimini çalıştırın; kurulum paketini NVDA ile deneyin.
3. GitHub deposu ve sürüm yayını oluşturulduğunda aynı paketi sürüm eki olarak yükleyin. update.json dosyasındaki bağlantının bu dosyayı indirdiğini ve SHA-256 değerinin eşleştiğini doğrulayın.
4. Eklentiyi NVDA Eklenti Mağazası’nın kayıt formuyla sunun. Kaynak kodu, lisansı, doğrudan indirme adresini, sürümü ve uyumluluk bilgilerini belirtin. NVDA API sürümünü ve uygun kararlı/deneme kanalını başvuru sırasında doğrulayın.
5. Sonraki mağaza sürümleri için de başvuru sürecini izleyin. Yayımlanmış sürümün paketini sonradan değiştirmeyin; değişiklikler için yeni sürüm kullanın.

Resmî başvuru kılavuzu: https://github.com/nvaccess/addon-datastore/blob/master/docs/submitters/submissionGuide.md

`update.json`, geliştiricinin Kurum Rehberi ve Engelsiz Mail depolarındaki `surum`, `link`, `sha256`, `aciklama` düzenini kullanır. Depoya bu dosyanın eklenmesi, kendiliğinden mağaza başvurusu oluşturmaz. ExtraSettings içinde ayrı bir güncelleme denetleyicisi bulunmaz; mağazanın sunduğu güncellemeler NVDA tarafından yönetilir.

## Lisansın kökeni

LICENSE metni, https://github.com/MehmetAykurt/kurum-rehberi/blob/main/LICENSE adresindeki GNU GPL sürüm 2 metninden alınmıştır. Program GNU GPL sürüm 2 kapsamında dağıtılır. NVDA sınama kaynaklarının kendi bildirimleri korunur. Kaynak depoya kullanıcı kayıtları, kişisel ayarlar, günlükler ve yerel yedekler eklenmez.
