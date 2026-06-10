# LocalReader Plus

**A modern, privacy-focused PDF/EPUB reader with AI-powered text-to-speech, multilingual support, and smart audio caching ~~that authors didn't active~~ .**

_And i found out it has many bug and problem that why i started to debug this project_

# What difference from LocalReader Pro(main)

   -  have feature that handle reading number 
   -  have more smart chunk to make models not overload itself, is use IPA for count if reach limit of 510 phoneme if it nearly limit will cut it, smoothly.
   -  have support with gpu NVDIA _NEED more setup_
   -  delay audio startup or buffer startup, set to 1000ms - 1 seconde
   -  No .exe options for windows

   _THIS model is has been modifly for use with gpu NVDIA if can't detect if will fallback for cpu 
   don't worries, if didn't use windows is has detect os in this code, maybe didn't have problem with Mac and Linux_

# many bug has been fix  #
   
   **-fix pause setting not respond.**
   
   **-fix preload system when apply pause setthing.** _tread off, need to wait a little to effect when change pause , i mean it need to wait buffer that preload forward end._
   
   **-fix cache system cause repreat and skip reading, randomly.**
   
   **-fix buffer not works as expect**

# windows installation

   _Only have manual install and if needed to use with NVIDIA GPU more setup to do use it_
   _Install **Python 3.12** if not install yet._
  
   **First method** choose folders that needed to install and open teminal - _can delete .git in folder_
   
```
git clone https://github.com/curium-rp/LocalReader-Plus.git
```
 
   **Second mrthod** download zip and unzip it.

   go to **LocalReader-Plus\ "dist"**  open teminal inside folder dist and run 
```
pip install -r requirements.txt
```

```
python main.py
```

**run or skip to next step for GPU setup**

if missing something add it 
 
   > pip install

what missing 

