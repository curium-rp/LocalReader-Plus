# LocalReader Plus

**A modern, privacy-focused PDF/EPUB reader with AI-powered text-to-speech, multilingual support, and smart audio caching ~~that authors didn't active~~ .**
### Supprot LavaSR for Upscale audio from 24 up to 48kHz
 **bata apps**



# What difference from LocalReader Pro(main)

   -  Have feature that handle reading number 
   -  Have more smart chunk (cut sentence before reach limit), is use IPA for count if reach limit of 510 phoneme if it nearly limit will cut it, smoothly.
   -  Have support with GPU NVDIA _NEED more setup_
   -  Delay audio startup or buffer startup, set to 1000ms - 1 seconde
   -  No .exe options for windows.
   -  Have themes

   _THIS Apps has been modifly for use with gpu NVDIA if can't detect or didn't have it will fallback for CPU don't worries_

# Many bug has been fix  #
   
   **-Fix pause setting not respond.**
   
   **-Fix preload system when apply pause setthing.** _tread off, when play audio and change in real-time, it need to wait a little for settings apply when change pause, i mean it need to wait -old buffer setting end._
   
   **-Fix cache system cause repreat and skip reading, randomly.**
   
   **-Fix buffer not works as expect**
   
---
# Windows installation

   _Install **Python 3.12** if not install yet._
   
  
  ## **First method for download** 
   Choose folders that needed to install and open teminal - _can delete .git in folder_
   
```
git clone https://github.com/curium-rp/LocalReader-Plus
```

```
cd LocalReader-Plus\dist
pip install -r requirements.txt
```
**And run**

Uninstall onnxruntime and install back 
```
pip uninstall onnxruntime 
```
```
pip install onnxruntime
```
```
python main.py
```
 ---
   ## **Second method** download zip and unzip it.

   Go to **LocalReader-Plus\ "dist"**  open teminal inside folder dist and run 
```
pip install -r requirements.txt
```
**And run**
Uninstall onnxruntime and install back 
```
pip uninstall onnxruntime 
```
```
pip install onnxruntime
```

```
python main.py
```



**(Run or skip to next step for NVIDIA GPU setup)**

---
If missing something just install it.
 
   > pip install

And open trick tell what missing, i will add in requirements.txt

