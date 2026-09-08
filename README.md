<div align="center">
  <h1>LocalReader Plus</h1>
</div>

**A modern app that used Kokoro TTS, rebuilt offline reader: fixed, optimized, and significantly improved.**
---
### 🚨Update september 8, 2026 ##
---
<div align="center">
  <h1>Brief</h1>
  <img src="docs/images/image1.png" alt="Brief" width="70%">
</div>




---

**Python versions support**:
- Python 3.10 - 3.13 (Tested on windows: 3.10 to 3.13 run without any issue)
- For python 3.14 can run and seem memories leak it gone.

### Models

*   **High Quality (FP32)**: Load `kokoro.onnx`. (Recommended)
    *   **Priority**: Load CUDA Execution Provider (GPU). 
    *   **Fallback**: CPU 
    *   **Performance**: CPU run near real-time (tested on AMD Ryzen 4800HS, Initial load slower then GPU load). Recommended for maximum audio quality.
 

>For manual install, default dependencies it ONNXRUNTIME CPU it will run on CPU
</br>

## Windows Installation 

### use executable files tool (.exe) - Recommended

&emsp;It will install UV if you don't have yet through PowerShell and you can select onnxruntime CPU/GPU version with .exe or select optional CMD GUI shortcut for change onnxruntime version later.

&emsp;&emsp;-Recommended to used CPU VERSION (Standard) in page ONNX Engine Configuration on Installer (.exe). it mean run Kokoro model on CPU
  
&emsp;&emsp;-It will install all dependencies, automatically.
   
&emsp;&emsp;-This Apps use UV to manage dependencies and virtual environment.


    


</br>

