# Windows Installation Guide for YouTube Downloader UI

This application relies on a few external tools in addition to Python. Follow the steps below to prepare a Windows machine for running `ui.py`.

## 1. Install Python 3.10+
1. Download the latest Python 3.10 (or newer) installer from [python.org](https://www.python.org/downloads/windows/).
2. Run the installer and check **Add Python to PATH** on the first screen.
3. Complete the installation using the default options.

> If Python is already installed, ensure `python` is available from PowerShell by running:
>
> ```powershell
> python --version
> ```

## 2. Install `yt-dlp`
`yt-dlp` performs the actual video downloads.

```powershell
python -m pip install --upgrade yt-dlp
```

## 3. Install FFmpeg (includes `ffmpeg` and `ffprobe`)
1. Download the latest **release full build** from [https://www.gyan.dev/ffmpeg/builds/](https://www.gyan.dev/ffmpeg/builds/).
2. Extract the archive (e.g., `ffmpeg-*-full_build.7z`) to `C:\ffmpeg`.
3. Add `C:\ffmpeg\bin` to the system PATH:
   - Open **Settings → System → About → Advanced system settings**.
   - Click **Environment Variables…**.
   - Under **System variables**, select **Path** → **Edit** → **New** and enter `C:\ffmpeg\bin`.
4. Open a new PowerShell window and verify:

```powershell
ffmpeg -version
ffprobe -version
```

## 4. (Optional) Install `pywin32`
If you plan to use Windows-specific features in future extensions, install:

```powershell
python -m pip install pywin32
```

## 5. Obtain the Application Files
Clone or copy the repository into a convenient location, for example `D:\YouTube`.

```powershell
cd D:\
git clone https://github.com/<your-account>/yt-downloader.git
```

The repository now ships with a Tkinter interface instead of the legacy batch script.  Refer to [README.md](README.md) for a project overview and usage tips.

## 6. Run the UI
From PowerShell:

```powershell
cd D:\YouTube
python ui.py
```

If everything is configured correctly, the Tkinter interface will open and allow you to queue downloads.

## 7. Troubleshooting
- **Command not found** errors typically indicate a missing PATH entry. Revisit steps 1 and 3.
- Make sure PowerShell is restarted after changing environment variables.
- For permission issues, launch PowerShell as Administrator.

With these dependencies installed, the application is ready to download and transcode YouTube videos on Windows.
