

**Kokoro_onnx FP32 and FP8 //Onnxruntime.**
---
### 🚨Update follow with main branch ##
---
### This branch will have .iss, .vbs, and .cmd files for use to create .exe with Inno setup for windows system.

### It stil no icon cuz I don't have skills of acts, if apps has icon will change vbs shortcut to C++ or C# to have icon .exe shortcut like normally program do.
### For .iss code needed to change path files before compile files
</br>
</br>
</br>
</br>
</br>
</br>
</br>
</br>
</br>
</br>
</br>
</br>
</br>
</br></br>
---

**Python versions support**:
- Python 3.10 - 3.13 (Tested on windows: 3.10 to 3.13 run without any issue)
- For python 3.14 it can run but it has memory leak when use run models on CPU be carefull, no issue with GPU



## ⚙️ Technical Details

### Architecture

| Layer               | Technology                        |
| ------------------- | --------------------------------- |
| **Frontend**        | Vanilla JavaScript + Tailwind CSS |
| **Backend**         | FastAPI (Python)                  |
| **TTS Engine**      | Kokoro-82M (ONNX Runtime)         |
| **Desktop Wrapper** | pywebview                         |
| **Audio Export**    | pydub + FFMPEG                    |
| **EPUB Support**    | EbookLib + BeautifulSoup4         |
| **PDF Support**     | PyMuPDF (fitz)                    |

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

### Storage & Installation Estimates

| Component                   | Estimated Size     | Notes                                             |
| :---                        | :---               | :---                                              |
| **App ZIP & Source**        | ~4 MB              | Core application logic and UI                     |
| **Python Environment**      | ~800 MB            | ONNX Runtime, FastAPI, etc. *(PyTorch removed)*   |
| **TTS Engine (GPU)**        | ~309 MB            | Standard FP32 model                               |
| **TTS Engine (CPU)**        | ~87 MB             | Quantized INT8 model                              |
| **Voice Pack**              | ~30 MB             | Shared acoustic data for voices                   |
| **Audio Cache (SQLite)**    | ~200 MB            | Auto-managed (Maximum limit)                      |
| **Document Cache**          | ~~~~               | A little bit larger then original files                               |
| **FFmpeg**                  | ~100 MB            | *Optional* - Downloaded on-demand for MP3 exports |
| **Exported Audio**          | Varies             | ~1 MB (MP3) / ~2.7 MB (WAV) per minute of audio   |
| **CUDA (12.xx)**            | 3.0 - 4.5 GB       | *Optional* - System-level GPU acceleration        |
| **cuDNN (9.xx)**            | ~3.0 GB            | *Optional* - Deep learning GPU primitives         |
>


---

## 🔳 License

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
| **[EbookLib](https://github.com/aerkalov/ebooklib)**                   | AGPL         | EPUB document parsing and unpacking    |
| **[BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/)**   | MIT          | HTML sanitization and TOC generation   |
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
  