> **Note:** If you want manual install, need to run without a virtual environment or prefer the standard `.venv` method or need to manual install with uv, please see the full instructions in [`INSTALL.txt`](https://github.com/curium-rp/LocalReader-Plus/blob/main/INSTALL.txt).



</br>

## 🍎🐧 Mac & Linux Installation (Virtual Environment with Uv)

> **Note:** I tested on VMware/Wsl Linux can't do test as a full install, it may has some problem or error please understand, and Mac os no hardware tested yet, it may not work.

> **Note:** If you prefer the standard `.venv` method, please visit [`INSTALL.txt`](https://github.com/curium-rp/LocalReader-Plus/blob/main/INSTALL.txt).
>

**Extra setup `ffmpeg` for pydub if didn't have it.**
>
Mac (using homebrew):
```
brew install ffmpeg
```
Linux it will need `ffmpeg` for pydub and `libsndfile` for soundfile and some of pywebview support (using aptitude):
```
sudo apt update && sudo apt install -y build-essential gcc pkg-config libgirepository1.0-dev libcairo2-dev python3-dev gir1.2-gtk-3.0 gir1.2-webkit2-4.1 ffmpeg libavcodec-extra libsndfile1
```

### Step 1: Install Uv

If you don't have `uv` installed, use one of the following commands:

**Using Curl (Linux & macOS):**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Using Homebrew (macOS Alternative):**

```bash
brew install uv

```

### Step 2: Setup and Run Environment

Open your Terminal inside the `LocalReader-Plus/dist/` folder and run the following commands to set everything up:

bash
```bash

uv venv --python 3.12

uv pip install -r requirements.txt

uv run main.py

```
>⚠️ Note **Linux**, if has error fail to build 'pygobject' run this command, then go back to run `uv pip install` again
```
uv pip install --no-cache pycairo PyGObject pywebview[gtk]
```
</br>

>*`uv pip` and `uv run` will automatically find and use virtual environment during subsequent invocations.*

</br>
---

---
</br>

## NVIDIA GPU (WINDOWS) manual setup onnxruntime-gpu
 >more info of about ONNXRUNTIME Execution Providers visit [onnxruntime.ai/docs](https://onnxruntime.ai/docs/execution-providers/)

</br>

### First method install the necessary CUDA and cuDNN runtime DLLs alongside the onnxruntime-gpu package

</br>

If use manual install, first navigate to folder `dist` and open Terminal
If use .exe go to program or folder that you install and click folder `LocalReader plus` > open PowerShell/CMD

>if used installer to install app used shortcut `engine_setup.CMD` or used command line to change it



```
uv pip install onnxruntime-gpu[cuda,cudnn]
```
That all and run `uv run main.py`

</br>
---

### Second method install full version

   install **cuda v12 [https://developer.nvidia.com/cuda-12-8-0-download-archive](https://developer.nvidia.com/cuda-12-8-0-download-archive)**
  
   install **cudnn v9 [https://developer.nvidia.com/cudnn-downloads](https://developer.nvidia.com/cudnn-downloads)**

   _Recomment to use custom install for not break NVDIA app check out of old version of NVDIA apps out and continue_

   _if this process break normal app NVDIA -stick with loading icon- just go download NVDIA app it and re-install_


**IF change files paths install location, you need to go for change paths inside `main.py` to make apps know it**
   
```powershell

uv pip install onnxruntime-gpu
```
>`uv run main.py`


  if Active Hardware Linked = $${\color{green}CUDA}$$  it mean it run on NVIDIA GPU.


---

**Uninstalling:**

**Remove .venv and folder of LocalReader_plus**: 

for uninstall uv go [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)  and scroll down to buttom of web it will have Uninstallation.

For use .exe you can go to settings "installed apps" and uninstall LocalReader Plus

</br>

## 🔘for Original visit [Original LocalReader-Pro](https://github.com/revisionhiep-create/LocalReader-Pro)

</br>

### Custom Pause Settings

1. Open **"Pause Settings"** section in sidebar
2. Adjust sliders to set pause duration (0-2000ms):
   - **Comma (,)** -it may has negative effects for output voice (audio glitches) when use this setting. Default: 0ms
   - **spam symbols (...,?!?,???)** - _it will start apply when have to full stop, (?) and (!), more then 2 
      -it can mix together and it can stack when it has more spam it, more spam more pause default: 0ms
   - **Question (?)** - Default: 600ms
   - **Exclamation (!)** - Default: 600ms
   - **Colon (:)** - Default: 500ms
   - **Semicolon (;)** - Default: 500ms

 **!Playback Behavior :**
   - `Header Pause (H)` Gives the user breathing room between a Chapter Title and the story text (0ms to 10s). default 2 second
         -It will apply in front of header 100% and close H 30% 
         -It will apply less settings ms by H2/2, H3/1.5 - apply H2 half of H1 tag

   - `Image Pause` Creates a temporary silence while an image or cover is displayed on the screen before reading continues (0ms to 20s). default 3 second
   - `Scene Pause` Handles dramatic pauses for elegant scene changes (like *** or ◇◇◇).have it (0ms to 5s). default 1 second
   - `Segment Pause (N)` Controls the tiny micro-pauses between standard text blocks/sentences will have 0-2000ms. default 500ms

   
> Settings save automatically

  </br>
---

## 🔳 Keyboard Shortcuts

| Key                |  Media key          | Action             |
| :---               | :---                | :---               |
| `Space`            | Play/Pause Track    | Play/Pause         |
| `←`                | Previous Track      | Previous Sentence  |
| `→`                | Next Track          | Next Sentence      |
| `Ctrl+F` / `Cmd+F` |          -          | Open Search        |
| `ESC`              |          -          | Close Search       |

---
</br>

## ⚙️ Technical Details

### Architecture

| Layer               | Technology                        |
| ------------------- | --------------------------------- |
| **Frontend**        | Vanilla JavaScript + Tailwind CSS |
| **Backend**         | FastAPI (Python)                  |

<br/>

<details>
<summary><b>Click to expand the full File Tree)</b></summary>

```text
│   .gitattributes
│   .gitignore
│   COPYING
│   INSTALL.txt
│   README.md
│
└───dist
    │   main.py                           # App entry point (FastAPI + WebView)
    │   window_manager.py                 # Native frameless window & event controls
    │   requirements.txt                  # Locked Python dependencies
    │   uv.toml                           # UV tool configuration
    │
    ├───app
    │   │   config.py                     # Anchored paths and system configuration
    │   │   models.py                     # Pydantic data schemas & state models
    │   │   server.py                     # Lifespan startup & route registration
    │   │   state.py                      # Global State Instances 
    │   │   utils.py                      # JSON storage and helper utilities
    │   │
    │   ├───locales/                      # UI translations (en, es, fr, zh)
    │   │
    │   ├───logic/                       
    │   │       html_normalizer.py        # Fast C-based DOM & footnote normalizer
    │   │       smart_content_detector.py  
    │   │       text_normalizer.py        # Sentence & pronunciation rules
    │   │       chinese_g2p.py            # Chinese Grapheme-to-Phoneme converter
    │   │       japanese_g2p.py           # Japanese Grapheme-to-Phoneme converter
    │   │       language_switcher.py      # Dynamic TTS voice routing
    │   │       dependency_manager.py     
    │   │       downloader.py             # Model fetcher & downloader
    │   │       syllable.py               # helper calculator for tts.py
    │   │
    │   ├───models/                       # Local AI models & phonetic data
    │   │   │   kokoro.onnx               # Kokoro-82M TTS neural model
    │   │   │   voices.bin                # Voice embedding vectors
    │   │   │
    │   │   └───Kanjium/
    │   │           kanjium_pitch.json    # Japanese pitch accent dictionary
    │   │
    │   ├───routers/                      # REST API Endpoints
    │   │       library.py                # Book upload, extraction, and TOC
    │   │       tts.py                    # Audio synthesis & pause controls
    │   │       render.py                 # HTML & document settings
    │   │       view.py                   # Usage Documentation Models 
    │   │       settings.py               
    │   │       theme.py                  # Theme color & styling routes
    │   │       timer.py                  # Background sleep timer
    │   │       export.py                 # MP3 & audio export engine
    │   │       system.py                 # Hardware status & engine loader
    │   │
    │   └───ui/                           
    │       │   index.html                # Main Reader Single Page Application
    │       │
    │       ├───css/
    │       │       reader-typography.css # Font & multi-column page styles
    │       │       style.css             # Base application & drawer theme
    │       │
    │       ├───js/
    │       │   │   app.js                # Core UI coordinator
    │       │   │   shortcuts.js          # Keyboard navigation
    │       │   │   search.js             # In-book search engine
    │       │   │
    │       │   └───modules/              # Dedicated ES6 feature controllers
    │       │           topbar.js         # VS Code-style draggable header
    │       │           recent.js         # Sigil-style 1-9 recent files
    │       │           reader-layout.js  # margin control
    │       │           horizontal.js     # Horizontal scroll & layout mode
    │       │           typography.js     # Center control settings(topbar)
    │       │           progress.js       # reading position sync
    │       │           themes.js         # Theme switching & palette hooks
    │       │           tts.js            # WebAudio playback & highlight sync
    │       │           library.js        # Bookshelf & document selector
    │       │           timer.js          # Sleep countdown HUD
    │       │           export.js         # Audio export 
    │       │           resize.js         # Panel & sidebar drag handles
    │       │           api.js            # Backend fetch client
    │       │           state.js          # Frontend reactive state store
    │       │           ui.js             # Toast notifications & DOM utilities
    |       |           wakelock.js       # Api: control sleep screen
    │       │
    │       └───lib/
    │               tailwindcss-with-all-plugins.js # Local offline Tailwind CSS
    │               lucide.min.js                   # Local vector icons
    │
    └───userdata/                         # Local database & user book content

```
</details>

<br/>


**Additional folders created during use:**

- `dist/bin/` - FFMPEG binaries  (auto import it has in system)
- `app/models/` - TTS engine models (auto-downloaded based on your choice)
- `dist/audio files`- for files that Export is inside this folder

### Storage & Installation Estimates

| Component                   | Estimated Size     | Notes                                             |
| :---                        | :---               | :---                                              |
| **App ZIP & Source**        | ~4 MB              | Core application logic and UI                     |
| **Python Environment**      | ~800 MB            | ONNX Runtime, FastAPI, etc. *(PyTorch removed)*   |
| **TTS Engine (FP32)**        | ~309 MB            | Standard FP32 model  (Recommended)                |
| **TTS Engine (INT8)**        | ~87 MB             | Quantized INT8 model                              |
| **Voice Pack**              | ~30 MB             | Shared acoustic data for voices                   |
| **Document Cache**          | ~~~~               | A little bit larger then original files                               |
| **FFmpeg**                  | ~100 MB            | *Optional* - Downloaded on-demand for MP3 exports |
| **Exported Audio**          | Varies             | ~1 MB (MP3) / ~2.7 MB (WAV) per minute of audio   |
| **CUDA (12.xx)**            | 3.0 - 4.5 GB       | *Optional* - System-level GPU acceleration        |
| **cuDNN (9.xx)**            | ~3.0 GB            | *Optional* - Deep learning GPU primitives         |
>
>Preloading DLLs from NVIDIA Site Packages with onnxruntime [cuda and cudnn] : Estimated Size 2.37 GB


> **🎙️ Export ** > Support export with Toc point to point and single point _
   - Point to point mean can select start point and end point with Header tag (default)
   - Separate files, mean point to point but will save one by one of chapter/header.
   - Single mode, mean select only one chapter of books and Export.
   


#### Estimated Installation Totals
*Calculated using the Base App + Python Environment + Models. Excludes optional CUDA/cuDNN installations, user document caches, and exported audio files.* 

* **Total (FP32 model):** ~900 MB 
* **Total (INT8 model):** ~1000 MB *(Standard Quality)*
* **Total (Both Engines):** ~1100 MB *(Maximum Flexibility)*

---

### Data Storage

- **100% Local:** All documents, settings, and exports stored on your machine, only used internet when install or download voice models
- **No Cloud:** Zero data sent to external servers
- **No Accounts:** No login, no sign-up, no user tracking

### Analytics & Telemetry

- **Zero Tracking:** No analytics, no usage stats, no crash reports
- **No Cookies:** Web UI runs locally
- **No Logs:** App doesn't phone home

### File Access

- **Read-Only Documents:** PDFs/EPUBs are only read (never modified)
- **Writable Folders:** Only `userdata/`, `audio files/`, `models/`, `bin/`, and `.cache/`
- **No Background Access:** App closes completely when you exit

---



### LocalReader Plus (Fork of LocalReader Pro)

- **Application Logic:** Proprietary modification fork (Feel free to review, modify, and use personally).
- **Redistribution:** Please contact the author for permission.
- **Open Source copyright Note:** This project links to and utilizes dependencies licensed under open-source agreements (including MIT, AGPL, and GPL). In compliance with those underlying libraries, the raw source code of this fork is publicly accessible for review and personal modification here on GitHub.


### 📜 Open Source Acknowledgements

This project is made possible thanks to the following open-source libraries and frameworks:

| Component/Library                                                      | License      | Usage                                  |
| :---                                                                   | :---         | :---                                   |
| **[FastAPI](https://fastapi.tiangolo.com/)**                           | MIT          | High-performance backend API framework |
| **[Kokoro-ONNX](https://github.com/thewh1teagle/kokoro-onnx)**         | MIT          | Core TTS Engine wrapper                |
| **[ONNX Runtime](https://onnxruntime.ai/)**                            | MIT          | Hardware-accelerated AI inference      |
| **[PyMuPDF](https://pymupdf.readthedocs.io/)**                         | GNU AGPL     | Native PDF text and image extraction   |
| **[Selectolax](https://github.com/rushter/selectolax)**                | MIT          | HTML sanitization, TOC generation   |
| **[Fugashi](https://github.com/polm/fugashi)**                         | MIT          | Japanese morphological analysis        |
| **[jaconv](https://github.com/ikegami-yukino/jaconv)**                 | MIT          | Jp/zh character width normalization    |
| **[num2words](https://github.com/savoirfairelinux/num2words)**         |  LGPL        |Handle reading number             |
| **[FFmpeg](https://ffmpeg.org/)**                                      | GPL / LGPL   | On-demand audio format conversion      |
---

## ⚪ Credits

### Core Technologies

- **TTS Engine:** [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) by hexgrad
- **UI Framework:** [Tailwind CSS](https://tailwindcss.com/) / [github](https://github.com/tailwindlabs/tailwindcss)
- **Icons:** [Lucide](https://lucide.dev/)
- **Audio Processing:** [FFMPEG](https://ffmpeg.org/)
  
---
</br>

### Found a Bug? Support ###

  0.  check error massage in terminal (If use .exe it will have `error.log` report )
  1. Open ticket with:
      - Python version (`python --version`)
      - OS
      - Error message or screenshot

      

  _New feature? ticket, request, or help me and pull request_
      

 
</br>
---
</br>

**Engine:** Kokoro onnx-82M (Dual-Mode: FP32/INT8)

**Last Original LocalReader Pro updated**: January 6, 2026
---
</br>
---

**Epub or Pdf files should not be DRM (Digital Rights Management)**

**Enjoy listening ! 🔳⚪**

