# Flask Self-Hosted Backup

A simple Flask server to backup photos and videos from your iPhone directly to your PC over your local network.

## Features

- 📸 Backup photos and videos from iPhone to PC
- 🚀 Fast transfers (same network)
- 🔄 Prevents duplicate files
- 🎬 Converts QuickTime videos to MP4 (requires ffmpeg)
- 📊 Web dashboard to monitor uploads
- 🔌 Works with iOS Shortcuts

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure save directory:**
   Edit `receiver.py` and change `SAVE_DIR` (line 23) to your desired folder:
   ```python
   SAVE_DIR = r"C:\Users\kmavillanosa\Pictures\IPHONE"  # <-- change this
   ```

3. **Run the server:**
   ```bash
   python receiver.py
   ```
   
   Or run in background on Windows:
   ```bash
   start_background.bat
   ```

4. **Access the dashboard:**
   Open `http://localhost:5001` in your browser

## iPhone Setup

1. Make sure your iPhone and PC are on the same network
2. Install the Shortcuts app from the App Store
3. Visit `http://YOUR_PC_IP:5001/shortcut` on your iPhone for setup instructions

## Optional: FFmpeg for Video Conversion

FFmpeg is only needed to convert `.quicktime` files to `.mp4`. Regular videos (`.mov`, `.mp4`) work without it.

## Stop the Server

On Windows, use:
```bash
stop_background.bat
```

Or press `Ctrl+C` if running in terminal.