---
## This is what needed to do for KOKORO model for run on NVIDIA GPU on WINDOWS

   install **cuda v12 [https://developer.nvidia.com/cuda-12-8-0-download-archive](https://developer.nvidia.com/cuda-12-8-0-download-archive)**
  
   install **cudnn v9 [https://developer.nvidia.com/cudnn-downloads](https://developer.nvidia.com/cudnn-downloads)**

if this process break normal app NVDIA -stick with loading icon- just go download NVDIA app it and re-install

    
   **Go to or find it "CUDNN> v9.XX >bin"**
Default locations 

> C:\Program Files\NVIDIA\CUDNN\v9.23\bin\12.9\x64

   It has many of  **.dll** files in bis folder **copy** all of em to **LocalReader-Plus\dist\bin** if didn't have create it

   Install onnxruntime-gpu  _Make sure you don't have onnxruntime cpu, it will cause conflicts_
   
   **-First uninstall both**
   
```
pip uninstall onnxruntime onnxruntime-gpu -y

```
   **-Second install onnxruntime-gpu**

```
pip install onnxruntime-gpu
```

   _Open powershell in **"dist"** folder_

> python main.py 

  Try to play it if didn't see red color text and  see yellow text say in last parts something like  "only guarantees to be correct if indices are not duplicated"  (don't forgot to download GPU models is need voice engine to works)
   
   It mean is run on GPU enjoy.


---

**Uninstalling:**

To completely remove the supporting software (Python and Libraries):

**Remove Libraries**: If you haven't deleted the folder yet, open a terminal in the "dist" folder and run: `pip uninstall -r requirements.txt`

**Uninstall Python**: Go to Windows Settings > Apps > Installed Apps, search for "Python 3.12", and select Uninstall.

**Clear Model Cache**: Many voices and AI models are stored in your user profile. You can delete the `.cache` folder in your user directory (usually `C:\Users\<YourName>\.cache\kokoro`) to free up additional space.

---

### Linux / Manual Installation

**Prerequisites:** Python 3.10 - 3.13 (Recommended: Python 3.12)

> ⚠️ **Important:** Python 3.14+ is not yet supported due to `onnxruntime` compatibility.

**Step 1: Install Python**

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3.12 python3.12-pip python3.12-venv

# Verify installation
python3.12 --version
```

**Step 2: Extract and Navigate**

```bash
unzip LocalReader-Plus-main.zip
cd LocalReader-Plus-main/dist
```

**Step 3: Install Dependencies**

```bash
# Option A: Using pip
pip install -r requirements.txt

# Option B: Using python -m pip (if pip not in PATH)
python3.12 -m pip install -r requirements.txt
```

This will install:

- FastAPI (web framework)
- uvicorn (web server)
- torch (PyTorch for ML)
- kokoro-onnx (TTS engine)
- pydub (audio processing)
- pywebview (desktop wrapper)
- And other dependencies

**Installation time:** 5-10 minutes (downloading PyTorch ~2GB)

**Step 4: Launch the App**

```bash
python3.12 main.py
```

---
## 🔘for full Key Features visit [Original LocalReader-Pro](https://github.com/revisionhiep-create/LocalReader-Pro)

## Themes on icon "LocalReader" and GUI
<div align="center">
  <img src="docs/images/image1.png" alt="Themes settings and UI" width="100%">
</div>

### Custom Pause Settings

1. Open **"Pause Settings"** section in sidebar
2. Adjust sliders to set pause duration (0-2000ms):
   - **Comma (,)** - Default: 250ms
   - **Period (.)** - Default: 600ms
   - **Question (?)** - Default: 600ms
   - **Exclamation (!)** - Default: 600ms
   - **Colon (:)** - Default: 500ms
   - **Semicolon (;)** - Default: 500ms
   - **Newline** - Dynamics adjustment (Hidden) 
          
            speed = [0.50, 0.75, 1.00, 1.20, 1.35, 1.50, 1.75, 2.00, 2.50, 3.00]
            pause = [800,  550,  400,  320,  100,  85,   70,   50,   35,   25]
            You can change in tts.py in dist\app\routers keyward "dynamics adjust"
   
3. Settings save automatically

**Smart Behavior:**

- Pauses apply only to single punctuation or the last char of a group
- `"..."` creates ONE pause (e.g. 600ms), not three
- `"?!` creates ONE pause (based on `!`)

~~- `Title\n` creates a soft pause (300ms)~~ can't find code that have this function and if we need this, is need to redesign many things.


---

## 🔳 Keyboard Shortcuts

| Key                | Action            |
| ------------------ | ----------------- |
| `Space`            | Play/Pause        |
| `←`                | Previous Sentence |
| `→`                | Next Sentence     |
| `Ctrl+F` / `Cmd+F` | Open Search       |
| `ESC`              | Close Search      |

---

## ⚙️ Technical Details

### Architecture

| Layer               | Technology                        |
| ------------------- | --------------------------------- |
| **Frontend**        | Vanilla JavaScript + Tailwind CSS |
| **Backend**         | FastAPI (Python)                  |
| **TTS Engine**      | Kokoro-82M (ONNX Runtime)         |
| **Desktop Wrapper** | pywebview                         |
| **PDF Parsing**     | PDF.js (Mozilla)                  |
| **Audio Export**    | pydub + FFMPEG                    |
| **EPUB Support**    | ebooklib + xhtml2pdf              |
| **LavaSR V2**       | Upscale audio                     |

### File Structure

```
LocalReader-Plus
├── README.md
├── CHANGELOG.md
└── dist/
    ├── main.py                  # App entry point (FastAPI + WebView)
    │
    ├── app/
    │   ├── server.py            # FastAPI initialization
    │   ├── state.py             # Global engine/status singleton
    │   ├── routers/             # API Controllers (TTS, Library, Export,etc.)
    │   ├── logic/               # Core logic (Normalize, Detector, Cache)
    │   ├── locales/             # UI Translations (EN, ES, FR, ZH, JA)
    │   |── ui/
    │   └── LavaSR               # Upscale voice auto downloads enhance_v2 models
    │       ├── index.html       # Main SPA
    │       ├── css/style.css    # Premium styling
    │       └── js/modules/      # ES6 Logic modules
    │
    └── userdata/                # User settings and book database
```

**Additional folders created during use:**

- `bin/` - FFMPEG binaries  ~~(auto-downloaded on first export)~~
- `models/` - TTS engine models (auto-downloaded based on your choice)
- `userdata/audio_cache.db` - SQLite Audio Cache

### Storage Requirements

| Component                 | Size                                |
| ------------------------- | --------------------------          |
| **-ZIP**                  | ~1 MB                               |
| **App Files**             | ~3 MB                               |
| **Python Dependencies**   | ~2 GB (PyTorch, etc.)               |
| **TTS Engine (GPU Mode)** | ~309 MB                             |
| **TTS Engine (CPU Mode)** | ~87 MB                              |
| **Voice Pack (shared)**   | ~30 MB                              |
| **FFMPEG**                | ~100 MB (optional for MP3 output)   |
| **Audio Cache (SQLite)**  | ~200 MB max (auto-managed)          |
| **Per Document Cache**    | ~1-5+ MB                            |
| **Exported WAV / MP3**    | ~1 MB / ~2.7 MB per minute of audio |
| **Cudnn 9.xx**            | ~3 GB (optional)                    |
| **Cuda 12.xx**            | ~3 GB to 4.5 GB (optional)          |

**Exported WAV or MP3 for hole books can't pick start point**

**Total (GPU Mode):** ~2.6 GB (without exported audio)  _not include Cudnn and cuda_
**Total (CPU Mode):** ~2.4 GB (saves ~220MB)  
**Total (Both Engines):** ~2.8 GB (maximum flexibility)

### System Requirements

| Component      | Minimum                     | Recommended                    |
| -------------- | --------------------------- | ------------------------------ |
| **OS**         | Windows 10+ / Ubuntu 20.04+ | Windows 11 / Ubuntu 22.04+     |
| **Python**     | 3.10 - 3.13                 | 3.12.10                        |
| **RAM**        | 4 GB                        | 8 GB+                          |
| **Disk Space** | 3 GB free                   | 20 GB+ free                    |
| **CPU**        | Dual-core 2.0 GHz           | Quad-core 2.5 GHz+   NVIDA GPU |
| **Internet**   | Required for setup only     | Offline after setup            |

---

## 🔘 Privacy & Security

### Data Storage

- **100% Local:** All documents, settings, and exports stored on your machine
- **No Cloud:** Zero data sent to external servers
- **No Accounts:** No login, no sign-up, no user tracking

### Network Usage

- **Setup Only:** Internet required for:
  1. Downloading Python (Windows installer only, ~100 MB)
  2. Installing dependencies (~2 GB)
  3. Downloading Kokoro-82M model (~309 MB)
  4. Downloading FFMPEG (~100 MB, optional)
- **Fully Offline:** After setup, works without internet indefinitely

### Analytics & Telemetry

- **Zero Tracking:** No analytics, no usage stats, no crash reports
- **No Cookies:** Web UI runs locally
- **No Logs:** App doesn't phone home

### File Access

- **Read-Only Documents:** PDFs/EPUBs are only read (never modified)
- **Writable Folders:** Only `userdata/`, `models/`, `bin/`, and `.cache/`
- **No Background Access:** App closes completely when you exit

---

## 🔳 License

###LocalReader plus (main LocalReader Pro )

- **Code:** Proprietary (review, modify, use personally)
- **Redistribution:** Contact author for permission

### Third-Party Components

| Component        | License      |
| ---------------- | ------------ |
| **Kokoro-82M**   | Apache 2.0   |
| **FastAPI**      | MIT          |
| **PyTorch**      | BSD-3-Clause |
| **PDF.js**       | Apache 2.0   |
| **Tailwind CSS** | MIT          |
| **Lucide Icons** | ISC          |
| **FFMPEG**       | LGPL 2.1+    |
| **Cudnn 9.xx**   | EULA         |
| **Cuda 12.xx**   | EULA         |
| **LavaSR V2**    | Apache-2.0   |
---

## ⚪ Credits

### Core Technologies

- **TTS Engine:** [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) by hexgrad
- **PDF Rendering:** [PDF.js](https://mozilla.github.io/pdf.js/) by Mozilla
- **UI Framework:** [Tailwind CSS](https://tailwindcss.com/) / [github](https://github.com/tailwindlabs/tailwindcss)
- **Icons:** [Lucide](https://lucide.dev/)
- **Audio Processing:** [FFMPEG](https://ffmpeg.org/)
- **Audio Upscaling:** [LavaSR](https://github.com/ysharma3501/LavaSR)
  
---

### Found a Bug? Support

  1. Check **Troubleshooting** section above
  2. Verify you're on latest version 
~~3. Check CHANGELOG.md for known issues~~
  4. Open ticket with:
      - Python version (`python --version`)
      - OS
      - Error message or screenshot
      
   
 

---


**Engine:** Kokoro-82M (Dual-Mode: CPU/GPU)
~~Last Updated LocalReader Pro: January 6, 2026~~

**Last Updated this fork** June 14, 2026

Bata 

---

**Enjoy ! 🔳⚪**