# this is what needed to do for KOKORO model for run on GPU for NVIDIA WINDOWS

   install **cuda v12 [https://developer.nvidia.com/cuda-90-download-archive](https://developer.nvidia.com/cuda-90-download-archive)**
  
   install **cudnn v9 [https://developer.nvidia.com/cudnn-downloads](https://developer.nvidia.com/cudnn-downloads)**

if this process break normal app NVDIA -stick with loading icon- just go download NVDIA app it and re-install

    
   **go to or find it "CUDNN> v9.XX >bin"**
default locations 
> C:\Program Files\NVIDIA\CUDNN\v9.23\bin\12.9\x64

   it has many of  **.dll** files in bis folder **copy** all of em to **LocalReader-Plus\dist\bin** if didn't have create it

   install onnxruntime-gpu  _Make sure you don't have onnxruntime cpu as it will cause conflicts_
   
   **-first uninstall both**
   
```
pip uninstall onnxruntime onnxruntime-gpu -y

```
   **-second install onnxruntime-gpu**

```
pip install onnxruntime-gpu
```

   _open powershell in **"dist"** folder try to play it_

> python main.py 

   if didn't see read color text and kokoro run on GPU when play audio
   it mean is run on GPU enjoy.


---

## 🔘 Key Features


~~**Uninstalling:**~~



To completely remove the supporting software (Python and Libraries):

**Uninstall Python**: Go to Windows Settings > Apps > Installed Apps, search for "Python 3.12", and select Uninstall.

**Remove Libraries**: If you haven't deleted the folder yet, open a terminal in the "dist" folder and run: `pip uninstall -r requirements.txt`

**Clear Model Cache**: Many voices and AI models are stored in your user profile. You can delete the `.cache` folder in your user directory (usually `C:\Users\<YourName>\.cache\kokoro`) to free up additional space.

**Installation Size:**

zip file -1 MB  unzip files 3-4MB
- ~~Installer: ~24 MB~~
- Full installation: ~2.6 GB (including Python dependencies)

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

### You can read full deteil in LocalReader Pro [LocalReader Pro](https://github.com/revisionhiep-create/LocalReader-Pro)**

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
~~- `Title\n` creates a soft pause (300ms)~~ can't find code and if do needed to redesign many things.


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

### File Structure

```
LocalReader-Pro/
├── build_installer.py           # Master build script
├── installer_logic.py           # setup.exe core logic
├── README.md
├── CHANGELOG.md
│
└── dist/
    ├── main.py                  # App entry point (FastAPI + WebView)
    ├── launch.vbs               # Silent runner
    │
    ├── app/
    │   ├── server.py            # FastAPI initialization
    │   ├── state.py             # Global engine/status singleton
    │   ├── routers/             # API Controllers (TTS, Library, Export, etc.)
    │   ├── logic/               # Core logic (Normalize, Detector, Cache)
    │   ├── locales/             # UI Translations (EN, ES, FR, ZH, JA)
    │   └── ui/
    │       ├── index.html       # Main SPA
    │       ├── css/style.css    # Premium styling
    │       └── js/modules/      # ES6 Logic modules
    │
    └── userdata/                # User settings and book database
```

**Additional folders created during use:**

- `bin/` - FFMPEG binaries (auto-downloaded on first export)
- `models/` - TTS engine models (auto-downloaded based on your choice)
- `userdata/audio_cache.db` - SQLite Audio Cache

### Storage Requirements

| Component                 | Size                       |
| ------------------------- | -------------------------- |
| **-ZIP**                  | ~1 MB                      |
| **App Files**             | ~3 MB                      |
| **Python Dependencies**   | ~2 GB (PyTorch, etc.)      |
| **TTS Engine (GPU Mode)** | ~309 MB                    |
| **TTS Engine (CPU Mode)** | ~87 MB                     |
| **Voice Pack (shared)**   | ~30 MB                     |
| **FFMPEG**                | ~100 MB (optional)         |
| **Audio Cache (SQLite)**  | ~200 MB max (auto-managed) |
| **Per Document Cache**    | ~1-5 MB                    |
| **Exported MP3**          | ~1 MB per minute of audio  |
| **Cudnn 9.xx**            | ~3 GB (optional)           |
| **Cuda 12.xx**            | ~3 GB to 4.5 GB (optional) |

**Total (GPU Mode):** ~2.6 GB (without exported audio)  _not include Cudnn and cuda_
**Total (CPU Mode):** ~2.4 GB (saves ~220MB)  
**Total (Both Engines):** ~2.8 GB (maximum flexibility)

### System Requirements

| Component      | Minimum                     | Recommended                |
| -------------- | --------------------------- | -------------------------- |
| **OS**         | Windows 10+ / Ubuntu 20.04+ | Windows 11 / Ubuntu 22.04+ |
| **Python**     | 3.10 - 3.13                 | 3.12.10                    |
| **RAM**        | 4 GB                        | 8 GB+                      |
| **Disk Space** | 3 GB free                   | 20 GB+ free                |
| **CPU**        | Dual-core 2.0 GHz           | Quad-core 2.5 GHz+         |
| **Internet**   | Required for setup only     | Offline after setup        |

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
---

## ⚪ Credits

### Core Technologies

- **TTS Engine:** [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) by hexgrad
- **PDF Rendering:** [PDF.js](https://mozilla.github.io/pdf.js/) by Mozilla
- **UI Framework:** [Tailwind CSS](https://tailwindcss.com/)-(https://github.com/tailwindlabs/tailwindcss)
- **Icons:** [Lucide](https://lucide.dev/)
- **Audio Processing:** [FFMPEG](https://ffmpeg.org/)

### Python Libraries

- FastAPI, uvicorn, torch, onnxruntime, pydub, soundfile, pywebview, ebooklib, beautifulsoup4, and more (see `requirements.txt`)

---

## 🔘 Support

### Found a Bug?

1. Check **Troubleshooting** section above
2. Verify you're on latest version 
3. Check `CHANGELOG.md` for known issues
4. Contact developer with:
   - Python version (`python --version`)
   - Error message or screenshot
   - Steps to reproduce

### Feature Requests

- Review `CHANGELOG.md` to see if already implemented
- Describe use case and expected behavior
- Provide examples or mockups if applicable

---


**Engine:** Kokoro-82M (Dual-Mode: CPU/GPU)
**Last Updated LocalReader Pro:** January 6, 2026

**Last Updated this fork** June 10, 2026

---

**Enjoy your reading! 🔳⚪**
