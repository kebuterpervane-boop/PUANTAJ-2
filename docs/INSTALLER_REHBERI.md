# PuantajApp Installer Rehberi (NSIS & Inno Setup)

## 1. Hedef: Veritabanı Dosyasını Merkezi Yola Taşıma
- Uygulama ilk açıldığında eski `puantaj.db` dosyasını otomatik olarak `%APPDATA%/SaralGroup/PuantajApp/puantaj.db` konumuna taşır (kodda hazır).
- Installer'ın ayrıca eski veritabanını kopyalamasına gerek yoktur, sadece eski dosyayı silmemeli.

## 2. NSIS ile Kurulum Scripti Örneği
```nsis
!define APPNAME "PuantajApp"
!define COMPANY "SaralGroup"
!define DBFOLDER "PuantajApp"

InstallDir "$APPDATA\${COMPANY}\${DBFOLDER}"

Section "Install"
  SetOutPath "$INSTDIR"
  File "puantaj.exe"
  ; Veritabanı dosyasını kopyalama: GEREKLİ DEĞİL, uygulama ilk açılışta taşıyacak
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\puantaj.exe"
  ; Veritabanını silme: KULLANICIYA BIRAKILMALI
SectionEnd
```

## 3. Inno Setup ile Kurulum Scripti Örneği
```inno
[Setup]
AppName=PuantajApp
AppVersion=1.0
DefaultDirName={userappdata}\SaralGroup\PuantajApp

[Files]
Source: "puantaj.exe"; DestDir: "{app}"; Flags: ignoreversion
; Veritabanı dosyasını kopyalama: GEREKLİ DEĞİL

[UninstallDelete]
Type: files; Name: "{app}\puantaj.exe"
; Veritabanını silme: KULLANICIYA BIRAKILMALI
```

## 4. Notlar
- Veritabanı dosyasını installer ile kopyalamayın, uygulama ilk açılışta otomatik taşıyacak.
- Eski exe ile aynı klasördeki `puantaj.db` dosyasını silmeyin, uygulama ilk açılışta otomatik yedekleyip taşıyacak.
- Yedekler `%APPDATA%/SaralGroup/PuantajApp/` altında `.orig.YYYYMMDDHHMM.db` olarak tutulur.
- Uygulama güncellendiğinde kullanıcı eski verilerini kaybetmez.
