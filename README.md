# YouTube Downloader UI

A cross-platform Tkinter application that wraps `yt-dlp`, `ffmpeg`, and `ffprobe` to download and transcode YouTube videos.  The tool began life as a Windows batch script but now provides a richer desktop interface with metadata previews and parallel downloads.

## Features
- **Multi-download queue** – submit multiple URLs and monitor their status in real time.
- **Thumbnail & title preview** – see what will be downloaded before starting.
- **Dynamic bitrate control** – automatically calculates an appropriate H.264 bitrate, always using a slow preset for quality.
- **Flexible output management** – either store each download in its own folder or collect everything into a shared `All_Videos` directory.
- **Automatic clean-up** – removes intermediate source and temporary files once a download finishes.

## Requirements
- Python 3.10 or newer
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp)
- [`ffmpeg`](https://ffmpeg.org/) (which includes `ffprobe`)
- Optional: [`Pillow`](https://python-pillow.org/) for broader thumbnail format support

Windows users can follow the detailed instructions in [WINDOWS_SETUP.md](WINDOWS_SETUP.md).  On other platforms, install the dependencies through your package manager or `pip`, ensuring the command line tools are available on the `PATH`.

## Getting Started
1. Clone or download this repository.
2. Install the required dependencies listed above.
3. From the project directory, launch the UI:
   ```bash
   python ui.py
   ```

## Using the UI
1. **Paste a YouTube URL** into the input field.  The application will fetch the title and thumbnail preview automatically.
2. (Optional) **Choose a root folder** where download folders or the `All_Videos` directory should live.  The default is `D:/YouTube` on Windows.
3. **Toggle “В окрему папку”** to decide whether to keep each video in its own folder (checked) or collect all finished MP4 files into a shared `All_Videos` folder (unchecked).
4. Press **“Старт”** to enqueue the download.  You can queue multiple videos—the worker threads run in parallel.
5. Monitor progress in the **status list** and the **log pane**.  Once a download finishes successfully, use the **“Відкрити папку”** button to open its output folder.

## Troubleshooting
- Confirm that `python`, `yt-dlp`, `ffmpeg`, and `ffprobe` are callable from a terminal or PowerShell session.
- If thumbnails fail to display, ensure Pillow is installed: `python -m pip install Pillow`.
- Review the log pane for any error messages emitted by the external tools.

## License
This project is released under the MIT License.  See [LICENSE](LICENSE) if provided, or adapt to your needs.
