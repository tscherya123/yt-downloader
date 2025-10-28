@echo off
setlocal EnableExtensions
chcp 65001 >nul

:: ===== КОНФІГ =====
set "ROOT=D:\YouTube"
set "MODE=DYNAMIC"        :: DYNAMIC або FIXED
set "FIXED_VBIT=50M"      :: якщо MODE=FIXED
set "AUDIO_BIT=320k"
set "X264_PRESET=slow"

if not exist "%ROOT%" mkdir "%ROOT%"

:: ===== URL =====
echo Встав посилання на YouTube і натисни Enter:
set /p URL=URL:
if "%URL%"=="" ( echo [ERROR] Порожній URL. & pause & exit /b 1 )

:: ===== ПАПКИ =====
for /f %%a in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyy-MM-dd_HH-mm-ss\")"') do set "TS=%%a"
set "WORKDIR=%ROOT%\DL_%TS%"
set "TEMPDIR=%WORKDIR%\temp"
mkdir "%WORKDIR%" >nul 2>&1
mkdir "%TEMPDIR%" >nul 2>&1
pushd "%WORKDIR%"

echo.
echo [1/4] Завантаження...
yt-dlp -f "bv*+ba/b" -S "res,fps,br" --hls-prefer-ffmpeg -N 8 ^
  -P "%CD%" --paths "temp:%TEMPDIR%" -o "source.%%(ext)s" "%URL%"
if errorlevel 1 (
  echo [ERROR] Завантаження впало.
  popd & pause & exit /b 1
)

:: Прибрати службовий шаблон, якщо лишився
del /f /q "source.%%(ext)s" 2>nul

:: ===== Знаходимо фактичний source.* =====
set "SRC="
for %%F in ("source.*") do set "SRC=%%~fF"
if "%SRC%"=="" (
  echo [ERROR] Не знайдено source.*
  popd & pause & exit /b 1
)

:: ===== Читаємо кодеки через PowerShell (стабільно) =====
for /f %%a in ('powershell -NoProfile -Command ^
  "$p='%SRC%';" ^
  "$v=(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=nw=1:nk=1 `"$p`");" ^
  "$a=(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of default=nw=1:nk=1 `"$p`");" ^
  "Write-Output ($v+','+$a)"') do set "VA=%%a"

for /f "tokens=1,2 delims=," %%i in ("%VA%") do (
  set "VIDEO_CODEC=%%i"
  set "AUDIO_CODEC=%%j"
)

echo [2/4] Відео: %VIDEO_CODEC%   Аудіо: %AUDIO_CODEC%

:: ===== Якщо вже MP4-сумісне — не перекодовуємо =====
if /I "%VIDEO_CODEC%"=="h264" if /I "%AUDIO_CODEC%"=="aac" (
  echo [INFO] Уже H.264+AAC. Перейменовую у video.mp4 без перекодування.
  ren "%SRC%" "video.mp4"
  goto done
)

:: ===== Обчислюємо бітрейт =====
set "VBIT=%FIXED_VBIT%"
if /I "%MODE%"=="DYNAMIC" (
  for /f %%a in ('powershell -NoProfile -Command ^
    "$p='%SRC%';" ^
    "$dur=[double](ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 `"$p`"); if(-not $dur -or $dur -le 0){$dur=1};" ^
    "$size=(Get-Item `"$p`").Length;" ^
    "$total=[math]::Floor(($size*8)/$dur);" ^
    "$aud=320000; $vid=[math]::Max(800000,$total-$aud);" ^
    "$head=1.15; $t=[math]::Floor($vid*$head);" ^
    "$mbit=[math]::Ceiling($t/1000000.0);" ^
    "if($mbit -lt 4){$mbit=4};" ^
    "Write-Host -NoNewline $mbit"') do set "MBIT=%%a"
  if not "%MBIT%"=="" set "VBIT=%MBIT%M"
)

if "%VBIT%"=="" set "VBIT=%FIXED_VBIT%"

echo [3/4] Цільовий відео-бітрейт: %VBIT%

:: ===== Перекодування -> video.mp4 =====
echo [4/4] Перекодування у MP4...
ffmpeg -hide_banner -stats -y -i "%SRC%" ^
  -c:v libx264 -preset %X264_PRESET% -pix_fmt yuv420p ^
  -b:v %VBIT% -minrate %VBIT% -maxrate %VBIT% -bufsize 100M -profile:v high ^
  -c:a aac -b:a %AUDIO_BIT% -movflags +faststart "video.mp4"
if errorlevel 1 (
  echo [ERROR] Перекодування впало.
  popd & pause & exit /b 1
)

:done
echo.
echo [DONE] %CD%
echo   - video.mp4 готово
popd
pause
endlocal
